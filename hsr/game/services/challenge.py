"""Challenge (MoC / PF / AS) session state, scene entry and battle payloads.

The client's endgame flow is *not* "start command -> battle": it enters a
challenge scene with ``StartChallengeScRsp.scene``, walks up to the NPC monster
and casts a skill at it.  That cast (``SceneCastSkillCsReq``) is where the
server must hand over the real ``SceneBattleInfo``; it is built here from
``EndgameData`` + the session lineup.

S2 scope: state + scene entry + battle payload.  ``StartChallenge`` itself
(and the settle/leave chain) is wired in later steps, so nothing calls
:func:`enter` yet.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from hsr.game.services import battle as battle_service
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.game.services.endgame import AS, PF, ChallengeNode
from hsr.protocol.gen import StarRail_pb2 as pb

# 客户端认的触发战斗类型（pearl-sr 对所有挑战目标都用 TRIGGER_BATTLE）。
TRIGGER_BATTLE = pb.MonsterBattleType.Value("MONSTER_BATTLE_TYPE_TRIGGER_BATTLE")
ALL_WAVES = 0xFFFFFFFF

# 虚构叙事/末日幻影的关卡目标（sr-recover-new battle.py 的取值）：
#   PF slot1 = 10003（stage >= 30309011）否则 10002；slot5 = 2001/2002
#   AS slot1 = 90005
PF_TARGET_ID = 10002
PF_TARGET_ID_HIGH = 10003
AS_TARGET_ID = 90005
PF_HIGH_STAGE = 30309011
# 虚构叙事的积分档 id（pearl-sr 对 20000 < id < 30000 的挑战回 40000）。
PF_SCORE_ID = 40000
FORMAL = pb.AvatarType.AvatarType_AvatarFormalType


@dataclass
class ChallengeState:
    """One active challenge attempt (owned by the session)."""

    challenge_id: int = 0
    mode: str = ""
    node: ChallengeNode = field(default_factory=ChallengeNode)
    second: bool = False
    # True = 由 ``/debug/challenge`` 直接把玩家丢进 arena（不经忘却之庭大厅）。
    # 结算后要自己把玩家送回世界场景，因为没有 LeaveChallenge 可走。
    direct: bool = False
    buff_ids: list[int] = field(default_factory=list)
    # Base avatar ids chosen for this attempt (empty = use the current lineup).
    # 上半与下半是两套队伍：客户端在点水晶后的队伍配置界面里同时下发
    # ``avatar_lineup_first`` / ``avatar_lineup_second``（4.6.54 实测，
    # StartChallengeCsReq 58 字节）。只存第一套会让下半继续用上半的队伍
    # （2026-11-11 真机）。``avatars`` 保留为"当前这一半的队伍"。
    avatars: list[int] = field(default_factory=list)
    first_avatars: list[int] = field(default_factory=list)
    second_avatars: list[int] = field(default_factory=list)

    stage_id: int = 0
    monster_entity_id: int = 0
    battle_id: int = 0
    # Every battle id handed to the client for this attempt.  The client settles
    # the id it acted on, which is not always the newest one (see ``battle_info``),
    # so the result handler must accept any id from this set.
    battle_ids: set[int] = field(default_factory=set)
    # 1 = 上半/第一节点, 2 = 下半/第二节点 (only when the config has a node2).
    stage: int = 1
    # Scene to return to on LeaveChallenge (the lobby the player came from).
    return_plane_id: int = 0
    return_floor_id: int = 0
    return_entry_id: int = 0


def enter(
    session,
    challenge_id: int,
    *,
    second: bool = False,
    buff_ids: list[int] | None = None,
    avatars: list[int] | None = None,
    first_avatars: list[int] | None = None,
    second_avatars: list[int] | None = None,
) -> tuple[ChallengeState, pb.SceneInfo]:
    """Resolve a challenge and switch the session into its scene.

    Returns ``(state, scene)``; ``scene`` is what ``StartChallengeScRsp.scene``
    will carry in S4.  Raises ``ValueError`` for an unknown challenge id.

    ``first_avatars``/``second_avatars`` are both lineups the client configured
    in the pre-battle 队伍配置 界面; ``avatars`` is the team of the node being
    entered (kept for callers that only pass one).
    """
    runtime = session.runtime
    challenge = runtime.endgame.challenge(challenge_id)
    if challenge is None:
        raise ValueError(f"unknown challenge id {challenge_id}")
    node = challenge.node(second)
    chosen = [int(avatar_id) for avatar_id in (avatars or []) if avatar_id]
    first = [int(a) for a in (first_avatars or []) if a] or ([] if second else list(chosen))
    second_list = [int(a) for a in (second_avatars or []) if a] or (list(chosen) if second else [])
    if not chosen:
        chosen = second_list if second else first
    previous = (session.scene.plane_id, session.scene.floor_id, session.scene.entry_id)
    scene, monster_entity_id = scene_service.build_challenge_scene(
        session,
        plane_id=node.plane_id,
        floor_id=node.floor_id,
        entry_id=node.entrance_id,
        world_id=node.world_id,
        group_id=node.maze_group_id,
        monster_id=node.monster_id,
        stage_id=node.stage_id,
        avatars=chosen or None,
    )
    state = ChallengeState(
        challenge_id=challenge.id,
        mode=challenge.mode,
        node=node,
        second=second,
        buff_ids=list(
            buff_ids
            if buff_ids is not None
            else ([challenge.buff_id] if challenge.buff_id else [])
        ),
        avatars=chosen,
        first_avatars=first,
        second_avatars=second_list,
        stage_id=node.stage_id,
        monster_entity_id=monster_entity_id,
        return_plane_id=int(previous[0]),
        return_floor_id=int(previous[1]),
        return_entry_id=int(previous[2]),
    )
    session.challenge = state
    runtime.logger.info(
        "challenge entered: id=%d mode=%s scene=%d/%d entry=%d stage=%d monster=%d"
        " first=%s second=%s",
        state.challenge_id,
        state.mode,
        node.plane_id,
        node.floor_id,
        node.entrance_id,
        state.stage_id,
        state.monster_entity_id,
        first,
        second_list,
    )
    return state, scene



def clear(session) -> None:
    """Drop the active challenge (battle included)."""
    session.challenge = None
    session.battle_info = None


def lineup_info(session, second: bool | None = None) -> pb.LineupInfo:
    """Challenge ``LineupInfo`` (extra_lineup_type=Challenge/Challenge2).

    ``second`` picks the 下半 team (``state.second_avatars``); default follows the
    half currently being played.  Falls back to the open-world lineup when the
    client sent no ids.
    """
    state = session.challenge
    runtime = session.runtime
    roster = runtime.roster
    if second is None:
        second = bool(state is not None and state.second)
    avatars: list[int] = []
    if state is not None:
        avatars = list(state.second_avatars if second else state.first_avatars)
        if not avatars:
            avatars = list(state.avatars)
    if not avatars:
        avatars = [slot for slot in session.lineup.slots if slot]
    extra_lineup_type = (
        pb.ExtraLineupType.ExtraLineupType_LineupChallenge2
        if second
        else pb.ExtraLineupType.ExtraLineupType_LineupChallenge
    )
    lineup = pb.LineupInfo(
        name="",
        # DanhengServer/`LineupManager.GetExtraLineup` key extra lineups at
        # ``type + 10`` (Challenge=11, Challenge2=13); index 0 for both made the
        # client treat the two teams as the same lineup.
        index=int(extra_lineup_type) + 10,
        leader_slot=0,
        plane_id=0,
        is_virtual=False,
        extra_lineup_type=extra_lineup_type,
        mp=lineup_service.current_mp(session),
        max_mp=lineup_service.max_mp(session),
    )
    team_size = getattr(runtime.cfg.world, "team_size", 4)
    for slot, avatar_id in enumerate(avatars[:team_size]):
        base = roster.base_id(int(avatar_id))
        if not base:
            continue
        lineup.avatar_list.append(
            pb.LineupAvatar(
                avatar_type=FORMAL,
                sp_bar=pb.SpBarInfo(cur_sp=10000, max_sp=10000),
                hp=lineup_service.FULL_HP,
                satiety=100,
                id=roster.lineup_id(
                    base,
                    tb_path=getattr(session, "tb_multi_path", None),
                    march_path=getattr(session, "march_multi_path", None),
                ),
                slot=slot,
            )
        )
    return lineup


def lineups(session) -> list[pb.LineupInfo]:
    """Lineup list for Start/GetCur responses.

    ``StageNum >= 2`` carries **both** teams: DanhengServer and LunarCore do the
    same for the same field, and with only the current team the client has no
    avatars to render the other half — the 4.6.54 symptom is a black battle
    screen that still simulates and settles by itself.
    """
    state = getattr(session, "challenge", None)
    first = lineup_info(session, second=False)
    if state is None:
        return [first]
    challenge = session.runtime.endgame.challenge(state.challenge_id)
    if challenge is None or challenge.node2 is None:
        return [first]
    return [first, lineup_info(session, second=True)]


def cur_challenge(session, *, finished: bool | None = None) -> pb.CurChallenge:
    """``CurChallenge`` for Start/GetCur/Settle responses.

    ``finished`` overrides the status (settle: True=finish / False=failed).
    """
    state = session.challenge
    if finished is None:
        status = pb.ChallengeStatus.CHALLENGE_DOING
    else:
        status = (
            pb.ChallengeStatus.CHALLENGE_FINISH
            if finished
            else pb.ChallengeStatus.CHALLENGE_FAILED
        )
    lineup = lineup_info(session)
    cur = pb.CurChallenge(
        challenge_id=state.challenge_id,
        status=status,
        extra_lineup_type=lineup.extra_lineup_type,
        score_id=PF_SCORE_ID if state.mode == PF else 0,
        score_two=0,
    )
    if state.mode == PF:
        cur.stage_info.cur_story_buffs.CopyFrom(
            pb.ChallengeStoryBuffList(buff_list=state.buff_ids)
        )
    elif state.mode == AS:
        cur.stage_info.cur_boss_buffs.CopyFrom(
            pb.ChallengeBossBuffList(
                buff_list=state.buff_ids,
                challenge_boss_const=1,
            )
        )
    return cur


def settle_notify(session, *, win: bool, stars: int = 3) -> pb.ChallengeSettleNotify:
    """``ChallengeSettleNotify`` which makes the client leave the arena.

    Without it the client treats the fight as "still running": it drops the
    player back into the challenge scene with the monster respawned and the loop
    never ends (seen on 4.6.54, 2026-11-10).  Star/score are local-sandbox
    values: 3 stars on a win, 0 on a loss; no reward items.
    """
    state = session.challenge
    return pb.ChallengeSettleNotify(
        challenge_id=state.challenge_id,
        is_win=win,
        star=max(0, int(stars)) if win else 0,
        challenge_score=0,
        score_two=0,
        cur_challenge=cur_challenge(session, finished=win),
    )


def can_advance(session) -> bool:
    """True when the attempt is still on 上半 and the config has a 下半.

    MOC/PF/AS rows with ``StageNum=2`` carry a second node (its own entrance,
    maze group, monster and stage); clearing 上半 must load that node instead of
    settling the run, otherwise the client shows the first-half result and then
    throws the player out (guide/03 §3.26).
    """
    state = getattr(session, "challenge", None)
    if state is None or state.second:
        return False
    challenge = session.runtime.endgame.challenge(state.challenge_id)
    return challenge is not None and challenge.node2 is not None


def advance_scene(session) -> pb.SceneInfo:
    """Rebuild the arena as node2 and return the new ``SceneInfo`` (scene mode).

    Used when ``[endgame] advance = "scene"``: the client is told to re-enter the
    challenge scene, which is what LunarCore does (``EnterSceneByServerScNotify``)
    and what our 4.6.54 runs do accept (the player can attack the 下半 monster).
    """
    state = session.challenge
    challenge = session.runtime.endgame.challenge(state.challenge_id)
    node = challenge.node2
    team = list(state.second_avatars) or list(state.avatars)
    scene, monster_entity_id = scene_service.build_challenge_scene(
        session,
        plane_id=node.plane_id,
        floor_id=node.floor_id,
        entry_id=node.entrance_id,
        world_id=node.world_id,
        group_id=node.maze_group_id,
        monster_id=node.monster_id,
        stage_id=node.stage_id,
        avatars=team or None,
    )
    state.node = node
    state.stage_id = node.stage_id
    state.monster_entity_id = monster_entity_id
    state.second = True
    state.stage = 2
    state.avatars = team
    state.battle_id = 0
    state.battle_ids.clear()
    session.battle_info = None
    session.runtime.logger.info(
        "challenge advance (scene): id=%d mode=%s scene=%d/%d entry=%d stage=%d team=%s",
        state.challenge_id,
        state.mode,
        node.plane_id,
        node.floor_id,
        node.entrance_id,
        node.stage_id,
        team,
    )
    return scene


def advance_stage(session) -> tuple[pb.SceneGroupRefreshScNotify, pb.LineupInfo]:
    """Switch the active attempt to 下半 (node2) **in place**.

    Mirrors DanhengServer ``ChallengeMemoryInstance.AdvanceStage`` and LunarCore
    ``ChallengeInstance.advanceStage``: unload ``MazeGroupID1``, load
    ``MazeGroupID2`` in the same scene, move the lineup to ``LineupChallenge2``.
    Returns ``(scene_group_refresh, lineup)`` for the handler to send.

    Why not rebuild a ``SceneInfo``: the client is already inside the arena and
    keeps its battle-area binding; handing it a fresh scene (new entry/entities)
    is what produced the 4.6.54 下半黑屏 — the battle still simulates and then
    settles itself.
    """
    state = session.challenge
    challenge = session.runtime.endgame.challenge(state.challenge_id)
    node = challenge.node2
    # 下半是另一套队伍：优先用客户端在队伍配置里给的 second lineup。
    team = list(state.second_avatars) or list(state.avatars)
    notify, monster_entity_id = scene_service.swap_challenge_group(
        session,
        group_id=node.maze_group_id,
        monster_id=node.monster_id,
        stage_id=node.stage_id,
    )
    state.node = node
    state.stage_id = node.stage_id
    state.monster_entity_id = monster_entity_id
    state.second = True
    state.stage = 2
    state.avatars = team
    # 下半是新一场战斗：清掉上半的 battle id。留着会让迟到的上半结果
    # 通过 ``req.battle_id in challenge.battle_ids`` 被当成有效结果而提前结算。
    state.battle_id = 0
    state.battle_ids.clear()
    session.battle_info = None
    session.runtime.logger.info(
        "challenge advance: id=%d mode=%s -> stage=2 group=%d stage_id=%d team=%s",
        state.challenge_id,
        state.mode,
        node.maze_group_id,
        node.stage_id,
        team,
    )
    return notify, lineup_info(session, second=True)



def enter_second_half(session) -> pb.SceneInfo:
    """Client-driven 下半: the client re-sent ``StartChallenge`` with only the
    second lineup (pearl-sr flow) and expects a fresh ``SceneInfo``.

    The server-driven auto-advance uses :func:`advance_stage` instead, which swaps
    the maze group in place.
    """
    state = session.challenge
    challenge = session.runtime.endgame.challenge(state.challenge_id)
    node = challenge.node2
    team = list(state.second_avatars) or list(state.avatars)
    scene, monster_entity_id = scene_service.build_challenge_scene(
        session,
        plane_id=node.plane_id,
        floor_id=node.floor_id,
        entry_id=node.entrance_id,
        world_id=node.world_id,
        group_id=node.maze_group_id,
        monster_id=node.monster_id,
        stage_id=node.stage_id,
        avatars=team or None,
    )
    state.node = node
    state.stage_id = node.stage_id
    state.monster_entity_id = monster_entity_id
    state.second = True
    state.stage = 2
    state.avatars = team
    state.battle_id = 0
    state.battle_ids.clear()
    session.battle_info = None
    session.runtime.logger.info(
        "challenge enter second half: id=%d mode=%s scene=%d/%d entry=%d stage=%d team=%s",
        state.challenge_id,
        state.mode,
        node.plane_id,
        node.floor_id,
        node.entrance_id,
        node.stage_id,
        team,
    )
    return scene


def start_direct(session, challenge_id: int) -> pb.SceneInfo | None:
    """Start a challenge without the 忘却之庭 lobby (debug / 模拟器入口).

    Mirrors pearl-sr's custom stage selection: the server is told the challenge
    id (``GET /debug/challenge?id=...``) and pushes the arena scene to the
    client.  Needed because the 4.6.54 beta client hard-locks on a broken
    map-NPC row (``MapNPC:* -400001`` has no ``ConfigEntityPath``) when it
    loads the train-car lobby floor, which makes the lobby path unusable.
    """
    challenge = session.runtime.endgame.challenge(challenge_id)
    if challenge is None:
        return None
    team = [slot for slot in session.lineup.slots if slot]
    state, scene = enter(
        session,
        challenge.id,
        avatars=team,
        first_avatars=team,
    )
    state.direct = True
    session.runtime.logger.info(
        "challenge direct start: id=%d (%s) stage=%d team=%s",
        challenge.id,
        challenge.mode,
        state.stage_id,
        team,
    )
    return scene


def rounds_limit(mode: str) -> int:
    """PF is a 4-round mode; MoC/AS use the standard 30."""
    return 4 if mode == PF else 30


def target_info(mode: str, stage_id: int) -> dict[int, list[int]]:
    """``SceneBattleInfo.battle_target_info`` slots for the mode."""
    if mode == PF:
        first = PF_TARGET_ID_HIGH if stage_id >= PF_HIGH_STAGE else PF_TARGET_ID
        return {1: [first], 2: [], 3: [], 4: [], 5: [2001, 2002]}
    if mode == AS:
        return {1: [AS_TARGET_ID]}
    return {}


def battle_info(session) -> pb.SceneBattleInfo:
    """Build the challenge ``SceneBattleInfo`` and cache it on the session.

    The client triggers a challenge fight with ``SceneCastSkillCsReq`` at the
    scene's monster, and it sends that packet **repeatedly** while the arena is
    loading (4.6.54 real machine 2026-11-11: 8 casts in 17 s).  It then settles
    the battle id from the response it acted on — which may be an older id than
    the last one handed out.  Minting a new id per cast made the server hold a
    later id, the result was rejected as "not the active battle", no
    ``ChallengeSettleNotify`` was sent and the monster respawned forever
    (guide/03 §3.25).  So reuse the active battle until a result/quit clears it.
    """
    state = getattr(session, "challenge", None)
    if state is None:
        raise ValueError("no active challenge")
    active = getattr(session, "battle_info", None)
    if active is not None and active.battle_id in state.battle_ids:
        return active
    runtime = session.runtime
    stage = runtime.endgame.stage(state.stage_id)
    if stage is None:
        # Data pack is missing this stage; keep the player unblocked with the
        # cocoon default and make the gap obvious in the log.
        runtime.logger.warning(
            "challenge %d stage %d has no wave table; using cocoon default",
            state.challenge_id,
            state.stage_id,
        )
        info = runtime.battle.build_battle_info(session)
        session.battle_info = info
        state.battle_ids.add(info.battle_id)
        return info

    battle_id = runtime.battle.next_battle_id(session)
    info = pb.SceneBattleInfo(
        battle_id=battle_id,
        world_level=runtime.cfg.player.world_level,
        stage_id=state.stage_id,
        rounds_limit=rounds_limit(state.mode),
        logic_random_seed=secrets.randbits(32) or 123456789,
        # 字段 7 在 4.6.54 descriptor 里被混淆成 JAOIOKBBMFC，实为
        # monster_wave_length（对照 pearl-sr protocol.pb.zig:28008）。pearl 每次都填；
        # 不填时多波次战斗客户端可能预载不到波次数据（2026-11-11 下半黑屏排查项）。
        JAOIOKBBMFC=len(stage.waves),
    )

    for index, wave in enumerate(stage.waves):
        info.monster_wave_list.append(
            pb.SceneMonsterWave(
                battle_wave_id=index + 1,
                monster_param=pb.SceneMonsterWaveParam(level=stage.level),
                monster_list=[pb.SceneMonster(monster_id=mid) for mid in wave],
            )
        )
    info.battle_avatar_list.extend(
        battle_service.build_battle_avatars(session, avatars=state.avatars or None)
    )
    info.buff_list.extend(battle_service.build_technique_buffs(session))
    for buff_id in state.buff_ids:
        # Stage blessing: not owned by an avatar (0xFFFFFFFF) and targeted at
        # the whole team (index 0) — same shape as pearl addStageBlessings.
        info.buff_list.append(
            pb.BattleBuff(
                id=buff_id,
                level=1,
                owner_index=ALL_WAVES,
                wave_flag=ALL_WAVES,
                target_index_list=[0],
            )
        )
    for slot, targets in target_info(state.mode, state.stage_id).items():
        info.battle_target_info[slot].CopyFrom(
            pb.BattleTargetList(
                battle_target_list=[
                    pb.BattleTarget(id=target_id, progress=0) for target_id in targets
                ]
            )
        )
    state.battle_id = battle_id
    state.battle_ids.add(battle_id)
    session.battle_info = info
    if state.second:
        # 真机诊断：下半的 battle_info 与上半逐字段对照用（黑屏时先把这行贴出来）。
        runtime.logger.info(
            "challenge battle info (下半): stage=%d rounds=%d avatars=%s waves=%s",
            state.stage_id,
            info.rounds_limit,
            [int(avatar.id) for avatar in info.battle_avatar_list],
            [
                [int(monster.monster_id) for monster in wave.monster_list]
                for wave in info.monster_wave_list
            ],
        )
    runtime.logger.info(
        "challenge battle info: id=%d challenge=%d mode=%s stage=%d waves=%d"
        " avatars=%d buffs=%d rounds=%d",
        battle_id,
        state.challenge_id,
        state.mode,
        state.stage_id,
        len(info.monster_wave_list),
        len(info.battle_avatar_list),
        len(info.buff_list),
        info.rounds_limit,
    )
    return info
