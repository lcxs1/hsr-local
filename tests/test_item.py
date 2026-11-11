from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.game.handlers import item as item_handlers
from hsr.game.services.roster import Roster
from hsr.protocol.gen import StarRail_pb2 as pb


class ItemTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cfg = AppConfig.load()
        cls.roster = Roster.load(
            cfg.paths.resolve(cfg.paths.freesr),
            cfg.player,
            logging.getLogger("hsr.test"),
        )
        cls.session = SimpleNamespace(runtime=SimpleNamespace(roster=cls.roster))

    async def test_get_bag_lists_freesr_equipment(self):
        rsp = await item_handlers.on_get_bag(self.session, pb.GetBagCsReq())
        self.assertEqual(rsp.retcode, 0)
        self.assertGreater(len(rsp.relic_list), 0)
        self.assertGreater(len(rsp.equipment_list), 0)

    async def test_mark_item_list_is_empty(self):
        rsp = await item_handlers.on_get_mark_item_list(
            self.session, pb.GetMarkItemListCsReq()
        )
        self.assertEqual(rsp.retcode, 0)
        # field 14 (obfuscated name in the 4.6.54 descriptor) is the marked list
        self.assertEqual(len(rsp.PEPKCBILIFO), 0)


if __name__ == "__main__":
    unittest.main()
