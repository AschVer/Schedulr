import 'dart:convert';
import 'dart:io';
import 'dart:math';

/// Builds the one-shot token that hands the school session to the QQ bot.
///
/// The token is transport encoding, not encryption: it carries the WebVPN
/// cookie, so it is as sensitive as a password. Keep it in a private chat and
/// regenerate it after it is used.
final class SchedulrBindToken {
  const SchedulrBindToken._();

  static const String prefix = 'SCHEDULR1.';
  static const int version = 1;
  static const int minProfile = 1;
  static const int maxProfile = 2;
  static const int maxCookieLength = 4096;

  static String encode({
    required int profile,
    required String cookie,
    required DateTime issuedAt,
    String? nonce,
  }) {
    if (profile < minProfile || profile > maxProfile) {
      throw ArgumentError.value(profile, 'profile', 'Unsupported profile.');
    }
    final normalized = normalizeCookie(cookie);
    if (normalized.isEmpty) {
      throw ArgumentError.value(cookie, 'cookie', 'Cookie is empty.');
    }
    if (normalized.length > maxCookieLength) {
      throw ArgumentError.value(cookie, 'cookie', 'Cookie is too long.');
    }
    final payload = <String, Object?>{
      'v': version,
      'p': profile,
      'c': normalized,
      'u': issuedAt.toUtc().millisecondsSinceEpoch ~/ 1000,
      'n': nonce ?? _nonce(),
    };
    final raw = utf8.encode(jsonEncode(payload));
    final packed = ZLibCodec(level: 9).encode(raw);
    final body = base64Url.encode(packed).replaceAll('=', '');
    return '$prefix$body';
  }

  /// The exact message the user pastes into the bot chat.
  static String messageFor(String token) => '/课表 绑定 $token';

  static String normalizeCookie(String value) {
    var text = value.trim();
    if (text.toLowerCase().startsWith('cookie:')) {
      text = text.substring('cookie:'.length).trim();
    }
    text = text.replaceAll(RegExp(r'[\r\n]+'), ' ');
    text = text.replaceAll(RegExp(r'\s*;\s*'), '; ').trim();
    while (text.endsWith(';')) {
      text = text.substring(0, text.length - 1).trimRight();
    }
    return text;
  }

  static String _nonce() {
    final random = Random.secure();
    final bytes = List<int>.generate(8, (_) => random.nextInt(256));
    return bytes
        .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
        .join();
  }
}

/// Flattens WebView cookies into one `Cookie:` request header value.
///
/// The BITC WebVPN keeps several halves of the session (portal and timetable
/// host), so the header has to carry them all; later duplicates are dropped
/// because the same cookie name is only meaningful once per request.
abstract final class BitcCookieHeader {
  static String fromCookies(Iterable<Map<String, Object?>> cookies) {
    final seen = <String>{};
    final parts = <String>[];
    for (final cookie in cookies) {
      final name = cookie['name'];
      final value = cookie['value'];
      if (name is! String || value is! String) continue;
      if (name.isEmpty || value.isEmpty) continue;
      if (!seen.add(name)) continue;
      parts.add('$name=$value');
    }
    return parts.join('; ');
  }
}
