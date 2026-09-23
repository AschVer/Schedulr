import 'dart:convert';

/// The updater manifest published to GitHub Releases by the CI workflow.
class UpdateManifest {
  const UpdateManifest({
    required this.versionCode,
    required this.versionName,
    required this.apkUrl,
    required this.apkSize,
    required this.sha256,
    required this.releaseNotes,
    this.publishedAt,
  });

  /// Android `versionCode` of the published build. Monotonically increases
  /// (CI uses a unix-seconds build number) so every new build is detectable.
  final int versionCode;
  final String versionName;
  final String apkUrl;
  final int apkSize;
  final String sha256;
  final String releaseNotes;
  final DateTime? publishedAt;

  bool isNewerThan(int currentBuild) => versionCode > currentBuild;

  static UpdateManifest? tryParse(String body) {
    final Object? decoded;
    try {
      decoded = jsonDecode(body);
    } on FormatException {
      return null;
    }
    if (decoded is! Map<String, Object?>) return null;
    final versionCode = decoded['versionCode'];
    final apkUrl = decoded['apkUrl'];
    final sha = decoded['sha256'];
    if (versionCode is! int ||
        apkUrl is! String ||
        apkUrl.isEmpty ||
        sha is! String ||
        sha.isEmpty) {
      return null;
    }
    // Only accept manifests that point at our own GitHub Releases asset, so a
    // tampered/redirected manifest cannot make the app install an arbitrary APK.
    if (!_isTrustedApkUrl(apkUrl)) return null;
    final publishedRaw = decoded['publishedAt'];
    return UpdateManifest(
      versionCode: versionCode,
      versionName: decoded['versionName'] is String
          ? decoded['versionName'] as String
          : '',
      apkUrl: apkUrl,
      apkSize: decoded['apkSize'] is int ? decoded['apkSize'] as int : 0,
      sha256: sha,
      releaseNotes: decoded['releaseNotes'] is String
          ? decoded['releaseNotes'] as String
          : '',
      publishedAt: publishedRaw is String
          ? DateTime.tryParse(publishedRaw)
          : null,
    );
  }

  static bool _isTrustedApkUrl(String url) {
    final uri = Uri.tryParse(url);
    if (uri == null || uri.scheme != 'https') return false;
    if (uri.host != 'github.com' && uri.host != 'objects.githubusercontent.com') {
      return false;
    }
    return uri.path.contains('/AschVer/Schedulr/releases/download/');
  }
}
