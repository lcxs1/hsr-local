from __future__ import annotations

import unittest

from hsr.net import handshake


class HandshakeTest(unittest.TestCase):
    def test_request_detection(self):
        req = b"\x00\x00\x00\xff" + b"\x00" * 8 + b"\x00" * 4 + b"\xff\xff\xff\xff"
        self.assertTrue(handshake.looks_like_request(req))
        hs = handshake.parse(req)
        self.assertIsNotNone(hs)
        self.assertEqual(hs.code, handshake.CONNECT)

    def test_blob_modes(self):
        conv, token, blob, kconv = handshake.new_session_ids("u32_le_token")
        self.assertEqual(len(blob), 8)
        # handshake fields are big-endian
        self.assertEqual(int.from_bytes(blob[0:4], "big"), conv)
        self.assertEqual(int.from_bytes(blob[4:8], "big"), token)
        # KCP prefix is the same two u32s in little-endian
        kcp_bytes = kconv.to_bytes(8, "big")
        self.assertEqual(kcp_bytes[0:4], conv.to_bytes(4, "little"))
        self.assertEqual(kcp_bytes[4:8], token.to_bytes(4, "little"))

        conv64, _token, blob64, kconv64 = handshake.new_session_ids("u64_be")
        self.assertEqual(int.from_bytes(blob64, "big"), conv64)
        self.assertEqual(kconv64, conv64)

    def test_response(self):
        blob = bytes.fromhex("1122334455667788")
        rsp = handshake.build_response(blob)
        self.assertEqual(len(rsp), 20)
        self.assertEqual(rsp[:4], b"\x00\x00\x01\x45")
        self.assertEqual(rsp[4:12], blob)
        self.assertEqual(rsp[-4:], b"\x14\x51\x45\x45")


if __name__ == "__main__":
    unittest.main()
