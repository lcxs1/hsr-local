from __future__ import annotations

import asyncio
import logging
import unittest

from hsr.config import AppConfig, PlayerSettings
from hsr.game.handlers import lineup as lineup_handlers
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.game.services.roster import Roster
from hsr.protocol.gen import StarRail_pb2 as pb

log = logging.getLogger("hsr.test")


class RosterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = AppConfig.load()
        cfg.player.gender = 2
        cls.roster = Roster.load(cfg.paths.resolve(cfg.paths.freesr), cfg.player, log)
        cls.cfg = cfg

    def test_loaded(self):
        self.assertGreater(len(self.roster.avatars), 0)
        self.assertGreater(len(self.roster.forms), 0)
        self.assertIsNone(self.roster.load_error)

    def test_repair_lineup(self):
        repaired = self.roster.repair_lineup([999999, 1506, 1506, 1501], size=4)
        self.assertEqual(repaired[:2], [1506, 1501])
        self.assertEqual(len(repaired), 4)
        self.assertEqual(len(set(repaired)), 4)
        self.assertTrue(all(self.roster.has_base(a) for a in repaired))
        self.assertTrue(self.roster.has_base(8006))  # Trailblazer form -> base 8001

    def test_avatar_sync(self):
        sync = self.roster.build_avatar_sync()
        self.assertGreater(len(sync.avatar_list), 0)
        self.assertGreater(len(sync.avatar_path_data_info_list), 0)
        bases = {a.base_avatar_id for a in sync.avatar_list}
        self.assertIn(1001, bases)
        self.assertIn(8001, bases)
        for avatar in sync.avatar_list:
            self.assertGreater(avatar.level, 0)

    def test_multi_path_form_is_used_in_lineup(self):
        self.assertTrue(self.roster.has_form(8004))
        # config player.gender = 2 (female), so the base 8001 resolves to 8002
        self.assertEqual(self.roster.lineup_id(8001), 8002)
        self.assertEqual(self.roster.lineup_id(8001, tb_path=8003), 8004)
        self.assertEqual(self.roster.lineup_id(1001, march_path=1224), 1224)
        sync = self.roster.build_avatar_sync(tb_path=8003)
        avatar = next(a for a in sync.avatar_list if a.base_avatar_id == 8001)
        self.assertEqual(avatar.cur_multi_path_avatar_type, 8004)

    def test_gender_normalizes_trailblazer_path(self):
        male = Roster(self.roster.data, PlayerSettings(gender=1, tb_multi_path=8002))
        female = Roster(self.roster.data, PlayerSettings(gender=2, tb_multi_path=8001))
        self.assertEqual(male.tb_path(), 8001)
        self.assertEqual(female.tb_path(), 8002)
        self.assertEqual(male.gender_for(), 1)
        self.assertEqual(female.gender_for(), 2)

    def test_bag(self):
        bag = self.roster.build_bag()
        # Counts come from the file, not a constant: freesr-data.json is replaced
        # by the SR Tools site and hot-reloaded at runtime.
        self.assertEqual(len(bag.relic_list), len(self.roster.data.relics))
        self.assertEqual(len(bag.equipment_list), len(self.roster.data.lightcones))
        for relic in bag.relic_list[:5]:
            self.assertGreater(relic.unique_id, 0)
            self.assertGreater(relic.tid, 0)

    def _session(self, lineup, *, tb_multi_path=None, march_multi_path=None):
        class Session:
            pass

        session = Session()
        session.runtime = type("R", (), {"cfg": self.cfg, "roster": self.roster})()
        session.lineup = lineup_service.LineupState(slots=list(lineup))
        session.scene = scene_service.SceneState.from_config(self.cfg.world)
        session.tb_multi_path = tb_multi_path
        session.march_multi_path = march_multi_path
        return session

    def test_lineup_and_scene(self):
        session = self._session(self.roster.default_lineup())
        lineup = lineup_service.build_lineup(session)
        self.assertEqual(len(lineup.avatar_list), 4)
        scene = scene_service.build_scene(session)
        self.assertEqual(scene.plane_id, self.cfg.world.plane_id)
        self.assertEqual(scene.floor_id, self.cfg.world.floor_id)
        self.assertEqual(scene.entry_id, self.cfg.world.entry_id)
        self.assertEqual(len(scene.entity_group_list), 2)
        for avatar in lineup.avatar_list:
            self.assertEqual(avatar.hp, lineup_service.FULL_HP)

    def test_lineup_avatar_data_covers_owned_and_lineup_forms(self):
        # female config: base 8001 resolves to 8002, so the lineup needs 8002.
        session = self._session(
            [8001, 1001, 1506, 1503], march_multi_path=1224
        )
        data = lineup_service.build_avatar_data(session)
        ids = [entry.id for entry in data]
        self.assertEqual(len(ids), len(set(ids)))  # id is a map key, no dupes
        # every owned base id is present (Danheng's granularity)
        self.assertLessEqual(set(self.roster.forms), set(ids))
        # ...and so are the form ids the lineup itself uses
        self.assertIn(8002, ids)
        self.assertIn(1224, ids)
        for entry in data:
            self.assertEqual(entry.hp, lineup_service.FULL_HP)
            self.assertEqual(
                entry.avatar_type, pb.AvatarType.AvatarType_AvatarFormalType
            )

    def test_lineup_avatar_data_handler(self):
        session = self._session([1506, 1501, 1502, 1503])
        rsp = asyncio.run(
            lineup_handlers.on_get_avatar_data(session, pb.GetLineupAvatarDataCsReq())
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertTrue(rsp.avatar_data_list)
        self.assertTrue(set(self.roster.forms) <= {e.id for e in rsp.avatar_data_list})


if __name__ == "__main__":
    unittest.main()
