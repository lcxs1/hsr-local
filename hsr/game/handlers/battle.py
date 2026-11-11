"""Cocoon battle handlers (Phase 2): start, quick start, result, quit.

历战余响（Echo of War / FarmElement）也在这里：它和凝滞虚影共用同一个
``SceneBattleInfo`` 构造器，区别只是 stage 来源（``farm_element_stage_map``）。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from hsr.game.services import challenge as challenge_service
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session

# 历战余响的本周奖励周期：客户端只拿它显示"剩余时间"，给足一周即可。
FARM_GACHA_WINDOW_SEC = 7 * 24 * 3600


def _battle_info_for_stage(session: Session, stage_id: int, world_level: int):
    battle = session.runtime.battle
    battle_id = battle.next_battle_id(session)
    info = battle.build_battle_info(
        session,
        stage_id=stage_id,
        world_level=world_level or None,
        battle_id=battle_id,
    )
    session.battle_info = info
    return info


def _battle_info(session: Session, cocoon_id: int, world_level: int):
    battle = session.runtime.battle
    stage_id = battle.resolve_stage(cocoon_id, world_level)
    return stage_id, _battle_info_for_stage(session, stage_id, world_level)


async def on_start_cocoon(
    session: Session, req: pb.StartCocoonStageCsReq
) -> pb.StartCocoonStageScRsp:
    stage_id, info = _battle_info(session, req.cocoon_id, req.world_level)
    session.runtime.logger.info(
        "start cocoon: cocoon_id=%d(%s) wave=%d prop=%d world_level=%d"
        " -> stage=%d battle_id=%d",
        req.cocoon_id,
        session.runtime.battle.name_of_cocoon(req.cocoon_id),
        req.wave,
        req.prop_entity_id,
        req.world_level,
        stage_id,
        info.battle_id,
    )
    return pb.StartCocoonStageScRsp(
        retcode=0,
        cocoon_id=req.cocoon_id,
        wave=req.wave,
        prop_entity_id=req.prop_entity_id,
        challenge_cnt=0,
        battle_info=info,
    )


async def on_quick_start_cocoon(
    session: Session, req: pb.QuickStartCocoonStageCsReq
) -> pb.QuickStartCocoonStageScRsp:
    stage_id, info = _battle_info(session, req.cocoon_id, req.world_level)
    session.runtime.logger.info(
        "quick start cocoon: cocoon_id=%d(%s) wave=%d world_level=%d times=%d"
        " -> stage=%d battle_id=%d",
        req.cocoon_id,
        session.runtime.battle.name_of_cocoon(req.cocoon_id),
        req.wave,
        req.world_level,
        req.HECCOBFBJFI,
        stage_id,
        info.battle_id,
    )
    return pb.QuickStartCocoonStageScRsp(
        retcode=0,
        cocoon_id=req.cocoon_id,
        wave=req.wave,
        battle_info=info,
    )


async def _enter_second_half(session: Session) -> None:
    """Move the attempt to 下半 according to ``[endgame] advance``.

    * ``scene``   — rebuild node2 and ``EnterSceneByServerScNotify`` (LunarCore).
      4.6.54 accepts it (the player can reach and attack the 下半 monster).
    * ``refresh`` — same scene, swap the maze group (DanhengServer).  4.6.54
      crashes in the client's ``_RefreshGroup_Legacy``, kept for A/B only.
    * ``none``    — only the result ack; the client must send ``StartChallenge``
      itself (pearl flow), handled in ``on_start_challenge``.
    """
    state = getattr(session, "challenge", None)
    tierce = bool(state is not None and getattr(state, "tierce", False))
    # 星启模式没有"上下半"一说：节点之间一律重建 SceneInfo，refresh 只对 node1→node2 有意义。
    mode = "scene" if tierce else session.runtime.cfg.endgame.advance
    if mode == "none":
        session.runtime.logger.info("challenge advance: skipped (advance=none)")
        return
    if mode == "refresh":
        refresh, lineup = challenge_service.advance_stage(session)
        await session.send(refresh)
        await session.send(
            pb.ChallengeLineupNotify(extra_lineup_type=lineup.extra_lineup_type)
        )
        await session.send(pb.SyncLineupNotify(lineup=lineup))
        return
    scene = (
        challenge_service.advance_tierce(session)
        if tierce
        else challenge_service.advance_scene(session)
    )
    lineup = challenge_service.lineup_info(session)
    await session.send(
        pb.ChallengeLineupNotify(extra_lineup_type=lineup.extra_lineup_type)
    )
    await session.send(pb.SyncLineupNotify(lineup=lineup))
    await session.send(
        pb.EnterSceneByServerScNotify(
            reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
            lineup=lineup,
            scene=scene,
        )
    )


async def on_pve_result(session: Session, req: pb.PVEBattleResultCsReq) -> pb.PVEBattleResultScRsp:
    try:
        end_name = pb.BattleEndStatus.Name(req.end_status)
    except ValueError:
        end_name = f"UNKNOWN({req.end_status})"
    session.runtime.logger.info(
        "battle result: battle_id=%d stage_id=%d end_status=%d (%s)",
        req.battle_id,
        req.stage_id,
        req.end_status,
        end_name,
    )
    active = getattr(session, "battle_info", None)
    challenge = getattr(session, "challenge", None)
    # The client settles the id it acted on, which can be older than the last id
    # handed out (repeated SceneCastSkill while the arena loads).  Accept any id
    # this attempt handed out, not just the active one.
    owns_battle = active is not None and (
        active.battle_id == req.battle_id
        or (challenge is not None and req.battle_id in challenge.battle_ids)
    )
    if active is None:
        session.runtime.logger.warning(
            "battle result %d arrived without an active battle; accepting late result",
            req.battle_id,
        )
    elif not owns_battle:
        session.runtime.logger.warning(
            "battle result id %d does not match active battle %d; keeping active battle",
            req.battle_id,
            active.battle_id,
        )
    else:
        # WIN/LOSE/QUIT all end the current battle.  There is no reward
        # pipeline; the only state to clear is the active battle id.
        session.battle_info = None

    rsp = pb.PVEBattleResultScRsp(
        retcode=0,
        battle_id=req.battle_id,
        stage_id=req.stage_id,
        end_status=req.end_status,
    )
    if challenge is not None and owns_battle:
        won = req.end_status == pb.BattleEndStatus.Value("BATTLE_END_WIN")
        session.runtime.logger.info(
            "challenge battle result: challenge=%d mode=%s stage=%d win=%s",
            challenge.challenge_id,
            challenge.mode,
            challenge.stage_id,
            won,
        )
        # Settlement must follow the result ack; without the notify the client
        # stays in the arena and respawns the monster forever.
        await session.send(rsp)
        if won and challenge_service.can_advance(session):
            await _enter_second_half(session)
            return None
        await session.send(challenge_service.settle_notify(session, win=won))
        if getattr(challenge, "direct", False):
            await _leave_direct(session)
        return None
    return rsp


async def _leave_direct(session: Session) -> None:
    """Return a ``/debug/challenge`` attempt to the open world.

    The client never started this through the lobby, so it has no
    ``LeaveChallenge`` button; the server has to send the world scene itself.
    """
    challenge_service.clear(session)
    session.lobby_return = None
    session.scene = scene_service.SceneState.from_config(session.runtime.cfg.world)
    scene = scene_service.build_scene(session)
    await session.send(pb.QuitBattleScNotify())
    await session.send(
        pb.EnterSceneByServerScNotify(
            reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
            lineup=lineup_service.build_lineup(session),
            scene=scene,
        )
    )
    session.runtime.logger.info("challenge direct start: returned to open world")



async def on_get_cur_battle(
    session: Session, req: pb.GetCurBattleInfoCsReq
) -> pb.GetCurBattleInfoScRsp:
    active = getattr(session, "battle_info", None)
    if active is None:
        return pb.GetCurBattleInfoScRsp()
    return pb.GetCurBattleInfoScRsp(battle_info=active)


async def on_quit_battle(session: Session, req: pb.QuitBattleCsReq) -> pb.QuitBattleScRsp:
    active = getattr(session, "battle_info", None)
    if active is not None:
        session.runtime.logger.info("quit battle: battle_id=%d", active.battle_id)
    session.battle_info = None
    return pb.QuitBattleScRsp()


async def on_cocoon_sweep(session: Session, req: pb.CocoonSweepCsReq) -> pb.CocoonSweepScRsp:
    # No reward pipeline yet; a successful empty sweep is safer than a soft-lock.
    return pb.CocoonSweepScRsp(cocoon_id=req.cocoon_id)


async def on_start_quick_farm_element(
    session: Session, req: pb.StartQuickFarmElementCsReq
) -> pb.StartQuickFarmElementRsp:
    """历战余响「快速挑战」（指南 → 历战余响）。

    ``PAOFHFLFFHD`` 是 farm element id（真机日志确认，也是 rsp 里回显的那个字段）；
    4.6.54 的 descriptor 把它混淆了，所以只按字段号收发。stage 由
    ``data/battle.json.farm_element_stage_map`` 决定，未登记就落到默认关卡并告警。
    """
    battle = session.runtime.battle
    farm_id = int(req.PAOFHFLFFHD)
    stage_id = battle.resolve_farm_stage(farm_id, req.world_level)
    info = _battle_info_for_stage(session, stage_id, req.world_level)
    session.runtime.logger.info(
        "quick start farm element: farm_id=%d world_level=%d unk=%d -> stage=%d battle_id=%d",
        farm_id,
        req.world_level,
        req.HECCOBFBJFI,
        stage_id,
        info.battle_id,
    )
    return pb.StartQuickFarmElementRsp(
        retcode=0,
        PAOFHFLFFHD=req.PAOFHFLFFHD,
        world_level=req.world_level,
        battle_info=info,
    )


async def on_get_farm_stage_gacha_info(
    session: Session, req: pb.GetFarmStageGachaInfoCsReq
) -> pb.GetFarmStageGachaInfoScRsp:
    # 客户端拿这个列表判断"本周还能不能打"；丹恒回 now+1h、LunarCore 回 INT_MAX，
    # 我们给一周，既不显示"已结束"也不会溢出 int32。
    now = int(time.time())
    return pb.GetFarmStageGachaInfoScRsp(
        farm_stage_gacha_info_list=[
            pb.FarmStageGachaInfo(
                gacha_id=gacha_id,
                begin_time=0,
                end_time=now + FARM_GACHA_WINDOW_SEC,
            )
            for gacha_id in req.farm_stage_gacha_id_list
        ]
    )


async def on_active_farm_element(
    session: Session, req: pb.ActiveFarmElementCsReq
) -> pb.ActiveFarmElementScRsp:
    session.runtime.logger.info(
        "activate farm element: entity=%d world_level=%d unk=%d",
        req.entity_id,
        req.world_level,
        req.HECCOBFBJFI,
    )
    return pb.ActiveFarmElementScRsp(
        entity_id=req.entity_id,
        world_level=req.world_level or session.runtime.cfg.player.world_level,
    )


async def on_deactivate_farm_element(
    session: Session, req: pb.DeactivateFarmElementCsReq
) -> pb.DeactivateFarmElementScRsp:
    return pb.DeactivateFarmElementScRsp(entity_id=req.entity_id)


async def on_farm_element_sweep(
    session: Session, req: pb.FarmElementSweepCsReq
) -> pb.FarmElementSweepScRsp:
    # No drop pipeline (same decision as CocoonSweep): answer success with an empty
    # ItemList instead of soft-locking the sweep button.
    return pb.FarmElementSweepScRsp(PAOFHFLFFHD=req.PAOFHFLFFHD, multiple_drop_data=pb.ItemList())


def register(router: Router) -> None:
    router.add("StartCocoonStageCsReq", on_start_cocoon)
    router.add("QuickStartCocoonStageCsReq", on_quick_start_cocoon)
    router.add("PVEBattleResultCsReq", on_pve_result)
    router.add("GetCurBattleInfoCsReq", on_get_cur_battle)
    router.add("QuitBattleCsReq", on_quit_battle)
    router.add("CocoonSweepCsReq", on_cocoon_sweep)
    router.add("StartQuickFarmElementCsReq", on_start_quick_farm_element)
    router.add("GetFarmStageGachaInfoCsReq", on_get_farm_stage_gacha_info)
    router.add("ActiveFarmElementCsReq", on_active_farm_element)
    router.add("DeactivateFarmElementCsReq", on_deactivate_farm_element)
    router.add("FarmElementSweepCsReq", on_farm_element_sweep)
