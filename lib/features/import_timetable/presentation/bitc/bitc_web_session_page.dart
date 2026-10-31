import 'dart:async';
import 'dart:convert';

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../../../../app/widgets/adaptive_scaffold.dart';
import '../../../../core/platform/adaptive_ui.dart';

class BitcWebSessionPage extends StatefulWidget {
  const BitcWebSessionPage({
    required this.request,
    this.savedAccount,
    this.autoFetch = false,
    this.onClearSession,
    super.key,
  });

  final BitcTimetableWebRequest request;
  final String? savedAccount;
  final bool autoFetch;

  /// Clears the WebView cookies and the persisted BITC session snapshot so the
  /// user can re-authenticate from a clean slate. Wired by the caller.
  final Future<void> Function()? onClearSession;

  @override
  State<BitcWebSessionPage> createState() => _BitcWebSessionPageState();
}

class BitcTimetableWebRequest {
  const BitcTimetableWebRequest({
    required this.academicYearStart,
    required this.termCode,
  });

  final String academicYearStart;
  final String termCode;
}

class _BitcWebSessionPageState extends State<BitcWebSessionPage> {
  static final _entryUri = Uri.parse(
    'https://jwxt.vpn.bitc.edu.cn/kbcx/'
    'xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default',
  );

  /// The campus web-VPN portal. Entering here lets the gateway rebuild the
  /// per-session site permission table before the timetable host is requested.
  static final _portalUri = Uri.parse('https://vpn.bitc.edu.cn/');

  late final WebViewController _controller;
  Completer<String>? _payloadCompleter;
  bool _isLoading = true;
  bool _isFetching = false;
  bool _canFetch = false;
  bool _autoFetchAttempted = false;
  String? _message;
  String? _gatewayError;
  bool _isClearingSession = false;

  /// Bumped on every main-frame navigation so late JavaScript reads from an
  /// older page can never overwrite the state of the page the user sees.
  int _navigationGeneration = 0;

  /// At most one automatic "go through the VPN portal" recovery per page
  /// instance. Later failures show the manual repair panel instead of looping.
  int _recoveryAttempts = 0;
  bool _pendingPortalBootstrap = false;
  bool _isRecovering = false;
  bool _showRawPage = false;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..addJavaScriptChannel(
        'SchedulrBridge',
        onMessageReceived: (message) {
          final completer = _payloadCompleter;
          if (completer == null || completer.isCompleted) return;
          final envelope = jsonDecode(message.message);
          if (envelope is! Map<String, Object?>) {
            completer.completeError(
              const FormatException('Invalid bridge response.'),
            );
            return;
          }
          final error = envelope['error'];
          final payload = envelope['payload'];
          if (error is String && error.isNotEmpty) {
            completer.completeError(StateError(error));
          } else if (payload is String) {
            completer.complete(payload);
          } else {
            completer.completeError(
              const FormatException('Missing timetable payload.'),
            );
          }
        },
      )
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageStarted: (_) {
            _navigationGeneration++;
            if (!mounted) return;
            setState(() {
              _isLoading = true;
              _message = null;
            });
          },
          onPageFinished: (url) {
            final generation = _navigationGeneration;
            unawaited(_handlePageFinished(url, generation));
          },
          onNavigationRequest: (request) {
            final uri = Uri.tryParse(request.url);
            if (uri == null ||
                uri.scheme != 'https' ||
                !_isAllowedHost(uri.host)) {
              setState(() => _message = '已阻止离开北京信息职业技术学院域名的跳转。');
              return NavigationDecision.prevent;
            }
            return NavigationDecision.navigate;
          },
          onWebResourceError: (error) {
            if (error.isForMainFrame == false || !mounted) return;
            setState(() {
              _isLoading = false;
              _canFetch = false;
              _message = '学校登录页面加载失败，请检查网络或 VPN 服务状态。';
            });
          },
        ),
      )
      ..loadRequest(_entryUri);
  }

  bool _isAllowedHost(String host) {
    return host == 'bitc.edu.cn' || host.endsWith('.bitc.edu.cn');
  }

  bool _isEntryPage(Uri? uri) {
    return uri != null &&
        uri.host == 'jwxt.vpn.bitc.edu.cn' &&
        uri.path.contains('xskbcx_cxXskbcxIndex.html');
  }

  Future<void> _prefillAccount() async {
    final account = widget.savedAccount?.trim();
    if (account == null || account.isEmpty || !mounted) return;
    try {
      await _controller.runJavaScript(buildBitcAccountPrefillScript(account));
    } on Object {
      // Login markup can change; the user can still enter the account manually.
    }
  }

  Future<String> _readPageText() async {
    try {
      final result = await _controller.runJavaScriptReturningResult(
        'document.body ? document.body.innerText : ""',
      );
      return result is String ? result : '';
    } on Object {
      return '';
    }
  }

  Future<bool> _pageHasPasswordField() async {
    try {
      final result = await _controller.runJavaScriptReturningResult(
        '!!document.querySelector("input[type=password]")',
      );
      return result == true || result == 'true';
    } on Object {
      return false;
    }
  }

  bool _looksLikeLoginPage(Uri? uri) {
    final host = uri?.host.toLowerCase() ?? '';
    final path = uri?.path.toLowerCase() ?? '';
    return host.startsWith('iam.') ||
        host == 'cas.bitc.edu.cn' ||
        path.contains('login') ||
        path.contains('cas') ||
        path.contains('auth');
  }

  Future<void> _handlePageFinished(String url, int generation) async {
    if (!mounted || generation != _navigationGeneration) return;
    final uri = Uri.tryParse(url);
    final onEntry = _isEntryPage(uri);
    setState(() {
      _isLoading = false;
      _canFetch = false;
    });

    // The web-VPN error page is served with HTTP 200 at the requested URL and
    // may paint its text just after `onPageFinished`, so give it a beat before
    // trusting the page.
    await Future<void>.delayed(const Duration(milliseconds: 280));
    if (!mounted || generation != _navigationGeneration) return;
    final pageText = await _readPageText();
    if (!mounted || generation != _navigationGeneration) return;

    if (isBitcGatewayErrorText(pageText)) {
      await _handleGatewayError(generation);
      return;
    }

    setState(() {
      _gatewayError = null;
      _canFetch = onEntry;
    });

    if (widget.savedAccount != null &&
        uri != null &&
        _isAllowedHost(uri.host) &&
        !onEntry) {
      unawaited(_prefillAccount());
    }

    if (_pendingPortalBootstrap) {
      await _continuePortalBootstrap(uri, generation);
      return;
    }

    if (widget.autoFetch && onEntry && !_autoFetchAttempted) {
      _autoFetchAttempted = true;
      unawaited(_fetchTimetable());
    }
  }

  Future<void> _handleGatewayError(int generation) async {
    if (!mounted || generation != _navigationGeneration) return;
    final canAutoRecover =
        _recoveryAttempts < 1 && !_isRecovering && !_isClearingSession;
    setState(() {
      _canFetch = false;
      _showRawPage = false;
      _message = null;
    });

    if (canAutoRecover) {
      // The school's error page recommends clearing cookies, but a stale
      // permission table can usually be rebuilt without logging the user out
      // by entering the VPN portal first. Only fall back to a full logout when
      // that does not work.
      _recoveryAttempts++;
      _pendingPortalBootstrap = true;
      setState(() {
        _gatewayError = null;
        _isRecovering = true;
        _message = '学校网关提示登录状态已失效，正在通过 VPN 门户重新建立会话…';
      });
      await _loadPortal();
      return;
    }

    setState(() {
      _gatewayError = '学校 VPN 网关返回「权限表不同步」，暂时无法进入教务系统。';
    });
  }

  Future<void> _loadPortal() async {
    try {
      await _controller.clearCache();
      if (!mounted) return;
      await _controller.loadRequest(_portalUri);
    } on Object {
      if (!mounted) return;
      setState(() {
        _message = '打开学校 VPN 门户失败，请检查网络后重试。';
        _pendingPortalBootstrap = false;
      });
    } finally {
      if (mounted) setState(() => _isRecovering = false);
    }
  }

  Future<void> _continuePortalBootstrap(Uri? uri, int generation) async {
    final host = uri?.host.toLowerCase() ?? '';
    if (host != 'vpn.bitc.edu.cn') {
      // Already left the portal (login finished or redirect happened).
      if (mounted) setState(() => _pendingPortalBootstrap = false);
      if (widget.autoFetch && _isEntryPage(uri) && !_autoFetchAttempted) {
        _autoFetchAttempted = true;
        unawaited(_fetchTimetable());
      }
      return;
    }

    final waitingForLogin =
        _looksLikeLoginPage(uri) || await _pageHasPasswordField();
    if (!mounted || generation != _navigationGeneration) return;
    if (waitingForLogin) {
      setState(() => _message = '请在学校 IAM 页面完成登录，登录成功后会自动进入个人课表。');
      return;
    }

    setState(() {
      _pendingPortalBootstrap = false;
      _autoFetchAttempted = false;
      _message = '登录成功，正在打开个人课表…';
    });
    // Give the gateway a moment to finish writing the permission table.
    await Future<void>.delayed(const Duration(milliseconds: 600));
    if (!mounted || generation != _navigationGeneration) return;
    await _controller.loadRequest(_entryUri);
  }

  /// Clears the current login session (WebView cookies + persisted snapshot)
  /// and reopens the VPN portal so the user can authenticate from a clean
  /// slate — the remedy the school's own error page recommends.
  Future<void> _clearCookiesAndRelogin() async {
    if (_isClearingSession || !mounted) return;
    setState(() {
      _isClearingSession = true;
      _gatewayError = null;
      _showRawPage = true;
      _pendingPortalBootstrap = true;
      _recoveryAttempts = 1;
      _message = '正在清理登录态…';
    });
    try {
      await (widget.onClearSession?.call() ??
          WebViewCookieManager().clearCookies());
      await _controller.clearCache();
      if (!mounted) return;
      setState(() => _message = '登录态已清理，请在学校 IAM 页面重新登录。');
      await _controller.loadRequest(_portalUri);
    } on Object {
      if (!mounted) return;
      setState(() => _message = '清理登录态失败，请重新打开本页后重试。');
    } finally {
      if (mounted) setState(() => _isClearingSession = false);
    }
  }

  Future<void> _openPortal() async {
    if (_isClearingSession || !mounted) return;
    setState(() {
      _gatewayError = null;
      _showRawPage = true;
      _pendingPortalBootstrap = true;
      _message = '正在打开学校 VPN 门户…';
    });
    await _loadPortal();
  }

  Future<void> _retryEntry() async {
    if (_isFetching || _isClearingSession || !mounted) return;
    setState(() {
      _gatewayError = null;
      _showRawPage = true;
      _pendingPortalBootstrap = false;
      _message = '正在重新加载个人课表…';
    });
    await _controller.loadRequest(_entryUri);
  }

  Future<void> _reloadPage() async {
    if (_isFetching || _isClearingSession) return;
    if (_gatewayError != null) {
      await _retryEntry();
      return;
    }
    await _controller.reload();
  }

  Future<void> _fetchTimetable() async {
    setState(() {
      _isFetching = true;
      _message = null;
    });
    try {
      final completer = Completer<String>();
      _payloadCompleter = completer;
      await _controller.runJavaScript(
        buildBitcTimetableBridgeScript(
          academicYearStart: widget.request.academicYearStart,
          termCode: widget.request.termCode,
        ),
      );
      final payload = await completer.future.timeout(
        const Duration(seconds: 20),
      );
      final decoded = jsonDecode(payload);
      if (decoded is! Map<String, Object?> ||
          decoded['kbList'] is! List<Object?> ||
          decoded['timingProfiles'] is! List<Object?>) {
        throw const FormatException('Response is not a timetable payload.');
      }
      if (!mounted) return;
      Navigator.of(context).pop(payload);
    } on Object {
      if (!mounted) return;
      final pageText = await _readPageText();
      if (!mounted) return;
      if (isBitcGatewayErrorText(pageText)) {
        await _handleGatewayError(_navigationGeneration);
        return;
      }
      setState(() {
        _message = '无法读取课表。请确认页面已经显示“个人课表查询”，然后重试。';
      });
    } finally {
      _payloadCompleter = null;
      if (mounted) setState(() => _isFetching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isCupertino = usesCupertinoConventions(context);
    final enabled = _canFetch && !_isFetching && !_isClearingSession;
    final actionLabel = _isFetching ? '正在读取课表…' : '读取当前学期课表';
    final status = _statusCard(theme);

    final body = Column(
      children: [
        if (_isLoading) const LinearProgressIndicator(minHeight: 3),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: status,
        ),
        if (_message case final message?)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  Icons.info_outline_rounded,
                  size: 18,
                  color: theme.colorScheme.onSurfaceVariant,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    message,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: 10),
        Expanded(
          // Keep the platform view mounted while the repair panel is shown so
          // the school page (and its session) survives toggling the panel.
          child: Stack(
            children: [
              Positioned.fill(child: WebViewWidget(controller: _controller)),
              if (_gatewayError != null && !_showRawPage)
                Positioned.fill(
                  child: ColoredBox(
                    color: theme.colorScheme.surface,
                    child: _GatewayErrorView(
                      summary: _gatewayError!,
                      busy: _isClearingSession,
                      onRelogin: _clearCookiesAndRelogin,
                      onOpenPortal: _openPortal,
                      onShowPage: () => setState(() => _showRawPage = true),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
    );

    final bottomBar = SafeArea(
      minimum: const EdgeInsets.fromLTRB(16, 8, 16, 12),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (!_canFetch)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                _gatewayError != null ? '网关恢复并进入个人课表后才可读取' : '进入个人课表页面后即可读取',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ),
          SizedBox(
            width: double.infinity,
            height: 52,
            child: isCupertino
                ? CupertinoButton.filled(
                    key: const ValueKey('bitc-fetch'),
                    padding: EdgeInsets.zero,
                    onPressed: enabled ? _fetchTimetable : null,
                    child: Text(actionLabel),
                  )
                : FilledButton.icon(
                    key: const ValueKey('bitc-fetch'),
                    onPressed: enabled ? _fetchTimetable : null,
                    icon: _isFetching
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.download_rounded),
                    label: Text(actionLabel),
                  ),
          ),
        ],
      ),
    );

    return AdaptiveScaffold(
      title: const Text('登录 BITC 教务'),
      actions: isCupertino
          ? const []
          : [
              IconButton(
                tooltip: '重新加载',
                onPressed: _isFetching ? null : _reloadPage,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
      cupertinoTrailing: isCupertino
          ? CupertinoButton(
              key: const ValueKey('bitc-reload'),
              padding: EdgeInsets.zero,
              onPressed: _isFetching ? null : _reloadPage,
              child: const Icon(CupertinoIcons.refresh),
            )
          : null,
      body: body,
      bottomNavigationBar: isCupertino ? null : bottomBar,
      cupertinoBottomAction: isCupertino ? bottomBar : null,
    );
  }

  Widget _statusCard(ThemeData theme) {
    final (
      IconData icon,
      String title,
      String subtitle,
      Color background,
      Color foreground,
    ) = switch ((_gatewayError != null, _canFetch, _pendingPortalBootstrap)) {
      (true, _, _) => (
        Icons.cloud_off_rounded,
        '教务网关连不上',
        '学校 VPN 返回「权限表不同步」，需要重建登录会话。',
        theme.colorScheme.errorContainer,
        theme.colorScheme.onErrorContainer,
      ),
      (_, true, _) => (
        Icons.verified_rounded,
        '已进入个人课表',
        '可以读取本学期课程了。应用只读取课表字段，不读取或保存密码。',
        theme.colorScheme.primaryContainer,
        theme.colorScheme.onPrimaryContainer,
      ),
      (_, _, true) => (
        Icons.sync_rounded,
        '正在重建学校登录',
        '通过学校 VPN 门户重新建立会话，稍后自动进入课表。',
        theme.colorScheme.secondaryContainer,
        theme.colorScheme.onSecondaryContainer,
      ),
      _ => (
        Icons.shield_outlined,
        '登录学校教务',
        '在下方学校页面完成 VPN / IAM 登录。只允许访问 bitc.edu.cn 域名。',
        theme.colorScheme.surfaceContainerHigh,
        theme.colorScheme.onSurface,
      ),
    };

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(18),
      ),
      child: Row(
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: foreground.withValues(alpha: 0.12),
              shape: BoxShape.circle,
            ),
            child: Icon(icon, size: 21, color: foreground),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: theme.textTheme.titleSmall?.copyWith(
                    color: foreground,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: foreground.withValues(alpha: 0.82),
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _GatewayErrorView extends StatelessWidget {
  const _GatewayErrorView({
    required this.summary,
    required this.busy,
    required this.onRelogin,
    required this.onOpenPortal,
    required this.onShowPage,
  });

  final String summary;
  final bool busy;
  final VoidCallback onRelogin;
  final VoidCallback onOpenPortal;
  final VoidCallback onShowPage;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 440),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 72,
                height: 72,
                decoration: BoxDecoration(
                  color: theme.colorScheme.errorContainer,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  Icons.wifi_tethering_error_rounded,
                  size: 34,
                  color: theme.colorScheme.onErrorContainer,
                ),
              ),
              const SizedBox(height: 18),
              Text(
                '学校网关暂时进不去教务系统',
                textAlign: TextAlign.center,
                style: theme.textTheme.titleLarge?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                summary,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                '这不是账号或密码错误。清理学校登录状态后重新登录一般即可恢复；'
                '若多次失败，请稍后再试或确认校园网 / VPN 服务正常。',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                  height: 1.45,
                ),
              ),
              const SizedBox(height: 22),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: FilledButton.icon(
                  key: const ValueKey('bitc-clear-session'),
                  onPressed: busy ? null : onRelogin,
                  icon: busy
                      ? const SizedBox.square(
                          dimension: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.restart_alt_rounded),
                  label: Text(busy ? '正在清理…' : '清理登录态并重新登录'),
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: OutlinedButton.icon(
                  key: const ValueKey('bitc-open-portal'),
                  onPressed: busy ? null : onOpenPortal,
                  icon: const Icon(Icons.open_in_new_rounded),
                  label: const Text('打开学校 VPN 门户'),
                ),
              ),
              const SizedBox(height: 4),
              TextButton(
                key: const ValueKey('bitc-show-raw-page'),
                onPressed: busy ? null : onShowPage,
                child: const Text('查看学校返回的页面'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Recognizes the campus web-VPN gateway failure page (网瑞达 / WRD WebVPN).
///
/// The page is returned with HTTP 200 and contains phrases such as
/// `站点访问异常`, `权限表不同步` and `site jwxt.vpn.bitc.edu.cn not found`.
/// It also echoes the requested timetable URL, so matching on `xskbcx` would
/// wrongly classify the error page as a normal timetable page.
bool isBitcGatewayErrorText(String text) {
  if (text.trim().isEmpty) return false;
  final lower = text.toLowerCase();
  if (lower.contains('权限表不同步') || lower.contains('站点访问异常')) {
    return true;
  }
  return RegExp(
    r'\bsite\s+[a-z0-9._-]+\s+not\s+found\b',
    caseSensitive: false,
  ).hasMatch(lower);
}

String buildBitcAccountPrefillScript(String accountId) {
  final account = accountId.trim();
  if (account.isEmpty) return '';
  return '''
    (function () {
      const account = ${jsonEncode(account)};
      const selectors = [
        'input[name="username"]',
        'input[name="userName"]',
        'input[id="username"]',
        'input[id="userName"]',
        'input[type="text"]'
      ];
      const field = selectors.map(function (selector) {
        return document.querySelector(selector);
      }).find(function (element) {
        return element && element.type !== 'password';
      });
      if (field && !field.value) {
        field.value = account;
        field.dispatchEvent(new Event('input', {bubbles: true}));
        field.dispatchEvent(new Event('change', {bubbles: true}));
      }
    })();
  ''';
}

String buildBitcTimetableBridgeScript({
  required String academicYearStart,
  required String termCode,
}) {
  final year = jsonEncode(academicYearStart);
  final term = jsonEncode(termCode);
  return '''
    (async function () {
      if (location.hostname !== 'jwxt.vpn.bitc.edu.cn') {
        throw new Error('Not on the timetable host');
      }
      const common = {xnm: $year, xqm: $term};
      async function postJson(path, values) {
        const response = await fetch(path, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
          },
          body: new URLSearchParams(values).toString()
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
      }
      const rawPayload = await postJson('/kbcx/xskbcx_cxXsgrkb.html', {
        ...common, kzlx: 'ck', xsdm: ''
      });
      const courseKeys = [
        'jxb_id', 'kch', 'kcmc', 'xm', 'xqj', 'jcs', 'jcor', 'zcd',
        'xqmc', 'cdmc'
      ];
      const calendarKeys = ['rq', 'xqj', 'zc'];
      const campusRows = Array.isArray(rawPayload.kbList) ? rawPayload.kbList : [];
      const campusBySourceId = new Map();
      campusRows.forEach(function (item) {
        const sourceId = item && item.xqh_id != null
          ? String(item.xqh_id).trim() : '';
        if (!sourceId || campusBySourceId.has(sourceId)) return;
        campusBySourceId.set(sourceId, {
          sourceId: sourceId,
          name: item.xqmc == null || String(item.xqmc).trim() === ''
            ? '校区 ' + (campusBySourceId.size + 1)
            : String(item.xqmc).trim()
        });
      });
      if (campusBySourceId.size === 0) {
        campusBySourceId.set('', {sourceId: '', name: '默认校区'});
      }
      const campuses = Array.from(campusBySourceId.values()).map(
        function (campus, index) {
          return {...campus, id: 'profile-' + index};
        }
      );
      const profileIdBySourceId = new Map(
        campuses.map(function (campus) { return [campus.sourceId, campus.id]; })
      );
      const courses = campusRows.map(function (item) {
        const course = {};
        courseKeys.forEach(function (key) {
          if (Object.prototype.hasOwnProperty.call(item, key)) course[key] = item[key];
        });
        const sourceId = item && item.xqh_id != null
          ? String(item.xqh_id).trim() : '';
        course.timingProfileId = profileIdBySourceId.get(sourceId) || 'profile-0';
        return course;
      });
      const timingProfiles = await Promise.all(campuses.map(async function (campus) {
        const profile = {id: campus.id, name: campus.name};
        try {
          const values = {...common, xqh_id: campus.sourceId};
          const groupsRaw = await postJson(
            '/kbcx/xskbcx_cxRsd.html?gnmkdm=N2151', values
          );
          const periodsRaw = await postJson(
            '/kbcx/xskbcx_cxRjc.html?gnmkdm=N2151', values
          );
          const groupRows = Array.isArray(groupsRaw)
            ? groupsRaw : (Array.isArray(groupsRaw.items) ? groupsRaw.items : []);
          const periodRows = Array.isArray(periodsRaw)
            ? periodsRaw : (Array.isArray(periodsRaw.items) ? periodsRaw.items : []);
          profile.groups = groupRows.map(function (item) {
            const group = {code: item.rsdm, name: item.rsdmc, count: item.rsdzjs};
            if (item.rsdywmc != null) group.englishName = item.rsdywmc;
            return group;
          });
          profile.periods = periodRows.map(function (item) {
            const period = {
              number: item.jcmc,
              startTime: item.qssj,
              endTime: item.jssj,
              groupCode: item.rsdm
            };
            if (item.rsdmc != null) period.groupName = item.rsdmc;
            if (item.rsdjcmc != null) period.displayName = item.rsdjcmc;
            return period;
          });
        } catch (_) {
          profile.warning = '学校作息接口请求失败';
        }
        return profile;
      }));
      const calendarAnchors = Array.isArray(rawPayload.rqazcList)
        ? rawPayload.rqazcList.map(function (item) {
            const anchor = {};
            calendarKeys.forEach(function (key) {
              if (Object.prototype.hasOwnProperty.call(item, key)) anchor[key] = item[key];
            });
            return anchor;
          }) : [];
      const minimized = {
        kbList: courses,
        sjkList: Array.isArray(rawPayload.sjkList)
          ? rawPayload.sjkList.map(function () { return {}; }) : [],
        rqazcList: calendarAnchors,
        timingProfiles: timingProfiles
      };
      const zsType = typeof rawPayload.zs;
      if (rawPayload.zs === null ||
          ['string', 'number', 'boolean'].includes(zsType)) {
        minimized.zs = rawPayload.zs;
      }
      SchedulrBridge.postMessage(JSON.stringify({
        payload: JSON.stringify(minimized)
      }));
    })().catch(function (error) {
      SchedulrBridge.postMessage(JSON.stringify({
        error: String(error && error.message ? error.message : error)
      }));
    });
  ''';
}
