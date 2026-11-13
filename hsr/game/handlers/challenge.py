"""Challenge handlers: 混沌回忆 (MOC) / 虚构叙事 (PF) / 末日幻影 (AS).

Protocol flow (verified against pearl-sr on 4.6.54):

    GetChallengeCsReq            -> list of 843 challenges
    StartChallengeCsReq(id)      -> scene + cur_challenge + lineup, session enters
    (client walks to the NPC and casts a skill -> SceneCastSkillScRsp battle)
    PVEBattleResultCsReq         -> battle settles; challenge stays until leave
    GetCurChallengeCsReq         -> re-read the active challenge
    LeaveChallengeCsReq          -> back to the open world + QuitBattle

Peak (异相仲裁) / Tierce are separate command families and not wired yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.game.services import challenge as challenge_service
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.protocol import pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session

MODE_LABEL = {"MOC": "混沌回忆", "PF": "虚构叙事", "AS": "末日幻影"}

# pearl-sr 对全部挑战回 star=7 / taken_reward=42；客户端只把它们当"已解锁/已领奖"。
CHALLENGE_STAR = 7
CHALLENGE_TAKEN_REWARD = 42
PF_SCORE_ID = challenge_service.PF_SCORE_ID


def _challenge_list(session: Session) -> list[pb.Challenge]:
    """All challenges.  ``unlock_all`` (A 口径) marks every floor as cleared so
    the client lets the player pick any of them; a real history replaces this in
    M3 (see docs/design/04)."""
    unlock_all = bool(session.runtime.cfg.endgame.unlock_all)
    star = CHALLENGE_STAR if unlock_all else 0
    taken = CHALLENGE_TAKEN_REWARD if unlock_all else 0
    items: list[pb.Challenge] = []
    for challenge in sorted(session.runtime.endgame.challenges.values(), key=lambda c: c.id):
        score_id = PF_SCORE_ID if challenge.mode == "PF" else 0
        items.append(
            pb.Challenge(
                challenge_id=challenge.id,
                star=star,
                taken_reward=taken,
                score_id=score_id,
                score_two=score_id,
            )
        )
    return items


def _request_buffs(challenge, req: pb.StartChallengeCsReq, second: bool) -> list[int]:
    """Pick the player-selected buff + the challenge's own MazeBuffID."""
    picked = 0
    stage_info = req.stage_info
    if challenge.mode == "PF" and stage_info.HasField("story_info"):
        picked = stage_info.story_info.buff_two if second else stage_info.story_info.buff_one
    elif challenge.mode == "AS" and stage_info.HasField("boss_info"):
        picked = stage_info.boss_info.buff_two if second else stage_info.boss_info.buff_one
    return [buff for buff in (int(picked), challenge.buff_id) if buff]


def _group_list(session: Session) -> list[pb.ChallengeGroup]:
    """One ``ChallengeGroup`` per challenge group (MOC/PF/AS).

    4.6.54 的忘却之庭界面按"关卡组"出卡片，组信息来自这里而不是 ``challenge_list``；
    只发 ``challenge_list`` 时界面只剩零星两个默认组（2026-11-11 真机：混沌回忆的
    关卡组页面整个缺失）。``taken_stars_count_reward`` 置 0 = 未领取星数奖励，
    与 ``Challenge.taken_reward`` 的"已解锁"语义互不冲突。

    ``[endgame] group_list = "rewards"`` 是 M0 探针开关：DanhengServer 只发**领过奖**
    的组（新号等于空列表），用来对比卡片是不是被这 109 条"假组"顶掉了。
    """
    if session.runtime.cfg.endgame.group_list == "rewards":
        return []
    groups: dict[int, pb.ChallengeGroup] = {}
    for challenge in session.runtime.endgame.challenges.values():
        if challenge.group_id and challenge.group_id not in groups:
            groups[challenge.group_id] = pb.ChallengeGroup(
                group_id=challenge.group_id,
                taken_stars_count_reward=0,
            )
    return [groups[group_id] for group_id in sorted(groups)]


def _max_level_list(session: Session) -> list[pb.ChallengeHistoryMaxLevel]:
    """M0 探针：Himeko-Nova-SR 每个挑战发一条 ``ChallengeHistoryMaxLevel``。

    字段语义未确认（看着像“该模式已通关的最高层”）。这里按组的最大层发：
    回忆组 100 = 15，混沌回忆 12 层组 = 12，PF/AS = 4。用来验证“混沌回忆被锁”
    是不是缺这个列表（界面上的 `15/15` 提示正对得上）。
    ``[endgame] send_max_level = false`` 时保持现状（一条都不发）。
    """
    if not session.runtime.cfg.endgame.send_max_level:
        return []
    floors_by_group: dict[int, int] = {}
    for challenge in session.runtime.endgame.challenges.values():
        if not challenge.group_id:
            continue
        floors_by_group[challenge.group_id] = max(
            floors_by_group.get(challenge.group_id, 0), int(challenge.floor or 0)
        )
    # groups without a Floor column (100 / 900) are counted by size instead
    sizes: dict[int, int] = {}
    for challenge in session.runtime.endgame.challenges.values():
        sizes[challenge.group_id] = sizes.get(challenge.group_id, 0) + 1
    out: list[pb.ChallengeHistoryMaxLevel] = []
    for challenge in sorted(session.runtime.endgame.challenges.values(), key=lambda c: c.id):
        level = floors_by_group.get(challenge.group_id, 0) or sizes.get(challenge.group_id, 0)
        if challenge.mode == "MOC":
            out.append(
                pb.ChallengeHistoryMaxLevel(level=level or 12, reward_display_type=101212)
            )
        else:
            out.append(pb.ChallengeHistoryMaxLevel(level=4, reward_display_type=101404))
    return out


def _active_season(session: Session) -> tuple[int, str]:
    """``(group_id, label)`` of the 混沌回忆 rotation the server is serving."""
    cfg_group = int(session.runtime.cfg.endgame.moc_group_id or 0)
    endgame = session.runtime.endgame
    group_id = cfg_group or endgame.active_group("MOC")
    season = endgame.season(group_id) if group_id else None
    if season is None:
        return group_id, f"group={group_id}" if group_id else "group=unknown"
    return group_id, (
        f"group={group_id} ({season.name}) "
        f"{season.begin:%Y-%m-%d}..{season.end:%Y-%m-%d}"
        if season.begin and season.end
        else f"group={group_id} ({season.name})"
    )


async def on_get_challenge(
    session: Session, req: pb.GetChallengeCsReq
) -> pb.GetChallengeScRsp:
    rsp = pb.GetChallengeScRsp(
        retcode=0,
        challenge_list=_challenge_list(session),
        challenge_group_list=_group_list(session),
        max_level_list=_max_level_list(session),
    )
    _, season = _active_season(session)
    session.runtime.logger.info(
        "challenge list: %d entries / %d groups / %d max-level; active MOC season %s",
        len(rsp.challenge_list),
        len(rsp.challenge_group_list),
        len(rsp.max_level_list),
        season,
    )
    return rsp


def _monster_entity_id(scene: pb.SceneInfo, group_id: int) -> int:
    """Entity id of the trigger NPC inside one arena group of a ``SceneInfo``."""
    for group in scene.entity_group_list:
        if int(group.group_id) != int(group_id):
            continue
        for entity in group.entity_list:
            if entity.HasField("npc_monster"):
                return int(entity.entity_id)
    return 0


def _group_lineup(session: Session) -> pb.ChallengeLineupList:
    """Previous-clear lineup shown in the challenge group panel."""
    runtime = session.runtime
    roster = runtime.roster
    avatars: list[pb.ChallengeAvatarInfo] = []
    for index, base in enumerate(slot for slot in session.lineup.slots if slot):
        form = roster.preferred_form(
            base,
            tb_path=getattr(session, "tb_multi_path", None),
            march_path=getattr(session, "march_multi_path", None),
        )
        if form is None:
            continue
        avatars.append(
            pb.ChallengeAvatarInfo(
                avatar_type=pb.AvatarType.AvatarType_AvatarFormalType,
                id=form.avatar_id,
                level=form.level,
                index=index,
            )
        )
    return pb.ChallengeLineupList(avatar_list=avatars)


async def on_challenge_group_statistics(
    session: Session, req: pb.GetChallengeGroupStatisticsCsReq
) -> pb.GetChallengeGroupStatisticsScRsp:
    """Group panel statistics (last clear, lineup, buffs).

    The 4.6.54 client dereferences the nested statistics object in
    ``ChallengeGroupStatisticsData.Create``; returning only ``retcode`` +
    ``group_id`` throws a NullReferenceException inside the packet handler and
    corrupts the follow-up guide teleport (seen on 2026-11-10).  So always fill
    the matching oneof branch with a level/lineup.
    """
    runtime = session.runtime
    group_id = int(req.group_id)
    entries = [c for c in runtime.endgame.challenges.values() if c.group_id == group_id]
    lineup = _group_lineup(session)
    if entries:
        mode = entries[0].mode
        level = max(int(entry.floor) for entry in entries) or 1
        buff = entries[0].buff_id
        score_id = PF_SCORE_ID if mode == "PF" else 0
    else:
        mode, level, buff, score_id = "MOC", 1, 0, 0

    rsp = pb.GetChallengeGroupStatisticsScRsp(retcode=0, group_id=group_id)
    if mode == "PF":
        rsp.challenge_story.CopyFrom(
            pb.ChallengeStoryStatistics(
                record_id=0,
                PPBHLLOJNEK=pb.EIKPHEMHIOH(
                    level=level,
                    score_id=score_id,
                    buff_one=buff,
                    buff_two=buff,
                    lineup_list=[lineup],
                ),
            )
        )
    elif mode == "AS":
        rsp.challenge_boss.CopyFrom(
            pb.ChallengeBossStatistics(
                record_id=0,
                PPBHLLOJNEK=pb.AANLJBLOOFO(
                    level=level,
                    score_id=score_id,
                    buff_one=buff,
                    buff_two=buff,
                    lineup_list=[lineup],
                ),
            )
        )
    else:
        rsp.challenge_default.CopyFrom(
            pb.ChallengeStatistics(
                record_id=0,
                PPBHLLOJNEK=pb.ADKJKMKBFDC(
                    level=level,
                    round_count=0,
                    lineup_list=[lineup],
                ),
            )
        )
    runtime.logger.debug(
        "challenge group statistics: group=%d mode=%s level=%d", group_id, mode, level
    )
    return rsp


async def on_start_challenge(
    session: Session, req: pb.StartChallengeCsReq
) -> pb.StartChallengeScRsp:
    runtime = session.runtime
    challenge = runtime.endgame.challenge(req.challenge_id)
    if challenge is None:
        runtime.logger.warning("start challenge: unknown id %d", req.challenge_id)
        return pb.StartChallengeScRsp(retcode=1)

    first_ids = [int(avatar.id) for avatar in req.avatar_lineup_first]
    first_ids = first_ids or [int(avatar_id) for avatar_id in req.first_lineup]
    second_ids = [int(avatar.id) for avatar in req.avatar_lineup_second]
    second_ids = second_ids or [int(avatar_id) for avatar_id in req.second_lineup]
    active = getattr(session, "challenge", None)
    if (
        not first_ids
        and second_ids
        and active is not None
        and active.challenge_id == challenge.id
        and active.stage == 1
        and active.has_next
    ):
        # 客户端在"前往下半"里重发 StartChallengeCsReq，只带 avatar_lineup_second
        # （pearl-sr `onStartChallenge` 同样的分支）。这是同一局的第二阶段，不能
        # 当成新挑战清状态，否则会丢掉返回场景与上半进度。
        active.phases[1].team = [
            runtime.roster.base_id(avatar_id) for avatar_id in second_ids if avatar_id
        ]
        scene = challenge_service.enter_second_half(session)
        runtime.logger.info(
            "start challenge (second half): id=%d team=%s",
            challenge.id,
            active.phases[1].team,
        )
        return pb.StartChallengeScRsp(
            retcode=0,
            scene=scene,
            cur_challenge=challenge_service.cur_challenge(session),
            lineup_list=challenge_service.lineups(session),
        )

    second = bool(second_ids) and not first_ids
    avatars = second_ids if second else first_ids
    if not avatars:
        avatars = [slot for slot in session.lineup.slots if slot]
    avatars = [runtime.roster.base_id(avatar_id) for avatar_id in avatars if avatar_id]
    first_avatars = [
        runtime.roster.base_id(avatar_id) for avatar_id in first_ids if avatar_id
    ]
    second_avatars = [
        runtime.roster.base_id(avatar_id) for avatar_id in second_ids if avatar_id
    ]

    buff_ids = _request_buffs(challenge, req, second)
    # Starting a new challenge replaces any previous attempt/battle.
    challenge_service.clear(session)
    state, scene = challenge_service.enter(
        session,
        challenge.id,
        second=second,
        buff_ids=buff_ids,
        avatars=avatars,
        first_avatars=first_avatars,
        second_avatars=second_avatars,
    )
    runtime.logger.info(
        "start challenge: id=%d (%s) phase=%d/%d scene=%d/%d entry=%d stage=%d"
        " teams=%s buffs=%s",
        challenge.id,
        MODE_LABEL.get(challenge.mode, challenge.mode),
        state.index + 1,
        len(state.phases),
        state.node.plane_id,
        state.node.floor_id,
        state.node.entrance_id,
        state.stage_id,
        [phase.team for phase in state.phases],
        buff_ids,
    )

    return pb.StartChallengeScRsp(
        retcode=0,
        scene=scene,
        cur_challenge=challenge_service.cur_challenge(session),
        lineup_list=challenge_service.lineups(session),
    )


async def on_start_challenge_tierce(
    session: Session, req: pb.StartChallengeTierceCsReq
) -> pb.StartChallengeTierceScRsp:
    """4.7.0「星启模式」开打：一层 3 个节点、一次下发 3 套队伍。

    混沌回忆第 12 层的编队界面走的就是这条（4.7.0 真机：点「混沌回忆」按钮
    客户端发 ``StartChallengeTierceCsReq``，服务端没实现 → 按钮没反应）。
    """
    runtime = session.runtime
    challenge = runtime.endgame.challenge(req.challenge_id)
    tierce_stage = runtime.endgame.tierce_stage(req.challenge_id)
    if challenge is None and tierce_stage is None:
        runtime.logger.warning(
            "start challenge tierce: unknown id %d", req.challenge_id
        )
        return pb.StartChallengeTierceScRsp(retcode=1)
    teams = [
        [
            runtime.roster.base_id(int(avatar.id))
            for avatar in entry.tierce_avatar_id_list
            if avatar.id
        ]
        for entry in req.lineup_tierce_list
    ]
    buff_ids = [int(entry.buff_id) for entry in req.lineup_tierce_list if entry.buff_id]
    challenge_service.clear(session)
    state, scene = challenge_service.enter_tierce(
        session,
        req.challenge_id,
        stage_index=int(req.stage_index),
        teams=teams,
        buff_ids=buff_ids or None,
    )
    runtime.logger.info(
        "start challenge tierce: id=%d (%s) stage_index=%d teams=%s buffs=%s",
        req.challenge_id,
        MODE_LABEL.get(state.mode, state.mode),
        state.stage_index,
        teams,
        state.buff_ids,
    )
    return pb.StartChallengeTierceScRsp(
        retcode=0,
        scene=scene,
        cur_tierce_challenge=pb.CurTierceChallenge(
            challenge_id=req.challenge_id,
            stage_index=state.stage_index,
            lineup_list=challenge_service.lineups(session),
        ),
    )


async def on_get_cur_challenge(
    session: Session, req: pb.GetCurChallengeCsReq
) -> pb.GetCurChallengeScRsp:
    state = getattr(session, "challenge", None)
    if state is None or state.mode == challenge_service.PEAK:
        # 异相仲裁有自己的 GetCurChallengePeak；别把 Peak 当成普通挑战回包。
        return pb.GetCurChallengeScRsp(retcode=0)
    return pb.GetCurChallengeScRsp(
        retcode=0,
        cur_challenge=challenge_service.cur_challenge(session),
        lineup_list=challenge_service.lineups(session),
    )


async def on_leave_challenge(
    session: Session, req: pb.LeaveChallengeCsReq
) -> pb.LeaveChallengeScRsp:
    runtime = session.runtime
    active = getattr(session, "challenge", None)
    if active is not None and active.mode == challenge_service.PEAK:
        # 客户端用挑战的离开命令收尾一次 Peak 时，走 Peak 的返回场景逻辑。
        from hsr.game.handlers import peak as peak_handlers

        rsp = await peak_handlers.on_leave_peak(session, pb.LeaveChallengePeakCsReq())
        return pb.LeaveChallengeScRsp(retcode=rsp.retcode)
    if active is not None:
        runtime.logger.info(
            "leave challenge: id=%d mode=%s phase=%d/%d",
            active.challenge_id,
            active.mode,
            active.index + 1,
            len(active.phases),
        )
    target = None
    lobby_return = getattr(session, "lobby_return", None)
    if lobby_return:
        # 进来打挑战前玩家是被"指导传送"送到大厅（列车）的；打完后直接回世界，
        # 别把玩家留在车厢里（2026-11-11 真机：退出后停在列车、传送按钮又没反应）。
        target = tuple(lobby_return)
    elif active is not None and active.return_entry_id:
        target = (
            active.return_plane_id,
            active.return_floor_id,
            active.return_entry_id,
        )
    challenge_service.clear(session)
    session.lobby_return = None

    if target is not None:
        # Return to the scene the player entered the challenge from (the
        # 忘却之庭 lobby when started via the guide 传送), not the open world.
        scene = scene_service.enter_scene(
            session,
            plane_id=target[0],
            floor_id=target[1],
            entry_id=target[2],
        )
    else:
        session.scene = scene_service.SceneState.from_config(runtime.cfg.world)
        scene = scene_service.build_scene(session)
    lineup = lineup_service.build_lineup(session)
    # 退出挑战必须把额外阵容关掉：参考实现（Hyacine 4.0 HandlerLeaveChallenge）
    # 发 ChallengeLineupNotify(LineupNone) 再同步普通阵容。少了这一步，客户端
    # 之后还以 LineupChallenge2 的身份开野外战斗，换队/开战会错乱。
    await session.send(
        pb.ChallengeLineupNotify(
            extra_lineup_type=pb.ExtraLineupType.ExtraLineupType_LineupNone
        )
    )
    await session.send(pb.SyncLineupNotify(lineup=lineup))
    await session.send(pb.QuitBattleScNotify())
    await session.send(
        pb.EnterSceneByServerScNotify(
            reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
            lineup=lineup,
            scene=scene,
        )
    )
    return pb.LeaveChallengeScRsp(retcode=0)


async def on_enter_next_phase(
    session: Session, req: pb.EnterChallengeNextPhaseCsReq
) -> pb.EnterChallengeNextPhaseScRsp:
    """Client-driven phase switch (``EnterChallengeNextPhaseCsReq``).

    The message exists in the 4.6.54/4.7.0 table and the response carries a
    ``SceneInfo``; 混沌回忆/虚构叙事 switch phases server-side (reference:
    HyacineCore replies ``RetChallengeNotDoing``), so here we only re-send the
    **current** phase's scene for clients that ask.  Never advances on its own:
    a stray/stale request must not skip a half.
    """
    state = getattr(session, "challenge", None)
    if state is None:
        return pb.EnterChallengeNextPhaseScRsp(retcode=1)
    scene = scene_service.build_challenge_scene(
        session,
        plane_id=state.node.plane_id,
        floor_id=state.node.floor_id,
        entry_id=state.node.entrance_id,
        world_id=state.node.world_id,
        group_id=state.node.maze_group_id,
        monster_id=state.node.monster_id,
        stage_id=state.stage_id,
        avatars=state.avatars or None,
    )[0]
    session.runtime.logger.info(
        "enter challenge next phase: id=%d phase=%d (re-sending current scene)",
        state.challenge_id,
        state.index + 1,
    )
    return pb.EnterChallengeNextPhaseScRsp(retcode=0, scene=scene)


async def on_restart_challenge_phase(
    session: Session, req: pb.RestartChallengePhaseCsReq
) -> pb.RestartChallengePhaseScRsp:
    """Restart the current phase (``RestartChallengePhaseCsReq``).

    Used by the client's "重打本半场" button.  Rebuild the current phase scene
    and drop the battle cache; the challenge itself keeps its progress.
    """
    state = getattr(session, "challenge", None)
    if state is None:
        return pb.RestartChallengePhaseScRsp(retcode=1)
    state.battle_id = 0
    state.battle_ids.clear()
    session.battle_info = None
    # 重打本半场：轮次预算与阵亡数都回退到本半场开始时的值（参考实现“重打”语义）。
    if state.phase_start_rounds:
        state.rounds_left = int(state.phase_start_rounds)
    state.dead_avatars = int(state.phase_start_dead_avatars)
    scene = scene_service.build_challenge_scene(
        session,
        plane_id=state.node.plane_id,
        floor_id=state.node.floor_id,
        entry_id=state.node.entrance_id,
        world_id=state.node.world_id,
        group_id=state.node.maze_group_id,
        monster_id=state.node.monster_id,
        stage_id=state.stage_id,
        avatars=state.avatars or None,
    )[0]
    state.monster_entity_id = _monster_entity_id(scene, state.node.maze_group_id)
    session.runtime.logger.info(
        "restart challenge phase: id=%d phase=%d group=%d",
        state.challenge_id,
        state.index + 1,
        state.node.maze_group_id,
    )
    return pb.RestartChallengePhaseScRsp(retcode=0, scene=scene)


async def on_take_cumulative_reward(
    session: Session, req: pb.TakeChallengeCumulativeRewardCsReq
) -> pb.TakeChallengeCumulativeRewardScRsp:
    # No reward pipeline (same decision as the cocoon/backpack drops). Ack so the
    # "claim" button does not soft-lock; the star display already comes from
    # GetChallengeScRsp.
    return pb.TakeChallengeCumulativeRewardScRsp(retcode=0, group_id=req.group_id)


async def on_leave_challenge_tierce(
    session: Session, req: pb.LeaveChallengeTierceCsReq
) -> pb.LeaveChallengeTierceScRsp:
    """星启模式退出：和普通挑战一样回世界/大厅，只换回包类型。"""
    rsp = await on_leave_challenge(session, pb.LeaveChallengeCsReq())
    return pb.LeaveChallengeTierceScRsp(retcode=rsp.retcode)


def register(router: Router) -> None:
    router.add("GetChallengeCsReq", on_get_challenge)
    router.add("GetChallengeGroupStatisticsCsReq", on_challenge_group_statistics)
    router.add("StartChallengeCsReq", on_start_challenge)
    router.add("StartChallengeTierceCsReq", on_start_challenge_tierce)
    router.add("GetCurChallengeCsReq", on_get_cur_challenge)
    router.add("LeaveChallengeCsReq", on_leave_challenge)
    router.add("LeaveChallengeTierceCsReq", on_leave_challenge_tierce)
    router.add("EnterChallengeNextPhaseCsReq", on_enter_next_phase)
    router.add("RestartChallengePhaseCsReq", on_restart_challenge_phase)
    router.add("TakeChallengeCumulativeRewardCsReq", on_take_cumulative_reward)

