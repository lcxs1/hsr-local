"""Regression tests for the 2026-11-12 hardening fixes.

Covers the P0 lineup DoS (unbounded slot) plus the smaller input-validation fixes:
- LineupState.set_slot clamps/rejects out-of-range slots.
- lineup handlers ignore out-of-range slot/leader values.
- on_scene_map_info caps the number of SceneMapInfo entries.
"""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.game.handlers import lineup as lineup_handlers
from hsr.game.handlers import scene as scene_handlers
from hsr.game.services import scene as scene_service
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
        self.sent: list[object] = []

    async def send(self, msg, name: str | None = None) -> None:
        self.sent.append(msg)


class LineupHardeningTest(unittest.TestCase):
    def test_set_slot_rejects_negative(self):
        state = LineupState(slots=[1, 2, 3, 4])
        with self.assertRaises(ValueError):
            state.set_slot(-1, 1001)

    def test_set_slot_rejects_out_of_range(self):
        state = LineupState(slots=[1, 2, 3, 4])
        # A malicious client can send slot up to 2**31-1; it must not grow the list.
        with self.assertRaises(ValueError):
            state.set_slot(2**31 - 1, 1001)
        self.assertEqual(len(state.slots), 4)

    def test_set_slot_accepts_valid(self):
        state = LineupState(slots=[0, 0, 0, 0])
        state.set_slot(2, 1001, team_size=4)
        self.assertEqual(state.slots[2], 1001)


class LineupHandlerHardeningTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = AppConfig.load()
        self.roster = Roster.load(
            self.cfg.paths.resolve(self.cfg.paths.freesr),
            self.cfg.player,
            logging.getLogger("hsr.test"),
        )
        self.save = SaveStore.load(Path(self.tmp.name) / "player.json")
        self.session = _Session(self.cfg, self.roster, self.save)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_replace_ignores_out_of_range_slot(self):
        req = pb.ReplaceLineupCsReq(
            leader_slot=0,
            lineup_slot_list=[
                pb.LineupSlotData(slot=2**31 - 1, id=1506),
                pb.LineupSlotData(slot=0, id=1501),
            ],
        )
        await lineup_handlers.on_replace(self.session, req)
        # 越界 slot 被忽略；合法 slot=0 生效；列表不被撑爆。
        self.assertEqual(len(self.session.lineup.slots), 4)
        self.assertEqual(self.session.lineup.slots[0], 1501)

    async def test_join_ignores_out_of_range_slot(self):
        req = pb.JoinLineupCsReq(base_avatar_id=1506, slot=2**31 - 1)
        await lineup_handlers.on_join(self.session, req)
        self.assertEqual(len(self.session.lineup.slots), 4)

    async def test_change_leader_rejects_out_of_range(self):
        req = pb.ChangeLineupLeaderCsReq(slot=99)
        await lineup_handlers.on_change_leader(self.session, req)
        self.assertEqual(self.session.lineup.leader, 0)


class SceneMapInfoHardeningTest(unittest.IsolatedAsyncioTestCase):
    async def test_scene_map_info_clamps_count(self):
        session = SimpleNamespace(runtime=SimpleNamespace(logger=logging.getLogger("hsr.test")))
        calls: list[int] = []
        original = scene_service.build_map_info

        def fake_build_map_info(_session):
            calls.append(1)
            return pb.SceneMapInfo(floor_id=1)

        scene_service.build_map_info = fake_build_map_info
        try:
            req = pb.GetSceneMapInfoCsReq(
                scene_identifiers=[pb.SceneIdentifier() for _ in range(100_000)]
            )
            rsp = await scene_handlers.on_scene_map_info(session, req)
            self.assertEqual(len(rsp.scene_map_info), scene_handlers._MAX_SCENE_MAP_INFO)
            self.assertEqual(len(calls), scene_handlers._MAX_SCENE_MAP_INFO)
        finally:
            scene_service.build_map_info = original


if __name__ == "__main__":
    unittest.main()
