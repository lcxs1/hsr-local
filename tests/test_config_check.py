from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.config_check import validate
from hsr.game.services.battle import BattleData, StageInfo
from hsr.game.services.progress import ProgressBook
from hsr.game.services.roster import Roster
from hsr.game.services.save import SaveStore
from hsr.game.services.tutorials import TutorialBook


class ConfigCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = AppConfig()
        self.cfg.world.floor_id = 20313001
        self.cfg.world.entry_id = 2031301
        self.roster = Roster.fallback(self.cfg.player)
        self.save = SaveStore.load(Path(self.tmp.name) / "player.json")
        self.tutorials = TutorialBook()
        self.progress = ProgressBook()

    def tearDown(self):
        self.tmp.cleanup()

    def _rt(self, *, battle: BattleData | None = None):
        return SimpleNamespace(
            cfg=self.cfg,
            roster=self.roster,
            save=self.save,
            battle=battle or BattleData(),
            tutorials=self.tutorials,
            progress=self.progress,
            profile=None,
        )

    def test_missing_battle_stage_is_error(self):
        report = validate(self._rt())
        self.assertFalse(report.ok)
        self.assertTrue(any("default battle stage" in e for e in report.errors))

    def test_valid_minimal_config(self):
        battle = BattleData(stage_id=1052106, fallback_waves=[])
        battle.stages = {1052106: StageInfo(level=1, waves=[[1]])}
        report = validate(self._rt(battle=battle))
        self.assertTrue(report.ok, report.errors)

    def test_calyx_must_have_battle_mapping(self):
        self.cfg.world.calyx_cocoon_id = 1014
        battle = BattleData(stage_id=1052106, fallback_waves=[])
        battle.stages = {1052106: StageInfo(level=1, waves=[[1]])}
        report = validate(self._rt(battle=battle))
        self.assertFalse(report.ok)
        self.assertTrue(any("calyx cocoon" in e for e in report.errors))

        battle.cocoon_stage_map = {1014: 1052106}
        report = validate(self._rt(battle=battle))
        self.assertTrue(report.ok, report.errors)

    def test_calyx_mapping_to_missing_stage_is_error(self):
        self.cfg.world.calyx_cocoon_id = 1014
        battle = BattleData(stage_id=1052106, fallback_waves=[])
        battle.stages = {1052106: StageInfo(level=1, waves=[[1]])}
        battle.cocoon_stage_map = {1014: 999999}
        report = validate(self._rt(battle=battle))
        self.assertFalse(report.ok)
        self.assertTrue(any("missing stage" in e for e in report.errors))

    def test_bad_default_lineup_is_error(self):
        self.cfg.player.default_lineup = [999999]
        report = validate(self._rt())
        self.assertFalse(report.ok)
        self.assertTrue(any("unknown avatars" in e for e in report.errors))

    def test_saved_lineup_is_warned(self):
        self.save.player.lineup = [999999]
        report = validate(self._rt())
        self.assertTrue(any("saved lineup" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
