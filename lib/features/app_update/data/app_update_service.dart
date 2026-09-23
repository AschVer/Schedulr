import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import 'package:open_filex/open_filex.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'update_manifest.dart';

/// Where the CI publishes the updater manifest (public GitHub Releases asset).
const String kUpdateManifestUrl =
    'https://github.com/AschVer/Schedulr/releases/download/latest-debug/update.json';

class AppUpdateException implements Exception {
  AppUpdateException(this.message);
  final String message;
  @override
  String toString() => message;
}

typedef DownloadProgress = void Function(int received, int total);

/// Checks GitHub Releases for a newer debug build, downloads it with progress,
/// verifies integrity, and hands the APK to the system installer.
class AppUpdateService {
  AppUpdateService({Dio? dio}) : _dio = dio ?? Dio();

  final Dio _dio;

  Future<int> currentBuildNumber() async {
    final info = await PackageInfo.fromPlatform();
    return int.tryParse(info.buildNumber) ?? 0;
  }

  Future<String> currentVersionName() async {
    final info = await PackageInfo.fromPlatform();
    return info.version;
  }

  /// Returns the manifest if a newer build is published, otherwise null.
  Future<UpdateManifest?> checkForUpdate({
    String manifestUrl = kUpdateManifestUrl,
  }) async {
    final response = await _dio.get<String>(
      manifestUrl,
      options: Options(
        responseType: ResponseType.plain,
        receiveTimeout: const Duration(seconds: 15),
      ),
    );
    final body = response.data;
    if (body == null) return null;
    final manifest = UpdateManifest.tryParse(body);
    if (manifest == null) {
      throw AppUpdateException('更新清单格式无效。');
    }
    final current = await currentBuildNumber();
    return manifest.isNewerThan(current) ? manifest : null;
  }

  /// Downloads the APK to the cache dir and verifies its sha256.
  Future<File> downloadApk(
    UpdateManifest manifest, {
    DownloadProgress? onProgress,
  }) async {
    final tempDir = await getTemporaryDirectory();
    final file = File(p.join(tempDir.path, 'schedulr-update.apk'));
    if (await file.exists()) {
      await file.delete();
    }
    await _dio.download(
      manifest.apkUrl,
      file.path,
      options: Options(
        responseType: ResponseType.bytes,
        receiveTimeout: const Duration(minutes: 5),
        headers: const {'Accept': 'application/vnd.android.package-archive'},
      ),
      onReceiveProgress: (received, total) {
        if (total <= 0) {
          // GitHub asset redirects may omit content-length on the final hop;
          // fall back to the size declared in the manifest.
          total = manifest.apkSize > 0 ? manifest.apkSize : received;
        }
        onProgress?.call(received, total);
      },
    );
    await _verifySha256(file, manifest.sha256);
    return file;
  }

  Future<void> _verifySha256(File file, String expected) async {
    final digest = await sha256.bind(file.openRead()).first;
    final actual = digest.toString();
    if (actual.toLowerCase() != expected.trim().toLowerCase()) {
      await file.delete().catchError((_) {});
      throw AppUpdateException('安装包校验失败（哈希不匹配），已取消安装。');
    }
  }

  /// Hands the APK to the system package installer. On Android this opens the
  /// PackageInstaller confirmation (one user tap); the OS handles the rest.
  Future<void> installApk(File file) async {
    final result = await OpenFilex.open(file.path);
    if (result.type != ResultType.done) {
      throw AppUpdateException(
        '无法唤起安装界面：${result.message}。请在系统设置中授予“安装未知应用”权限后重试。',
      );
    }
  }
}
