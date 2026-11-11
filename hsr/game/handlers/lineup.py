"""Lineup handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.game.services import lineup as lineup_service
from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


def _persist(session: Session) -> None:
    session.runtime.save.set_lineup(session.lineup.slots, session.lineup.leader)


async def on_get_all(session: Session, req: pb.GetAllLineupDataCsReq) -> pb.GetAllLineupDataScRsp:
    return pb.GetAllLineupDataScRsp(
        cur_index=0,
        lineup_list=[lineup_service.build_lineup(session)],
    )


async def on_get_cur(session: Session, req: pb.GetCurLineupDataCsReq) -> pb.GetCurLineupDataScRsp:
    return pb.GetCurLineupDataScRsp(lineup=lineup_service.build_lineup(session))


async def on_get_avatar_data(
    session: Session, req: pb.GetLineupAvatarDataCsReq
) -> pb.GetLineupAvatarDataScRsp:
    # Asked before entering a cocoon to draw the team's HP; see the builder for
    # why both base and form ids are included.
    return pb.GetLineupAvatarDataScRsp(
        avatar_data_list=lineup_service.build_avatar_data(session)
    )


async def on_replace(session: Session, req: pb.ReplaceLineupCsReq) -> pb.ReplaceLineupScRsp:
    roster = session.runtime.roster
    for slot_data in req.lineup_slot_list:
        avatar_id = int(slot_data.id)
        base = roster.base_id(avatar_id)
        if avatar_id and not roster.has_base(base):
            session.runtime.logger.warning("lineup replace ignored unknown avatar %d", avatar_id)
            continue
        session.lineup.set_slot(slot_data.slot, base)
    if req.leader_slot:
        session.lineup.leader = req.leader_slot
    _persist(session)
    await lineup_service.notify(session)
    return pb.ReplaceLineupScRsp()


async def on_join(session: Session, req: pb.JoinLineupCsReq) -> pb.JoinLineupScRsp:
    roster = session.runtime.roster
    base = roster.base_id(int(req.base_avatar_id))
    if base and roster.has_base(base):
        session.lineup.set_slot(req.slot, base)
        _persist(session)
        await lineup_service.notify(session)
    else:
        session.runtime.logger.warning("lineup join ignored unknown avatar %d", req.base_avatar_id)
    return pb.JoinLineupScRsp()


async def on_quit(session: Session, req: pb.QuitLineupCsReq) -> pb.QuitLineupScRsp:
    base = session.runtime.roster.base_id(int(req.base_avatar_id))
    for slot, avatar_id in enumerate(session.lineup.slots):
        if session.runtime.roster.base_id(avatar_id) == base:
            session.lineup.slots[slot] = 0
            break
    _persist(session)
    await lineup_service.notify(session)
    return pb.QuitLineupScRsp()


async def on_change_leader(
    session: Session, req: pb.ChangeLineupLeaderCsReq
) -> pb.ChangeLineupLeaderScRsp:
    session.lineup.leader = req.slot
    _persist(session)
    return pb.ChangeLineupLeaderScRsp(slot=req.slot)


def register(router: Router) -> None:
    router.add("GetAllLineupDataCsReq", on_get_all)
    router.add("GetCurLineupDataCsReq", on_get_cur)
    router.add("GetLineupAvatarDataCsReq", on_get_avatar_data)
    router.add("ReplaceLineupCsReq", on_replace)
    router.add("JoinLineupCsReq", on_join)
    router.add("QuitLineupCsReq", on_quit)
    router.add("ChangeLineupLeaderCsReq", on_change_leader)
