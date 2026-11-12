"""Player handlers: heartbeat, logout, profile."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from hsr.game.services import lineup as lineup_service
from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


def _now_ms() -> int:
    return int(time.time() * 1000)


async def on_heartbeat(session: Session, req: pb.PlayerHeartBeatCsReq) -> pb.PlayerHeartBeatScRsp:
    now = _now_ms()
    rsp = pb.PlayerHeartBeatScRsp(
        server_time_ms=now,
        client_time_ms=req.client_time_ms,
    )

    # 秘技点的定时恢复搭在心跳上（客户端 5 秒一次）：不需要额外定时任务，
    # 客户端断开后自然停止。只有 [gameplay].technique_points = "refill" 才有动作。
    refill = lineup_service.tick_mp(session)
    if refill:
        for message in refill:
            await session.send(message)
        session.runtime.logger.debug(
            "technique points refilled: %d/%d",
            lineup_service.current_mp(session),
            lineup_service.max_mp(session),
        )

    if session.runtime.cfg.features.lua_console:
        script = session.runtime.lua.take()
        if script:
            # Message fields must be copied, not assigned (protobuf restriction).
            rsp.download_data.CopyFrom(pb.ClientDownloadData(version=51, time=now, data=script))
            session.runtime.logger.info("lua script pushed (%d bytes)", len(script))
    return rsp


async def on_board_data(
    session: Session, req: pb.GetPlayerBoardDataCsReq
) -> pb.GetPlayerBoardDataScRsp:
    return pb.GetPlayerBoardDataScRsp(
        signature="",
        current_head_icon_id=200143,
        unlocked_head_icon_list=[pb.HeadIconData(id=200143)],
        display_avatar_vec=pb.DisplayAvatarVec(is_display=False),
    )


async def on_logout(session: Session, req: pb.PlayerLogoutCsReq):
    session.runtime.logger.info("client requested logout")
    # Do not cancel our own worker from inside a handler; let the loop close it.
    import asyncio

    asyncio.create_task(session.close("logout"))
    return None


async def on_set_paused(session: Session, req: pb.SetClientPausedCsReq) -> pb.SetClientPausedScRsp:
    return pb.SetClientPausedScRsp(paused=req.paused)


async def on_sync_res_version(
    session: Session, req: pb.SyncClientResVersionCsReq
) -> pb.SyncClientResVersionScRsp:
    """客户端资源版本同步：原样回显版本号。

    官方（抓包 #651 → #655）收到 LJKMPIMKOMO=17145778 就回同一个值。以前这条走
    通用空回包，等于告诉客户端「服务端资源版本 = 0」；客户端在开战前会发它
    （混沌回忆每一次战斗前都发），版本对不上时进战斗会卡住。
    """
    session.runtime.logger.debug("client res version: %d", req.LJKMPIMKOMO)
    return pb.SyncClientResVersionScRsp(
        retcode=0,
        LJKMPIMKOMO=req.LJKMPIMKOMO,
    )


def register(router: Router) -> None:
    router.add("PlayerHeartBeatCsReq", on_heartbeat)
    router.add("GetPlayerBoardDataCsReq", on_board_data)
    router.add("SetClientPausedCsReq", on_set_paused)
    router.add("SyncClientResVersionCsReq", on_sync_res_version)
    router.add("PlayerLogoutCsReq", on_logout, expect_rsp=False)
