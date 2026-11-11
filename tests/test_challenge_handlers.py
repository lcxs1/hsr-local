from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hsr.game.handlers import battle as battle_handlers
from hsr.game.handlers import challenge as challenge_handlers
from hsr.game.handlers import scene as scene_handlers
from hsr.game.services import challenge as challenge_service
from hsr.game.services import scene as scene_service
from hsr.protocol.gen import StarRail_pb2 as pb
from tests.support import build_session


class ChallengeHandlersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = build_session(Path(self.tmp.name))
        self.rt = self.session.runtime
        # 测试基线：上半 -> 下半 走重建场景（config.toml 里的探针值不参与单测）。
        self.rt.cfg.endgame.advance = "scene"
        self.sent: list = []

        async def record(msg, name=None) -> None:  # noqa: ANN001
            self.sent.append(msg)

        self.session.send = record  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    def _lineup(self, *avatars: int) -> list[pb.AvatarLineup]:
        return [
            pb.AvatarLineup(id=avatar_id, avatar_type=pb.AvatarType.AvatarType_AvatarFormalType)
            for avatar_id in avatars
        ]

    async def test_get_challenge_list(self) -> None:
        rsp = await challenge_handlers.on_get_challenge(
            self.session, pb.GetChallengeCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(len(rsp.challenge_list), 843)
        by_id = {entry.challenge_id: entry for entry in rsp.challenge_list}
        self.assertEqual(by_id[1101].star, challenge_handlers.CHALLENGE_STAR)
        self.assertEqual(by_id[1101].score_id, 0)
        self.assertEqual(by_id[20111].score_id, 40000)
        self.assertEqual(by_id[20111].score_two, 40000)
        self.assertEqual(by_id[30011].score_id, 0)

    async def test_max_level_list_probe(self) -> None:
        """send_max_level=true 时每个挑战一条，level = 该组最大层（Himeko 写法）。"""
        self.rt.cfg.endgame.send_max_level = False
        rsp = await challenge_handlers.on_get_challenge(
            self.session, pb.GetChallengeCsReq()
        )
        self.assertEqual(len(rsp.max_level_list), 0)
        self.rt.cfg.endgame.send_max_level = True
        rsp = await challenge_handlers.on_get_challenge(
            self.session, pb.GetChallengeCsReq()
        )
        self.assertEqual(len(rsp.max_level_list), len(rsp.challenge_list))
        levels = {entry.level for entry in rsp.max_level_list}
        self.assertIn(15, levels)
        self.assertIn(12, levels)

    async def test_challenge_group_list_covers_every_group(self) -> None:
        """The 忘却之庭 card list needs one ChallengeGroup per group.

        2026-11-11 real machine: only ``challenge_list`` was sent, so the
        混沌回忆 groups had no card and the page looked empty.
        """
        rsp = await challenge_handlers.on_get_challenge(
            self.session, pb.GetChallengeCsReq()
        )
        expected = {
            challenge.group_id
            for challenge in self.rt.endgame.challenges.values()
            if challenge.group_id
        }
        got = [group.group_id for group in rsp.challenge_group_list]
        self.assertEqual(sorted(got), sorted(expected))
        self.assertEqual(got, sorted(got), "groups must be sorted")
        self.assertTrue(all(group.taken_stars_count_reward == 0 for group in rsp.challenge_group_list))
        # every mode present: MOC 101, PF 2011, AS 3011 style group ids
        modes = {challenge.mode for challenge in self.rt.endgame.challenges.values()}
        self.assertEqual(modes, {"MOC", "PF", "AS"})

    async def test_group_statistics_is_populated(self) -> None:
        """The client NREs on an empty statistics oneof (seen on 4.6.54)."""
        endgame = self.rt.endgame
        cases = (
            (endgame.challenge(15).group_id, "challenge_default"),
            (endgame.challenge(20111).group_id, "challenge_story"),
            (endgame.challenge(30011).group_id, "challenge_boss"),
        )
        for group_id, field in cases:
            rsp = await challenge_handlers.on_challenge_group_statistics(
                self.session, pb.GetChallengeGroupStatisticsCsReq(group_id=group_id)
            )
            self.assertEqual(rsp.retcode, 0)
            self.assertEqual(rsp.group_id, group_id)
            self.assertTrue(rsp.HasField(field), (group_id, field))
            stats = getattr(rsp, field)
            record = stats.PPBHLLOJNEK
            self.assertGreater(record.level, 0)
            self.assertTrue(record.lineup_list)
            self.assertTrue(record.lineup_list[0].avatar_list)

    async def test_start_moc(self) -> None:
        req = pb.StartChallengeCsReq(
            challenge_id=1101,
            avatar_lineup_first=self._lineup(1506, 1501, 1502, 1503),
        )
        rsp = await challenge_handlers.on_start_challenge(self.session, req)
        self.assertEqual(rsp.retcode, 0)
        node = self.rt.endgame.challenge(1101).node1
        self.assertEqual(rsp.scene.floor_id, node.floor_id)
        self.assertEqual(rsp.scene.entry_id, node.entrance_id)
        self.assertEqual(rsp.cur_challenge.challenge_id, 1101)
        self.assertEqual(
            rsp.cur_challenge.status, pb.ChallengeStatus.CHALLENGE_DOING
        )
        # 1101 has a 下半: both teams go out in the response (DanhengServer and
        # LunarCore send both for StageNum >= 2); the second one falls back to the
        # first when the client only configured one lineup.
        self.assertEqual(len(rsp.lineup_list), 2)
        lineup, second = rsp.lineup_list
        self.assertEqual(
            lineup.extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge,
        )
        self.assertEqual(
            second.extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )
        # extra lineup 的 proto ``index`` 恒为 0，阵容种类只由
        # ``extra_lineup_type`` 区分（Danheng ``LineupInfo.ToProto`` 的写法；
        # 旧实现写成 type+10 是把存储键当成了 proto 字段）。
        self.assertEqual(lineup.index, 0)
        self.assertEqual(second.index, 0)
        self.assertEqual(len(lineup.avatar_list), 4)
        self.assertEqual(
            sorted(avatar.id for avatar in lineup.avatar_list),
            [1501, 1502, 1503, 1506],
        )

        state = self.session.challenge
        self.assertIsNotNone(state)
        self.assertEqual(state.challenge_id, 1101)
        self.assertEqual(state.stage_id, node.stage_id)
        # the NPC can now be attacked and yields the real battle payload
        cast = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertTrue(cast.HasField("battle_info"))
        self.assertEqual(cast.battle_info.stage_id, node.stage_id)

        # GetCurSceneInfo must show the arena (NPC group), not the calyx group.
        current = scene_service.build_scene(self.session)
        group_ids = [group.group_id for group in current.entity_group_list]
        self.assertIn(node.maze_group_id, group_ids)
        self.assertNotIn(self.rt.cfg.world.calyx_group_id, group_ids)

    async def test_start_pf_with_story_buff(self) -> None:
        req = pb.StartChallengeCsReq(
            challenge_id=20111,
            avatar_lineup_first=self._lineup(1506, 1501, 1502, 1503),
        )
        req.stage_info.story_info.buff_one = 3034001
        rsp = await challenge_handlers.on_start_challenge(self.session, req)
        self.assertEqual(rsp.retcode, 0)
        buffs = list(rsp.cur_challenge.stage_info.cur_story_buffs.buff_list)
        self.assertEqual(buffs, [3034001, self.rt.endgame.challenge(20111).buff_id])
        self.assertEqual(rsp.cur_challenge.score_id, 40000)

    async def test_start_pf_second_node(self) -> None:
        challenge = self.rt.endgame.challenge(20111)
        self.assertIsNotNone(challenge.node2)
        req = pb.StartChallengeCsReq(
            challenge_id=20111,
            avatar_lineup_second=self._lineup(1506, 1501, 1502, 1503),
        )
        req.stage_info.story_info.buff_two = 3034002
        rsp = await challenge_handlers.on_start_challenge(self.session, req)
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.scene.floor_id, challenge.node2.floor_id)
        self.assertEqual(
            rsp.cur_challenge.extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )
        self.assertEqual(
            rsp.lineup_list[1].extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )
        buffs = list(rsp.cur_challenge.stage_info.cur_story_buffs.buff_list)
        self.assertEqual(buffs, [3034002, challenge.buff_id])
        self.assertTrue(self.session.challenge.second)

    async def test_start_as_with_boss_buff(self) -> None:
        req = pb.StartChallengeCsReq(
            challenge_id=30011,
            avatar_lineup_first=self._lineup(1506, 1501, 1502, 1503),
        )
        req.stage_info.boss_info.buff_one = 3110002
        rsp = await challenge_handlers.on_start_challenge(self.session, req)
        self.assertEqual(rsp.retcode, 0)
        boss = rsp.cur_challenge.stage_info.cur_boss_buffs
        self.assertEqual(boss.challenge_boss_const, 1)
        self.assertEqual(
            list(boss.buff_list),
            [3110002, self.rt.endgame.challenge(30011).buff_id],
        )

    async def test_get_cur_challenge(self) -> None:
        empty = await challenge_handlers.on_get_cur_challenge(
            self.session, pb.GetCurChallengeCsReq()
        )
        self.assertEqual(empty.retcode, 0)
        self.assertFalse(empty.HasField("cur_challenge"))

        req = pb.StartChallengeCsReq(
            challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
        )
        await challenge_handlers.on_start_challenge(self.session, req)
        cur = await challenge_handlers.on_get_cur_challenge(
            self.session, pb.GetCurChallengeCsReq()
        )
        self.assertTrue(cur.HasField("cur_challenge"))
        self.assertEqual(cur.cur_challenge.challenge_id, 1101)
        self.assertEqual(len(cur.lineup_list), 2)

    async def test_leave_challenge_restores_world(self) -> None:
        req = pb.StartChallengeCsReq(
            challenge_id=30011, avatar_lineup_first=self._lineup(1506, 1501)
        )
        await challenge_handlers.on_start_challenge(self.session, req)
        rsp = await challenge_handlers.on_leave_challenge(
            self.session, pb.LeaveChallengeCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertIsNone(self.session.challenge)
        self.assertIsNone(self.session.battle_info)
        self.assertEqual(self.session.scene.plane_id, self.rt.cfg.world.plane_id)
        self.assertEqual(self.session.scene.floor_id, self.rt.cfg.world.floor_id)
        kinds = {type(message).__name__ for message in self.sent}
        self.assertIn("QuitBattleScNotify", kinds)
        self.assertIn("EnterSceneByServerScNotify", kinds)
        enter = next(
            message
            for message in self.sent
            if type(message).__name__ == "EnterSceneByServerScNotify"
        )
        self.assertEqual(enter.scene.plane_id, self.rt.cfg.world.plane_id)
        world = scene_service.build_scene(self.session)
        group_ids = [group.group_id for group in world.entity_group_list]
        self.assertIn(self.rt.cfg.world.calyx_group_id, group_ids)

    async def test_enter_scene_lobby(self) -> None:
        req = pb.EnterSceneCsReq(
            entry_id=100000104,
            scene_identifier=pb.SceneIdentifier(floor_id=10000000),
        )
        rsp = await scene_handlers.on_enter_scene(self.session, req)
        # pearl-sr order: EnterSceneScRsp first, then EnterSceneByServerScNotify,
        # so the handler sends both itself and returns None.
        self.assertIsNone(rsp)
        self.assertEqual(self.session.scene.plane_id, 10000)
        self.assertEqual(self.session.scene.floor_id, 10000000)
        self.assertEqual(self.session.scene.entry_id, 100000104)
        names = [type(message).__name__ for message in self.sent]
        self.assertEqual(names, ["EnterSceneScRsp", "EnterSceneByServerScNotify"])
        scene = self.sent[1].scene
        self.assertEqual(scene.plane_id, 10000)
        self.assertEqual(scene.floor_id, 10000000)
        anchor = self.rt.endgame.anchor(100000104)
        self.assertEqual(
            self.session.scene.position,
            (anchor["x"], anchor["y"], anchor["z"]),
        )
        # outside the fixed open world there is no calyx group
        current = scene_service.build_scene(self.session)
        group_ids = [group.group_id for group in current.entity_group_list]
        self.assertNotIn(self.rt.cfg.world.calyx_group_id, group_ids)

    async def test_enter_scene_unknown_keeps_world(self) -> None:
        rsp = await scene_handlers.on_enter_scene(
            self.session, pb.EnterSceneCsReq(entry_id=99999999)
        )
        self.assertIsNotNone(rsp)
        self.assertEqual(self.session.scene.plane_id, self.rt.cfg.world.plane_id)
        self.assertEqual(len(self.sent), 0)

    async def test_leave_returns_to_world_after_lobby_teleport(self) -> None:
        """Guide 传送 into the lobby, fight, leave -> back to the world (not the train).

        2026-11-11 real machine: after a challenge the player was stuck in the
        列车车厢 (plane 10000) and the 传送 button did nothing.
        """
        world = (self.rt.cfg.world.plane_id, self.rt.cfg.world.floor_id, self.rt.cfg.world.entry_id)
        await scene_handlers.on_enter_scene(
            self.session,
            pb.EnterSceneCsReq(
                entry_id=100000104,
                scene_identifier=pb.SceneIdentifier(floor_id=10000000),
            ),
        )
        self.assertEqual(self.session.scene.plane_id, 10000)
        self.assertEqual(tuple(self.session.lobby_return), world)
        self.sent.clear()
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        rsp = await challenge_handlers.on_leave_challenge(
            self.session, pb.LeaveChallengeCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(self.session.scene.plane_id, world[0])
        self.assertEqual(self.session.scene.floor_id, world[1])
        self.assertEqual(self.session.scene.entry_id, world[2])
        self.assertIsNone(self.session.lobby_return)
        enter = next(
            message
            for message in self.sent
            if type(message).__name__ == "EnterSceneByServerScNotify"
        )
        self.assertEqual(enter.scene.plane_id, world[0])

    async def test_leave_returns_to_lobby_without_teleport_record(self) -> None:
        """No lobby teleport in this session -> fall back to where the fight started."""
        await scene_handlers.on_enter_scene(
            self.session,
            pb.EnterSceneCsReq(
                entry_id=100000104,
                scene_identifier=pb.SceneIdentifier(floor_id=10000000),
            ),
        )
        # Simulate a session that was already in the lobby at login.
        self.session.lobby_return = None
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        await challenge_handlers.on_leave_challenge(
            self.session, pb.LeaveChallengeCsReq()
        )
        self.assertEqual(self.session.scene.plane_id, 10000)
        self.assertEqual(self.session.scene.entry_id, 100000104)

    async def test_reenter_same_entry_reloads_scene(self) -> None:
        """Clicking 传送 again for the current entry must not be a no-op."""
        await scene_handlers.on_enter_scene(
            self.session,
            pb.EnterSceneCsReq(
                entry_id=100000104,
                scene_identifier=pb.SceneIdentifier(floor_id=10000000),
            ),
        )
        self.sent.clear()
        rsp = await scene_handlers.on_enter_scene(
            self.session,
            pb.EnterSceneCsReq(
                entry_id=100000104,
                scene_identifier=pb.SceneIdentifier(floor_id=10000000),
            ),
        )
        self.assertIsNone(rsp)
        kinds = [type(message).__name__ for message in self.sent]
        self.assertEqual(kinds, ["EnterSceneScRsp", "EnterSceneByServerScNotify"])
        self.assertEqual(self.session.scene.entry_id, 100000104)

    async def test_reenter_scene_during_challenge_keeps_phase_scene(self) -> None:
        """挑战中客户端重进同一入口 → 回**当前半场**的挑战场景，不是野外场景。

        `advance=sync` 发完 `SyncServerSceneChangeNotify` 之后客户端会自己再走一遍
        进场（可能发 `EnterSceneCsReq`）。这条如果落到野外场景装配，下半的迷宫组
        和队伍会被冲掉。
        """
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_first=self._lineup(1506, 1501),
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        node1 = self.rt.endgame.challenge(1101).node1
        self.sent.clear()
        rsp = await scene_handlers.on_enter_scene(
            self.session,
            pb.EnterSceneCsReq(entry_id=node1.entrance_id),
        )
        self.assertIsNone(rsp)
        kinds = [type(message).__name__ for message in self.sent]
        self.assertEqual(kinds, ["EnterSceneScRsp", "EnterSceneByServerScNotify"])
        scene = self.sent[1].scene
        self.assertEqual(scene.entry_id, node1.entrance_id)
        group_ids = [group.group_id for group in scene.entity_group_list]
        self.assertIn(node1.maze_group_id, group_ids)
        self.assertEqual(
            self.sent[1].lineup.extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge,
        )
        # 挑战状态没被这次重进改掉
        self.assertEqual(self.session.challenge.stage, 1)
        self.assertEqual(self.session.challenge.node.maze_group_id, node1.maze_group_id)

    async def test_second_lineup_is_kept_for_lower_half(self) -> None:
        """The pre-battle 队伍配置 sends both lineups; 下半 must use the second."""
        rsp = await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_first=self._lineup(1506, 1501),
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        self.assertEqual(rsp.retcode, 0)
        state = self.session.challenge
        first = [self.rt.roster.base_id(1506), self.rt.roster.base_id(1501)]
        second = [self.rt.roster.base_id(1414), self.rt.roster.base_id(1502)]
        self.assertEqual(state.first_avatars, first)
        self.assertEqual(state.second_avatars, second)
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        self.assertEqual(state.stage, 2)
        self.assertEqual(state.avatars, second)
        kinds = [type(message).__name__ for message in self.sent]
        self.assertIn("ChallengeLineupNotify", kinds)
        lineup_notify = self.sent[kinds.index("ChallengeLineupNotify")]
        self.assertEqual(
            lineup_notify.extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )
        second_battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        team = [avatar.id for avatar in second_battle.battle_info.battle_avatar_list]
        self.assertEqual(sorted(team), sorted(second))

    async def test_start_challenge_with_second_lineup_enters_lower_half(self) -> None:
        """pearl-sr flow: the client re-sends StartChallenge for 下半 (second only)."""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_first=self._lineup(1506, 1501),
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        self.sent.clear()
        rsp = await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        self.assertEqual(rsp.retcode, 0)
        state = self.session.challenge
        self.assertEqual(state.stage, 2)
        self.assertEqual(state.challenge_id, 1101)
        node2 = self.rt.endgame.challenge(1101).node2
        self.assertEqual(rsp.scene.entry_id, node2.entrance_id)
        self.assertEqual(state.avatars, [self.rt.roster.base_id(1414), self.rt.roster.base_id(1502)])

    async def test_unknown_challenge_id(self) -> None:
        rsp = await challenge_handlers.on_start_challenge(
            self.session, pb.StartChallengeCsReq(challenge_id=99999999)
        )
        self.assertEqual(rsp.retcode, 1)
        self.assertIsNone(self.session.challenge)

    async def test_two_stage_challenge_advances_to_second_half(self) -> None:
        """Clear 上半 -> load 下半 (node2 + LineupChallenge2), do not settle."""
        self.rt.cfg.endgame.advance = "scene"
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        first_battle_id = battle.battle_info.battle_id
        result = await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=first_battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        self.assertIsNone(result)
        self.assertIsNone(self.session.battle_info)
        self.assertIsNotNone(self.session.challenge)
        kinds = [type(message).__name__ for message in self.sent]
        self.assertNotIn("ChallengeSettleNotify", kinds)
        # advance=scene（LunarCore 做法）：阵容切换先在结果包前发，场景重建在结果后。
        self.assertEqual(
            kinds[-3:],
            [
                "PVEBattleResultScRsp",
                "SyncLineupNotify",
                "EnterSceneByServerScNotify",
            ],
        )
        self.assertEqual(kinds[0], "ChallengeLineupNotify")
        node2 = self.rt.endgame.challenge(1101).node2
        self.assertEqual(self.session.challenge.stage, 2)
        self.assertTrue(self.session.challenge.second)
        self.assertEqual(self.session.challenge.stage_id, node2.stage_id)
        self.assertEqual(self.session.challenge.node.entrance_id, node2.entrance_id)
        scene = self.sent[-1].scene
        self.assertEqual(scene.entry_id, node2.entrance_id)
        group_ids = [group.group_id for group in scene.entity_group_list]
        self.assertIn(node2.maze_group_id, group_ids)
        lineup_notify = self.sent[kinds.index("ChallengeLineupNotify")]
        self.assertEqual(
            lineup_notify.extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )

    async def test_advance_inplace_uses_reference_packets(self) -> None:
        """``advance=inplace``：同场景原地换组，包形与参考实现逐条对齐。

        参考（HyacineCore 4.0 ``ChallengeMemoryInstance.NextPhase`` /
        DanhengServer ``AdvanceStage``）：
        ``ChallengeLineupNotify`` → 角色组刷新 → 卸旧迷宫组 → 装新迷宫组；
        入口不变时**不重建 SceneInfo**（重建就是 4.6.54/4.7.0 的下半黑屏）。
        """
        self.rt.cfg.endgame.advance = "inplace"
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_first=self._lineup(1506, 1501),
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        kinds = [type(message).__name__ for message in self.sent]
        # 阵容切换在结果包之前（客户端还在战斗态就会处理，参考实现如此），
        # 场景组刷新在结果包之后（战斗中发的组刷新会被客户端丢掉）。
        self.assertEqual(kinds[0], "ChallengeLineupNotify")
        self.assertEqual(
            self.sent[0].extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )
        self.assertEqual(kinds.count("PVEBattleResultScRsp"), 1)
        self.assertGreater(kinds.index("PVEBattleResultScRsp"), 0)
        self.assertNotIn("EnterSceneByServerScNotify", kinds)
        self.assertNotIn("SyncLineupNotify", kinds)

        refreshes = [
            message
            for message in self.sent
            if type(message).__name__ == "SceneGroupRefreshScNotify"
        ]
        # 角色组刷新 + 装 node2（默认不卸 node1）
        self.assertEqual(len(refreshes), 2)
        avatar_group = self.rt.cfg.world.avatar_group_id
        rsp_index = kinds.index("PVEBattleResultScRsp")
        refresh_indexes = [
            index
            for index, message in enumerate(self.sent)
            if type(message).__name__ == "SceneGroupRefreshScNotify"
        ]
        before_rsp = [index for index in refresh_indexes if index < rsp_index]
        after_rsp = [index for index in refresh_indexes if index > rsp_index]
        self.assertEqual(len(before_rsp), 1)
        self.assertEqual(len(after_rsp), 1)
        avatar_refresh = self.sent[before_rsp[0]]
        self.assertEqual(
            [entry.group_id for entry in avatar_refresh.group_refresh_list],
            [avatar_group],
        )
        self.assertTrue(
            any(
                item.HasField("delete_entity") for item in avatar_refresh.group_refresh_list[0].refresh_entity
            )
        )
        self.assertTrue(
            any(
                item.HasField("add_entity") for item in avatar_refresh.group_refresh_list[0].refresh_entity
            )
        )
        for refresh in refreshes:
            for entry in refresh.group_refresh_list:
                # 参考实现一个字段都不多设：state 不写、group_info 不带
                self.assertEqual(entry.state, 0)
                self.assertFalse(entry.HasField("group_info"))
                self.assertEqual(
                    entry.refresh_type,
                    pb.SceneGroupRefreshType.SCENE_GROUP_REFRESH_TYPE_LOADED,
                )
        node1 = self.rt.endgame.challenge(1101).node1
        node2 = self.rt.endgame.challenge(1101).node2
        groups = [entry.group_id for refresh in refreshes for entry in refresh.group_refresh_list]
        # 默认不卸旧组：4.7.0 把 `refresh_type=LOADED` 的卸载包当成“激活组”，
        # 报 "Reload ServerGroup Error, Already Exists" 并在 _ActiveServerHoyoGroup NRE。
        self.assertNotIn(node1.maze_group_id, groups)
        self.assertIn(node2.maze_group_id, groups)
        # 装新组只发 add（一条 LOADED），不带 delete
        load = [
            entry
            for refresh in refreshes
            for entry in refresh.group_refresh_list
            if entry.group_id == node2.maze_group_id
        ][0]
        added = [item.add_entity for item in load.refresh_entity if item.HasField("add_entity")]
        self.assertTrue(added)
        self.assertFalse(
            any(item.HasField("delete_entity") for item in load.refresh_entity)
        )
        self.assertEqual(added[0].npc_monster.event_id, node2.stage_id)
        self.assertGreaterEqual(added[0].entity_id, 1010)
        # 状态推进到下半，且下半用的对手是第二队
        state = self.session.challenge
        self.assertEqual(state.index, 1)
        self.assertEqual(state.stage, 2)
        self.assertTrue(state.second)
        self.assertEqual(state.stage_id, node2.stage_id)
        self.assertEqual(
            state.avatars,
            [self.rt.roster.base_id(1414), self.rt.roster.base_id(1502)],
        )

    async def test_advance_sync_uses_reference_packets(self) -> None:
        """``advance=sync``（默认）：LunarCore ``advanceStage`` 的包顺序。

        ``ChallengeLineupNotify`` → ``PVEBattleResultScRsp`` →
        ``SyncServerSceneChangeNotify`` + ``SyncLineupNotify`` +
        ``EnterSceneByServerScNotify``（整份新 ``SceneInfo``，``entity_group_list``
        里声明下半的迷宫组）。

        关键点：**一条 ``SceneGroupRefreshScNotify`` 都不发**。4.7.0 客户端收到
        “激活一个它没声明过的组”的通知会走 ``MapDef._ActiveServerHoyoGroup`` 并
        NRE，下半就只剩一个看得见、打不动的 NPC。
        """
        self.rt.cfg.endgame.advance = "sync"
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_first=self._lineup(1506, 1501),
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        kinds = [type(message).__name__ for message in self.sent]
        self.assertEqual(kinds[0], "ChallengeLineupNotify")
        self.assertEqual(
            self.sent[0].extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )
        rsp_index = kinds.index("PVEBattleResultScRsp")
        # 阵容包在结果包前，场景切换包在结果包后（LunarCore advanceStage 顺序）
        self.assertEqual(kinds[:rsp_index], ["ChallengeLineupNotify", "SyncLineupNotify"])
        self.assertEqual(
            kinds[rsp_index + 1 :],
            ["SyncServerSceneChangeNotify", "EnterSceneByServerScNotify"],
        )
        self.assertNotIn("SceneGroupRefreshScNotify", kinds)
        self.assertNotIn("ChallengeSettleNotify", kinds)

        node2 = self.rt.endgame.challenge(1101).node2
        scene = self.sent[-1].scene
        self.assertEqual(scene.entry_id, node2.entrance_id)
        groups = {group.group_id: group for group in scene.entity_group_list}
        self.assertIn(node2.maze_group_id, groups)
        monsters = [entity for entity in groups[node2.maze_group_id].entity_list]
        self.assertTrue(monsters)
        self.assertEqual(monsters[0].npc_monster.event_id, node2.stage_id)
        # 场上角色组必须是第二队（否则客户端把上半那队站在下半场地里）
        avatar_group = groups[self.rt.cfg.world.avatar_group_id]
        self.assertEqual(len(avatar_group.entity_list), 2)

        state = self.session.challenge
        self.assertEqual(state.index, 1)
        self.assertEqual(state.stage, 2)
        self.assertTrue(state.second)
        self.assertEqual(state.stage_id, node2.stage_id)
        self.assertEqual(
            state.monster_entity_id,
            next(
                entity.entity_id
                for entity in monsters
                if entity.HasField("npc_monster")
            ),
        )
        self.assertEqual(
            state.avatars,
            [self.rt.roster.base_id(1414), self.rt.roster.base_id(1502)],
        )
        # 下半再打一场用的是新实体 id 与新队伍
        second_battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertEqual(second_battle.battle_info.stage_id, node2.stage_id)
        self.assertEqual(
            [avatar.id for avatar in second_battle.battle_info.battle_avatar_list],
            state.avatars,
        )

    async def test_advance_declare_uses_group_info_not_entity_patch(self) -> None:
        """``advance=declare``：原地**声明**下一个迷宫组，不推场景、不重载 stage。

        `GroupRefreshInfo.group_info` 与 `SceneInfo.entity_group_list` 是同一个
        message（`SceneEntityGroupInfo`），所以组可以直接“声明”出来，而不用 4.7
        会 NRE 的逐实体 patch；也不发 `SyncServerSceneChangeNotify`，避免客户端
        重载 stage 之后立刻进战斗卡在进场动画。
        """
        self.rt.cfg.endgame.advance = "declare"
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_first=self._lineup(1506, 1501),
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        kinds = [type(message).__name__ for message in self.sent]
        self.assertEqual(kinds[0], "ChallengeLineupNotify")
        self.assertNotIn("EnterSceneByServerScNotify", kinds)
        self.assertNotIn("SyncServerSceneChangeNotify", kinds)
        self.assertNotIn("ChallengeSettleNotify", kinds)
        node2 = self.rt.endgame.challenge(1101).node2
        rsp_index = kinds.index("PVEBattleResultScRsp")
        after = self.sent[rsp_index + 1 :]
        self.assertEqual([type(m).__name__ for m in after], ["SceneGroupRefreshScNotify"])
        entry = after[0].group_refresh_list[0]
        self.assertEqual(entry.group_id, node2.maze_group_id)
        self.assertTrue(entry.HasField("group_info"))
        self.assertEqual(entry.group_info.group_id, node2.maze_group_id)
        # 逐实体 patch 为空：组是靠 group_info 声明的
        self.assertEqual(len(entry.refresh_entity), 0)
        monsters = [e for e in entry.group_info.entity_list if e.HasField("npc_monster")]
        self.assertTrue(monsters)
        self.assertEqual(monsters[0].npc_monster.event_id, node2.stage_id)
        # 后半场仍然能正常开打
        lower = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertEqual(lower.battle_info.stage_id, node2.stage_id)
        self.assertEqual(
            self.session.challenge.monster_entity_id, monsters[0].entity_id
        )

    async def test_advance_scene_mode_still_available(self) -> None:
        """``advance=scene`` 保留做 A/B：重建 SceneInfo 走 EnterSceneByServerScNotify。"""
        self.rt.cfg.endgame.advance = "scene"
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        kinds = [type(message).__name__ for message in self.sent]
        self.assertEqual(kinds[-1], "EnterSceneByServerScNotify")
        node2 = self.rt.endgame.challenge(1101).node2
        scene = self.sent[-1].scene
        self.assertEqual(scene.entry_id, node2.entrance_id)
        group_ids = [group.group_id for group in scene.entity_group_list]
        self.assertIn(node2.maze_group_id, group_ids)

    async def test_advance_none_waits_for_client_start(self) -> None:
        """advance=none：服务端不推任何包，等客户端自己发 StartChallenge（pearl 流程）。"""
        self.rt.cfg.endgame.advance = "none"
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        self.assertEqual([type(message).__name__ for message in self.sent], ["PVEBattleResultScRsp"])
        self.assertEqual(self.session.challenge.stage, 1)
        rsp = await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_second=self._lineup(1414, 1502)
            ),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(self.session.challenge.stage, 2)

    async def test_start_direct_pushes_arena_without_lobby(self) -> None:
        """模拟器口径的入口：不走忘却之庭，直接把仓库场景推给客户端。"""
        scene = challenge_service.start_direct(self.session, 1101)
        self.assertIsNotNone(scene)
        node = self.rt.endgame.challenge(1101).node1
        self.assertEqual(scene.plane_id, node.plane_id)
        self.assertEqual(scene.floor_id, node.floor_id)
        self.assertTrue(self.session.challenge.direct)
        cast = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertTrue(cast.HasField("battle_info"))

    async def test_start_direct_can_enter_second_half_first(self) -> None:
        """`/debug/challenge?second=1`：把下半当**第一场**战斗来打（真机探针）。

        下半的战斗在“换半场之后”打不出来，用这个入口可以直接对比
        「下半的战斗包」和「换半场那一下」哪个才是问题。
        """
        scene = challenge_service.start_direct(self.session, 1101, second=True)
        self.assertIsNotNone(scene)
        node2 = self.rt.endgame.challenge(1101).node2
        self.assertEqual(self.session.challenge.index, 1)
        self.assertEqual(self.session.challenge.stage_id, node2.stage_id)
        self.assertEqual(scene.entry_id, node2.entrance_id)
        self.assertIn(
            node2.maze_group_id, [group.group_id for group in scene.entity_group_list]
        )
        self.assertEqual(self.sent, [])  # start_direct 只装配，发包子系统在调用方
        cast = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertEqual(cast.battle_info.stage_id, node2.stage_id)
        # 下半是最后一阶段：打赢就直接结算
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=cast.battle_info.battle_id,
                stage_id=cast.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        kinds = [type(message).__name__ for message in self.sent]
        self.assertIn("ChallengeSettleNotify", kinds)

    async def test_direct_attempt_returns_to_world_after_settle(self) -> None:
        challenge_service.start_direct(self.session, 1)  # 单半层，直接结算
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        kinds = [type(message).__name__ for message in self.sent]
        self.assertIn("ChallengeSettleNotify", kinds)
        self.assertIn("QuitBattleScNotify", kinds)
        self.assertEqual(kinds[-1], "EnterSceneByServerScNotify")
        self.assertEqual(self.sent[-1].scene.plane_id, self.rt.cfg.world.plane_id)
        self.assertIsNone(self.session.challenge)

    async def test_stale_first_half_result_is_ignored_after_advance(self) -> None:
        """A duplicate 上半 result must not settle the 下半."""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        first = pb.PVEBattleResultCsReq(
            battle_id=battle.battle_info.battle_id,
            stage_id=battle.battle_info.stage_id,
            end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
        )
        await battle_handlers.on_pve_result(self.session, first)
        self.assertEqual(self.session.challenge.stage, 2)
        self.sent.clear()
        await battle_handlers.on_pve_result(self.session, first)
        kinds = [type(message).__name__ for message in self.sent]
        self.assertNotIn("ChallengeSettleNotify", kinds)
        self.assertEqual(self.session.challenge.stage, 2)

    async def test_second_half_result_settles(self) -> None:
        """After 下半 the run settles and LeaveChallenge returns to the lobby."""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        for index in range(2):
            battle = await scene_handlers.on_cast_skill(
                self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
            )
            await battle_handlers.on_pve_result(
                self.session,
                pb.PVEBattleResultCsReq(
                    battle_id=battle.battle_info.battle_id,
                    stage_id=battle.battle_info.stage_id,
                    end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
                ),
            )
            # first win -> 下半 (stage 2); second win -> settle, stage stays 2
            self.assertEqual(self.session.challenge.stage, 2)
            settle_kinds = [
                type(message).__name__ for message in self.sent
            ].count("ChallengeSettleNotify")
            self.assertEqual(settle_kinds, 1 if index == 1 else 0)

        settle = self.sent[-1]
        self.assertEqual(type(settle).__name__, "ChallengeSettleNotify")
        self.assertEqual(settle.challenge_id, 1101)
        self.assertTrue(settle.is_win)
        self.assertEqual(settle.star, 3)
        self.assertEqual(
            settle.cur_challenge.status, pb.ChallengeStatus.CHALLENGE_FINISH
        )

    async def test_leave_challenge_resets_extra_lineups(self) -> None:
        """退出挑战要关掉 LineupChallenge/Challenge2（参考实现 HandlerLeaveChallenge）。

        少了 ``ChallengeLineupNotify(LineupNone)``，客户端在下一场野外战斗里还拿
        额外阵容当当前阵容（换队/开战错乱）。
        """
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        self.sent.clear()
        await challenge_handlers.on_leave_challenge(
            self.session, pb.LeaveChallengeCsReq()
        )
        kinds = [type(message).__name__ for message in self.sent]
        self.assertEqual(
            kinds,
            [
                "ChallengeLineupNotify",
                "SyncLineupNotify",
                "QuitBattleScNotify",
                "EnterSceneByServerScNotify",
            ],
        )
        self.assertEqual(
            self.sent[0].extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupNone,
        )
        self.assertEqual(self.sent[1].lineup.extra_lineup_type, 0)
        self.assertIsNone(self.session.challenge)

    async def test_enter_next_phase_never_advances(self) -> None:
        """``EnterChallengeNextPhase`` 只重发当前阶段场景，不能自己跳半场。"""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        state = self.session.challenge
        rsp = await challenge_handlers.on_enter_next_phase(
            self.session, pb.EnterChallengeNextPhaseCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.scene.entry_id, state.node.entrance_id)
        self.assertEqual(state.index, 0)

        # 没有挑战时回 retcode=1，别让客户端等一个空场景。
        challenge_service.clear(self.session)
        rsp = await challenge_handlers.on_enter_next_phase(
            self.session, pb.EnterChallengeNextPhaseCsReq()
        )
        self.assertEqual(rsp.retcode, 1)

    async def test_restart_phase_rebuilds_current_phase(self) -> None:
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertTrue(self.session.challenge.battle_ids)
        rsp = await challenge_handlers.on_restart_challenge_phase(
            self.session, pb.RestartChallengePhaseCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        state = self.session.challenge
        self.assertEqual(state.index, 0)
        self.assertFalse(state.battle_ids)
        self.assertIsNone(self.session.battle_info)
        node = self.rt.endgame.challenge(1101).node1
        self.assertEqual(rsp.scene.entry_id, node.entrance_id)
        # 重开后还能再打，且是新 battle id
        again = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertNotEqual(again.battle_info.battle_id, battle.battle_info.battle_id)

    async def test_result_stats_drive_round_count(self) -> None:
        """剩余轮次 / 阵亡人数要落进 CurChallenge（旧实现永远是 0/30）。"""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        state = self.session.challenge
        countdown = self.rt.endgame.challenge(1101).countdown
        self.assertEqual(state.rounds_left, countdown)
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        req = pb.PVEBattleResultCsReq(
            battle_id=battle.battle_info.battle_id,
            stage_id=battle.battle_info.stage_id,
            end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
        )
        req.stt.round_cnt = 2
        await battle_handlers.on_pve_result(self.session, req)
        self.assertEqual(state.rounds_left, countdown - 2)
        cur = challenge_service.cur_challenge(self.session)
        self.assertEqual(cur.round_count, 2)

    async def test_avatar_group_refresh_only_when_team_changes(self) -> None:
        """两队相同时不刷角色组（多发一条组刷新只会给 legacy 路径添乱）。"""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        refreshes = [
            message
            for message in self.sent
            if type(message).__name__ == "SceneGroupRefreshScNotify"
        ]
        groups = [
            entry.group_id
            for refresh in refreshes
            for entry in refresh.group_refresh_list
        ]
        self.assertNotIn(self.rt.cfg.world.avatar_group_id, groups)

    async def test_advance_client_mode_waits(self) -> None:
        """``advance=client``：不推包，等客户端自己发 StartChallenge（pearl 流程）。"""
        self.rt.cfg.endgame.advance = "client"
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        self.assertEqual(
            [type(message).__name__ for message in self.sent], ["PVEBattleResultScRsp"]
        )
        self.assertEqual(self.session.challenge.index, 0)
        rsp = await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_second=self._lineup(1414, 1502)
            ),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(self.session.challenge.index, 1)

    async def test_round_budget_is_shared_across_phases(self) -> None:
        """轮次预算跨半场共用：下半只能拿到「上半用剩的」，重打本半场回退。"""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        state = self.session.challenge
        countdown = self.rt.endgame.challenge(1101).countdown
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertEqual(battle.battle_info.rounds_limit, countdown)
        req = pb.PVEBattleResultCsReq(
            battle_id=battle.battle_info.battle_id,
            stage_id=battle.battle_info.stage_id,
            end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
        )
        req.stt.round_cnt = 3
        await battle_handlers.on_pve_result(self.session, req)
        self.assertEqual(state.index, 1)
        self.assertEqual(state.rounds_left, countdown - 3)
        lower = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertEqual(lower.battle_info.rounds_limit, countdown - 3)

        await challenge_handlers.on_restart_challenge_phase(
            self.session, pb.RestartChallengePhaseCsReq()
        )
        self.assertEqual(state.rounds_left, countdown - 3)
        retry = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.assertEqual(retry.battle_info.rounds_limit, countdown - 3)

    async def test_advance_can_unload_old_group(self) -> None:
        """`unload_old_group=true`：卸载用 `refresh_type=UNLOAD`（整组），不是 LOADED。

        4.7.0 把 LOADED 的卸载包当成“激活组”→ `Already Exists` + NRE，
        所以默认不卸；这条只验证开关真的换成 UNLOAD。
        """
        self.rt.cfg.endgame.advance = "inplace"
        self.rt.cfg.endgame.unload_old_group = True
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        refreshes = [
            message
            for message in self.sent
            if type(message).__name__ == "SceneGroupRefreshScNotify"
        ]
        entries = [
            entry for refresh in refreshes for entry in refresh.group_refresh_list
        ]
        node1 = self.rt.endgame.challenge(1101).node1
        unload = [entry for entry in entries if entry.group_id == node1.maze_group_id]
        self.assertTrue(unload)
        self.assertEqual(
            unload[0].refresh_type,
            pb.SceneGroupRefreshType.SCENE_GROUP_REFRESH_TYPE_UNLOAD,
        )
        self.assertFalse(unload[0].refresh_entity)

    async def test_scene_info_uses_current_phase_team(self) -> None:
        """挑战中 GetCurSceneInfo 要用场上这一队，不能用野外阵容。"""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101,
                avatar_lineup_first=self._lineup(1506, 1501),
                avatar_lineup_second=self._lineup(1414, 1502),
            ),
        )
        first_scene = scene_service.build_scene(self.session)
        first_ids = [
            entity.actor.base_avatar_id
            for group in first_scene.entity_group_list
            for entity in group.entity_list
            if entity.HasField("actor")
        ]
        self.assertEqual(first_ids, [self.rt.roster.base_id(1506), self.rt.roster.base_id(1501)])

        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        second_scene = scene_service.build_scene(self.session)
        second_ids = [
            entity.actor.base_avatar_id
            for group in second_scene.entity_group_list
            for entity in group.entity_list
            if entity.HasField("actor")
        ]
        self.assertEqual(
            second_ids,
            [self.rt.roster.base_id(1414), self.rt.roster.base_id(1502)],
        )

    async def test_single_stage_challenge_settles_immediately(self) -> None:
        """MOC ids 1..10 have no node2: the first win settles the run."""
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        result = await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        self.assertIsNone(result)
        self.assertIsNone(self.session.battle_info)
        kinds = [type(message).__name__ for message in self.sent[-2:]]
        self.assertEqual(kinds, ["PVEBattleResultScRsp", "ChallengeSettleNotify"])
        settle = self.sent[-1]
        self.assertEqual(settle.challenge_id, 1)
        self.assertTrue(settle.is_win)
        self.assertEqual(settle.star, 3)
        self.assertEqual(
            settle.cur_challenge.status, pb.ChallengeStatus.CHALLENGE_FINISH
        )

    async def test_repeated_cast_reuses_battle_id(self) -> None:
        """The client re-casts while the arena loads; ids must not churn.

        4.6.54 real machine 2026-11-11: 8 casts -> 8 battle ids, the result came
        back for an older id, the settle notify was skipped and the monster
        respawned forever.  Repeated casts must hand back the same battle.
        """
        await challenge_handlers.on_start_challenge(
            self.session,
            pb.StartChallengeCsReq(
                challenge_id=1101, avatar_lineup_first=self._lineup(1506, 1501)
            ),
        )
        seen = []
        for _ in range(8):
            battle = await scene_handlers.on_cast_skill(
                self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
            )
            seen.append(battle.battle_info.battle_id)
        self.assertEqual(len(set(seen)), 1, seen)
        self.assertEqual(set(seen), self.session.challenge.battle_ids)

        # A result that names an id from the attempt is settled even if a later
        # cast already happened.
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=seen[0],
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        self.assertNotIn(
            "ChallengeSettleNotify",
            [type(message).__name__ for message in self.sent],
        )
        self.assertEqual(self.session.challenge.stage, 2)



    async def test_tierce_start_enters_first_node(self) -> None:
        """4.7.0 星启模式：三套队伍 + stage_index=0 回节点一的场景。

        真机 2026-11-11 21:05：混沌回忆第 12 层编队界面点「混沌回忆」，
        客户端发 ``StartChallengeTierceCsReq``（93 字节、三套阵容），服务端没
        实现 → 按钮点了没反应。这里锁住协议字段与状态。
        """
        rsp = await challenge_handlers.on_start_challenge_tierce(
            self.session,
            pb.StartChallengeTierceCsReq(
                challenge_id=5512,
                stage_index=0,
                lineup_tierce_list=[
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1506, 1501)),
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1502, 1503)),
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1201, 1202)),
                ],
            ),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.cur_tierce_challenge.challenge_id, 5512)
        self.assertEqual(rsp.cur_tierce_challenge.stage_index, 0)
        self.assertEqual(len(rsp.cur_tierce_challenge.lineup_list), 3)
        state = self.session.challenge
        self.assertTrue(state.tierce)
        self.assertEqual(len(state.tierce_teams), 3)
        self.assertEqual(state.stage_index, 0)
        self.assertEqual(rsp.scene.plane_id, state.node.plane_id)

        # 第二、第三个节点：场景换成 node2（我们的表只有两个节点）。
        first_group = state.node.maze_group_id

        async def win_current_node() -> None:
            battle = await scene_handlers.on_cast_skill(
                self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
            )
            await battle_handlers.on_pve_result(
                self.session,
                pb.PVEBattleResultCsReq(
                    battle_id=battle.battle_info.battle_id,
                    stage_id=battle.battle_info.stage_id,
                    end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
                ),
            )

        self.assertTrue(challenge_service.can_advance(self.session))
        await win_current_node()
        self.assertNotIn(
            "ChallengeSettleNotify",
            [type(message).__name__ for message in self.sent],
        )
        self.assertEqual(state.stage_index, 1)
        self.assertNotEqual(state.node.maze_group_id, first_group)
        # 第三节点打完才结算。
        self.assertTrue(challenge_service.can_advance(self.session))
        await win_current_node()
        self.assertEqual(state.stage_index, 2)
        self.assertFalse(challenge_service.can_advance(self.session))
        await win_current_node()
        self.assertIn(
            "ChallengeSettleNotify",
            [type(message).__name__ for message in self.sent],
        )

    async def test_tierce_unknown_id(self) -> None:
        rsp = await challenge_handlers.on_start_challenge_tierce(
            self.session,
            pb.StartChallengeTierceCsReq(
                challenge_id=999999,
                lineup_tierce_list=[
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1506))
                ],
            ),
        )
        self.assertEqual(rsp.retcode, 1)
        self.assertIsNone(self.session.challenge)

    async def test_tierce_leave_returns_to_world(self) -> None:
        await challenge_handlers.on_start_challenge_tierce(
            self.session,
            pb.StartChallengeTierceCsReq(
                challenge_id=5512,
                lineup_tierce_list=[
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1506, 1501))
                ],
            ),
        )
        rsp = await challenge_handlers.on_leave_challenge_tierce(
            self.session, pb.LeaveChallengeTierceCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertIsNone(self.session.challenge)
        names = [type(message).__name__ for message in self.sent]
        self.assertIn("QuitBattleScNotify", names)
        self.assertIn("EnterSceneByServerScNotify", names)


    async def test_tierce_real_awakening_id_5513(self) -> None:
        """真机 4.7.0：客户端发的星启 id 是 ``tierce`` 表的行（5513 = 5512 + 1）。

        三个节点 = 5512 的上半 / 5512 的下半 / tierce 行自己（污染等级 3）。
        """
        rsp = await challenge_handlers.on_start_challenge_tierce(
            self.session,
            pb.StartChallengeTierceCsReq(
                challenge_id=5513,
                stage_index=0,
                lineup_tierce_list=[
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1506, 1501)),
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1502, 1503)),
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1201, 1202)),
                ],
            ),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.cur_tierce_challenge.challenge_id, 5513)
        state = self.session.challenge
        self.assertEqual(state.mode, "MOC")
        groups = [node.maze_group_id for node in state.tierce_nodes]
        stages = [node.stage_id for node in state.tierce_nodes]
        self.assertEqual(len(groups), 3)
        # 节点三 = tierce 行自己的 stage（30126123，污染 boss）。
        self.assertEqual(stages[2], 30126123)
        self.assertNotEqual(stages[0], stages[2])
        self.assertEqual(rsp.scene.plane_id, state.node.plane_id)


    async def test_tierce_advance_by_group_swap(self) -> None:
        """星启换节点：同场景换组（``inplace``），不重建 SceneInfo。

        4.7.0 真机 21:35：重建 ``SceneInfo`` 进节点二 → 客户端"场景不对" + 战斗黑屏
        （和 4.6.54 下半同一症状），所以节点之间走原地换组。
        """
        self.rt.cfg.endgame.advance = "inplace"
        await challenge_handlers.on_start_challenge_tierce(
            self.session,
            pb.StartChallengeTierceCsReq(
                challenge_id=5513,
                stage_index=0,
                lineup_tierce_list=[
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1506, 1501)),
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1502, 1503)),
                    pb.LineupTierce(tierce_avatar_id_list=self._lineup(1201, 1202)),
                ],
            ),
        )
        battle = await scene_handlers.on_cast_skill(
            self.session, pb.SceneCastSkillCsReq(cast_entity_id=1)
        )
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=battle.battle_info.battle_id,
                stage_id=battle.battle_info.stage_id,
                end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
            ),
        )
        names = [type(message).__name__ for message in self.sent]
        self.assertIn("SceneGroupRefreshScNotify", names)
        self.assertNotIn("EnterSceneByServerScNotify", names)
        self.assertEqual(self.session.challenge.stage_index, 1)
        groups = [
            entry.group_id
            for message in self.sent
            if type(message).__name__ == "SceneGroupRefreshScNotify"
            for entry in message.group_refresh_list
        ]
        # 装新组（node2 的 10）；默认不卸 node1 的 9
        self.assertIn(10, groups)
        self.assertNotIn(9, groups)


if __name__ == "__main__":
    unittest.main()
