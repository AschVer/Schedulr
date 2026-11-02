from __future__ import annotations

import base64
import json
import sys
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from schedulr_core.bind import (  # noqa: E402
    BindError,
    BindPayload,
    decode_bind_token,
    encode_bind_token,
    looks_like_bind_token,
)

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=TZ)
COOKIE = (
    "JSESSIONID=ABC123; _webvpn_key=xyz789; _astraeus_session=token"
)


def payload(**overrides) -> BindPayload:
    values = {
        "profile": 1,
        "cookie": COOKIE,
        "issued_at": NOW,
        "nonce": "8f14e45fceea167a",
    }
    values.update(overrides)
    return BindPayload(**values)


def raw_token(**document) -> str:
    """Build a token without the encoder's own validation."""

    base = {
        "v": 1,
        "p": 1,
        "c": COOKIE,
        "u": int(NOW.timestamp()),
        "n": "8f14e45fceea167a",
    }
    base.update(document)
    packed = zlib.compress(
        json.dumps(base, separators=(",", ":")).encode("utf-8"), 9
    )
    body = base64.urlsafe_b64encode(packed).rstrip(b"=").decode("ascii")
    return "SCHEDULR1." + body


class BindTokenRoundTripTest(unittest.TestCase):
    def test_round_trip(self):
        token = encode_bind_token(payload())
        self.assertTrue(token.startswith("SCHEDULR1."))
        self.assertNotIn("=", token)
        self.assertNotIn("JSESSIONID", token)

        decoded = decode_bind_token(token, now=NOW, max_age_seconds=3600)
        self.assertEqual(decoded.profile, 1)
        self.assertEqual(decoded.cookie, COOKIE)
        self.assertEqual(decoded.nonce, "8f14e45fceea167a")
        self.assertEqual(decoded.issued_at, NOW)

    def test_second_profile(self):
        token = encode_bind_token(payload(profile=2))
        decoded = decode_bind_token(token, now=NOW, max_age_seconds=3600)
        self.assertEqual(decoded.profile, 2)

    def test_token_stays_short(self):
        long_cookie = "; ".join(
            f"name{index}=value{'x' * 40}" for index in range(12)
        )
        token = encode_bind_token(payload(cookie=long_cookie))
        self.assertLess(len(token), 1200, "token should stay pasteable")

    def test_whitespace_and_padding_tolerated(self):
        token = encode_bind_token(payload())
        messy = "  " + token[:12] + "\n" + token[12:] + "=\n"
        decoded = decode_bind_token(messy, now=NOW, max_age_seconds=3600)
        self.assertEqual(decoded.cookie, COOKIE)

    def test_cookie_is_normalized(self):
        token = encode_bind_token(payload(cookie="a=1;b=2;"))
        decoded = decode_bind_token(token, now=NOW, max_age_seconds=3600)
        self.assertEqual(decoded.cookie, "a=1; b=2")


class BindTokenRejectionTest(unittest.TestCase):
    def assert_rejected(self, token: str, *, now: datetime = NOW) -> None:
        with self.assertRaises(BindError):
            decode_bind_token(token, now=now, max_age_seconds=3600)

    def test_expired(self):
        token = encode_bind_token(payload())
        self.assert_rejected(token, now=NOW + timedelta(minutes=61))

    def test_future_timestamp(self):
        token = encode_bind_token(payload())
        self.assert_rejected(token, now=NOW - timedelta(minutes=30))

    def test_wrong_prefix(self):
        self.assert_rejected("SCHEDULR9.AAAA")
        self.assert_rejected("hello world")

    def test_corrupted_body(self):
        token = encode_bind_token(payload())
        self.assert_rejected(token[:-4] + "AAAA")

    def test_profile_out_of_range(self):
        self.assert_rejected(raw_token(p=3))
        self.assert_rejected(raw_token(p=0))
        self.assert_rejected(raw_token(p="1"))
        self.assert_rejected(raw_token(v=2))
        self.assert_rejected(raw_token(c=""))
        self.assert_rejected(raw_token(n=""))
        self.assert_rejected(raw_token(u=10**20))

    def test_empty_cookie(self):
        self.assert_rejected(encode_bind_token(payload(cookie="  ")))

    def test_detection_helper(self):
        self.assertTrue(looks_like_bind_token(" schedulr1.abc "))
        self.assertFalse(looks_like_bind_token("/课表"))
        self.assertFalse(looks_like_bind_token(""))


if __name__ == "__main__":
    unittest.main()
