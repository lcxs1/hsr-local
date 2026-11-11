from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hsr.game.handlers import scene as scene_handlers
from hsr.game.services import challenge as challenge_service
from hsr.protocol.gen import StarRail_pb2 as pb
from tests.support import build_session


class ChallengeBattleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = build_session(Path(self.tmp.name))
        self.rt = self.session.runtime

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    async def _cast(self, challenge_id: int, **kwargs):
        state, scene = challenge_service.enter(self.session, challenge_id, **kwargs)
        rsp = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        return state, scene, rsp

    async def test_moc_battle(self) -> None:
        state, _scene, rsp = await self._cast(1101)
        self.assertEqual(rsp.cast_entity_id, 1)
        self.assertTrue(rsp.HasField("battle_info"))
        info = rsp.battle_info
        self.assertEqual(info.stage_id, state.stage_id)
        stage = self.rt.endgame.stage(state.stage_id)
        self.assertIsNotNone(stage)
        self.assertEqual(len(info.monster_wave_list), len(stage.waves))
        # field 7 = monster_wave_length in 4.6.54 (obfuscated as JAOIOKBBMFC);
        # pearl-sr always fills it, and a stale 0 can break multi-wave loading.
        self.assertEqual(info.JAOIOKBBMFC, len(stage.waves))
        # 轮次上限来自挑战配置（ChallengeCountDown）；PF 才是固定 4。
        self.assertEqual(
            info.rounds_limit, self.rt.endgame.challenge(1101).countdown
        )
        self.assertGreater(len(info.battle_avatar_list), 0)
        self.assertEqual(len(rsp.monster_battle_info), 1)
        self.assertEqual(
            rsp.monster_battle_info[0].target_monster_entity_id,
            state.monster_entity_id,
        )
        self.assertEqual(
            rsp.monster_battle_info[0].monster_battle_type,
            challenge_service.TRIGGER_BATTLE,
        )
        # MoC has no battle target slots
        self.assertEqual(len(info.battle_target_info), 0)
        self.assertEqual(self.session.battle_info.battle_id, info.battle_id)
        self.assertEqual(state.battle_id, info.battle_id)

    async def test_pf_battle(self) -> None:
        state, _scene, rsp = await self._cast(20111)
        info = rsp.battle_info
        self.assertEqual(state.mode, "PF")
        self.assertEqual(info.rounds_limit, 4)
        self.assertEqual(sorted(info.battle_target_info.keys()), [1, 2, 3, 4, 5])
        self.assertEqual(info.battle_target_info[1].battle_target_list[0].id, 10003)
        self.assertEqual(
            [target.id for target in info.battle_target_info[5].battle_target_list],
            [2001, 2002],
        )
        # the challenge's own buff (MazeBuffID) is appended as a battle buff
        self.assertTrue(info.buff_list)

    async def test_as_battle(self) -> None:
        state, _scene, rsp = await self._cast(30011)
        info = rsp.battle_info
        self.assertEqual(state.mode, "AS")
        # 末日幻影的配置行没有 ChallengeCountDown（countdown=0）→ 兜底 30。
        self.assertEqual(self.rt.endgame.challenge(30011).countdown, 0)
        self.assertEqual(info.rounds_limit, 30)
        self.assertEqual(info.battle_target_info[1].battle_target_list[0].id, 90005)
        self.assertEqual(len(rsp.monster_battle_info), 1)

    async def test_hit_target_overrides_monster(self) -> None:
        """客户端指名打哪只怪就用哪只；不存在的 id 回落到当前怪。

        换半场后场上怪物实体 id 会变（1000 → 1010）。客户端偶尔还拿旧 id 施放，
        回一个已删掉的实体 id 会让它开不了战斗，所以这里必须过滤。
        """
        state, _scene = challenge_service.enter(self.session, 1101)
        rsp = await scene_handlers.on_cast_skill(
            self.session,
            pb.SceneCastSkillCsReq(
                cast_entity_id=1,
                hit_target_entity_id_list=[state.monster_entity_id],
            ),
        )
        self.assertEqual(
            rsp.monster_battle_info[0].target_monster_entity_id,
            state.monster_entity_id,
        )
        stale = await scene_handlers.on_cast_skill(
            self.session,
            pb.SceneCastSkillCsReq(cast_entity_id=1, hit_target_entity_id_list=[4242]),
        )
        self.assertEqual(
            stale.monster_battle_info[0].target_monster_entity_id,
            state.monster_entity_id,
        )

    async def test_explicit_avatars(self) -> None:
        _state, _scene, rsp = await self._cast(1101, avatars=[1506, 1501])
        ids = [avatar.id for avatar in rsp.battle_info.battle_avatar_list]
        self.assertEqual(len(ids), 2)
        self.assertIn(1506, ids)
        self.assertIn(1501, ids)

    async def test_no_challenge_keeps_ack(self) -> None:
        rsp = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=5)
        )
        self.assertEqual(rsp.cast_entity_id, 5)
        self.assertFalse(rsp.HasField("battle_info"))
        self.assertEqual(len(rsp.monster_battle_info), 0)


if __name__ == "__main__":
    unittest.main()

