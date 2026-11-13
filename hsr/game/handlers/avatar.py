"""Avatar handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.game.services import lineup as lineup_service
from hsr.game.services import snapshot
from hsr.game.services.roster import MARCH_PATHS, TB_BASE, TB_PATHS
from hsr.protocol import pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


def _path_base(avatar_id: int) -> int:
    if avatar_id in TB_PATHS:
        return TB_BASE
    if avatar_id in MARCH_PATHS:
        return 1001
    return 0


def _apply_path(session: Session, avatar_id: int) -> bool:
    base = _path_base(avatar_id)
    if not base or not session.runtime.roster.has_form(avatar_id):
        return False
    if base == TB_BASE:
        expected_parity = 0 if session.runtime.cfg.player.gender == 2 else 1
        if avatar_id % 2 != expected_parity:
            return False
        session.tb_multi_path = avatar_id
    else:
        session.march_multi_path = avatar_id
    session.runtime.save.set_paths(
        tb_multi_path=session.tb_multi_path,
        march_multi_path=session.march_multi_path,
    )
    return True


async def _notify_path_change(session: Session, base: int, avatar_id: int) -> None:
    """Push the three notifies the client needs after a path switch.

    Order is the one verified on 4.6.54 (README「队伍与命途」): path notify, then
    the full avatar sync, then scene/lineup refresh.  The last two resolve the
    *form* id from ``session.tb_multi_path`` / ``march_multi_path`` at send time,
    so they only make sense after the new path has been stored.
    """
    await session.send(
        pb.AvatarPathChangedNotify(
            base_avatar_id=base,
            cur_multi_path_avatar_type=avatar_id,
        )
    )
    await session.send(pb.PlayerSyncScNotify(avatar_sync=snapshot.avatar_sync(session)))
    await lineup_service.notify(session)


async def on_get_avatar_data(
    session: Session, req: pb.GetAvatarDataCsReq
) -> pb.GetAvatarDataScRsp:
    sync = snapshot.avatar_sync(session)
    skins = getattr(session.runtime, "skins", None)
    return pb.GetAvatarDataScRsp(
        is_get_all=True,
        # tells the client that Trailblazer and March 7th are multi-path bases
        basic_type_id_list=[1001, 8001],
        avatar_list=sync.avatar_list,
        avatar_path_data_info_list=sync.avatar_path_data_info_list,
        skin_list=skins.all_ids() if skins is not None else [],
    )


async def on_set_avatar_path(
    session: Session, req: pb.SetAvatarPathCsReq
) -> pb.SetAvatarPathScRsp:
    avatar_id = int(req.avatar_id)
    base = _path_base(avatar_id)
    if not _apply_path(session, avatar_id):
        session.runtime.logger.warning("invalid avatar path switch: %d", avatar_id)
        return pb.SetAvatarPathScRsp(avatar_id=req.avatar_id, retcode=1)

    session.runtime.logger.info("avatar path changed: base=%d path=%d", base, avatar_id)
    await _notify_path_change(session, base, avatar_id)
    return pb.SetAvatarPathScRsp(avatar_id=req.avatar_id, retcode=0)


async def on_set_multiple_avatar_paths(
    session: Session, req: pb.SetMultipleAvatarPathsCsReq
) -> pb.SetMultipleAvatarPathsScRsp:
    changed: list[tuple[int, int]] = []
    for value in req.avatar_id_list:
        avatar_id = int(value)
        base = _path_base(avatar_id)
        if not _apply_path(session, avatar_id):
            session.runtime.logger.warning("invalid avatar path switch: %d", avatar_id)
            return pb.SetMultipleAvatarPathsScRsp(retcode=1)
        changed.append((base, avatar_id))

    session.runtime.logger.info("avatar paths changed: %s", changed)
    if changed:
        await _notify_path_change(session, changed[-1][0], changed[-1][1])
    return pb.SetMultipleAvatarPathsScRsp(retcode=0)


async def on_unlock_avatar_path(
    session: Session, req: pb.UnlockAvatarPathCsReq
) -> pb.UnlockAvatarPathScRsp:
    # Every path present in freesr-data.json is already owned; acknowledge so
    # the client does not leave its path-change UI stuck on a loading state.
    session.runtime.logger.info("avatar path unlock requested: %d", int(req.avatar_id))
    return pb.UnlockAvatarPathScRsp(
        avatar_id=req.avatar_id,
        basic_type_id_list=[1001, 8001],
        retcode=0,
    )


def _apply_skin(session: Session, avatar_id: int, skin_id: int) -> bool:
    base = session.runtime.roster.base_id(avatar_id)
    if not session.runtime.roster.has_base(base):
        return False
    if not session.runtime.skins.valid(base, skin_id):
        return False
    session.runtime.save.set_skin(base, skin_id)
    return True


async def on_dress_avatar_skin(
    session: Session, req: pb.DressAvatarSkinCsReq
) -> pb.DressAvatarSkinScRsp:
    skin_id = int(req.skin_id)
    if not _apply_skin(session, req.avatar_id, skin_id):
        session.runtime.logger.warning(
            "invalid skin switch: avatar=%d skin=%d", req.avatar_id, skin_id
        )
        return pb.DressAvatarSkinScRsp(retcode=1)

    session.runtime.logger.info(
        "avatar skin changed: avatar=%d skin=%d", req.avatar_id, skin_id
    )
    await session.send(pb.PlayerSyncScNotify(avatar_sync=snapshot.avatar_sync(session)))
    return pb.DressAvatarSkinScRsp(retcode=0)


async def on_take_off_avatar_skin(
    session: Session, req: pb.TakeOffAvatarSkinCsReq
) -> pb.TakeOffAvatarSkinScRsp:
    if not _apply_skin(session, req.avatar_id, 0):
        session.runtime.logger.warning("invalid skin removal: avatar=%d", req.avatar_id)
        return pb.TakeOffAvatarSkinScRsp(retcode=1)

    session.runtime.logger.info("avatar skin removed: avatar=%d", req.avatar_id)
    await session.send(pb.PlayerSyncScNotify(avatar_sync=snapshot.avatar_sync(session)))
    return pb.TakeOffAvatarSkinScRsp(retcode=0)


def register(router: Router) -> None:
    router.add("GetAvatarDataCsReq", on_get_avatar_data)
    router.add("SetAvatarPathCsReq", on_set_avatar_path)
    router.add("SetMultipleAvatarPathsCsReq", on_set_multiple_avatar_paths)
    router.add("UnlockAvatarPathCsReq", on_unlock_avatar_path)
    router.add("DressAvatarSkinCsReq", on_dress_avatar_skin)
    router.add("TakeOffAvatarSkinCsReq", on_take_off_avatar_skin)
