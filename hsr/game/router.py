"""Command router: cmd id -> typed handler.

Handlers are registered explicitly by command *name* (not by guessing from the
function name). This avoids the acronym trap in echium's decorator
(`on_p_v_e_battle_result` -> `PVEBattleResultCsReq`).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from google.protobuf.message import Message

from hsr.net.frame import Frame

if TYPE_CHECKING:
    from hsr.config import AppConfig
    from hsr.net.session import Session
    from hsr.protocol.registry import Registry

Handler = Callable[["Session", Message], Awaitable[Message | None]]

# High-frequency packets that would flood the log if every one was printed.
QUIET_CMDS = {1474}  # SceneEntityMoveCsReq


class Router:
    def __init__(self, cfg: AppConfig, registry: Registry, logger: logging.Logger):
        self.cfg = cfg
        self.registry = registry
        self.logger = logger
        self.handlers: dict[int, tuple[str, Handler]] = {}
        self.unknown_counts: Counter[int] = Counter()
        self.unknown_names: dict[int, str] = {}

    # ------------------------------------------------------------- register
    def add(self, req_name: str, fn: Handler, *, expect_rsp: bool = True) -> None:
        cmd = self.registry.id_of(req_name)
        if cmd in self.handlers:
            raise ValueError(f"duplicate handler for {req_name} (cmd {cmd})")
        self.handlers[cmd] = (req_name, fn)
        rsp = self.registry.rsp_name_of(req_name)
        if expect_rsp and rsp is None:
            self.logger.warning("no ScRsp mapping for %s", req_name)

    def on(self, req_name: str):
        def deco(fn: Handler) -> Handler:
            self.add(req_name, fn)
            return fn

        return deco

    # ------------------------------------------------------------- dispatch
    async def dispatch(self, session: Session, pkt: Frame) -> None:
        entry = self.handlers.get(pkt.cmd)
        name = self.registry.name_of(pkt.cmd)

        if entry is None:
            await self._unknown(session, pkt, name)
            return

        req_name, fn = entry
        req: Message | None
        if pkt.body:
            req = self.registry.decode(req_name, pkt.body)
        else:
            cls = self.registry.message_cls(req_name)
            req = cls() if cls else None
        if req is None:
            self.logger.warning("failed to decode %s (cmd %d, %d bytes)", req_name, pkt.cmd, len(pkt.body))
            await self._unknown(session, pkt, req_name)
            return

        if pkt.cmd not in QUIET_CMDS:
            self.logger.debug("-> %s (cmd %d) %d bytes", req_name, pkt.cmd, len(pkt.body))

        try:
            rsp = await fn(session, req)
        except Exception:  # noqa: BLE001 - one bad handler must not kill the server
            self.logger.exception("handler %s failed", req_name)
            pair = self.registry.empty_rsp_bytes(pkt.cmd)
            if pair:
                await session.send_empty(pair[0])
            return

        if rsp is not None:
            await session.send(rsp)

    async def _unknown(self, session: Session, pkt: Frame, name: str) -> None:
        first = pkt.cmd not in self.unknown_counts
        self.unknown_counts[pkt.cmd] += 1
        self.unknown_names.setdefault(pkt.cmd, name)
        if first and pkt.cmd not in QUIET_CMDS:
            # INFO once per distinct command so a force-killed test run still
            # leaves the unknown list in packets.log/report.
            self.logger.info("unhandled command: %s (cmd %d)", name, pkt.cmd)
        mode = self.cfg.server.unknown_mode
        if pkt.cmd not in QUIET_CMDS:
            self.logger.debug("unhandled %s (cmd %d) %d bytes", name, pkt.cmd, len(pkt.body))
        if mode == "silent":
            return
        if mode == "error":
            self.logger.error("unhandled %s (cmd %d)", name, pkt.cmd)
            return
        pair = self.registry.empty_rsp_bytes(pkt.cmd)
        if pair:
            await session.send_empty(pair[0])

    def unknown_summary(self, limit: int = 30) -> list[str]:
        """One line per distinct unhandled command, most frequent first."""
        items = sorted(
            self.unknown_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        lines: list[str] = []
        for cmd, count in items[:limit]:
            lines.append(f"{cmd:>5} {self.unknown_names.get(cmd, '?')} x{count}")
        if len(items) > limit:
            lines.append(f"... {len(items) - limit} more")
        return lines

    def log_unknown_summary(self) -> None:
        total = sum(self.unknown_counts.values())
        if not total:
            self.logger.info("unknown commands: 0")
            return
        self.logger.info(
            "unknown commands: %d distinct / %d packets",
            len(self.unknown_counts),
            total,
        )
        for line in self.unknown_summary():
            self.logger.info("  %s", line)
