"""Per-client game session.

One session owns one KCP state machine. UDP datagrams are fed into KCP; complete
application messages are queued and processed sequentially by a worker task, so
handlers never race each other (login order matters).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from hsr.log import get_logger
from hsr.net import frame as frame_mod
from hsr.net.frame import Frame, FrameError

if TYPE_CHECKING:
    from hsr.runtime import Runtime

log = get_logger("hsr.session")


def now_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class Session:
    def __init__(
        self,
        runtime: Runtime,
        transport,
        addr: tuple[str, int],
        blob: bytes,
        conv_key: int,
        kcp_conv: int,
    ):
        from hsr.net.kcp import KCP

        self.runtime = runtime
        self.transport = transport
        self.addr = addr
        self.blob = blob
        self.conv_key = conv_key
        self.closed = False
        self.last_recv = time.monotonic()
        # 客户端连上来的时刻：玩家登录后多久点「传送」要靠它算（见
        # EndgameSettings.lobby_grace_seconds 的 4.7.0 真机数据）。
        self.created_at = self.last_recv

        self.kcp = KCP(kcp_conv)
        self.kcp.output = self._output
        self.kcp.setmtu(runtime.cfg.server.kcp_mtu)
        self.kcp.wndsize(runtime.cfg.server.kcp_sndwnd, runtime.cfg.server.kcp_rcvwnd)
        # 快速模式 (nodelay=1, interval=10ms, resend=2, nc=1)：关掉拥塞控制、10ms 推进
        # 一次。参数与 echium 一致；改这里会影响心跳和小包的往返延迟（docs/design/01 §3.3）。
        self.kcp.nodelay(1, 10, 2, 1)

        self.queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=1024)
        self.worker = asyncio.create_task(self._run())
        self.on_close: Callable[[Session], None] | None = None

        from hsr.game.services.lineup import LineupState
        from hsr.game.services.scene import SceneState

        saved = runtime.save.player
        wanted = (
            saved.lineup or runtime.cfg.player.default_lineup or runtime.roster.default_lineup()
        )
        slots = runtime.roster.repair_lineup(wanted, runtime.cfg.world.team_size)
        if slots != list(saved.lineup or wanted):
            runtime.logger.warning(
                "lineup repaired (freesr changed?): %s -> %s",
                saved.lineup or wanted,
                slots,
            )
        leader = int(saved.leader)
        if not 0 <= leader < len(slots):
            leader = 0
        self.lineup = LineupState(
            slots=slots,
            leader=leader,
            mp=runtime.cfg.gameplay.technique_points_max,
        )
        self.tb_multi_path = self._resolve_tb_path(
            saved.tb_multi_path,
            runtime.cfg.player.tb_multi_path,
            runtime.roster,
        )
        self.march_multi_path = self._resolve_path(
            saved.march_multi_path,
            runtime.cfg.player.march_multi_path,
            runtime.roster,
            (1001, 1224),
        )
        self.scene = SceneState.from_config(runtime.cfg.world)
        if runtime.cfg.world.persist_position and len(saved.pos) == 3:
            self.scene.position = (saved.pos[0], saved.pos[1], saved.pos[2])
        self.battle_seq = 0
        self.battle_info = None
        # Active endgame challenge (MoC/PF/AS); see hsr/game/services/challenge.py.
        self.challenge = None
        # 进入忘却之庭大厅（plane 10000）之前所在的场景 (plane, floor, entry)。
        # 挑战结束/退出大厅时用它回到"干净"的世界场景，而不是把玩家留在大厅
        # （2026-11-11 真机：战斗中退出后停在列车车厢，传送按钮又因同 entry 变成空操作）。
        self.lobby_return: tuple[int, int, int] | None = None


    @staticmethod
    def _resolve_tb_path(saved_path: int, config_path: int, roster) -> int:
        """Pick a valid Trailblazer form, then align it to configured gender."""
        for candidate in (saved_path, config_path):
            if candidate in range(8001, 8011) and roster.has_form(candidate):
                return roster.tb_path(candidate)
        return roster.tb_path()

    @staticmethod
    def _resolve_path(
        saved_path: int, config_path: int, roster, allowed: tuple[int, ...]
    ) -> int:
        """Pick a valid form for March 7th (no gender variants)."""
        for candidate in (saved_path, config_path):
            if candidate in allowed and roster.has_form(candidate):
                return candidate
        return allowed[0]

    # ------------------------------------------------------------- hot reload
    def refresh_freesr(
        self, *, tb_path: int | None = None, march_path: int | None = None
    ) -> bool:
        """Re-resolve paths + lineup after ``runtime.roster`` was replaced.

        Called by the freesr hot reload (``/srtools`` or the file watcher) on
        every live session.  ``tb_path``/``march_path`` are the optional forms
        pushed by the SR Tools page; without them the session keeps its own
        choice, corrected against the new roster.

        Returns ``True`` when the lineup changed, i.e. the caller must push a
        new ``LineupInfo`` (``lineup.notify``) instead of only the avatar sync.
        """
        runtime = self.runtime
        roster = runtime.roster
        self.tb_multi_path = self._resolve_tb_path(
            tb_path or self.tb_multi_path,
            runtime.cfg.player.tb_multi_path,
            roster,
        )
        self.march_multi_path = self._resolve_path(
            march_path or self.march_multi_path,
            runtime.cfg.player.march_multi_path,
            roster,
            (1001, 1224),
        )
        if (
            runtime.save.player.tb_multi_path != self.tb_multi_path
            or runtime.save.player.march_multi_path != self.march_multi_path
        ):
            runtime.save.set_paths(
                tb_multi_path=self.tb_multi_path,
                march_multi_path=self.march_multi_path,
            )

        wanted = [avatar_id for avatar_id in self.lineup.slots if avatar_id]
        slots = roster.repair_lineup(wanted, runtime.cfg.world.team_size)
        if slots == list(self.lineup.slots):
            return False
        runtime.logger.warning(
            "lineup repaired after freesr reload: %s -> %s", self.lineup.slots, slots
        )
        self.lineup.slots = slots
        if not 0 <= self.lineup.leader < len(slots):
            self.lineup.leader = 0
        runtime.save.set_lineup(slots, self.lineup.leader)
        return True

    # ------------------------------------------------------------- transport
    def _output(self, data: bytes) -> None:
        if not self.closed:
            self.transport.sendto(data, self.addr)

    def feed(self, data: bytes) -> None:
        if self.closed:
            return
        self.last_recv = time.monotonic()
        try:
            self.kcp.input(data)
            self.kcp.flush()
            self.kcp.update(now_ms())
        except Exception as exc:  # noqa: BLE001 - malformed packet must not kill the server
            log.warning("kcp input error from %s: %s", self.addr, exc)
            return

        while True:
            buf = self.kcp.recv()
            if not buf:
                break
            try:
                pkt = frame_mod.parse(buf)
            except FrameError as exc:
                log.warning("bad frame from %s: %s", self.addr, exc)
                continue
            try:
                self.queue.put_nowait(pkt)
            except asyncio.QueueFull:
                log.warning("session queue full, dropping cmd=%s", pkt.cmd)

    async def _run(self) -> None:
        from hsr.protocol import pb

        # Handlers resolve classes through hsr.protocol.pb, and the worker (plus
        # every task it spawns) must use the snapshot of the profile it serves.
        with pb.using(self.runtime.registry.pb):
            try:
                while not self.closed:
                    pkt = await self.queue.get()
                    try:
                        await self.runtime.router.dispatch(self, pkt)
                    except Exception:  # noqa: BLE001 - one bad packet must not kill the worker
                        log.exception("session worker error on cmd=%s", pkt.cmd)
                    finally:
                        self.queue.task_done()
            except asyncio.CancelledError:
                pass

    # ----------------------------------------------------------------- send
    def send_frame(self, cmd: int, body: bytes = b"") -> None:
        if self.closed:
            return
        try:
            self.kcp.send(frame_mod.encode(cmd, body))
            self.kcp.flush()
            self.kcp.update(now_ms())
        except Exception:  # noqa: BLE001
            log.exception("failed to send cmd=%s", cmd)

    async def send(self, msg, name: str | None = None) -> None:
        cmd, body = self.runtime.registry.encode(msg, name)
        self.send_frame(cmd, body)

    async def send_empty(self, cmd: int) -> None:
        self.send_frame(cmd, b"")

    # ------------------------------------------------------------------ life
    def tick(self, now: int) -> None:
        if self.closed:
            return
        try:
            self.kcp.update(now)
        except Exception:  # noqa: BLE001
            log.exception("kcp update failed")

    def timed_out(self, seconds: float) -> bool:
        return (time.monotonic() - self.last_recv) > seconds

    async def close(self, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        self.worker.cancel()
        if self.on_close:
            try:
                self.on_close(self)
            except Exception:  # noqa: BLE001
                pass
        log.info("session %s closed%s", self.addr, f" ({reason})" if reason else "")
