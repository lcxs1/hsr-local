"""异相仲裁 (ChallengePeak) chain: data, start, battle, settle, confirm, leave."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hsr.game.handlers import battle as battle_handlers
from hsr.game.handlers import peak as peak_handlers
from hsr.game.services import challenge as challenge_service
from hsr.game.services import peak as peak_service
from hsr.game.services.save import PlayerSave
from hsr.protocol.gen import StarRail_pb2 as pb
from tests.support import build_session


class PeakHandlersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.session = build_session(Path(self.tmp.name))
        self.rt = self.session.runtime
        self.sent: list = []

        async def record(msg, name=None) -> None:  # noqa: ANN001
            self.sent.append(msg)

        self.session.send = record  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    def _names(self) -> list[str]:
        return [msg.DESCRIPTOR.name for msg in self.sent]

    async def test_data_lists_every_group(self) -> None:
        rsp = await peak_handlers.on_get_peak_data(
            self.session, pb.GetChallengePeakDataCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(len(rsp.challenge_peak_groups), 11)
        self.assertEqual(rsp.current_peak_group_id, 1)
        group = rsp.challenge_peak_groups[0]
        self.assertEqual(group.peak_group_id, 1)
        self.assertEqual([p.peak_id for p in group.peaks], [101, 102, 103, 104])
        self.assertEqual(group.count_of_peaks, 3)
        self.assertEqual(group.obtained_stars, 0)
        self.assertEqual(group.taken_star_rewards, [])
        # BOSS 的默认祝福取 clients 表里第一个 buff
        self.assertEqual(group.peak_boss.easy_mode.buff_id, 3033006)
        self.assertFalse(group.peak_boss.hard_mode_has_passed)

    async def test_unknown_peak_is_rejected(self) -> None:
        rsp = await peak_handlers.on_start_peak(
            self.session, pb.StartChallengePeakCsReq(peak_id=999999)
        )
        self.assertEqual(rsp.retcode, 1)
        self.assertIsNone(self.session.challenge)

    async def test_start_enters_scene_by_notify(self) -> None:
        rsp = await peak_handlers.on_start_peak(
            self.session,
            pb.StartChallengePeakCsReq(peak_id=101, peak_avatar_id_list=[1506, 1501, 1502, 1503]),
        )
        self.assertEqual(rsp.retcode, 0)
        state = self.session.challenge
        self.assertIsNotNone(state)
        self.assertEqual(state.mode, peak_service.PEAK)
        self.assertEqual(state.peak_id, 101)
        self.assertEqual(state.peak_group_id, 1)
        self.assertFalse(state.peak_boss)
        self.assertEqual(len(state.phases), 1)
        self.assertEqual(state.stage_id, 30501011)
        self.assertEqual(state.current.team, [1506, 1501, 1502, 1503])
        # StartChallengePeakScRsp 没有 scene：场景必须走 EnterSceneByServerScNotify。
        self.assertIn("EnterSceneByServerScNotify", self._names())

    async def test_battle_and_settle_record_history(self) -> None:
        await peak_handlers.on_start_peak(
            self.session, pb.StartChallengePeakCsReq(peak_id=101)
        )
        info = challenge_service.battle_info(self.session)
        self.assertEqual(info.stage_id, 30501011)
        self.assertTrue(info.monster_wave_list)

        req = pb.PVEBattleResultCsReq(
            battle_id=info.battle_id,
            stage_id=info.stage_id,
            end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
        )
        req.stt.round_cnt = 3
        self.sent.clear()
        rsp = await battle_handlers.on_pve_result(self.session, req)
        self.assertIsNone(rsp, "peak settlement is not a plain PVEBattleResultScRsp")
        names = self._names()
        self.assertIn("PVEBattleResultScRsp", names)
        self.assertIn("ChallengePeakSettleScNotify", names)
        self.assertIn("ChallengePeakGroupDataUpdateScNotify", names)

        record = self.rt.save.peak_record(101)
        self.assertTrue(record.get("passed"))
        self.assertEqual(record.get("cycles"), 3)
        self.assertTrue(self.session.peak_settled)

        settle = next(m for m in self.sent if m.DESCRIPTOR.name == "ChallengePeakSettleScNotify")
        self.assertTrue(settle.is_win)
        self.assertEqual(settle.peak_id, 101)
        self.assertEqual(settle.cycles_used, 3)
        self.assertTrue(settle.is_first_pass)

        after = await peak_handlers.on_get_peak_data(
            self.session, pb.GetChallengePeakDataCsReq()
        )
        group = after.challenge_peak_groups[0]
        self.assertEqual([p.has_passed for p in group.peaks], [True, False, False, False])
        self.assertEqual(group.obtained_stars, 1)

    async def test_confirm_clears_attempt(self) -> None:
        await peak_handlers.on_start_peak(
            self.session, pb.StartChallengePeakCsReq(peak_id=101)
        )
        rsp = await peak_handlers.on_confirm_peak_settle(
            self.session, pb.ConfirmChallengePeakSettleCsReq(peak_id=101)
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.peak_id, 101)
        self.assertIsNone(self.session.challenge)

    async def test_restart_repushes_scene_with_new_battle(self) -> None:
        await peak_handlers.on_start_peak(
            self.session, pb.StartChallengePeakCsReq(peak_id=102)
        )
        first = challenge_service.battle_info(self.session)
        self.sent.clear()
        rsp = await peak_handlers.on_restart_peak(
            self.session, pb.ReStartChallengePeakCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertIn("EnterSceneByServerScNotify", self._names())
        self.assertEqual(self.session.challenge.battle_ids, set())
        second = challenge_service.battle_info(self.session)
        self.assertNotEqual(first.battle_id, second.battle_id)

    async def test_leave_returns_to_previous_scene(self) -> None:
        await peak_handlers.on_start_peak(
            self.session, pb.StartChallengePeakCsReq(peak_id=101)
        )
        self.sent.clear()
        rsp = await peak_handlers.on_leave_peak(
            self.session, pb.LeaveChallengePeakCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertIsNone(self.session.challenge)
        names = self._names()
        self.assertIn("QuitBattleScNotify", names)
        self.assertIn("EnterSceneByServerScNotify", names)

    async def test_hard_mode_and_boss_clear(self) -> None:
        rsp = await peak_handlers.on_set_hard_mode(
            self.session, pb.SetChallengePeakBossHardModeCsReq(peak_group_id=1, is_hard_mode=True)
        )
        self.assertTrue(rsp.is_hard_mode)
        self.assertTrue(self.rt.save.peak_group_record(1).get("hard"))

        start = await peak_handlers.on_start_peak(
            self.session, pb.StartChallengePeakCsReq(peak_id=104)
        )
        self.assertEqual(start.retcode, 0)
        self.assertTrue(self.session.challenge.peak_boss)
        info = challenge_service.battle_info(self.session)
        req = pb.PVEBattleResultCsReq(
            battle_id=info.battle_id,
            stage_id=info.stage_id,
            end_status=pb.BattleEndStatus.Value("BATTLE_END_WIN"),
        )
        self.sent.clear()
        await battle_handlers.on_pve_result(self.session, req)
        self.assertTrue(self.rt.save.peak_group_record(1).get("hard_passed"))
        settle = next(m for m in self.sent if m.DESCRIPTOR.name == "ChallengePeakSettleScNotify")
        self.assertTrue(settle.hard_mode_has_passed)

        data = await peak_handlers.on_get_peak_data(self.session, pb.GetChallengePeakDataCsReq())
        boss = data.challenge_peak_groups[0].peak_boss
        self.assertTrue(boss.hard_mode_has_passed)
        self.assertTrue(boss.hard_mode.has_passed)

    async def test_reward_and_lineup_are_acknowledged(self) -> None:
        rsp = await peak_handlers.on_take_peak_reward(
            self.session,
            pb.TakeChallengePeakRewardCsReq(peak_group_id=1, normal_reward_id_list=[1, 4]),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(self.rt.save.peak_group_record(1).get("taken"), [1, 4])
        self.assertIn("ChallengePeakGroupDataUpdateScNotify", self._names())

        lineup = await peak_handlers.on_set_mob_lineup(
            self.session,
            pb.SetChallengePeakMobLineupAvatarCsReq(
                peak_group_id=1,
                lineup_list=[pb.ChallengePeakLineup(peak_id=101, peak_avatar_id_list=[1506, 1501])],
            ),
        )
        self.assertEqual(lineup.retcode, 0)

    async def test_current_peak_reflects_attempt(self) -> None:
        empty = await peak_handlers.on_get_cur_peak(
            self.session, pb.GetCurChallengePeakCsReq()
        )
        self.assertEqual(empty.retcode, 0)
        self.assertEqual(empty.peak_id, 0)

        await peak_handlers.on_start_peak(
            self.session,
            pb.StartChallengePeakCsReq(peak_id=101, boss_buff_id=3033007),
        )
        cur = await peak_handlers.on_get_cur_peak(
            self.session, pb.GetCurChallengePeakCsReq()
        )
        self.assertEqual(cur.peak_id, 101)
        self.assertEqual(cur.boss_buff_id, 3033007)
        self.assertFalse(cur.has_passed)


class PeakSaveTest(unittest.TestCase):
    def test_roundtrip_keeps_peak_history(self) -> None:
        raw = {
            "schema": 2,
            "peaks": {"101": {"passed": 1, "cycles": 5, "stars": 1, "targets": [3001]}},
            "peak_groups": {"1": {"hard": 1, "hard_passed": 1, "taken": [1, 4]}},
        }
        save = PlayerSave.from_dict(raw)
        self.assertEqual(save.peaks[101]["cycles"], 5)
        self.assertEqual(save.peak_groups[1]["taken"], [1, 4])
        self.assertEqual(PlayerSave.from_dict(save.to_dict()).peaks, save.peaks)

    def test_old_save_loads_without_peak_fields(self) -> None:
        save = PlayerSave.from_dict({"schema": 1, "lineup": [8001]})
        self.assertEqual(save.peaks, {})
        self.assertEqual(save.peak_groups, {})


if __name__ == "__main__":
    unittest.main()
