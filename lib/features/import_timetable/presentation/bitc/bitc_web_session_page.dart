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

  late final WebViewController _controller;
  Completer<String>? _payloadCompleter;
  bool _isLoading = true;
  bool _isFetching = false;
  bool _canFetch = false;
  bool _autoFetchAttempted = false;
  String? _message;
  String? _gatewayError;
  bool _isClearingSession = false;

  /// Markers rendered by BITC's web-VPN gateway when it cannot route to the
  ///教务 backend or its permission table is out of sync. The gateway returns
  /// these as ordinary HTTP 200 HTML, so `onWebResourceError` never fires and
  /// the page text is the only reliable signal.
  static const _gatewayErrorMarkers = <String>[
    '权限表不同步',
    '站点访问异常',
    'not found',
    'site ',
  ];

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
          onPageStarted: (_) => setState(() {
            _isLoading = true;
            _message = null;
            _gatewayError = null;
          }),
          onPageFinished: (url) {
            final uri = Uri.tryParse(url);
            if (!mounted) return;
            setState(() {
              _isLoading = false;
              _canFetch =
                  uri?.host == 'jwxt.vpn.bitc.edu.cn' &&
                  uri?.path.contains('xskbcx_cxXskbcxIndex.html') == true;
            });
            unawaited(_detectGatewayError());
            if (widget.savedAccount != null &&
                uri != null &&
                _isAllowedHost(uri.host) &&
                !_canFetch) {
              unawaited(_prefillAccount());
            }
            if (widget.autoFetch && _canFetch && !_autoFetchAttempted) {
              _autoFetchAttempted = true;
              unawaited(_fetchTimetable());
            }
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

  Future<void> _prefillAccount() async {
    final account = widget.savedAccount?.trim();
    if (account == null || account.isEmpty || !mounted) return;
    try {
      await _controller.runJavaScript(buildBitcAccountPrefillScript(account));
    } on Object {
      // Login markup can change; the user can still enter the account manually.
    }
  }

  /// Reads the rendered page text and recognizes BITC's web-VPN gateway error
  /// pages (e.g. "权限表不同步" / "site ... not found"), which arrive as HTTP 200
  /// and therefore bypass `onWebResourceError`.
  Future<void> _detectGatewayError() async {
    if (_canFetch) return;
    String text;
    try {
      final result = await _controller.runJavaScriptReturningResult(
        'document.body ? document.body.innerText : ""',
      );
      text = result is String ? result : '';
    } on Object {
      return;
    }
    if (!mounted) return;
    if (_isGatewayErrorText(text)) {
      setState(() {
        _gatewayError = '学校 VPN 网关暂时无法访问教务系统，课表数据无法读取。'
            '这通常是网关的会话/权限表临时不同步导致的，点按下方按钮清理登录态后重新登录一般即可恢复；'
            '若仍失败，请稍后重试或确认校园网 / VPN 服务正常。';
        _message = null;
      });
    }
  }

  bool _isGatewayErrorText(String text) {
    if (text.isEmpty) return false;
    final lower = text.toLowerCase();
    return _gatewayErrorMarkers.any((marker) {
      final needle = marker.toLowerCase();
      return lower.contains(needle);
    }) &&
        // Avoid matching a normal timetable page that merely mentions a site.
        !lower.contains('xskbcx') &&
        !lower.contains('个人课表');
  }

  /// Clears the current login session (WebView cookies + persisted snapshot)
  /// and reloads the entry so the user can authenticate from a clean slate —
  /// the remedy the school's own error page recommends.
  Future<void> _clearCookiesAndRelogin() async {
    if (_isClearingSession || !mounted) return;
    setState(() {
      _isClearingSession = true;
      _message = '正在清理登录态…';
      _gatewayError = null;
    });
    try {
      await (widget.onClearSession?.call() ??
          WebViewCookieManager().clearCookies());
      await _controller.clearCache();
      if (!mounted) return;
      setState(() {
        _autoFetchAttempted = false;
        _message = '登录态已清理，请在学校 IAM 页面重新登录。';
      });
      await _controller.loadRequest(_entryUri);
    } on Object {
      if (!mounted) return;
      setState(() => _message = '清理登录态失败，请重新打开本页后重试。');
    } finally {
      if (mounted) setState(() => _isClearingSession = false);
    }
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
    final isCupertino = usesCupertinoConventions(context);
    final enabled = _canFetch && !_isFetching;
    final actionLabel = _isFetching ? '正在读取课表…' : '读取当前学期课表';
    return AdaptiveScaffold(
      title: const Text('登录 BITC 教务'),
      actions: isCupertino
          ? const []
          : [
              IconButton(
                tooltip: '重新加载',
                onPressed: _isFetching ? null : _controller.reload,
                icon: const Icon(Icons.refresh_rounded),
              ),
            ],
      cupertinoTrailing: isCupertino
          ? CupertinoButton(
              key: const ValueKey('bitc-reload'),
              padding: EdgeInsets.zero,
              onPressed: _isFetching ? null : _controller.reload,
              child: const Icon(CupertinoIcons.refresh),
            )
          : null,
      body: Column(
        children: [
          if (_isLoading) const LinearProgressIndicator(minHeight: 3),
          MaterialBanner(
            content: Text(
              _canFetch
                  ? '已进入个人课表页面。应用只读取课表接口，不读取或保存登录密码。'
                  : '请在学校 VPN/IAM 页面完成登录。页面仅允许访问 bitc.edu.cn 域名。',
            ),
            actions: const [SizedBox.shrink()],
          ),
          if (_gatewayError case final gatewayError?)
            Card(
              margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
              color: Theme.of(context).colorScheme.errorContainer,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '学校网关异常',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                        color: Theme.of(context).colorScheme.onErrorContainer,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      gatewayError,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.onErrorContainer,
                      ),
                    ),
                    const SizedBox(height: 8),
                    OutlinedButton.icon(
                      key: const ValueKey('bitc-clear-session'),
                      onPressed: _isClearingSession ? null : _clearCookiesAndRelogin,
                      icon: _isClearingSession
                          ? const SizedBox.square(
                              dimension: 16,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.delete_sweep_outlined),
                      label: Text(
                        _isClearingSession ? '正在清理…' : '清理登录态并重新登录',
                      ),
                    ),
                  ],
                ),
              ),
            ),
          if (_message case final message?)
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                message,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          Expanded(child: WebViewWidget(controller: _controller)),
        ],
      ),
      bottomNavigationBar: isCupertino
          ? null
          : SafeArea(
              minimum: const EdgeInsets.all(12),
              child: FilledButton.icon(
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
      cupertinoBottomAction: isCupertino
          ? SafeArea(
              minimum: const EdgeInsets.all(12),
              child: SizedBox(
                width: double.infinity,
                child: CupertinoButton.filled(
                  key: const ValueKey('bitc-fetch'),
                  onPressed: enabled ? _fetchTimetable : null,
                  child: Text(actionLabel),
                ),
              ),
            )
          : null,
    );
  }
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
