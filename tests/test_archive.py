from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace

from hsr.config import ROOT
from hsr.game.handlers import archive
from hsr.protocol.gen import StarRail_pb2 as pb
from hsr.protocol.registry import Registry


class _Session:
    def __init__(self):
        self.runtime = SimpleNamespace(logger=logging.getLogger("hsr.test"))


class ArchiveHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = Registry(ROOT / "proto")
        self.session = _Session()

    def test_cmd_ids_match_table(self):
        self.assertEqual(self.registry.id_of("GetArchiveDataCsReq"), 2374)
        self.assertEqual(self.registry.id_of("GetArchiveDataScRsp"), 2319)
        self.assertEqual(self.registry.id_of("GetUpdatedArchiveDataCsReq"), 2331)

    async def test_get_archive_data_sets_submessage(self):
        rsp = await archive.on_get_archive_data(
            self.session, pb.GetArchiveDataCsReq()
        )
        # 关键：不是空 body。4.7.0 客户端会直接解引用 archive_data。
        self.assertTrue(rsp.HasField("archive_data"))
        self.assertEqual(rsp.retcode, 0)

    async def test_get_updated_archive_data_sets_submessage(self):
        rsp = await archive.on_get_updated_archive_data(
            self.session, pb.GetUpdatedArchiveDataCsReq()
        )
        self.assertTrue(rsp.HasField("archive_data"))
        self.assertEqual(rsp.retcode, 0)


if __name__ == "__main__":
    unittest.main()
