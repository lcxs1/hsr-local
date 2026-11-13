"""Login / authentication handlers (Phase 0)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from hsr.protocol import pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


def _now_ms() -> int:
    return int(time.time() * 1000)


async def on_get_token(session: Session, req: pb.PlayerGetTokenCsReq) -> pb.PlayerGetTokenScRsp:
    player = session.runtime.cfg.player
    return pb.PlayerGetTokenScRsp(uid=player.uid)


async def on_login(session: Session, req: pb.PlayerLoginCsReq) -> pb.PlayerLoginScRsp:
    player = session.runtime.cfg.player
    basic = pb.PlayerBasicInfo(
        nickname=player.name,
        level=player.level,
        stamina=player.stamina,
        world_level=player.world_level,
    )
    return pb.PlayerLoginScRsp(
        basic_info=basic,
        server_timestamp_ms=_now_ms(),
        stamina=player.stamina,
    )


async def on_basic_info(session: Session, req: pb.GetBasicInfoCsReq) -> pb.GetBasicInfoScRsp:
    mode = session.runtime.cfg.experiments.popup_basic_info
    roster = getattr(session.runtime, "roster", None)
    gender = (
        roster.gender_for(getattr(session, "tb_multi_path", None))
        if roster is not None
        else session.runtime.cfg.player.gender
    )

    rsp = pb.GetBasicInfoScRsp(
        cur_day=1,
        is_gender_set=True,
        gender=gender,
    )
    if mode == "keep":
        # Old behaviour.  The client logs a NullReferenceException inside
        # _CmdGetBasicInfoScRsp for this (empty sub-message, no nested ones).
        rsp.player_setting_info.CopyFrom(pb.PlayerSettingInfo())
    elif mode == "nested":
        # Same message, but with every nested sub-message present so a client
        # that dereferences them cannot hit a null.
        setting = pb.PlayerSettingInfo()
        # 每个 message 类型的子字段都给一个空实例：4.6.54 只补了其中三个，
        # 4.8 又把 OJPAODIHAJE 换成 relic_final_setting / CustomValue，
        # 客户端拿到 null 就在 _CmdGetBasicInfoScRsp 里 NRE。按 schema 遍历最省事。
        for field in setting.DESCRIPTOR.fields:
            if field.type != 11 or getattr(field, "is_repeated", False):
                continue
            sub = getattr(pb, field.message_type.name, None)
            if sub is not None:
                getattr(setting, field.name).CopyFrom(sub())
        rsp.player_setting_info.CopyFrom(setting)
    # mode == "omit": leave player_setting_info unset (pearl-sr / sr-recover).
    return rsp


async def on_login_finish(
    session: Session, req: pb.PlayerLoginFinishCsReq
) -> pb.PlayerLoginFinishScRsp:
    return pb.PlayerLoginFinishScRsp()


def register(router: Router) -> None:
    router.add("PlayerGetTokenCsReq", on_get_token)
    router.add("PlayerLoginCsReq", on_login)
    router.add("GetBasicInfoCsReq", on_basic_info)
    router.add("PlayerLoginFinishCsReq", on_login_finish)
