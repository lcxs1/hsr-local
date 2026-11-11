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
        # extra lineups are keyed at type + 10 (DanhengServer LineupManager)
        self.assertEqual(lineup.index, 11)
        self.assertEqual(second.index, 13)
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
        # 默认 advance=scene（LunarCore 做法）：重建下半场景并让客户端重进。
        self.assertEqual(
            kinds[-3:],
            [
                "ChallengeLineupNotify",
                "SyncLineupNotify",
                "EnterSceneByServerScNotify",
            ],
        )
        node2 = self.rt.endgame.challenge(1101).node2
        self.assertEqual(self.session.challenge.stage, 2)
        self.assertTrue(self.session.challenge.second)
        self.assertEqual(self.session.challenge.stage_id, node2.stage_id)
        self.assertEqual(self.session.challenge.node.entrance_id, node2.entrance_id)
        scene = self.sent[-1].scene
        self.assertEqual(scene.entry_id, node2.entrance_id)
        group_ids = [group.group_id for group in scene.entity_group_list]
        self.assertIn(node2.maze_group_id, group_ids)
        lineup_notify = self.sent[-3]
        self.assertEqual(
            lineup_notify.extra_lineup_type,
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
        )

    async def test_advance_refresh_mode_sends_group_refresh(self) -> None:
        """advance=refresh：同场景换组（DanhengServer 做法，保留用于 A/B）。"""
        self.rt.cfg.endgame.advance = "refresh"
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
        self.assertEqual(
            kinds[-3:],
            ["SceneGroupRefreshScNotify", "ChallengeLineupNotify", "SyncLineupNotify"],
        )
        node1 = self.rt.endgame.challenge(1101).node1
        node2 = self.rt.endgame.challenge(1101).node2
        refresh = self.sent[-3]
        by_group = {entry.group_id: entry for entry in refresh.group_refresh_list}
        self.assertIn(node1.maze_group_id, by_group)
        self.assertIn(node2.maze_group_id, by_group)
        # 4.6.54 的 _RefreshGroup_Legacy 在刷角色组时 NRE：这里只换迷宫组
        self.assertNotIn(self.rt.cfg.world.avatar_group_id, by_group)
        added = [
            item.add_entity
            for item in by_group[node2.maze_group_id].refresh_entity
            if item.HasField("add_entity")
        ]
        self.assertTrue(added)
        self.assertEqual(added[0].npc_monster.event_id, node2.stage_id)
        self.assertGreaterEqual(added[0].entity_id, 1010)

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



if __name__ == "__main__":
    unittest.main()
