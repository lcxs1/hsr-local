"""ServerPrefs handlers (UI "already seen" flags)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.protocol import pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


async def on_get_all(
    session: Session, req: pb.GetAllServerPrefsDataCsReq
) -> pb.GetAllServerPrefsDataScRsp:
    return pb.GetAllServerPrefsDataScRsp(server_prefs_list=session.runtime.prefs.to_proto())


async def on_get_one(
    session: Session, req: pb.GetServerPrefsDataCsReq
) -> pb.GetServerPrefsDataScRsp:
    data = session.runtime.prefs.get(req.server_prefs_id) or b""
    return pb.GetServerPrefsDataScRsp(
        server_prefs=pb.ServerPrefs(
            server_prefs_id=req.server_prefs_id,
            data=data,
        )
    )


async def on_update(session: Session, req: pb.UpdateServerPrefsCsReq) -> pb.UpdateServerPrefsScRsp:
    prefs = req.server_prefs
    if prefs.server_prefs_id:
        # Logged so a real-client test can see which pref the client writes when
        # a guide/tip is acknowledged (red-dot / hint debugging).
        session.runtime.logger.info(
            "server prefs update: id=%d len=%d data=%s",
            prefs.server_prefs_id,
            len(prefs.data),
            prefs.data.hex(),
        )
        session.runtime.prefs.set(prefs.server_prefs_id, prefs.data)
    return pb.UpdateServerPrefsScRsp(server_prefs_id=prefs.server_prefs_id)


def register(router: Router) -> None:
    router.add("GetAllServerPrefsDataCsReq", on_get_all)
    router.add("GetServerPrefsDataCsReq", on_get_one)
    router.add("UpdateServerPrefsCsReq", on_update)
