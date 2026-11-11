from __future__ import annotations

import time
import unittest

from hsr.net.kcp import KCP


def ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class KcpTest(unittest.TestCase):
    def test_two_endpoints(self):
        blob = bytes.fromhex("1122334455667788")
        conv = int.from_bytes(blob, "big")
        a, b = KCP(conv), KCP(conv)
        qa: list[bytes] = []
        qb: list[bytes] = []
        a.output = qa.append
        b.output = qb.append
        for endpoint in (a, b):
            endpoint.setmtu(1400)
            endpoint.wndsize(256, 256)
            endpoint.nodelay(1, 10, 2, 1)

        a.send(b"hello")
        a.flush()
        a.update(ms())
        self.assertTrue(qa)

        for datagram in qa:
            b.input(datagram)
        b.flush()
        b.update(ms())
        self.assertEqual(b.recv(), b"hello")

        # ACKs must be accepted by the sender
        for datagram in qb:
            a.input(datagram)
        a.update(ms())
        self.assertEqual(a.state, 0)

    def test_multiple_messages(self):
        blob = bytes.fromhex("0011223344556677")
        conv = int.from_bytes(blob, "big")
        a, b = KCP(conv), KCP(conv)
        qa: list[bytes] = []
        qb: list[bytes] = []
        a.output = qa.append
        b.output = qb.append
        for endpoint in (a, b):
            endpoint.setmtu(1400)
            endpoint.wndsize(256, 256)
            endpoint.nodelay(1, 10, 2, 1)

        payloads = [b"one", b"two", b"three" * 100]
        for payload in payloads:
            a.send(payload)
        a.flush()
        a.update(ms())
        for datagram in qa:
            b.input(datagram)
        b.flush()
        b.update(ms())
        got = []
        while True:
            data = b.recv()
            if not data:
                break
            got.append(data)
        self.assertEqual(got, payloads)


if __name__ == "__main__":
    unittest.main()
