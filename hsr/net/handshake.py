"""20-byte UDP handshake.

Client -> server (20 bytes, big-endian fields):

    0   4B  code     255 = CONNECT, 404 = DISCONNECT
    4   8B  reserved
    12  4B  enet
    16  4B  tail     ff ff ff ff

Server -> client (20 bytes):

    00 00 01 45 | 8B session blob | 00 00 00 00 | 14 51 45 45

Two KCP session encodings exist in the wild. They are 28-byte KCP headers; only
the 8-byte session prefix differs:

    u32_le_token : handshake = conv(u32 BE) + token(u32 BE)
                   KCP       = conv(u32 LE) + token(u32 LE)   (echium / kcpshimmy)
    u64_be       : handshake = conv(u64 BE)
                   KCP       = conv(u64 BE)                    (64-bit variant)

The handshake blob and the KCP prefix are therefore *different byte sequences* for
u32_le_token. Keep them separate.
"""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass
from typing import Literal

CONNECT = 255
DISCONNECT = 404
REQUEST_TAIL = b"\xff\xff\xff\xff"
RESPONSE_MAGIC = 0x145
RESPONSE_TAIL = 0x14514545

ConvMode = Literal["u32_le_token", "u64_be"]


@dataclass(slots=True)
class Handshake:
    code: int
    enet: int
    raw: bytes


def looks_like_request(data: bytes) -> bool:
    return len(data) == 20 and data[-4:] == REQUEST_TAIL and data[:4] in (
        b"\x00\x00\x00\xff",
        b"\x00\x00\x01\x94",
    )


def parse(data: bytes) -> Handshake | None:
    if len(data) != 20:
        return None
    code = struct.unpack_from(">I", data, 0)[0]
    if code not in (CONNECT, DISCONNECT):
        return None
    enet = struct.unpack_from(">I", data, 12)[0]
    return Handshake(code=code, enet=enet, raw=bytes(data))


def handshake_blob(mode: ConvMode, conv: int, token: int = 0) -> bytes:
    if mode == "u32_le_token":
        return struct.pack(">II", conv & 0xFFFFFFFF, token & 0xFFFFFFFF)
    return (conv & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")


def kcp_conv(mode: ConvMode, conv: int, token: int = 0) -> int:
    """The integer handed to KCP; KCP writes it as 8 bytes big-endian."""
    if mode == "u32_le_token":
        return int.from_bytes(struct.pack("<II", conv & 0xFFFFFFFF, token & 0xFFFFFFFF), "big")
    return conv & 0xFFFFFFFFFFFFFFFF


def kcp_conv_from_handshake(mode: ConvMode, blob: bytes) -> int:
    if mode == "u32_le_token":
        conv, token = struct.unpack(">II", blob)
        return kcp_conv(mode, conv, token)
    return int.from_bytes(blob, "big")


def blob_key(mode: ConvMode, blob: bytes) -> int:
    """Human-facing session key (conv id)."""
    if mode == "u32_le_token":
        conv, _token = struct.unpack(">II", blob)
        return conv
    return int.from_bytes(blob, "big")


def new_session_ids(mode: ConvMode) -> tuple[int, int, bytes, int]:
    """Return (conv, token, handshake_blob, kcp_conv)."""
    if mode == "u32_le_token":
        conv = secrets.randbits(32)
        token = secrets.randbits(32)
    else:
        conv = secrets.randbits(63)
        token = 0
    return conv, token, handshake_blob(mode, conv, token), kcp_conv(mode, conv, token)


def build_response(blob: bytes) -> bytes:
    if len(blob) != 8:
        raise ValueError("session blob must be 8 bytes")
    return (
        struct.pack(">I", RESPONSE_MAGIC)
        + blob
        + b"\x00\x00\x00\x00"
        + struct.pack(">I", RESPONSE_TAIL)
    )
