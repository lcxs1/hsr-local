from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.game.handlers import avatar as avatar_handlers
from hsr.game.handlers import lineup as lineup_handlers
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.game.services import snapshot
from hsr.game.services.lineup import LineupState
from hsr.game.services.roster import Roster
from hsr.game.services.save import SaveStore
from hsr.protocol.gen import StarRail_pb2 as pb


class _Session:
    def __init__(self, cfg: AppConfig, roster: Roster, save: SaveStore):
        self.runtime = SimpleNamespace(
            cfg=cfg, roster=roster, save=save, logger=logging.getLogger("hsr.test")
        )
        self.lineup = LineupState(slots=[1506, 1501, 1502, 1503], leader=0)
        self.scene = scene_service.SceneState.from_config(cfg.world)
        self.tb_multi_path = 8001
        self.march_multi_path = 1001
        self.sent: list[object] = []

    async def send(self, msg, name: str | None = None) -> None:
        self.sent.append(msg)


class ContractTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = AppConfig.load()
        self.cfg.player.gender = 2
        self.roster = Roster.load(
            self.cfg.paths.resolve(self.cfg.paths.freesr),
            self.cfg.player,
            logging.getLogger("hsr.test"),
        )
        self.save = SaveStore.load(Path(self.tmp.name) / "player.json")
        self.session = _Session(self.cfg, self.roster, self.save)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_replace_lineup_persists_and_refreshes(self):
        req = pb.ReplaceLineupCsReq(
            leader_slot=1,
            lineup_slot_list=[
                pb.LineupSlotData(slot=0, id=1506),
                pb.LineupSlotData(slot=1, id=1501),
                pb.LineupSlotData(slot=2, id=1502),
                pb.LineupSlotData(slot=3, id=1503),
            ],
        )
        rsp = await lineup_handlers.on_replace(self.session, req)
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(self.session.lineup.slots[:4], [1506, 1501, 1502, 1503])
        self.assertEqual(self.session.lineup.leader, 1)
        self.assertEqual(self.save.player.lineup, [1506, 1501, 1502, 1503])
        kinds = {type(m).__name__ for m in self.session.sent}
        self.assertIn("SceneGroupRefreshScNotify", kinds)
        self.assertIn("SyncLineupNotify", kinds)

    async def test_scene_payload_uses_world_pack(self):
        scene = scene_service.build_scene(self.session)
        self.assertEqual(scene.plane_id, self.cfg.world.plane_id)
        self.assertEqual(scene.floor_id, self.cfg.world.floor_id)
        self.assertEqual(scene.entry_id, self.cfg.world.entry_id)
        self.assertEqual(scene.leader_entity_id, self.cfg.world.leader_entity_id)
        self.assertEqual(scene.entity_group_list[0].group_id, self.cfg.world.avatar_group_id)
        self.assertEqual(scene.entity_group_list[0].entity_list[0].entity_id, 1)

    async def test_replace_lineup_accepts_protagonist_form(self):
        self.session.tb_multi_path = 8004
        req = pb.ReplaceLineupCsReq(
            lineup_slot_list=[
                pb.LineupSlotData(slot=0, id=8004),
                pb.LineupSlotData(slot=1, id=1501),
                pb.LineupSlotData(slot=2, id=1502),
                pb.LineupSlotData(slot=3, id=1503),
            ],
        )
        await lineup_handlers.on_replace(self.session, req)
        self.assertEqual(self.session.lineup.slots[0], 8001)
        lineup = lineup_service.build_lineup(self.session)
        self.assertEqual(lineup.avatar_list[0].id, 8004)

    async def test_set_avatar_path_persists_and_notifies(self):
        self.session.lineup = LineupState(slots=[8001, 1501, 1502, 1503], leader=0)
        rsp = await avatar_handlers.on_set_avatar_path(
            self.session, pb.SetAvatarPathCsReq(avatar_id=8004)
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(self.session.tb_multi_path, 8004)
        self.assertEqual(self.save.player.tb_multi_path, 8004)

        kinds = {type(m).__name__ for m in self.session.sent}
        self.assertIn("AvatarPathChangedNotify", kinds)
        self.assertIn("PlayerSyncScNotify", kinds)
        self.assertIn("SyncLineupNotify", kinds)

        lineup = lineup_service.build_lineup(self.session)
        self.assertEqual(lineup.avatar_list[0].id, 8004)
        sync = snapshot.avatar_sync(self.session)
        avatar = next(a for a in sync.avatar_list if a.base_avatar_id == 8001)
        self.assertEqual(avatar.cur_multi_path_avatar_type, 8004)

    async def test_set_avatar_path_rejects_other_gender(self):
        # config player.gender = 2 (female); 8003 is male and must not switch.
        rsp = await avatar_handlers.on_set_avatar_path(
            self.session, pb.SetAvatarPathCsReq(avatar_id=8003)
        )
        self.assertEqual(rsp.retcode, 1)
        self.assertEqual(self.session.tb_multi_path, 8001)


if __name__ == "__main__":
    unittest.main()
