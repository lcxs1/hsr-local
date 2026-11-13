from __future__ import annotations

import unittest

from hsr.config import ROOT, AppConfig
from hsr.version import DEFAULT_PROFILE, ProfileError, VersionProfile


class VersionProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = AppConfig.load()
        cls.profile = VersionProfile.load(ROOT, cls.cfg.server.profile)

    def test_profile_matches_config(self):
        self.assertEqual(self.profile.name, DEFAULT_PROFILE)
        self.assertEqual(self.profile.version, self.cfg.server.version)
        self.assertEqual(self.profile.client_build, self.cfg.server.client_build)
        self.assertTrue(self.profile.proto_dir.is_dir())

    def test_field_alias(self):
        # 4.6.54 / 4.7.0 的网关字段别名（4.7.51 是 4.8 字段名，见 test_4751_profile）
        legacy = VersionProfile.load(ROOT, "4.6.54")
        self.assertEqual(legacy.field_name("asset_bundle_url_b"), "asset_bundle_url_android")
        self.assertEqual(legacy.field_name("lua_version"), "mdk_res_version")
        self.assertEqual(self.profile.field_name("ip"), "ip")

    def test_world_pack_loaded(self):
        self.assertEqual(self.cfg.world.floor_id, 20313001)
        self.assertEqual(self.cfg.world.entry_id, 2031301)
        self.assertEqual(self.cfg.world.team_size, 4)
        self.assertEqual(self.cfg.world.calyx_cocoon_id, 1014)
        self.assertTrue(self.cfg.world.lighten_section_ranges)

    def test_missing_profile_raises(self):
        with self.assertRaises(ProfileError):
            VersionProfile.load(ROOT, "does-not-exist")

    def test_470_profile_reuses_46_proto(self):
        profile = VersionProfile.load(ROOT, "4.7.0")
        self.assertEqual(profile.version, "CNPRODWin4.7.0")
        self.assertEqual(profile.client_build, 17108910)
        self.assertEqual(profile.field_name("asset_bundle_url_b"), "asset_bundle_url_android")
        self.assertEqual(profile.field_name("lua_version"), "mdk_res_version")
        # 4.7.0 实测可直接用 4.6.54 的 proto 快照（reference/05 §2）
        self.assertEqual(profile.proto_dir, ROOT / "proto")

    def test_4751_profile(self):
        """4.7.51 beta: OSBETAWin4.7.51 / build 17100438, 4.8 协议快照。"""
        profile = VersionProfile.load(ROOT, "4.7.51")
        self.assertEqual(profile.version, "OSBETAWin4.7.51")
        self.assertEqual(profile.client_build, 17100438)
        self.assertEqual(profile.hotfix_key, "OSBETAWin4.7.51")
        self.assertEqual(profile.proto_dir, ROOT / "proto" / "4.7.51")
        self.assertTrue(profile.partial)
        self.assertTrue((profile.proto_dir / "classes.desc").is_file())
        # 4.8 网关字段：base_asset_bundle_url / lua_patch_version（reference/06 §8）
        self.assertEqual(profile.field_name("asset_bundle_url_b"), "base_asset_bundle_url")
        self.assertEqual(profile.field_name("lua_version"), "lua_patch_version")


if __name__ == "__main__":
    unittest.main()
