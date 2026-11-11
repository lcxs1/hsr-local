"""Game frame codec.

Wire layout (big-endian):

    4B  head magic  9D 74 C7 14
    2B  cmd         uint16
    2B  head_len    uint16
    4B  body_len    uint32
    nB  head        (protobuf field 1 varint sequence number, client -> server)
    mB  body        (protobuf message)
    4B  tail magic  D7 A1 52 C8
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEAD_MAGIC = b"\x9d\x74\xc7\x14"
TAIL_MAGIC = b"\xd7\xa1\x52\xc8"
HEADER_SIZE = 12
MAX_BODY = 8 * 1024 * 1024
MAX_HEAD = 64 * 1024


class FrameError(ValueError):
    pass


@dataclass(slots=True)
class Frame:
    cmd: int
    head: bytes = b""
    body: bytes = b""

    def encode(self) -> bytes:
        return encode(self.cmd, self.body, self.head)

    def __len__(self) -> int:
        return HEADER_SIZE + len(self.head) + len(self.body) + 4


def encode(cmd: int, body: bytes = b"", head: bytes = b"") -> bytes:
    if not 0 <= cmd <= 0xFFFF:
        raise FrameError(f"cmd out of range: {cmd}")
    if len(head) > 0xFFFF:
        raise FrameError(f"head too long: {len(head)}")
    total = HEADER_SIZE + len(head) + len(body) + 4
    buf = bytearray(total)
    view = memoryview(buf)
    view[0:4] = HEAD_MAGIC
    struct.pack_into(">HHI", view, 4, cmd, len(head), len(body))
    offset = HEADER_SIZE
    if head:
        view[offset : offset + len(head)] = head
        offset += len(head)
    if body:
        view[offset : offset + len(body)] = body
        offset += len(body)
    view[offset : offset + 4] = TAIL_MAGIC
    return bytes(buf)


def parse(buf: bytes | bytearray | memoryview) -> Frame:
    if len(buf) < HEADER_SIZE + 4:
        raise FrameError(f"frame too short: {len(buf)}")
    if bytes(buf[:4]) != HEAD_MAGIC:
        raise FrameError(f"bad head magic: {bytes(buf[:4]).hex()}")
    cmd, head_len, body_len = struct.unpack_from(">HHI", buf, 4)
    expected = HEADER_SIZE + head_len + body_len + 4
    if len(buf) != expected:
        raise FrameError(f"frame length mismatch: header says {expected}, got {len(buf)}")
    if head_len > MAX_HEAD:
        raise FrameError(f"head too long: {head_len}")
    if body_len > MAX_BODY:
        raise FrameError(f"body too long: {body_len}")
    if bytes(buf[-4:]) != TAIL_MAGIC:
        raise FrameError(f"bad tail magic: {bytes(buf[-4:]).hex()}")
    start = HEADER_SIZE
    head = bytes(buf[start : start + head_len])
    body = bytes(buf[start + head_len : start + head_len + body_len])
    return Frame(cmd=cmd, head=head, body=body)


@dataclass(slots=True)
class FrameReader:
    """Incremental reader for stream transports (TCP tap/replay tools)."""

    buffer: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        out: list[Frame] = []
        while len(self.buffer) >= HEADER_SIZE + 4:
            if bytes(self.buffer[:4]) != HEAD_MAGIC:
                raise FrameError(f"bad head magic: {bytes(self.buffer[:4]).hex()}")
            _, head_len, body_len = struct.unpack_from(">HHI", self.buffer, 4)
            total = HEADER_SIZE + head_len + body_len + 4
            if len(self.buffer) < total:
                break
            out.append(parse(self.buffer[:total]))
            del self.buffer[:total]
        return out
