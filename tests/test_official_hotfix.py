from __future__ import annotations

import base64
import unittest

from hsr.dispatch import official
from hsr.protocol.gen import StarRail_pb2 as pb


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _Opener:
    def __init__(self, body: bytes):
        self.body = body
        self.calls: list[tuple[str, float | None]] = []

    def open(self, url: str, timeout: float | None = None) -> _Response:
        self.calls.append((url, timeout))
        return _Response(self.body)


def _gateway_body(**overrides) -> bytes:
    msg = pb.GateServer(
        asset_bundle_url="https://cdn.example/asb/BetaLive/output_1_a_b",
        ex_resource_url="https://cdn.example/design_data/BetaLive/output_2_c_d",
        lua_url="https://cdn.example/lua/BetaLive/output_3_e_f",
        ifix_url="https://cdn.example/ifix/BetaLive/output_0_g_h",
    )
    msg.mdk_res_version = "3"
    msg.ifix_version = "0"
    for key, value in overrides.items():
        setattr(msg, key, value)
    return base64.b64encode(msg.SerializeToString())


class OfficialHotfixTest(unittest.TestCase):
    def test_host_by_version_prefix(self):
        self.assertEqual(official.host_for("CNPRODWin4.7.0"), "prod-gf-cn-dp01.bhsr.com")
        self.assertEqual(official.host_for("OSBETAWin4.6.54"), "beta-release01-asia.starrails.com")
        self.assertIsNone(official.host_for("4.7.0"))

    def test_fetch_parses_gate_server(self):
        opener = _Opener(_gateway_body())
        entry = official.fetch_hotfix(
            "CNPRODWin4.7.0",
            {"version": "CNPRODWin4.7.0", "dispatch_seed": "seed123", "channel_id": "1"},
            {"asset_bundle_url_b": "asset_bundle_url_android", "lua_version": "mdk_res_version"},
            opener=opener,
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["asset_bundle_url"], "https://cdn.example/asb/BetaLive/output_1_a_b")
        self.assertEqual(entry["lua_version"], "3")
        url, timeout = opener.calls[0]
        self.assertIn("prod-gf-cn-dp01.bhsr.com/query_gateway?", url)
        self.assertIn("dispatch_seed=seed123", url)
        self.assertEqual(timeout, official.TIMEOUT)

    def test_only_allowed_parameters_are_forwarded(self):
        opener = _Opener(_gateway_body())
        official.fetch_hotfix(
            "CNPRODWin4.7.0",
            {
                "version": "CNPRODWin4.7.0",
                "dispatch_seed": "seed123",
                "token": "secret",
                "account_uid": "42",
                "uid": "42",
            },
            opener=opener,
        )
        url, _ = opener.calls[0]
        self.assertNotIn("token", url)
        self.assertNotIn("account_uid", url)
        self.assertNotIn("uid=", url)

    def test_missing_dispatch_seed_skips_request(self):
        opener = _Opener(_gateway_body())
        entry = official.fetch_hotfix(
            "CNPRODWin4.7.0", {"version": "CNPRODWin4.7.0"}, opener=opener
        )
        self.assertIsNone(entry)
        self.assertEqual(opener.calls, [])

    def test_unknown_version_skips_request(self):
        opener = _Opener(_gateway_body())
        entry = official.fetch_hotfix(
            "FOO4.7.0", {"version": "FOO4.7.0", "dispatch_seed": "s"}, opener=opener
        )
        self.assertIsNone(entry)
        self.assertEqual(opener.calls, [])

    def test_empty_gateway_body_returns_none(self):
        entry = official.fetch_hotfix(
            "CNPRODWin4.7.0",
            {"version": "CNPRODWin4.7.0", "dispatch_seed": "s"},
            opener=_Opener(b""),
        )
        self.assertIsNone(entry)

    def test_gateway_without_cdn_url_returns_none(self):
        entry = official.fetch_hotfix(
            "CNPRODWin4.7.0",
            {"version": "CNPRODWin4.7.0", "dispatch_seed": "s"},
            opener=_Opener(_gateway_body(asset_bundle_url="")),
        )
        self.assertIsNone(entry)


if __name__ == "__main__":
    unittest.main()
