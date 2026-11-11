from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.game.handlers import mission as mission_handlers
from hsr.game.services.save import PlayerSave, SaveStore
from hsr.game.services.tutorials import TutorialBook
from hsr.protocol.gen import StarRail_pb2 as pb


class SaveStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "player.json"
        self.store = SaveStore.load(self.path, interval=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        self.store.set_lineup([1001, 8001, 0, 0], 1)
        self.store.set_position(20313, (1, 2, 3))
        again = SaveStore.load(self.path)
        self.assertEqual(again.player.lineup, [1001, 8001, 0, 0])
        self.assertEqual(again.player.leader, 1)
        self.assertEqual(again.player.pos, [1, 2, 3])
        self.assertEqual(again.player.plane_id, 20313)

    def test_tutorial_dedup(self):
        self.store.add_tutorial(1001)
        self.store.add_tutorial(1001)
        self.assertEqual(self.store.player.finished_tutorials, [1001])

    def test_atomic_write_leaves_no_tmp(self):
        self.store.set_lineup([1], 0)
        self.assertFalse((self.path.parent / "player.json.tmp").exists())

    def test_corrupt_file_keeps_defaults(self):
        self.path.write_text("nonsense", encoding="utf-8")
        store = SaveStore.load(self.path)
        self.assertEqual(store.player, PlayerSave())


class TutorialBookTest(unittest.IsolatedAsyncioTestCase):
    def test_lists_are_finished(self):
        book = TutorialBook(tutorials=[1, 2], guides=[(10, 0)])
        self.assertEqual([t.status for t in book.tutorial_list()], [2, 2])
        self.assertEqual(book.guide_list()[0].id, 10)

    def test_exported_guides_use_default_type(self):
        # The client asks for group-level guides with type 0
        # (TutorialGuideDefault); type 1 makes it re-unlock every guide at login.
        path = Path(__file__).resolve().parents[1] / "data" / "tutorials.json"
        book = TutorialBook.load(path)
        self.assertEqual(len(book.guides), 864)
        self.assertEqual({gtype for _, gtype in book.guides}, {0})

    async def test_get_tutorials_sends_everything(self):
        book = TutorialBook(tutorials=[11, 12], guides=[(21, 0)])
        save = SimpleNamespace(player=PlayerSave(finished_tutorials=[99], finished_guides=[98]))
        session = SimpleNamespace(runtime=SimpleNamespace(tutorials=book, save=save))
        rsp = await mission_handlers.on_get_tutorials(session, pb.GetTutorialCsReq())
        self.assertEqual({t.id for t in rsp.tutorial_list}, {11, 12, 99})
        guides = await mission_handlers.on_get_guides(session, pb.GetTutorialGuideCsReq())
        self.assertEqual({g.id for g in guides.tutorial_guide_list}, {21, 98})
        self.assertEqual({g.type for g in guides.tutorial_guide_list}, {0})


if __name__ == "__main__":
    unittest.main()
