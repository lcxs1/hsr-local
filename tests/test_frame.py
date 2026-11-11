from __future__ import annotations

import unittest

from hsr.net import frame


class FrameTest(unittest.TestCase):
    def test_roundtrip(self):
        raw = frame.encode(374, b"\x08\x01", head=b"\x08\x01")
        pkt = frame.parse(raw)
        self.assertEqual(pkt.cmd, 374)
        self.assertEqual(pkt.body, b"\x08\x01")
        self.assertEqual(pkt.head, b"\x08\x01")

    def test_empty(self):
        raw = frame.encode(5)
        self.assertEqual(len(raw), 16)
        pkt = frame.parse(raw)
        self.assertEqual(pkt.cmd, 5)
        self.assertEqual(pkt.body, b"")

    def test_bad_magic(self):
        raw = bytearray(frame.encode(1, b"x"))
        raw[0] ^= 0xFF
        with self.assertRaises(frame.FrameError):
            frame.parse(bytes(raw))

    def test_length_mismatch(self):
        raw = frame.encode(1, b"abc")[:-1]
        with self.assertRaises(frame.FrameError):
            frame.parse(raw)

    def test_incremental_reader(self):
        reader = frame.FrameReader()
        raw = frame.encode(7, b"hello")
        self.assertEqual(reader.feed(raw[:5]), [])
        out = reader.feed(raw[5:])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].body, b"hello")


if __name__ == "__main__":
    unittest.main()
