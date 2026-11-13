from __future__ import annotations

import base64
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import ROOT, AppConfig
from hsr.dispatch.app import build_dispatch, build_gateway, build_get_config
from hsr.dispatch.hotfix import FALLBACK, HotfixStore, is_local, localize
from hsr.runtime import Runtime
from hsr.version import VersionProfile


class DispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rt = Runtime.load()

    def test_dispatch(self):
        msg = build_dispatch(self.rt)
        self.assertEqual(msg.retcode, 0)
        self.assertEqual(msg.msg, "OK")
        self.assertTrue(msg.region_list[0].dispatch_url.endswith("/query_gateway"))
        self.assertGreater(len(msg.SerializeToString()), 50)

    def test_gateway_known_build(self):
        # 通用 GateServer 构造器仍按 4.6.54 快照生成；4.7.51 走手写线格式
        # （gateway_wire / test_gateway_4751_wire），见 reference/06 §8。
        rt = SimpleNamespace(cfg=self.rt.cfg, profile=VersionProfile.load(ROOT, "4.6.54"))
        store = HotfixStore(self.rt.cfg.hotfix_path)
        msg = build_gateway(rt, store, "OSBETAWin4.6.54")
        self.assertEqual(msg.ip, "127.0.0.1")
        self.assertEqual(msg.port, self.rt.cfg.server.game_port)
        self.assertTrue(msg.asset_bundle_url.startswith("https://"))
        self.assertTrue(msg.mdk_res_version)
        self.assertTrue(msg.unk1 and msg.unk8)

    def test_hotfix_fallback(self):
        store = HotfixStore(self.rt.cfg.hotfix_path)
        # unknown platform prefix -> hard fallback
        entry = store.for_version("FOO4.6.54")
        self.assertEqual(entry["asset_bundle_url"], FALLBACK["asset_bundle_url"])
        # same platform, unknown build -> closest cached build (not the fallback)
        closest = store.for_version("OSBETAWin4.9.99")
        # 4.7.51 是当前 OSBETAWin 里 build 最大的一条，且走 LOCAL_CDN（reference/06 §6）。
        self.assertIn("output_17100438", closest["asset_bundle_url"])

    def test_hotfix_4751_uses_local_replay(self):
        """4.7.51 走本地回放：官方 beta CDN 已 404（reference/06 §6）。"""
        store = HotfixStore(self.rt.cfg.hotfix_path)
        entry = store.for_version("OSBETAWin4.7.51")
        self.assertTrue(entry["asset_bundle_url"].startswith("LOCAL_CDN/"))
        self.assertEqual(entry["lua_version"], "17099231")
        # ifix_version=0：客户端本地没有 IFix 补丁，报非 0 版本会让它反复下载并 404。
        self.assertEqual(entry["ifix_version"], "0")

    def test_gateway_rewrites_local_cdn_entries(self):
        cfg = AppConfig.load()
        cfg.features.local_cdn = True
        rt = SimpleNamespace(
            cfg=cfg,
            profile=VersionProfile.load(ROOT, "4.7.0"),
            logger=logging.getLogger("hsr.test"),
        )
        msg = build_gateway(rt, HotfixStore(cfg.hotfix_path), "CNPRODWin4.7.0")
        base = f"http://{cfg.server.listen}:{cfg.server.http_port}/cdn/"
        self.assertTrue(msg.asset_bundle_url.startswith(base))
        self.assertTrue(msg.ex_resource_url.startswith(base))
        self.assertNotIn("LOCAL_CDN", msg.ex_resource_url)
        # 4.6.54 的字段别名在 4.7.0 profile 里同样生效
        self.assertTrue(msg.asset_bundle_url_android.startswith(base))

    def test_gateway_keeps_placeholder_when_replay_disabled(self):
        cfg = AppConfig.load()
        cfg.features.local_cdn = False
        rt = SimpleNamespace(
            cfg=cfg,
            profile=VersionProfile.load(ROOT, "4.7.0"),
            logger=logging.getLogger("hsr.test"),
        )
        msg = build_gateway(rt, HotfixStore(cfg.hotfix_path), "CNPRODWin4.7.0")
        # 没有本地回放时保持占位符：日志里会报 error，客户端随后资源校验失败。
        self.assertTrue(msg.asset_bundle_url.startswith("LOCAL_CDN/"))

    def test_hotfix_save_and_localize(self):
        entry = {
            "asset_bundle_url": "LOCAL_CDN/asb/x",
            "asset_bundle_url_b": "LOCAL_CDN/asb/x",
            "ex_resource_url": "https://cdn.example/design",
            "lua_url": "LOCAL_CDN/lua/y",
            "ifix_url": "",
            "lua_version": "",
            "ifix_version": "0",
        }
        self.assertTrue(is_local(entry))
        local = localize(entry, "http://127.0.0.1:21000/cdn")
        self.assertEqual(local["asset_bundle_url"], "http://127.0.0.1:21000/cdn/asb/x")
        self.assertEqual(local["ex_resource_url"], "https://cdn.example/design")
        self.assertFalse(is_local(local))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hotfix.json"
            path.write_text("{}\n", encoding="utf-8")
            store = HotfixStore(path)
            self.assertTrue(store.save("CNPRODWin4.9.0", entry))
            self.assertTrue(HotfixStore(path).has("CNPRODWin4.9.0"))

    def test_gateway_is_base64_protobuf(self):
        msg = build_dispatch(self.rt)
        raw = base64.b64encode(msg.SerializeToString())
        self.assertTrue(raw.startswith(b"Eg"))  # field 2 "OK" comes first

    def test_get_config_disables_announce_popup(self):
        config = build_get_config()
        self.assertEqual(config["announce_url"], "")
        self.assertFalse(config["enable_announce_pic_popup"])
        self.assertTrue(config["disable_ysdk_guard"])

    def test_get_config_old_payload(self):
        config = build_get_config("serve")
        self.assertNotIn("announce_url", config)
        self.assertNotIn("enable_announce_pic_popup", config)
        self.assertFalse(config["disable_ysdk_guard"])

    def test_proto_assets_exist(self):
        self.assertTrue((ROOT / "proto" / "StarRail.proto").exists())
        self.assertTrue((ROOT / "proto" / "CmdId.json").exists())
        self.assertTrue((ROOT / "proto" / "hsr.desc").exists())


if __name__ == "__main__":
    unittest.main()
