from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.game.handlers import avatar as avatar_handlers
from hsr.game.services import snapshot
from hsr.game.services.lineup import LineupState
from hsr.game.services.roster import Roster
from hsr.game.services.save import SaveStore
from hsr.game.services.skins import SkinBook
from hsr.protocol.gen import StarRail_pb2 as pb


class _Session:
    def __init__(self, cfg, roster, save, skins):
        self.runtime = SimpleNamespace(
            cfg=cfg,
            roster=roster,
            save=save,
            skins=skins,
            logger=logging.getLogger("hsr.test"),
        )
        self.lineup = LineupState(slots=[1001, 1506, 1501, 1502], leader=0)
        self.tb_multi_path = 8002
        self.march_multi_path = 1001
        self.sent: list[object] = []

    async def send(self, msg, name: str | None = None) -> None:
        self.sent.append(msg)


class SkinTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = AppConfig.load()
        self.cfg.player.gender = 2
        self.roster = Roster.load(
            self.cfg.paths.resolve(self.cfg.paths.freesr),
            self.cfg.player,
            logging.getLogger("hsr.test"),
        )
        self.save = SaveStore.load(Path(self.tmp.name) / "player.json")
        self.skins = SkinBook.load(self.cfg.paths.resolve(self.cfg.paths.skins))
        self.session = _Session(self.cfg, self.roster, self.save, self.skins)

    def tearDown(self):
        self.tmp.cleanup()

    def test_skins_loaded(self):
        self.assertEqual(len(self.skins.all_ids()), 8)
        self.assertTrue(self.skins.valid(1001, 1100101))
        self.assertFalse(self.skins.valid(1001, 1130301))

    async def test_dress_and_take_off(self):
        dressed = await avatar_handlers.on_dress_avatar_skin(
            self.session,
            pb.DressAvatarSkinCsReq(avatar_id=1001, skin_id=1100101),
        )
        self.assertEqual(dressed.retcode, 0)
        self.assertEqual(self.save.player.skins[1001], 1100101)
        self.assertIsInstance(self.session.sent[-1], pb.PlayerSyncScNotify)
        sync = snapshot.avatar_sync(self.session)
        path = next(p for p in sync.avatar_path_data_info_list if p.avatar_id == 1001)
        self.assertEqual(path.dressed_skin_id, 1100101)

        removed = await avatar_handlers.on_take_off_avatar_skin(
            self.session, pb.TakeOffAvatarSkinCsReq(avatar_id=1001)
        )
        self.assertEqual(removed.retcode, 0)
        self.assertNotIn(1001, self.save.player.skins)
        sync = snapshot.avatar_sync(self.session)
        path = next(p for p in sync.avatar_path_data_info_list if p.avatar_id == 1001)
        self.assertEqual(path.dressed_skin_id, 0)

    async def test_wrong_avatar_skin_is_rejected(self):
        rsp = await avatar_handlers.on_dress_avatar_skin(
            self.session,
            pb.DressAvatarSkinCsReq(avatar_id=1001, skin_id=1130301),
        )
        self.assertEqual(rsp.retcode, 1)
        self.assertNotIn(1001, self.save.player.skins)

    async def test_avatar_data_exposes_owned_skins(self):
        rsp = await avatar_handlers.on_get_avatar_data(
            self.session, pb.GetAvatarDataCsReq()
        )
        self.assertEqual(len(rsp.skin_list), 8)
        self.assertIn(1100101, rsp.skin_list)


if __name__ == "__main__":
    unittest.main()
