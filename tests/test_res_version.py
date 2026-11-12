from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hsr.game.handlers import player as player_handlers
from hsr.protocol.gen import StarRail_pb2 as pb
from tests.support import build_session


class SyncClientResVersionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = build_session(Path(self.tmp.name))

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    async def test_echoes_client_version(self) -> None:
        """官方把客户端的资源版本原样回显（抓包 #651 -> #655）。

        回 0 等于告诉客户端「服务端资源版本不同」，开战前那一步会卡住。
        """
        rsp = await player_handlers.on_sync_res_version(
            self.session, pb.SyncClientResVersionCsReq(LJKMPIMKOMO=17145778)
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.LJKMPIMKOMO, 17145778)

    async def test_registered_on_router(self) -> None:
        router = self.session.runtime.router
        cmd = self.session.runtime.registry.id_of("SyncClientResVersionCsReq")
        self.assertIn(cmd, router.handlers, "SyncClientResVersionCsReq 没有注册 handler")
        self.assertEqual(router.handlers[cmd][0], "SyncClientResVersionCsReq")


if __name__ == "__main__":
    unittest.main()
