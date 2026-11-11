"""秘技点（LineupInfo.mp / max_mp）的消耗与恢复策略。"""

from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.game.handlers import player as player_handlers
from hsr.game.handlers import scene as scene_handlers
from hsr.game.services import lineup as lineup_service
from hsr.game.services.lineup import LineupState
from hsr.protocol.gen import StarRail_pb2 as pb


class _Session:
    def __init__(self, cfg: AppConfig):
        self.runtime = SimpleNamespace(cfg=cfg, logger=logging.getLogger("hsr.test"), roster=None)
        self.lineup = LineupState(slots=[], leader=0, mp=cfg.gameplay.technique_points_max)
        self.sent: list[object] = []

    async def send(self, msg, name: str | None = None) -> None:
        self.sent.append(msg)

    def mp_notifies(self) -> list[pb.SceneCastSkillMpUpdateScNotify]:
        return [m for m in self.sent if isinstance(m, pb.SceneCastSkillMpUpdateScNotify)]

    def lineup_syncs(self) -> list[pb.SyncLineupNotify]:
        return [m for m in self.sent if isinstance(m, pb.SyncLineupNotify)]


class TechniquePointsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = AppConfig.load()
        self.cfg.features.lua_console = False

    def _session(self, **overrides) -> _Session:
        for key, value in overrides.items():
            setattr(self.cfg.gameplay, key, value)
        return _Session(self.cfg)

    async def _cast(self, session: _Session, entity_id: int = 3) -> None:
        await scene_handlers.on_cast_skill_cost_mp(
            session, pb.SceneCastSkillCostMpCsReq(skill_index=1, cast_entity_id=entity_id)
        )

    async def test_unlimited_stays_full_and_resyncs_every_cast(self):
        session = self._session(technique_points="unlimited")
        for _ in range(7):
            await self._cast(session)
        # 1448 客户端只认"扣"，所以每次都补一条 SyncLineupNotify 把全队点数刷满
        self.assertEqual(session.lineup.mp, 5)
        self.assertEqual([n.mp for n in session.mp_notifies()], [5] * 7)
        self.assertEqual(len(session.lineup_syncs()), 7)
        self.assertTrue(all(m.lineup.mp == 5 for m in session.lineup_syncs()))
        self.assertTrue(all(m.lineup.max_mp == 5 for m in session.lineup_syncs()))
        self.assertTrue(all(n.cast_entity_id == 3 for n in session.mp_notifies()))

    async def test_normal_consumes_until_zero_without_sync(self):
        session = self._session(technique_points="normal")
        for _ in range(7):
            await self._cast(session)
        self.assertEqual(session.lineup.mp, 0)
        self.assertEqual([n.mp for n in session.mp_notifies()], [4, 3, 2, 1, 0, 0, 0])
        self.assertEqual(session.lineup_syncs(), [])

    async def test_on_empty_refills_when_exhausted(self):
        session = self._session(technique_points="on_empty")
        points = []
        for _ in range(6):
            await self._cast(session)
            points.append(session.lineup.mp)
        self.assertEqual(points, [4, 3, 2, 1, 5, 4])
        # 只有"补点"的那一次需要刷阵容
        self.assertEqual(len(session.lineup_syncs()), 1)
        self.assertEqual(session.lineup_syncs()[0].lineup.mp, 5)

    async def test_refill_tick_restores_one_point_per_interval(self):
        session = self._session(technique_points="refill", technique_refill_seconds=5.0)
        await self._cast(session)
        await self._cast(session)
        self.assertEqual(session.lineup.mp, 3)
        self.assertEqual(session.lineup_syncs(), [])
        base = session.lineup.mp_updated_at

        self.assertIsNone(lineup_service.tick_mp(session, base + 1.0))
        self.assertEqual(session.lineup.mp, 3)

        messages = lineup_service.tick_mp(session, base + 5.0)
        self.assertIsNotNone(messages)
        self.assertEqual(session.lineup.mp, 4)
        self.assertEqual(
            [n.mp for n in messages if isinstance(n, pb.SceneCastSkillMpUpdateScNotify)], [4]
        )
        self.assertEqual(
            [m.lineup.mp for m in messages if isinstance(m, pb.SyncLineupNotify)], [4]
        )

        now = base + 5.0
        for _ in range(2):
            now += 5.0
            lineup_service.tick_mp(session, now)
        self.assertEqual(session.lineup.mp, 5)
        self.assertIsNone(lineup_service.tick_mp(session, now + 30.0))

    async def test_refill_tick_is_noop_in_other_modes(self):
        for mode in ("unlimited", "normal", "on_empty"):
            session = self._session(technique_points=mode)
            await self._cast(session)
            self.assertIsNone(lineup_service.tick_mp(session, 1e9))

    async def test_heartbeat_pushes_refill(self):
        session = self._session(technique_points="refill", technique_refill_seconds=0.001)
        await self._cast(session)
        self.assertEqual(session.lineup.mp, 4)
        session.sent.clear()

        await asyncio.sleep(0.01)
        rsp = await player_handlers.on_heartbeat(
            session, pb.PlayerHeartBeatCsReq(client_time_ms=12345)
        )
        self.assertEqual(rsp.client_time_ms, 12345)
        self.assertEqual([n.mp for n in session.mp_notifies()], [5])
        self.assertEqual([m.lineup.mp for m in session.lineup_syncs()], [5])

    async def test_heartbeat_is_quiet_in_unlimited(self):
        session = self._session(technique_points="unlimited")
        await self._cast(session)
        session.sent.clear()
        await player_handlers.on_heartbeat(session, pb.PlayerHeartBeatCsReq(client_time_ms=1))
        self.assertEqual(session.sent, [])

    def test_lineup_info_carries_points(self):
        session = _Session(self.cfg)
        session.lineup.mp = 3
        lineup = lineup_service.build_lineup(session)
        self.assertEqual((lineup.mp, lineup.max_mp), (3, 5))

    def test_points_are_clamped(self):
        session = _Session(self.cfg)
        session.lineup.mp = 99
        self.assertEqual(lineup_service.current_mp(session), 5)
        session.lineup.mp = -3
        self.assertEqual(lineup_service.current_mp(session), 0)


if __name__ == "__main__":
    unittest.main()
