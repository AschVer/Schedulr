import 'package:flutter/material.dart';

import 'data/app_update_service.dart';
import 'data/update_manifest.dart';

/// Drives the whole update flow inside one dialog:
/// checking -> found -> downloading (progress) -> open installer.
class AppUpdateDialog extends StatefulWidget {
  const AppUpdateDialog({
    required this.service,
    this.initialManifest,
    this.showUpToDate = false,
    super.key,
  });

  final AppUpdateService service;

  /// When non-null, skips the network check (caller already checked).
  final UpdateManifest? initialManifest;

  /// If true and no update is found, show a brief "已是最新版本" notice.
  final bool showUpToDate;

  @override
  State<AppUpdateDialog> createState() => _AppUpdateDialogState();
}

class _AppUpdateDialogState extends State<AppUpdateDialog> {
  late final Future<UpdateManifest?> _checkFuture;
  UpdateManifest? _manifest;
  Object? _error;
  bool _downloading = false;
  double _progress = 0;

  @override
  void initState() {
    super.initState();
    _manifest = widget.initialManifest;
    _checkFuture = widget.initialManifest != null
        ? Future<UpdateManifest?>.value(widget.initialManifest)
        : _check();
  }

  Future<UpdateManifest?> _check() async {
    try {
      return await widget.service.checkForUpdate();
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
      return null;
    }
  }

  Future<void> _downloadAndInstall() async {
    final manifest = _manifest;
    if (manifest == null) return;
    setState(() {
      _downloading = true;
      _progress = 0;
    });
    try {
      final file = await widget.service.downloadApk(
        manifest,
        onProgress: (received, total) {
          if (!mounted) return;
          setState(() => total > 0 ? received / total : _progress);
        },
      );
      await widget.service.installApk(file);
      if (mounted) Navigator.of(context).pop();
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _downloading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('应用更新'),
      content: FutureBuilder<UpdateManifest?>(
        future: _checkFuture,
        builder: (context, snapshot) {
          if (_error != null) {
            return Text(
              _error is AppUpdateException
                  ? '$_error'
                  : '检查更新失败，请稍后重试。',
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            );
          }
          if (snapshot.connectionState != ConnectionState.done) {
            return const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Row(
                children: [
                  SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  SizedBox(width: 12),
                  Text('正在检查更新…'),
                ],
              ),
            );
          }
          final manifest = snapshot.data;
          if (manifest == null) {
            return Text(
              widget.showUpToDate ? '当前已是最新版本。' : '暂无可更新的版本。',
            );
          }
          _manifest = manifest;
          return _buildAvailable(manifest);
        },
      ),
      actions: [
        TextButton(
          onPressed: _downloading ? null : () => Navigator.of(context).pop(),
          child: Text(_downloading ? '后台下载中…' : '稍后'),
        ),
        if (_manifest != null)
          FilledButton(
            onPressed: _downloading ? null : _downloadAndInstall,
            child: Text(_downloading ? '下载中…' : '立即更新'),
          ),
      ],
    );
  }

  Widget _buildAvailable(UpdateManifest manifest) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('发现新版本 v${manifest.versionName}（build ${manifest.versionCode}）'),
        if (manifest.releaseNotes.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(
            manifest.releaseNotes,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
        const SizedBox(height: 12),
        if (_downloading)
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              LinearProgressIndicator(value: _progress > 0 ? _progress : null),
              const SizedBox(height: 6),
              Text(
                '正在下载安装包… ${(_progress * 100).toStringAsFixed(0)}%',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          )
        else
          const Text('下载完成后将自动打开系统安装界面，点一次“安装”即可。'),
      ],
    );
  }
}

/// Convenience entry point used by the launch check and settings.
Future<void> showAppUpdateDialog(
  BuildContext context,
  AppUpdateService service, {
  bool showUpToDate = false,
}) {
  return showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (_) => AppUpdateDialog(
      service: service,
      showUpToDate: showUpToDate,
    ),
  );
}
