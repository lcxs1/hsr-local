from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.game.handlers import prefs as prefs_handlers
from hsr.game.services.prefs import PrefsStore
from hsr.protocol.gen import StarRail_pb2 as pb


class PrefsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "server_prefs.json"
        self.store = PrefsStore.load(self.path)
        self.session = SimpleNamespace(
            runtime=SimpleNamespace(prefs=self.store, logger=logging.getLogger("hsr.test"))
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        self.store.set(7, b"\x01\x02\x03")
        self.assertTrue(self.path.exists())
        again = PrefsStore.load(self.path)
        self.assertEqual(again.get(7), b"\x01\x02\x03")
        self.assertIsNone(again.get(8))

    async def test_update_then_get_all(self):
        req = pb.UpdateServerPrefsCsReq(
            server_prefs=pb.ServerPrefs(server_prefs_id=42, data=b"seen")
        )
        rsp = await prefs_handlers.on_update(self.session, req)
        self.assertEqual(rsp.server_prefs_id, 42)

        data = await prefs_handlers.on_get_all(self.session, pb.GetAllServerPrefsDataCsReq())
        self.assertEqual(len(data.server_prefs_list), 1)
        self.assertEqual(data.server_prefs_list[0].server_prefs_id, 42)
        self.assertEqual(data.server_prefs_list[0].data, b"seen")

        one = await prefs_handlers.on_get_one(
            self.session, pb.GetServerPrefsDataCsReq(server_prefs_id=42)
        )
        self.assertEqual(one.server_prefs.data, b"seen")

    def test_corrupt_file_is_ignored(self):
        self.path.write_text("{not json", encoding="utf-8")
        store = PrefsStore.load(self.path)
        self.assertEqual(store.values, {})


if __name__ == "__main__":
    unittest.main()
