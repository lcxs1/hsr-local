from __future__ import annotations

import unittest

from hsr.config import ROOT
from hsr.protocol.registry import Registry


class RegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = Registry(ROOT / "proto")

    def test_known_ids(self):
        self.assertEqual(self.reg.id_of("PlayerGetTokenCsReq"), 5)
        self.assertEqual(self.reg.id_of("PlayerLoginCsReq"), 74)
        self.assertEqual(self.reg.id_of("GetAvatarDataCsReq"), 374)
        self.assertEqual(self.reg.id_of("StartCocoonStageCsReq"), 1444)

    def test_rsp_mapping(self):
        self.assertEqual(self.reg.rsp_name_of("PlayerGetTokenCsReq"), "PlayerGetTokenScRsp")
        self.assertEqual(self.reg.rsp_name_of("GetAvatarDataCsReq"), "GetAvatarDataScRsp")
        self.assertEqual(self.reg.rsp_for(5), "PlayerGetTokenScRsp")

    def test_alias_suffix(self):
        # Some command names have numeric suffixes; the proto may or may not
        # contain the suffixed message. Unknown suffixes must fall back to the base.
        self.assertIsNotNone(self.reg.message_cls("GetDataCsReq1"))
        cls = self.reg.message_cls("GetDataCsReq99")
        self.assertIsNotNone(cls)
        self.assertEqual(cls.DESCRIPTOR.name, "GetDataCsReq")

    def test_decode_encode(self):
        cls = self.reg.message_cls("PlayerGetTokenCsReq")
        msg = cls()
        raw = msg.SerializeToString()
        decoded = self.reg.decode("PlayerGetTokenCsReq", raw)
        self.assertIsNotNone(decoded)
        cmd, body = self.reg.encode(decoded)
        self.assertEqual(cmd, 5)
        self.assertEqual(body, raw)

    def test_empty_rsp(self):
        pair = self.reg.empty_rsp_bytes(5)
        self.assertIsNotNone(pair)
        self.assertEqual(pair[0], 71)
        self.assertEqual(pair[1], b"")


if __name__ == "__main__":
    unittest.main()
