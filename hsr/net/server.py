"""UDP listener: handshake + KCP sessions + periodic tick."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from hsr.log import get_logger
from hsr.net import handshake
from hsr.net.session import Session, now_ms

if TYPE_CHECKING:
    from hsr.runtime import Runtime

log = get_logger("hsr.net")


class GameProtocol(asyncio.DatagramProtocol):
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.transport: asyncio.DatagramTransport | None = None
        self.sessions: dict[int, Session] = {}
        self.by_addr: dict[tuple[str, int], Session] = {}
        self._tick_task: asyncio.Task | None = None

    # ------------------------------------------------------------- lifecycle
    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport
        self._tick_task = asyncio.create_task(self._tick())

    def connection_lost(self, exc) -> None:  # type: ignore[override]
        if self._tick_task:
            self._tick_task.cancel()
        for session in list(self.sessions.values()):
            asyncio.create_task(session.close("transport lost"))

    # ------------------------------------------------------------------ recv
    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if handshake.looks_like_request(data):
            hs = handshake.parse(data)
            if hs is None:
                return
            if hs.code == handshake.CONNECT:
                self._on_connect(addr)
            elif hs.code == handshake.DISCONNECT:
                self._on_disconnect(addr)
            return

        session = self.by_addr.get(addr)
        if session is None:
            log.debug("packet from unknown addr %s (%d bytes)", addr, len(data))
            return
        session.feed(data)

    # ------------------------------------------------------------- handshake
    def _on_connect(self, addr) -> None:
        cfg = self.runtime.cfg.server
        if self.transport is None:
            return
        mode = cfg.kcp_conv_mode

        if cfg.single_client and self.sessions:
            for old in list(self.sessions.values()):
                asyncio.create_task(old.close("replaced by new client"))

        conv, token, blob, kcp_conv = handshake.new_session_ids(mode)
        conv_key = handshake.blob_key(mode, blob)
        session = Session(self.runtime, self.transport, addr, blob, conv_key, kcp_conv)
        session.on_close = self._remove
        self.sessions[conv_key] = session
        self.by_addr[addr] = session

        self.transport.sendto(handshake.build_response(blob), addr)
        log.info(
            "client %s connected: mode=%s conv=%s token=%s",
            addr,
            mode,
            conv,
            token,
        )

    def _on_disconnect(self, addr) -> None:
        session = self.by_addr.get(addr)
        if session:
            asyncio.create_task(session.close("client disconnect"))

    def _remove(self, session: Session) -> None:
        self.sessions.pop(session.conv_key, None)
        if self.by_addr.get(session.addr) is session:
            self.by_addr.pop(session.addr, None)

    # ----------------------------------------------------------------- tick
    async def _tick(self) -> None:
        timeout = self.runtime.cfg.server.heartbeat_timeout
        try:
            while True:
                now = now_ms()
                for session in list(self.sessions.values()):
                    if session.closed:
                        continue
                    session.tick(now)
                    if session.timed_out(timeout):
                        await session.close("heartbeat timeout")
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass


async def start(rt: Runtime) -> GameProtocol:
    loop = asyncio.get_running_loop()
    cfg = rt.cfg.server
    bind_host = cfg.game_bind_host or cfg.listen
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: GameProtocol(rt),
        local_addr=(bind_host, cfg.game_port),
    )
    rt.logger.info("game server listening on udp://%s:%d", bind_host, cfg.game_port)
    # The HTTP side (/srtools, file watcher) needs the live sessions to push
    # roster hot reloads; both run on the same event loop.
    rt.protocol = protocol
    return protocol


async def run_forever(rt: Runtime) -> None:
    await start(rt)
    while True:
        await asyncio.sleep(3600)
