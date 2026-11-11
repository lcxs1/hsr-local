from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hsr.config import AppConfig
from hsr.game.services.endgame import AS, MOC, PF, EndgameData, latest_index, mode_of

log = logging.getLogger("hsr.test")


class EndgameDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = AppConfig.load()
        cls.path = cls.cfg.paths.resolve(cls.cfg.paths.endgame)
        cls.data = EndgameData.load(cls.path, log)

    def test_loaded(self):
        self.assertIsNone(self.data.load_error)
        self.assertEqual(len(self.data.challenges), 843)
        self.assertEqual(len(self.data.challenges_for_mode(MOC)), 639)
        self.assertEqual(len(self.data.challenges_for_mode(PF)), 112)
        self.assertEqual(len(self.data.challenges_for_mode(AS)), 92)
        self.assertEqual(len(self.data.peak_stages), 44)
        self.assertEqual(len(self.data.tierce), 16)
        self.assertGreater(len(self.data.stages), 1000)
        self.assertTrue(self.data.scenes.keys() >= {30127, 30124, 30102, 30135})

    def test_mode_partition(self):
        self.assertEqual(mode_of(1), MOC)
        self.assertEqual(mode_of(19999), MOC)
        self.assertEqual(mode_of(20000), PF)
        self.assertEqual(mode_of(29999), PF)
        self.assertEqual(mode_of(30000), AS)
        self.assertEqual(mode_of(30234), AS)

    def test_pf_challenge(self):
        c = self.data.challenge(20111)
        self.assertIsNotNone(c)
        self.assertEqual(c.mode, PF)
        self.assertEqual(c.buff_id, 3031200)
        self.assertIn(2001, c.target_ids)
        node = c.node1
        self.assertEqual((node.plane_id, node.floor_id, node.entrance_id), (30127, 30127001, 3012701))
        self.assertEqual(node.maze_group_id, 9)
        self.assertEqual(node.monster_id, 2023030)
        self.assertEqual(node.stage_id, 30309011)
        stage = self.data.stage(node.stage_id)
        self.assertIsNotNone(stage)
        self.assertEqual(len(stage.waves), 3)
        group = self.data.scene_group(node.plane_id, node.maze_group_id)
        self.assertIsNotNone(group)
        self.assertEqual(len(group.monsters), 1)
        self.assertTrue(self.data.anchor(node.entrance_id))

    def test_moc_challenge(self):
        c = self.data.challenge(1101)
        self.assertIsNotNone(c)
        self.assertEqual(c.mode, MOC)
        self.assertIsNotNone(self.data.stage(c.node1.stage_id))
        self.assertIsNotNone(self.data.scene_group(c.node1.plane_id, c.node1.maze_group_id))

    def test_as_challenge(self):
        c = self.data.challenge(30011)
        self.assertIsNotNone(c)
        self.assertEqual(c.mode, AS)
        self.assertEqual(c.node1.plane_id, 30124)
        self.assertIsNotNone(self.data.stage(c.node1.stage_id))

    def test_peak(self):
        stage = self.data.peak_stage(101)
        self.assertIsNotNone(stage)
        self.assertEqual(stage.stage_id, 30501011)
        self.assertTrue(stage.tag_ids)
        self.assertIsNotNone(self.data.scene_group(stage.plane_id, stage.maze_group_id))
        group = self.data.peak_group(1)
        self.assertIsNotNone(group)
        boss = self.data.peak_boss(group.boss_level_id)
        self.assertIsNotNone(boss)
        self.assertTrue(boss.buff_ids)
        self.assertTrue(boss.hard_event_ids)

    def test_tierce(self):
        tierce = self.data.tierce_stage(5213)
        self.assertIsNotNone(tierce)
        self.assertTrue(tierce.stage_id)
        self.assertIsNotNone(self.data.stage(tierce.stage_id))
        self.assertIsNotNone(self.data.scene_group(tierce.plane_id, tierce.maze_group_id))

    def test_every_node_is_playable(self):
        """Every exported challenge node must have a stage table and scene group."""
        missing_stage = []
        missing_group = []
        for challenge in self.data.challenges.values():
            for node in (challenge.node1, challenge.node2):
                if node is None:
                    continue
                if self.data.stage(node.stage_id) is None:
                    missing_stage.append((challenge.id, node.stage_id))
                if self.data.scene_group(node.plane_id, node.maze_group_id) is None:
                    missing_group.append((challenge.id, node.plane_id, node.maze_group_id))
        self.assertEqual(missing_stage, [])
        self.assertEqual(missing_group, [])

    def test_load_missing_file_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = EndgameData.load(Path(tmp) / "nope.json", log)
        self.assertIsNotNone(data.load_error)
        self.assertEqual(data.challenges, {})


class SeasonTest(unittest.TestCase):
    """Rotation metadata from data/vendor (scripts/import_srtools.py)."""

    def _index(self, tmp: Path) -> Path:
        path = tmp / "endgame_index.json"
        path.write_text(
            json.dumps(
                {
                    "modes": {
                        "MOC": {
                            "groups": [
                                {
                                    "id": 1035,
                                    "name": "来生泅渡",
                                    "begin": "2026-09-21 04:00:00",
                                    "end": "2026-11-02 04:00:00",
                                },
                                {
                                    "id": 1036,
                                    "name": "物竞天择",
                                    "begin": "2026-11-02 04:00:00",
                                    "end": "2026-12-14 04:00:00",
                                },
                            ]
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_active_group_by_time(self):
        cfg = AppConfig.load()
        with tempfile.TemporaryDirectory() as tmp:
            data = EndgameData.load(
                cfg.paths.resolve(cfg.paths.endgame),
                log,
                seasons_path=self._index(Path(tmp)),
            )
        self.assertEqual(data.active_group(MOC, datetime(2026, 11, 11, 12, 0)), 1036)
        self.assertEqual(data.active_group(MOC, datetime(2026, 10, 1, 0, 0)), 1035)
        self.assertEqual(data.active_group(MOC, datetime(2027, 1, 1, 0, 0)), 0)
        self.assertEqual(data.active_group(PF, datetime(2026, 11, 11, 12, 0)), 0)
        self.assertEqual(data.season(1036).name, "物竞天择")

    def test_real_vendor_index(self):
        cfg = AppConfig.load()
        index = latest_index(cfg.data_dir)
        if index is None:
            self.skipTest("no data/vendor/srtools-*/endgame_index.json")
        data = EndgameData.load(cfg.paths.resolve(cfg.paths.endgame), log, seasons_path=index)
        self.assertTrue(data.seasons)
        group_id = data.active_group(MOC, datetime(2026, 11, 11, 12, 0))
        self.assertEqual(group_id, 1036)
        self.assertEqual(data.season(group_id).name, "物竞天择")


if __name__ == "__main__":
    unittest.main()
