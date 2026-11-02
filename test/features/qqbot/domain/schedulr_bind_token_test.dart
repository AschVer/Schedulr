import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:schedulr/features/qqbot/domain/schedulr_bind_token.dart';

Map<String, Object?> _decode(String token) {
  expect(token.startsWith(SchedulrBindToken.prefix), isTrue);
  final body = token.substring(SchedulrBindToken.prefix.length);
  expect(body.contains('='), isFalse, reason: 'padding is stripped');
  final padded = body + '=' * ((4 - body.length % 4) % 4);
  final raw = ZLibCodec().decode(base64Url.decode(padded));
  return jsonDecode(utf8.decode(raw)) as Map<String, Object?>;
}

void main() {
  group('SchedulrBindToken', () {
    test('encodes a payload the bot can decode', () {
      final issued = DateTime.utc(2026, 10, 26, 8);
      final token = SchedulrBindToken.encode(
        profile: 2,
        cookie: 'JSESSIONID=abc; _webvpn_key=xyz',
        issuedAt: issued,
        nonce: 'n1',
      );

      final payload = _decode(token);
      expect(payload['v'], 1);
      expect(payload['p'], 2);
      expect(payload['c'], 'JSESSIONID=abc; _webvpn_key=xyz');
      expect(payload['u'], issued.millisecondsSinceEpoch ~/ 1000);
      expect(payload['n'], 'n1');
    });

    test('generates a distinct nonce per token', () {
      final first = _decode(
        SchedulrBindToken.encode(
          profile: 1,
          cookie: 'a=1',
          issuedAt: DateTime.utc(2026, 10, 26),
        ),
      );
      final second = _decode(
        SchedulrBindToken.encode(
          profile: 1,
          cookie: 'a=1',
          issuedAt: DateTime.utc(2026, 10, 26),
        ),
      );
      expect(first['n'], isNot(second['n']));
    });

    test('rejects unsupported profile and empty cookie', () {
      expect(
        () => SchedulrBindToken.encode(
          profile: 3,
          cookie: 'a=1',
          issuedAt: DateTime.utc(2026, 10, 26),
        ),
        throwsArgumentError,
      );
      expect(
        () => SchedulrBindToken.encode(
          profile: 1,
          cookie: '   ',
          issuedAt: DateTime.utc(2026, 10, 26),
        ),
        throwsArgumentError,
      );
    });

    test('normalizes a pasted cookie header', () {
      expect(
        SchedulrBindToken.normalizeCookie(' Cookie:a=1;b=2 ; ;  '),
        'a=1; b=2',
      );
      expect(SchedulrBindToken.normalizeCookie('a=1;\r\nb=2;'), 'a=1; b=2');
      expect(SchedulrBindToken.normalizeCookie(''), '');
    });

    test('keeps the token pasteable', () {
      final cookie = List.generate(
        12,
        (index) => 'name$index=${'x' * 40}',
      ).join('; ');
      final token = SchedulrBindToken.encode(
        profile: 1,
        cookie: cookie,
        issuedAt: DateTime.utc(2026, 10, 26),
      );
      expect(token.length, lessThan(1200));
    });

    test('builds the command the bot expects', () {
      expect(SchedulrBindToken.messageFor('X'), '/课表 绑定 X');
    });
  });

  group('BitcCookieHeader', () {
    test('joins cookies and keeps the first of each name', () {
      final header = BitcCookieHeader.fromCookies([
        {'name': 'JSESSIONID', 'value': 'one', 'domain': 'vpn.bitc.edu.cn'},
        {'name': '_webvpn_key', 'value': 'two', 'domain': 'bitc.edu.cn'},
        {'name': 'JSESSIONID', 'value': 'duplicate', 'domain': 'jwxt.bitc.edu.cn'},
        {'name': 'empty', 'value': ''},
      ]);
      expect(header, 'JSESSIONID=one; _webvpn_key=two');
    });

    test('returns an empty header without cookies', () {
      expect(BitcCookieHeader.fromCookies(const []), '');
    });
  });
}
