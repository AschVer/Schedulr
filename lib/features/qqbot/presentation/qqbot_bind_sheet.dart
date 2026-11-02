import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../import_timetable/data/bitc_cookie_store.dart';
import '../domain/schedulr_bind_token.dart';

/// Hands the current school session to the QQ bot.
///
/// The bot cannot read this device's WebView cookies, so the app mints a
/// short-lived token the user pastes into a private chat with the bot.
Future<void> showQqBotBindSheet(
  BuildContext context, {
  required BitcCookieStore cookieStore,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (context) => _QqBotBindSheet(cookieStore: cookieStore),
  );
}

class _QqBotBindSheet extends StatefulWidget {
  const _QqBotBindSheet({required this.cookieStore});

  final BitcCookieStore cookieStore;

  @override
  State<_QqBotBindSheet> createState() => _QqBotBindSheetState();
}

class _QqBotBindSheetState extends State<_QqBotBindSheet> {
  int _profile = 1;
  bool _busy = false;
  String? _error;
  String? _token;
  String? _cookieHeader;

  Future<void> _generate() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final cookies = await widget.cookieStore.readBrowserCookies();
      final header = BitcCookieHeader.fromCookies(cookies);
      if (header.isEmpty) {
        setState(() {
          _busy = false;
          _error = '没有读到教务登录状态。先在 App 里登录一次教务网页，再回来生成口令。';
        });
        return;
      }
      final token = SchedulrBindToken.encode(
        profile: _profile,
        cookie: header,
        issuedAt: DateTime.now(),
      );
      setState(() {
        _busy = false;
        _token = token;
        _cookieHeader = header;
      });
    } on Object {
      setState(() {
        _busy = false;
        _error = '生成口令失败，请稍后重试。';
      });
    }
  }

  Future<void> _copy(String text, String message) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  void _selectProfile(int value) {
    if (value == _profile) return;
    setState(() {
      _profile = value;
      _token = null;
      _cookieHeader = null;
      _error = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final token = _token;
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          bottom: 20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('同步登录状态到 QQ 机器人', style: theme.textTheme.titleLarge),
              const SizedBox(height: 8),
              Text(
                '机器人用这份登录状态自己去教务取最新课表。'
                '它只保存会话 Cookie，不保存账号密码。',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 20),
              Text('同步到机器人里的哪个档案？', style: theme.textTheme.labelLarge),
              const SizedBox(height: 8),
              SegmentedButton<int>(
                segments: const [
                  ButtonSegment(value: 1, label: Text('1 · 一班')),
                  ButtonSegment(value: 2, label: Text('2 · 二班')),
                ],
                selected: {_profile},
                onSelectionChanged: (value) => _selectProfile(value.first),
              ),
              const SizedBox(height: 8),
              Text(
                '对应机器人插件配置里的「课表 1」和「课表 2」。',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 20),
              if (_error case final error?)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Text(
                    error,
                    style: TextStyle(color: theme.colorScheme.error),
                  ),
                ),
              if (token != null) _TokenResult(
                message: SchedulrBindToken.messageFor(token),
                cookieHeader: _cookieHeader ?? '',
                onCopyMessage: () => _copy(
                  SchedulrBindToken.messageFor(token),
                  '已复制整条消息，粘贴到和机器人的私聊里发送。',
                ),
                onCopyCookie: () => _copy(
                  _cookieHeader ?? '',
                  '已复制 Cookie，可填到机器人 WebUI 的配置里。',
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _busy ? null : _generate,
                  icon: _busy
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.vpn_key_outlined),
                  label: Text(token == null ? '生成同步口令' : '重新生成'),
                ),
              ),
              const SizedBox(height: 12),
              Text(
                '口令等同于学校登录态，只发私聊，别发群。'
                '用过的口令会立刻失效，需要时再生成。',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _TokenResult extends StatelessWidget {
  const _TokenResult({
    required this.message,
    required this.cookieHeader,
    required this.onCopyMessage,
    required this.onCopyCookie,
  });

  final String message;
  final String cookieHeader;
  final VoidCallback onCopyMessage;
  final VoidCallback onCopyCookie;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(12),
          ),
          child: SelectableText(
            message,
            style: theme.textTheme.bodySmall?.copyWith(
              fontFamily: 'monospace',
            ),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          '私聊机器人发送上面这条消息，机器人会校验并立即刷新课表。',
          style: theme.textTheme.bodySmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            FilledButton.tonalIcon(
              onPressed: onCopyMessage,
              icon: const Icon(Icons.copy_all_outlined),
              label: const Text('复制整条消息'),
            ),
            const SizedBox(width: 8),
            if (cookieHeader.isNotEmpty)
              TextButton(
                onPressed: onCopyCookie,
                child: const Text('只复制 Cookie'),
              ),
          ],
        ),
      ],
    );
  }
}
