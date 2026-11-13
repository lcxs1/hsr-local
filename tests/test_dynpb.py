"""4.8 (4.7.51 beta) 协议快照：descriptor 加载、字段改名、网关线格式。"""

from __future__ import annotations

import unittest

from hsr.config import ROOT
from hsr.dispatch.gateway_wire import build_gateway_4751
from hsr.protocol.registry import Registry

HOTFIX = {
    "asset_bundle_url": "http://127.0.0.1:21000/cdn/asb",
    "asset_bundle_url_b": "http://127.0.0.1:21000/cdn/base",
    "ex_resource_url": "http://127.0.0.1:21000/cdn/ex",
    "lua_url": "http://127.0.0.1:21000/cdn/lua",
    "ifix_url": "http://127.0.0.1:21000/cdn/ifix",
    "lua_version": "17099231",
    "ifix_version": "17102685",
}


class DynPbTest(unittest.TestCase):
    """The 4.8 snapshot is a descriptor set, not a generated module."""

    @classmethod
    def setUpClass(cls):
        cls.registry = Registry(ROOT / "proto" / "4.7.51", strict=False)
        cls.pb = cls.registry.pb

    def test_snapshot_is_descriptor_set(self):
        self.assertEqual(self.registry.snapshot, "classes.desc")
        self.assertGreater(self.pb.renamed_fields, 0)

    def test_handler_field_names_survive_renames(self):
        self.assertIn("world_id", self.pb.SceneInfo.DESCRIPTOR.fields_by_name)
        self.assertIn(
            "extra_lineup_type", self.pb.LineupInfo.DESCRIPTOR.fields_by_name
        )
        self.assertIn("slot", self.pb.LineupAvatar.DESCRIPTOR.fields_by_name)
        self.assertIn("scene", self.pb.GetCurSceneInfoScRsp.DESCRIPTOR.fields_by_name)

    def test_enum_aliases_keep_46_value_names(self):
        self.assertEqual(self.pb.AvatarType.AvatarType_AvatarFormalType, 3)
        self.assertEqual(self.pb.MissionStatus.MissionStatus_MissionFinish, 2)
        self.assertEqual(self.pb.BattleEndStatus.BATTLE_END_WIN, 1)

    def test_message_alias(self):
        self.assertEqual(self.pb.MazeGroup.DESCRIPTOR.name, "MapInfoGroup")

    def test_partial_command_table(self):
        self.assertTrue(self.registry.partial)
        # 4.8 命令号是一批批反查出来的：已抓到号的在表里，没抓到的还缺席。
        for name in (
            "PlayerGetTokenCsReq",
            "PlayerLoginCsReq",
            "GetBasicInfoCsReq",
            "GetCurSceneInfoCsReq",
            "GetMissionDataCsReq",
            "GetPlayerBoardDataCsReq",
            "GetLineupAvatarDataScRsp",
        ):
            self.assertTrue(self.registry.has(name), name)
        # 教程模块目前还没有命令号，用来验证"缺号不炸、只跳过"的行为。
        self.assertFalse(self.registry.has("GetTutorialCsReq"))

    def test_gateway_wire_fields(self):
        wire = build_gateway_4751("127.0.0.1", 23301, HOTFIX)
        msg = self.pb.GateServer()
        msg.ParseFromString(wire)
        self.assertEqual(msg.ip, "127.0.0.1")
        self.assertEqual(msg.port, 23301)
        self.assertEqual(msg.asset_bundle_url, HOTFIX["asset_bundle_url"])
        self.assertEqual(msg.base_asset_bundle_url, HOTFIX["asset_bundle_url_b"])
        self.assertEqual(msg.ifix_version, HOTFIX["ifix_version"])


if __name__ == "__main__":
    unittest.main()
