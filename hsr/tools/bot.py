"""Headless game client: drive the server without launching StarRail.

The bot speaks the real wire protocol (20-byte UDP handshake -> KCP -> framed
protobuf) using the same modules the server uses. It is the middle layer between
the in-process replay (hsr.replay) and the real client (game_test.ps1):

* replay : handlers only, no sockets, no framing, no KCP;
* bot    : sockets + KCP + frames + protobuf + session state (this module);
* client : the real game, needs a human/UI automation.

Typical use:

    rt = Runtime.load(profile="4.6.54")
    bot = Bot(rt.registry, port=23301)
    bot.connect()
    p = bot.call_empty("PlayerGetTokenCsReq")
    assert p.msg.uid == rt.cfg.player.uid
    bot.close()

Every received frame is kept in bot.pending until a waiter consumes it, so a
scenario can assert on notifies the server pushed on its own.
"""

from __future__ import annotations

import select
import socket
import time
from dataclasses import dataclass, field
from typing import Any

from hsr.net import frame as frame_mod
from hsr.net import handshake
from hsr.net.kcp import KCP
from hsr.protocol.registry import Registry

CONNECT_REQUEST = b"\x00\x00\x00\xff" + b"\x00" * 8 + b"\x00" * 4 + b"\xff" * 4
DISCONNECT_REQUEST = b"\x00\x00\x01\x94" + b"\x00" * 8 + b"\x00" * 4 + b"\xff" * 4
RESPONSE_MAGIC = b"\x00\x00\x01\x45"
RESPONSE_TAIL = b"\x14\x51\x45\x45"


class BotError(RuntimeError):
    """The bot could not connect or a scenario assertion failed."""


@dataclass
class Packet:
    """One decoded server -> bot frame."""

    cmd: int
    name: str
    body: bytes
    msg: Any = None
    at: float = field(default_factory=time.monotonic)

    def __str__(self) -> str:
        return f"{self.name}(cmd={self.cmd}, {len(self.body)}B)"

    def field(self, name: str, default: Any = None) -> Any:
        return getattr(self.msg, name, default) if self.msg is not None else default


def _now_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class Bot:
    """A single scripted client session."""

    def __init__(
        self,
        registry: Registry,
        host: str = "127.0.0.1",
        port: int = 23301,
        *,
        mode: str = "u32_le_token",
        timeout: float = 5.0,
        heartbeat: float = 4.0,
    ) -> None:
        self.registry = registry
        self.host = host
        self.port = port
        self.mode = mode
        self.timeout = timeout
        self.heartbeat = heartbeat
        self.blob: bytes | None = None
        self.pending: list[Packet] = []
        self.history: list[Packet] = []
        self._sock: socket.socket | None = None
        self._kcp: KCP | None = None
        self._last_send = 0.0

    # ------------------------------------------------------------- lifecycle
    def __enter__(self) -> Bot:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self, timeout: float = 3.0) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((self.host, self.port))
        sock.setblocking(False)
        sock.send(CONNECT_REQUEST)

        deadline = time.monotonic() + timeout
        blob = None
        while time.monotonic() < deadline:
            r, _, _ = select.select([sock], [], [], 0.1)
            if not r:
                continue
            data = sock.recv(4096)
            if len(data) == 20 and data[:4] == RESPONSE_MAGIC and data[-4:] == RESPONSE_TAIL:
                blob = data[4:12]
                break
        if blob is None:
            sock.close()
            raise BotError(
                f"no handshake response from udp://{self.host}:{self.port} "
                "(is the server running? firewall?)"
            )

        self.blob = blob
        kcp = KCP(handshake.kcp_conv_from_handshake(self.mode, blob))
        kcp.setmtu(1400)
        kcp.wndsize(256, 256)
        kcp.nodelay(1, 10, 2, 1)
        kcp.output = lambda buf: sock.send(buf)
        self._kcp = kcp
        self._sock = sock
        self._last_send = time.monotonic()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.send(DISCONNECT_REQUEST)
            except OSError:
                pass
            self._sock.close()
        self._sock = None
        self._kcp = None

    # ---------------------------------------------------------------- send
    def send_raw(self, cmd: int, body: bytes = b"") -> None:
        if self._kcp is None:
            raise BotError("bot is not connected")
        self._kcp.send(frame_mod.encode(cmd, body))
        self._kcp.flush()
        self._kcp.update(_now_ms())
        self._last_send = time.monotonic()

    def send(self, msg: Any, name: str | None = None) -> str:
        req_name = name or msg.DESCRIPTOR.name
        cmd = self.registry.id_of(req_name)
        self.send_raw(cmd, msg.SerializeToString() if msg is not None else b"")
        return req_name

    def call(
        self,
        msg: Any = None,
        name: str | None = None,
        *,
        timeout: float | None = None,
    ) -> Packet | None:
        """Send one CsReq and wait for its ScRsp.

        Returns None when the request has no ScRsp in the cmd table (the server
        answers such requests with a notify or nothing at all).
        """
        req_name = self.send(msg, name)
        rsp_name = self.registry.rsp_name_of(req_name)
        if rsp_name is None:
            return None
        return self.wait_for(rsp_name, timeout)

    def call_empty(self, req_name: str, *, timeout: float | None = None) -> Packet | None:
        cls = self.registry.message_cls(req_name)
        if cls is None:
            raise BotError(f"no message class for {req_name}")
        return self.call(cls(), req_name, timeout=timeout)

    # ---------------------------------------------------------------- recv
    def wait_for(self, name: str, timeout: float | None = None) -> Packet:
        """Return the next frame called name; other frames stay pending."""
        cmd = self.registry.id_of(name) if self.registry.has(name) else None
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            for index, packet in enumerate(self.pending):
                if packet.cmd == cmd or packet.name == name:
                    return self.pending.pop(index)
            if time.monotonic() >= deadline:
                seen = ", ".join(str(p) for p in self.pending) or "(none)"
                raise BotError(f"timeout waiting for {name}; pending: {seen}")
            self._pump(0.05)

    def drain(self, seconds: float = 0.5) -> list[Packet]:
        """Pump for some seconds and return the frames that arrived."""
        before = len(self.pending)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._pump(min(0.05, max(0.0, deadline - time.monotonic())))
        return self.pending[before:]

    def pump(self, seconds: float = 0.2) -> None:
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self._pump(min(0.05, max(0.0, until - time.monotonic())))

    def _pump(self, seconds: float) -> None:
        if self._sock is None or self._kcp is None:
            raise BotError("bot is not connected")
        r, _, _ = select.select([self._sock], [], [], max(0.0, seconds))
        if r:
            try:
                data = self._sock.recv(65535)
            except (BlockingIOError, ConnectionResetError):
                data = None
            if data:
                self._kcp.input(data)
                self._kcp.flush()
        self._kcp.update(_now_ms())
        while True:
            buf = self._kcp.recv()
            if not buf:
                break
            self._on_frame(buf)
        self._maybe_heartbeat()

    def _on_frame(self, buf: bytes) -> None:
        try:
            pkt = frame_mod.parse(buf)
        except frame_mod.FrameError:
            return
        name, msg = self.registry.decode_cmd(pkt.cmd, pkt.body)
        packet = Packet(cmd=pkt.cmd, name=name, body=pkt.body, msg=msg)
        self.pending.append(packet)
        self.history.append(packet)

    def _maybe_heartbeat(self) -> None:
        if self.heartbeat <= 0 or not self.registry.has("PlayerHeartBeatCsReq"):
            return
        if time.monotonic() - self._last_send < self.heartbeat:
            return
        cls = self.registry.message_cls("PlayerHeartBeatCsReq")
        if cls is None:
            return
        try:
            self.send(cls())
        except Exception:  # noqa: BLE001 - a heartbeat must never break a scenario
            pass
