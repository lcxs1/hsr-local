"""异相仲裁 (ChallengePeak) handlers — flow and data live in ``services/peak.py``.

Unlike ``StartChallengeScRsp``, ``StartChallengePeakScRsp`` has **no** ``scene``
field, so entering a Peak pushes the whole ``SceneInfo`` with
``EnterSceneByServerScNotify`` (same shape as pearl-sr ``onStartChallengePeak``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.game.services import challenge as challenge_service
from hsr.game.services import lineup as lineup_service
from hsr.game.services import peak as peak_service
from hsr.game.services import scene as scene_service
from hsr.protocol import pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session

NONE_REASON = pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE")


def _active_peak(session: Session):
    state = getattr(session, "challenge", None)
    return state if state is not None and state.mode == peak_service.PEAK else None


async def on_get_peak_data(
    session: Session, req: pb.GetChallengePeakDataCsReq
) -> pb.GetChallengePeakDataScRsp:
    rsp = peak_service.data_response(session)
    session.runtime.logger.info(
        "peak data: %d groups, current=%d",
        len(rsp.challenge_peak_groups),
        rsp.current_peak_group_id,
    )
    return rsp


async def on_get_cur_peak(
    session: Session, req: pb.GetCurChallengePeakCsReq
) -> pb.GetCurChallengePeakScRsp:
    return peak_service.cur_response(session)


async def on_start_peak(
    session: Session, req: pb.StartChallengePeakCsReq
) -> pb.StartChallengePeakScRsp:
    ref = peak_service.resolve(session, req.peak_id)
    if ref is None:
        session.runtime.logger.warning("start peak: unknown id %d", req.peak_id)
        return pb.StartChallengePeakScRsp(retcode=1)
    roster = session.runtime.roster
    avatars = [roster.base_id(int(avatar_id)) for avatar_id in req.peak_avatar_id_list if avatar_id]
    buff_id = int(req.boss_buff_id) or peak_service.default_buff(session, ref)
    # Starting a Peak replaces whatever attempt was running (same as StartChallenge).
    challenge_service.clear(session)
    session.peak_settled = False
    state, scene = peak_service.enter(session, ref, avatars=avatars, buff_id=buff_id)
    session.runtime.logger.info(
        "start peak: peak=%d group=%d boss=%s scene=%d/%d entry=%d group_id=%d"
        " stage=%d team=%s buff=%d",
        ref.peak_id,
        ref.group.id,
        ref.is_boss,
        state.node.plane_id,
        state.node.floor_id,
        state.node.entrance_id,
        state.node.maze_group_id,
        state.stage_id,
        state.current.team,
        buff_id,
    )
    await session.send(
        pb.EnterSceneByServerScNotify(
            reason=NONE_REASON,
            lineup=challenge_service.lineup_info(session),
            scene=scene,
        )
    )
    return pb.StartChallengePeakScRsp(retcode=0)


async def on_restart_peak(
    session: Session, req: pb.ReStartChallengePeakCsReq
) -> pb.ReStartChallengePeakScRsp:
    state = _active_peak(session)
    if state is None:
        return pb.ReStartChallengePeakScRsp(retcode=1)
    scene = challenge_service.rescene(session)
    session.peak_settled = False
    session.runtime.logger.info(
        "restart peak: peak=%d group=%d stage=%d",
        state.peak_id,
        state.peak_group_id,
        state.stage_id,
    )
    await session.send(
        pb.EnterSceneByServerScNotify(
            reason=NONE_REASON,
            lineup=challenge_service.lineup_info(session),
            scene=scene,
        )
    )
    return pb.ReStartChallengePeakScRsp(retcode=0)


async def on_confirm_peak_settle(
    session: Session, req: pb.ConfirmChallengePeakSettleCsReq
) -> pb.ConfirmChallengePeakSettleScRsp:
    state = _active_peak(session)
    peak_id = int(req.peak_id) or int(getattr(state, "peak_id", 0) or 0)
    if state is not None:
        challenge_service.clear(session)
    session.peak_settled = False
    session.runtime.logger.info("confirm peak settle: peak=%d", peak_id)
    return pb.ConfirmChallengePeakSettleScRsp(retcode=0, peak_id=peak_id)


async def on_leave_peak(
    session: Session, req: pb.LeaveChallengePeakCsReq
) -> pb.LeaveChallengePeakScRsp:
    state = _active_peak(session)
    target = None
    lobby_return = getattr(session, "lobby_return", None)
    if lobby_return:
        target = tuple(lobby_return)
    elif state is not None and state.return_entry_id:
        target = (state.return_plane_id, state.return_floor_id, state.return_entry_id)
    challenge_service.clear(session)
    session.peak_settled = False
    session.lobby_return = None
    if target is not None:
        scene = scene_service.enter_scene(
            session, plane_id=target[0], floor_id=target[1], entry_id=target[2]
        )
    else:
        session.scene = scene_service.SceneState.from_config(session.runtime.cfg.world)
        scene = scene_service.build_scene(session)
    lineup = lineup_service.build_lineup(session)
    # 和 LeaveChallenge 一样把额外阵容关掉，否则野外开战还挂 LineupChallenge。
    await session.send(
        pb.ChallengeLineupNotify(
            extra_lineup_type=pb.ExtraLineupType.ExtraLineupType_LineupNone
        )
    )
    await session.send(pb.SyncLineupNotify(lineup=lineup))
    await session.send(pb.QuitBattleScNotify())
    await session.send(
        pb.EnterSceneByServerScNotify(reason=NONE_REASON, lineup=lineup, scene=scene)
    )
    session.runtime.logger.info("leave peak: back to scene %d/%d", scene.floor_id, scene.entry_id)
    return pb.LeaveChallengePeakScRsp(retcode=0)


async def on_take_peak_reward(
    session: Session, req: pb.TakeChallengePeakRewardCsReq
) -> pb.TakeChallengePeakRewardScRsp:
    group_id = int(req.peak_group_id)
    # 没有奖励管线（和凝滞虚影/背包结算一致），只把"领过的档位"记进存档。
    taken = session.runtime.save.take_peak_rewards(
        group_id, list(req.normal_reward_id_list)
    )
    session.runtime.logger.info("peak reward: group=%d claimed=%s", group_id, taken)
    group = session.runtime.endgame.peak_group(group_id)
    if group is not None:
        await session.send(
            pb.ChallengePeakGroupDataUpdateScNotify(
                challenge_peak_group=peak_service.group_proto(session, group)
            )
        )
    return pb.TakeChallengePeakRewardScRsp(retcode=0, peak_group_id=group_id)


async def on_set_hard_mode(
    session: Session, req: pb.SetChallengePeakBossHardModeCsReq
) -> pb.SetChallengePeakBossHardModeScRsp:
    group_id = int(req.peak_group_id)
    hard = bool(req.is_hard_mode)
    session.runtime.save.set_peak_hard_mode(group_id, hard)
    state = _active_peak(session)
    if state is not None and int(state.peak_group_id) == group_id:
        state.peak_hard = hard
    session.runtime.logger.info("peak hard mode: group=%d hard=%s", group_id, hard)
    return pb.SetChallengePeakBossHardModeScRsp(
        retcode=0, peak_group_id=group_id, is_hard_mode=hard
    )


async def on_set_mob_lineup(
    session: Session, req: pb.SetChallengePeakMobLineupAvatarCsReq
) -> pb.SetChallengePeakMobLineupAvatarScRsp:
    group_id = int(req.peak_group_id)
    session.runtime.logger.info(
        "peak lineup: group=%d peaks=%s",
        group_id,
        [(int(entry.peak_id), [int(a) for a in entry.peak_avatar_id_list]) for entry in req.lineup_list],
    )
    group = session.runtime.endgame.peak_group(group_id)
    if group is not None:
        await session.send(
            pb.ChallengePeakGroupDataUpdateScNotify(
                challenge_peak_group=peak_service.group_proto(session, group)
            )
        )
    return pb.SetChallengePeakMobLineupAvatarScRsp(retcode=0)


def register(router: Router) -> None:
    router.add("GetChallengePeakDataCsReq", on_get_peak_data)
    router.add("GetCurChallengePeakCsReq", on_get_cur_peak)
    router.add("StartChallengePeakCsReq", on_start_peak)
    router.add("ReStartChallengePeakCsReq", on_restart_peak)
    router.add("ConfirmChallengePeakSettleCsReq", on_confirm_peak_settle)
    router.add("LeaveChallengePeakCsReq", on_leave_peak)
    router.add("TakeChallengePeakRewardCsReq", on_take_peak_reward)
    router.add("SetChallengePeakBossHardModeCsReq", on_set_hard_mode)
    router.add("SetChallengePeakMobLineupAvatarCsReq", on_set_mob_lineup)
