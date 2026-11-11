from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from hsr.config import ROOT, AppConfig, PathSettings, PlayerSettings, ServerSettings
from hsr.game.handlers import register_all
from hsr.game.lua import LuaConsole
from hsr.game.router import Router
from hsr.game.services.battle import BattleData
from hsr.game.services.prefs import PrefsStore
from hsr.game.services.progress import ProgressBook
from hsr.game.services.roster import Roster
from hsr.game.services.save import SaveStore
from hsr.game.services.tutorials import TutorialBook
from hsr.net import frame as frame_mod
from hsr.net import handshake
from hsr.net import server as net_server
from hsr.net.kcp import KCP
from hsr.protocol.registry import Registry
from hsr.runtime import Runtime

log = logging.getLogger("hsr.test")


def now_ms() -> int:
    return int(asyncio.get_running_loop().time() * 1000) & 0xFFFFFFFF


class _Client(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data, addr):  # noqa: ANN001
        self.queue.put_nowait(data)


class FlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = AppConfig(
            server=ServerSettings(game_port=0, http_port=0, kcp_conv_mode="u32_le_token"),
            paths=PathSettings(lua_dir=self.tmp.name),
            player=PlayerSettings(uid=4242, name="Tester"),
        )
        registry = Registry(ROOT / "proto")
        router = Router(cfg, registry, log)
        register_all(router)
        roster = Roster.fallback(cfg.player)
        self.rt = Runtime(
            cfg=cfg,
            registry=registry,
            router=router,
            roster=roster,
            lua=LuaConsole(Path(self.tmp.name)),
            logger=log,
            prefs=PrefsStore.load(Path(self.tmp.name) / "server_prefs.json"),
            save=SaveStore.load(Path(self.tmp.name) / "player.json"),
            tutorials=TutorialBook(),
            progress=ProgressBook(),
            battle=BattleData(),
        )
        self.protocol = await net_server.start(self.rt)
        self.server_addr = ("127.0.0.1", self.protocol.transport.get_extra_info("sockname")[1])

        loop = asyncio.get_running_loop()
        transport, client = await loop.create_datagram_endpoint(
            _Client, local_addr=("127.0.0.1", 0)
        )
        self.transport = transport
        self.client = client

    async def asyncTearDown(self):
        self.transport.close()
        if self.protocol.transport:
            self.protocol.transport.close()
        self.tmp.cleanup()

    async def _handshake(self) -> KCP:
        req = b"\x00\x00\x00\xff" + b"\x00" * 8 + b"\x00" * 4 + b"\xff\xff\xff\xff"
        self.transport.sendto(req, self.server_addr)
        rsp = await asyncio.wait_for(self.client.queue.get(), 2)
        self.assertEqual(len(rsp), 20)
        self.assertEqual(rsp[:4], b"\x00\x00\x01\x45")
        blob = rsp[4:12]
        kcp = KCP(handshake.kcp_conv_from_handshake("u32_le_token", blob))
        kcp.setmtu(1400)
        kcp.wndsize(256, 256)
        kcp.nodelay(1, 10, 2, 1)
        kcp.output = lambda data: self.transport.sendto(data, self.server_addr)
        return kcp

    async def _rpc(self, kcp: KCP, cmd: int, body: bytes) -> frame_mod.Frame:
        kcp.send(frame_mod.encode(cmd, body))
        kcp.flush()
        kcp.update(now_ms())
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            try:
                data = await asyncio.wait_for(self.client.queue.get(), 0.2)
            except TimeoutError:
                kcp.update(now_ms())
                continue
            kcp.input(data)
            kcp.flush()
            kcp.update(now_ms())
            while True:
                buf = kcp.recv()
                if not buf:
                    break
                return frame_mod.parse(buf)
        raise AssertionError("no response")

    async def test_login_flow(self):
        kcp = await self._handshake()
        cls = self.rt.registry.message_cls("PlayerGetTokenCsReq")
        pkt = await self._rpc(
            kcp, self.rt.registry.id_of("PlayerGetTokenCsReq"), cls().SerializeToString()
        )
        self.assertEqual(pkt.cmd, 71)
        rsp = self.rt.registry.decode("PlayerGetTokenScRsp", pkt.body)
        self.assertEqual(rsp.uid, 4242)

        cls = self.rt.registry.message_cls("PlayerLoginCsReq")
        pkt = await self._rpc(
            kcp, self.rt.registry.id_of("PlayerLoginCsReq"), cls().SerializeToString()
        )
        self.assertEqual(pkt.cmd, 19)
        rsp = self.rt.registry.decode("PlayerLoginScRsp", pkt.body)
        self.assertEqual(rsp.basic_info.nickname, "Tester")

        cls = self.rt.registry.message_cls("PlayerHeartBeatCsReq")
        req = cls(client_time_ms=12345)
        pkt = await self._rpc(kcp, 45, req.SerializeToString())
        self.assertEqual(pkt.cmd, 16)
        rsp = self.rt.registry.decode("PlayerHeartBeatScRsp", pkt.body)
        self.assertEqual(rsp.client_time_ms, 12345)

    async def test_unknown_gets_empty_rsp(self):
        kcp = await self._handshake()
        # GetCurAssistCsReq has no handler; the fallback should answer with an
        # empty GetCurAssistScRsp (assist/support list is always empty here).
        pkt = await self._rpc(kcp, self.rt.registry.id_of("GetCurAssistCsReq"), b"")
        self.assertEqual(pkt.cmd, self.rt.registry.id_of("GetCurAssistScRsp"))
        self.assertEqual(pkt.body, b"")
        self.assertTrue(
            any("GetCurAssistCsReq" in line for line in self.rt.router.unknown_summary())
        )
        with self.assertLogs("hsr.test", level="INFO") as logs:
            self.rt.router.log_unknown_summary()
        self.assertTrue(any("unknown commands" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
