"""KCP probe: act like the client without launching the game.

    python -m hsr.tools.probe                    # PlayerGetTokenCsReq
    python -m hsr.tools.probe --rpc PlayerLoginCsReq
    python -m hsr.tools.probe --rpc GetBasicInfoCsReq --json

This also doubles as a self-test of our KCP implementation (both endpoints use the
same code, so a green run means framing/handshake/KCP are at least self-consistent).
"""

from __future__ import annotations

import argparse
import select
import socket
import time

from hsr.config import ROOT
from hsr.net import frame as frame_mod
from hsr.net import handshake
from hsr.net.kcp import KCP
from hsr.protocol import codec
from hsr.protocol.registry import Registry


def now_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


def build_handshake() -> bytes:
    return b"\x00\x00\x00\xff" + b"\x00" * 8 + b"\x00" * 4 + b"\xff\xff\xff\xff"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="probe the game server over KCP")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=23301)
    ap.add_argument("--mode", default="u32_le_token", choices=["u32_le_token", "u64_be"])
    ap.add_argument("--rpc", default="PlayerGetTokenCsReq")
    ap.add_argument("--body-hex", default="")
    ap.add_argument("--json", dest="json_body", default="")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--raw", action="store_true", help="print raw response frames")
    args = ap.parse_args(argv)

    registry = Registry(ROOT / "proto")
    cls = registry.message_cls(args.rpc)
    if cls is None:
        ap.error(f"unknown rpc {args.rpc}")
    req = cls()
    if args.body_hex:
        req.ParseFromString(bytes.fromhex(args.body_hex))
    elif args.json_body:
        from google.protobuf import json_format

        json_format.Parse(args.json_body, req)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((args.host, args.port))
    sock.setblocking(False)

    sock.send(build_handshake())
    deadline = time.monotonic() + args.timeout
    blob = None
    while time.monotonic() < deadline:
        r, _, _ = select.select([sock], [], [], 0.1)
        if not r:
            continue
        data = sock.recv(4096)
        if len(data) == 20 and data[:4] == b"\x00\x00\x01\x45" and data[-4:] == b"\x14\x51\x45\x45":
            blob = data[4:12]
            break
    if blob is None:
        print("no handshake response (is the server running? firewall?)")
        return 1
    print(f"handshake ok: blob={blob.hex()}")

    kcp = KCP(handshake.kcp_conv_from_handshake(args.mode, blob))
    kcp.setmtu(1400)
    kcp.wndsize(256, 256)
    kcp.nodelay(1, 10, 2, 1)
    kcp.output = lambda buf: sock.send(buf)

    cmd = registry.id_of(args.rpc)
    kcp.send(frame_mod.encode(cmd, req.SerializeToString()))
    kcp.flush()
    kcp.update(now_ms())

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([sock], [], [], 0.05)
        if r:
            data = sock.recv(65535)
            kcp.input(data)
            kcp.flush()
        kcp.update(now_ms())
        while True:
            buf = kcp.recv()
            if not buf:
                break
            try:
                pkt = frame_mod.parse(buf)
            except frame_mod.FrameError as exc:
                print(f"bad frame: {exc}")
                continue
            name = registry.name_of(pkt.cmd)
            print(f"<- cmd={pkt.cmd} {name} body={len(pkt.body)}")
            msg = registry.decode(name, pkt.body)
            if msg is not None:
                print(codec.dump(msg))
            elif args.raw:
                print(pkt.body.hex())
            return 0
    print("timeout waiting for response")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
