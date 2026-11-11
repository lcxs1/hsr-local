"""Offline packet decoder.

Examples:

    python -m hsr.tools.decode --capture captures/tap.bin --list
    python -m hsr.tools.decode --capture captures/tap.bin --full --limit 20
    python -m hsr.tools.decode --cmd 74 --hex 0a03616263...
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from hsr.config import ROOT
from hsr.protocol import codec
from hsr.protocol.dynamic import DynamicPool
from hsr.protocol.registry import Registry

HEAD_MAGIC = b"\x9d\x74\xc7\x14"
TAIL_MAGIC = b"\xd7\xa1\x52\xc8"


def read_capture(path: Path):
    """Read tap capture format: [1B dir][4B BE len][frame]."""
    data = path.read_bytes()
    pos = 0
    index = 0
    while pos + 5 <= len(data):
        direction = chr(data[pos])
        pos += 1
        (length,) = struct.unpack_from(">I", data, pos)
        pos += 4
        raw = data[pos : pos + length]
        pos += length
        index += 1
        if len(raw) < 16 or raw[:4] != HEAD_MAGIC or raw[-4:] != TAIL_MAGIC:
            continue
        cmd, head_len, body_len = struct.unpack_from(">HHI", raw, 4)
        yield index, direction, cmd, raw[12 : 12 + head_len], raw[12 + head_len : 12 + head_len + body_len]


def decode_one(registry: Registry, pool: DynamicPool, cmd: int, body: bytes) -> str:
    name = registry.name_of(cmd)
    msg = registry.decode(name, body) if registry.message_cls(name) else None
    if msg is None:
        msg = pool.decode(name, body)
    if msg is None:
        return codec.dump_wire(body)
    return codec.dump(msg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="decode HSR protobuf packets")
    ap.add_argument("--capture", help="capture file produced by tools/tap.py")
    ap.add_argument("--cmd", type=int, help="decode a single cmd with --hex")
    ap.add_argument("--hex", help="hex body for --cmd")
    ap.add_argument("--list", action="store_true", help="only list frames")
    ap.add_argument("--full", action="store_true", help="decode every frame (implies --full)")
    ap.add_argument("--limit", type=int, default=0, help="max frames to print")
    args = ap.parse_args(argv)

    proto_dir = ROOT / "proto"
    registry = Registry(proto_dir)
    pool = DynamicPool(proto_dir / "hsr.desc")

    if args.cmd is not None:
        body = bytes.fromhex(args.hex or "")
        print(f"cmd={args.cmd} {registry.name_of(args.cmd)} body={len(body)}")
        print(decode_one(registry, pool, args.cmd, body))
        return 0

    if not args.capture:
        ap.error("need --capture or --cmd")

    path = Path(args.capture)
    if not path.is_absolute():
        path = (ROOT / path) if not path.exists() else path

    count = 0
    for index, direction, cmd, _head, body in read_capture(path):
        count += 1
        if args.limit and count > args.limit:
            break
        arrow = "C->S" if direction == "C" else "S->C"
        print(f"#{index:04d} {arrow} cmd={cmd:<6} {registry.name_of(cmd):<42} body={len(body)}")
        if args.full:
            print(decode_one(registry, pool, cmd, body))
            print()
    print(f"\n{count} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
