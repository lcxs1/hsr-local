from __future__ import annotations

import logging
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import ROOT, AppConfig
from hsr.game.handlers import battle as battle_handlers
from hsr.game.services.battle import BattleData, StageInfo
from hsr.game.services.lineup import LineupState
from hsr.game.services.roster import Roster
from hsr.protocol.gen import StarRail_pb2 as pb


def _battle_dir(tmp: str) -> tuple[Path, Path]:
    base = Path(tmp)
    battle = base / "battle.json"
    battle.write_text(
        '{"battle_id":1,"stage_id":1052106,"cycle_count":30,"world_level":5,'
        '"monster_waves":[[{"monster_id":5035011,"level":82,"amount":2}]],'
        '"cocoon_stage_map":{"1014":1052106}}',
        encoding="utf-8",
    )
    stages = base / "stages.json"
    stages.write_text(
        '{"stages":{"1052100":{"level":26,"waves":[[5035011]]},'
        '"1052106":{"level":82,"waves":[[5035011,5035012]]}}}',
        encoding="utf-8",
    )
    return battle, stages


class _Session:
    def __init__(self, cfg: AppConfig, battle: BattleData, roster: Roster):
        self.runtime = SimpleNamespace(
            cfg=cfg,
            battle=battle,
            roster=roster,
            logger=logging.getLogger("hsr.test"),
        )
        self.lineup = LineupState(slots=[1001, 1002, 8001, 1004], leader=0)
        self.tb_multi_path = 8002
        self.march_multi_path = 1001


class BattleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = AppConfig.load()
        self.tmp = tempfile.TemporaryDirectory()
        battle_path, stages_path = _battle_dir(self.tmp.name)
        self.battle = BattleData.load(battle_path, stages_path)
        self.roster = Roster.load(
            ROOT / "data" / "freesr-data.json",
            self.cfg.player,
            logging.getLogger("hsr.test"),
        )
        self.session = _Session(self.cfg, self.battle, self.roster)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_resolution(self):
        self.assertEqual(self.battle.resolve_stage(1052100), 1052100)
        self.assertEqual(self.battle.resolve_stage(1014), 1052106)
        self.assertEqual(self.battle.resolve_stage(9999), 1052106)

    def test_stage_resolution_picks_world_level_variant(self):
        # 1301..1310 are the 「生存指南」quick-start ids for the 10 stagnant
        # shadows; each maps to a 7-level family, so world_level shifts the stage.
        self.battle.cocoon_stage_map = {
            1301: 1052016, 1306: 1052066, 1310: 1052106
        }
        for level_index in range(7):
            self.battle.stages.setdefault(
                1052010 + level_index, StageInfo(level=26 + level_index * 8)
            )
            self.battle.stages.setdefault(
                1052100 + level_index, StageInfo(level=26 + level_index * 8)
            )
        self.assertEqual(self.battle.resolve_stage(1301, 5), 1052015)
        self.assertEqual(self.battle.resolve_stage(1301, 6), 1052016)
        self.assertEqual(self.battle.resolve_stage(1301, 0), 1052016)
        # out-of-range levels clamp to the highest variant
        self.assertEqual(self.battle.resolve_stage(1310, 9), 1052106)
        # unknown cocoon keeps the default stage regardless of world level
        self.assertEqual(self.battle.resolve_stage(4242, 5), self.battle.stage_id)

    def test_cocoon_names_loaded(self):
        self.battle.cocoon_names = {1310: "二相乐园", 1301: "毁灭的开端"}
        self.assertEqual(self.battle.name_of_cocoon(1310), "二相乐园")
        self.assertEqual(self.battle.name_of_cocoon(9999), "")

    def test_battle_info_shape(self):
        info = self.battle.build_battle_info(self.session, stage_id=1052106, world_level=5)
        self.assertEqual(info.battle_id, 1)
        self.assertEqual(info.stage_id, 1052106)
        self.assertEqual(info.rounds_limit, 30)
        self.assertNotEqual(info.logic_random_seed, 0)
        self.assertEqual(len(info.monster_wave_list), 1)
        self.assertEqual(
            [m.monster_id for m in info.monster_wave_list[0].monster_list],
            [5035011, 5035012],
        )
        self.assertEqual(info.monster_wave_list[0].monster_param.level, 82)

        self.assertEqual(len(info.battle_avatar_list), 4)
        self.assertEqual([a.id for a in info.battle_avatar_list], [1001, 1002, 8002, 1004])
        self.assertEqual([a.index for a in info.battle_avatar_list], [0, 1, 2, 3])
        first = info.battle_avatar_list[0]
        self.assertTrue(first.skilltree_list)
        self.assertEqual(first.avatar_type, pb.AvatarType.AvatarType_AvatarFormalType)
        self.assertTrue(first.relic_list)
        self.assertTrue(first.equipment_list)
        self.assertGreater(first.sp_bar.max_sp, 0)

    def test_fallback_waves(self):
        self.battle.stages.clear()
        info = self.battle.build_battle_info(self.session)
        self.assertEqual(len(info.monster_wave_list), 1)
        self.assertEqual(len(info.monster_wave_list[0].monster_list), 2)

    async def test_start_cocoon_handler(self):
        req = pb.StartCocoonStageCsReq(cocoon_id=1052100, wave=1, prop_entity_id=1337)
        rsp = await battle_handlers.on_start_cocoon(self.session, req)
        self.assertEqual(rsp.cocoon_id, 1052100)
        self.assertEqual(rsp.prop_entity_id, 1337)
        self.assertEqual(rsp.battle_info.stage_id, 1052100)
        self.assertTrue(rsp.battle_info.battle_avatar_list)
        self.assertEqual(rsp.battle_info.battle_id, 1)
        self.assertIsNotNone(self.session.battle_info)
        self.assertEqual(self.session.battle_info.battle_id, 1)

    async def test_second_start_gets_new_battle_id(self):
        req = pb.StartCocoonStageCsReq(cocoon_id=1052100, wave=0, prop_entity_id=1337)
        first = await battle_handlers.on_start_cocoon(self.session, req)
        second = await battle_handlers.on_start_cocoon(self.session, req)

        self.assertEqual(first.battle_info.battle_id, 1)
        self.assertEqual(second.battle_info.battle_id, 2)
        self.assertEqual(second.battle_info.stage_id, first.battle_info.stage_id)

    async def test_pve_result_handler(self):
        start = await battle_handlers.on_start_cocoon(
            self.session,
            pb.StartCocoonStageCsReq(cocoon_id=1052100, prop_entity_id=1337),
        )
        req = pb.PVEBattleResultCsReq(
            battle_id=start.battle_info.battle_id,
            stage_id=1052100,
            end_status=1,
        )
        rsp = await battle_handlers.on_pve_result(self.session, req)
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.battle_id, start.battle_info.battle_id)
        self.assertEqual(rsp.stage_id, 1052100)
        self.assertEqual(rsp.end_status, 1)
        self.assertIsNone(self.session.battle_info)

    async def test_quick_start_handler(self):
        rsp = await battle_handlers.on_quick_start_cocoon(
            self.session,
            pb.QuickStartCocoonStageCsReq(cocoon_id=1014, wave=0),
        )
        self.assertEqual(rsp.cocoon_id, 1014)
        self.assertEqual(rsp.battle_info.stage_id, 1052106)
        self.assertEqual(rsp.battle_info.battle_id, 1)
        self.assertIsNotNone(self.session.battle_info)

    async def test_all_end_statuses_clear_active_battle(self):
        for end_status in (
            pb.BattleEndStatus.BATTLE_END_WIN,
            pb.BattleEndStatus.BATTLE_END_LOSE,
            pb.BattleEndStatus.BATTLE_END_QUIT,
        ):
            start = await battle_handlers.on_start_cocoon(
                self.session,
                pb.StartCocoonStageCsReq(cocoon_id=1014, prop_entity_id=1337),
            )
            rsp = await battle_handlers.on_pve_result(
                self.session,
                pb.PVEBattleResultCsReq(
                    battle_id=start.battle_info.battle_id,
                    stage_id=1052106,
                    end_status=end_status,
                ),
            )
            self.assertEqual(rsp.end_status, end_status)
            self.assertIsNone(self.session.battle_info)

    async def test_stale_result_keeps_active_battle(self):
        start = await battle_handlers.on_start_cocoon(
            self.session,
            pb.StartCocoonStageCsReq(cocoon_id=1014, prop_entity_id=1337),
        )
        await battle_handlers.on_pve_result(
            self.session,
            pb.PVEBattleResultCsReq(
                battle_id=start.battle_info.battle_id + 99,
                stage_id=1052106,
                end_status=pb.BattleEndStatus.BATTLE_END_WIN,
            ),
        )
        self.assertIsNotNone(self.session.battle_info)
        self.assertEqual(self.session.battle_info.battle_id, start.battle_info.battle_id)

    async def test_quit_clears_active_battle(self):
        await battle_handlers.on_start_cocoon(
            self.session,
            pb.StartCocoonStageCsReq(cocoon_id=1014, prop_entity_id=1337),
        )
        await battle_handlers.on_quit_battle(self.session, pb.QuitBattleCsReq())
        self.assertIsNone(self.session.battle_info)

    async def test_quick_start_farm_element_uses_mapped_stage(self):
        self.battle.farm_element_stage_map = {102: 1052100}
        self.battle.farm_element_default_stage = 1052106
        rsp = await battle_handlers.on_start_quick_farm_element(
            self.session,
            pb.StartQuickFarmElementCsReq(
                world_level=5, PAOFHFLFFHD=102, HECCOBFBJFI=1
            ),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.PAOFHFLFFHD, 102)
        self.assertEqual(rsp.world_level, 5)
        self.assertEqual(rsp.battle_info.stage_id, 1052100)
        self.assertEqual(rsp.battle_info.battle_id, 1)
        self.assertEqual(len(rsp.battle_info.battle_avatar_list), 4)
        self.assertIsNotNone(self.session.battle_info)

    async def test_quick_start_farm_element_falls_back(self):
        self.battle.farm_element_stage_map = {}
        self.battle.farm_element_default_stage = 1052106
        rsp = await battle_handlers.on_start_quick_farm_element(
            self.session,
            pb.StartQuickFarmElementCsReq(world_level=5, PAOFHFLFFHD=999),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.battle_info.stage_id, 1052106)
        # 第二次仍然要拿到新 battle_id（否则「再来一次」会卡在结算页）
        again = await battle_handlers.on_start_quick_farm_element(
            self.session,
            pb.StartQuickFarmElementCsReq(world_level=5, PAOFHFLFFHD=999),
        )
        self.assertNotEqual(again.battle_info.battle_id, rsp.battle_info.battle_id)

    async def test_farm_stage_gacha_info(self):
        rsp = await battle_handlers.on_get_farm_stage_gacha_info(
            self.session,
            pb.GetFarmStageGachaInfoCsReq(farm_stage_gacha_id_list=[11, 22]),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual([i.gacha_id for i in rsp.farm_stage_gacha_info_list], [11, 22])
        for info in rsp.farm_stage_gacha_info_list:
            self.assertEqual(info.begin_time, 0)
            self.assertGreater(info.end_time, time.time())

    async def test_farm_element_activate_and_deactivate(self):
        active = await battle_handlers.on_active_farm_element(
            self.session,
            pb.ActiveFarmElementCsReq(entity_id=1510, world_level=5, HECCOBFBJFI=7),
        )
        self.assertEqual(active.retcode, 0)
        self.assertEqual(active.entity_id, 1510)
        self.assertEqual(active.world_level, 5)

        inactive = await battle_handlers.on_deactivate_farm_element(
            self.session, pb.DeactivateFarmElementCsReq(entity_id=1510)
        )
        self.assertEqual(inactive.entity_id, 1510)
        self.assertEqual(inactive.retcode, 0)

    async def test_farm_element_sweep_is_empty_but_ok(self):
        rsp = await battle_handlers.on_farm_element_sweep(
            self.session, pb.FarmElementSweepCsReq(world_level=5, PAOFHFLFFHD=102)
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.PAOFHFLFFHD, 102)
        self.assertEqual(len(rsp.multiple_drop_data.item_list), 0)

    async def test_get_cur_battle_info_returns_active_battle(self):
        start = await battle_handlers.on_start_cocoon(
            self.session,
            pb.StartCocoonStageCsReq(cocoon_id=1052100, prop_entity_id=1337),
        )
        current = await battle_handlers.on_get_cur_battle(
            self.session, pb.GetCurBattleInfoCsReq()
        )
        self.assertEqual(current.battle_info.battle_id, start.battle_info.battle_id)

        await battle_handlers.on_quit_battle(self.session, pb.QuitBattleCsReq())
        empty = await battle_handlers.on_get_cur_battle(
            self.session, pb.GetCurBattleInfoCsReq()
        )
        self.assertFalse(empty.HasField("battle_info"))


if __name__ == "__main__":
    unittest.main()
