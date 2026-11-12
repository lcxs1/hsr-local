"""Challenge (混沌回忆 / 虚构叙事 / 末日幻影 / 星启模式) phase state machine.

Why this module exists in this shape
-----------------------------------
A challenge is **not** "one command -> one battle".  It is a sequence of
*phases* (节点/半场), each with its own arena group, its own team and its own
battle stage:

    混沌回忆 1 层 = 上半（node1, 队伍一, LineupChallenge）
                 + 下半（node2, 队伍二, LineupChallenge2）
    4.7 星启模式  = 三个节点（三套队伍, LineupChallenge/2/3）

Everything else (scene entry, battle payload, settlement, leave) is derived from
:class:`Phase`.  That is the whole point of this rewrite: the previous version
kept ``node``/``second``/``stage``/``tierce_nodes``/``tierce_teams`` as five
parallel half-states and patched each symptom (``advance=none|refresh|scene``)
separately, which is why the same 下半 problems kept coming back.

The transition itself follows the reference implementation that actually gets a
second half to load (LunarCore ``ChallengeInstance.advanceStage``), packet for
packet:

    1. ``ChallengeLineupNotify`` with the next phase's ``extra_lineup_type``
    2. ``SyncServerSceneChangeNotify``  (server scene changed → client re-enters)
    3. ``SyncLineupNotify`` + ``EnterSceneByServerScNotify`` carrying the next
       phase's whole ``SceneInfo`` (``entity_group_list`` declares the new maze
       group)

with ``[endgame] advance``:

* ``sync`` (default) — the four packets above.  This is what the references do.
* ``inplace`` — patch maze groups entity-by-entity with
  ``SceneGroupRefreshScNotify``.  This is what 4.7.0 chokes on (the client runs
  ``MapDef._ActiveServerHoyoGroup`` on a group it never declared, NREs, keeps the
  visual NPC but never sends ``SceneCastSkillCsReq``); kept only as an A/B probe.
* ``scene``   — push the new ``SceneInfo`` **without**
  ``SyncServerSceneChangeNotify`` (4.6.54/4.7.0 black-screen path; A/B only).
* ``client``  — push nothing and wait for the client to send ``StartChallenge``
  again (pearl-sr style, works only if the client drives the phase itself).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from hsr.game.services import battle as battle_service
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.game.services.endgame import AS, MOC, PF, ChallengeNode
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
# 混沌回忆/末日幻影的轮次上限（剧情/PF 是 4）。
DEFAULT_ROUNDS = 30
FORMAL = pb.AvatarType.AvatarType_AvatarFormalType

# 每个阶段用哪套额外阵容。第一层/上半 = Challenge，第二层/下半 = Challenge2，
# 星启第三节点 = Challenge3。参考实现里 extra lineup 的 proto ``index`` 一律 0
# （Danheng ``LineupInfo.ToProto``: ``if LineupType != LineupNone info.Index = 0``），
# 只有普通阵容才用 0..8 —— 旧实现把它写成 ``type + 10`` 是误读那个**存储键**。
PHASE_LINEUP_TYPES = (
    pb.ExtraLineupType.ExtraLineupType_LineupChallenge,
    pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
    pb.ExtraLineupType.ExtraLineupType_LineupChallenge3,
)


# ---------------------------------------------------------------------- models
@dataclass
class Phase:
    """One battle of a challenge: arena group + team + lineup slot."""

    index: int
    node: ChallengeNode
    team: list[int]
    lineup_type: int

    @property
    def stage_id(self) -> int:
        return int(self.node.stage_id)

    @property
    def label(self) -> str:
        return ("上半", "下半", "第三节", "第四节")[min(self.index, 3)]


@dataclass
class Transition:
    """Packets a phase switch needs, split around the battle-result ack.

    ``before`` goes out **before** ``PVEBattleResultScRsp`` (the client is still
    in the fight state and does process challenge/lineup messages there — that is
    what the reference does).  ``after`` goes out **after** the ack: scene group
    refreshes are dropped by some clients while a battle is running
    (reference comment: "Some clients ignore scene refresh packets while in
    battle; resend the removals after battle result").
    """

    before: list = field(default_factory=list)
    after: list = field(default_factory=list)
    settled: bool = False
    new_scene: bool = False

    @property
    def messages(self) -> list:
        return [*self.before, *self.after]

    def __bool__(self) -> bool:
        return bool(self.before or self.after)


@dataclass
class ChallengeState:
    """One active challenge attempt (owned by the session)."""

    challenge_id: int = 0
    mode: str = MOC
    phases: list[Phase] = field(default_factory=list)
    index: int = 0
    # True = 由 ``/debug/challenge`` 直接把玩家丢进 arena（不经忘却之庭大厅）。
    # 结算后要自己把玩家送回世界场景，因为没有 LeaveChallenge 可走。
    direct: bool = False
    buff_ids: list[int] = field(default_factory=list)
    # 剩余轮次（``CurChallenge.round_count = countdown - rounds_left``）与阵亡人数：
    # 客户端 HUD 与结算星级都读这两个数，旧实现从不更新，永远显示「剩余轮 30」。
    rounds_left: int = 0
    # 本半场开始时的剩余轮次：客户端「重打本半场」要回退到这里。
    phase_start_rounds: int = 0
    # 本半场开始时的阵亡人数：重打本半场时一起回退，避免跨重试累积虚高。
    phase_start_dead_avatars: int = 0
    dead_avatars: int = 0
    # 当前阶段实际发出的 battle id（客户端可能回一个更早的 id，见 battle_info）。
    battle_id: int = 0
    battle_ids: set[int] = field(default_factory=set)
    stage_id: int = 0
    monster_entity_id: int = 0
    # Scene to return to on LeaveChallenge (the lobby the player came from).
    return_plane_id: int = 0
    return_floor_id: int = 0
    return_entry_id: int = 0

    # ------------------------------------------------------------- properties
    @property
    def current(self) -> Phase:
        return self.phases[min(self.index, len(self.phases) - 1)]

    @property
    def last(self) -> Phase:
        return self.phases[-1]

    @property
    def has_next(self) -> bool:
        return self.index + 1 < len(self.phases)

    @property
    def node(self) -> ChallengeNode:
        return self.current.node

    @property
    def avatars(self) -> list[int]:
        return list(self.current.team)

    @property
    def first_avatars(self) -> list[int]:
        return list(self.phases[0].team) if self.phases else []

    @property
    def second_avatars(self) -> list[int]:
        return list(self.phases[1].team) if len(self.phases) > 1 else []

    @property
    def stage(self) -> int:
        """1-based phase number (上半 = 1) — kept for older callers/tests."""
        return self.index + 1

    @property
    def second(self) -> bool:
        return self.index > 0

    @property
    def stage_index(self) -> int:
        """4.7 星启模式节点号（0-based）— alias of :attr:`index`."""
        return self.index

    @property
    def tierce(self) -> bool:
        return len(self.phases) > 2

    @property
    def tierce_nodes(self) -> list[ChallengeNode]:
        return [phase.node for phase in self.phases]

    @property
    def tierce_teams(self) -> list[list[int]]:
        return [list(phase.team) for phase in self.phases]

    @property
    def lineup_type(self) -> int:
        return self.current.lineup_type


# ------------------------------------------------------------------ discovery
def _team(ids) -> list[int]:
    return [int(avatar_id) for avatar_id in (ids or []) if avatar_id]


def _rounds_for(mode: str, countdown: int) -> int:
    if countdown > 0:
        return int(countdown)
    return 4 if mode == PF else DEFAULT_ROUNDS


def _phases(challenge, first: list[int], second: list[int]) -> list[Phase]:
    """Build the phase list of one challenge row (node1 and, if any, node2)."""
    team1 = _team(first)
    team2 = _team(second) or team1
    phases = [Phase(0, challenge.node1, team1, PHASE_LINEUP_TYPES[0])]
    if challenge.node2 is not None:
        phases.append(Phase(1, challenge.node2, team2, PHASE_LINEUP_TYPES[1]))
    return phases


def tierce_nodes(session, challenge_id: int) -> list[ChallengeNode] | None:
    """星启模式（4.7.0「混沌回忆」最后一层）的三个节点。

    客户端发的 id 是 ``tierce`` 表里的行（真机：第 12 层发 **5513**，比普通过关
    行 5512 大 1）。这一行只带自己的场景（entrance/plane/group/monster/stage）
    和 ``pre_challenge_id``（= 5512），所以三个节点拼成：

    1. ``pre_challenge.node1``（上半）；
    2. ``pre_challenge.node2``（下半）；
    3. tierce 行自己（红底的「污染等级 3」节点）。

    没有 ``pre_challenge`` 数据时三个节点都用 tierce 行，保证一样能打。
    """
    data = session.runtime.endgame
    stage = data.tierce_stage(challenge_id)
    if stage is None:
        challenge = data.challenge(challenge_id)
        if challenge is None:
            return None
        second = challenge.node2 or challenge.node1
        return [challenge.node1, second, second]
    third = ChallengeNode(
        entrance_id=stage.entrance_id,
        plane_id=stage.plane_id,
        floor_id=stage.floor_id,
        world_id=stage.world_id,
        maze_group_id=stage.maze_group_id,
        monster_id=stage.monster_id,
        stage_id=stage.stage_id,
    )
    nodes: list[ChallengeNode] = []
    pre = data.challenge(stage.pre_challenge_id) if stage.pre_challenge_id else None
    if pre is not None:
        nodes.append(pre.node1)
        if pre.node2 is not None:
            nodes.append(pre.node2)
    if not nodes:
        nodes.append(third)
    while len(nodes) < 3:
        nodes.append(third)
    return nodes[:3]


def _tierce_phases(session, challenge_id: int, teams: list[list[int]]) -> list[Phase]:
    nodes = tierce_nodes(session, challenge_id) or []
    phases: list[Phase] = []
    fallback = teams[0] if teams else []
    for index, node in enumerate(nodes[:3]):
        team = _team(teams[index]) if index < len(teams) else list(fallback)
        phases.append(
            Phase(index, node, team or list(fallback), PHASE_LINEUP_TYPES[min(index, 2)])
        )
    return phases


# --------------------------------------------------------------------- enter
def _remember_return(session, state: ChallengeState) -> None:
    state.return_plane_id = int(session.scene.plane_id)
    state.return_floor_id = int(session.scene.floor_id)
    state.return_entry_id = int(session.scene.entry_id)


def _apply_phase_scene(session, state: ChallengeState) -> pb.SceneInfo:
    """Build the ``SceneInfo`` of the current phase and switch the session to it."""
    phase = state.current
    scene, monster_entity_id = scene_service.build_challenge_scene(
        session,
        plane_id=phase.node.plane_id,
        floor_id=phase.node.floor_id,
        entry_id=phase.node.entrance_id,
        world_id=phase.node.world_id,
        group_id=phase.node.maze_group_id,
        monster_id=phase.node.monster_id,
        stage_id=phase.stage_id,
        avatars=phase.team or None,
    )
    state.stage_id = phase.stage_id
    state.monster_entity_id = monster_entity_id
    state.battle_id = 0
    state.battle_ids.clear()
    session.battle_info = None
    # 进入某个阶段时的轮次预算（跨半场共用；重打本半场回退到这里）。
    state.phase_start_rounds = int(state.rounds_left)
    state.phase_start_dead_avatars = int(state.dead_avatars)
    return scene


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
    carries.  Raises ``ValueError`` for an unknown challenge id.

    ``first_avatars``/``second_avatars`` are the two teams the client configured
    in the pre-battle 队伍配置 界面 (``avatar_lineup_first/second``); ``second``
    starts the run directly on the 下半 phase (pearl-sr flow).
    """
    runtime = session.runtime
    challenge = runtime.endgame.challenge(challenge_id)
    if challenge is None:
        raise ValueError(f"unknown challenge id {challenge_id}")
    chosen = _team(avatars)
    first = _team(first_avatars) or ([] if second else list(chosen))
    second_list = _team(second_avatars) or (list(chosen) if second else [])
    if not chosen:
        chosen = second_list if second else first
    if not first and not second_list:
        first = list(chosen)

    phases = _phases(challenge, first, second_list)
    state = ChallengeState(
        challenge_id=challenge.id,
        mode=challenge.mode,
        phases=phases,
        index=1 if (second and len(phases) > 1) else 0,
        buff_ids=list(
            buff_ids
            if buff_ids is not None
            else ([challenge.buff_id] if challenge.buff_id else [])
        ),
        rounds_left=_rounds_for(challenge.mode, challenge.countdown),
    )
    state.phase_start_rounds = int(state.rounds_left)
    state.phase_start_dead_avatars = int(state.dead_avatars)
    _remember_return(session, state)
    session.challenge = state
    scene = _apply_phase_scene(session, state)
    runtime.logger.info(
        "challenge entered: id=%d mode=%s phase=%d/%d scene=%d/%d entry=%d"
        " stage=%d monster=%d teams=%s",
        state.challenge_id,
        state.mode,
        state.index + 1,
        len(phases),
        state.node.plane_id,
        state.node.floor_id,
        state.node.entrance_id,
        state.stage_id,
        state.monster_entity_id,
        [phase.team for phase in phases],
    )
    return state, scene


def enter_tierce(
    session,
    challenge_id: int,
    *,
    stage_index: int = 0,
    teams: list[list[int]] | None = None,
    buff_ids: list[int] | None = None,
) -> tuple[ChallengeState, pb.SceneInfo]:
    """4.7.0「星启模式」进节点：一层 3 个节点，每个节点一套队伍。

    客户端在编队界面点「混沌回忆」后发 ``StartChallengeTierceCsReq``（挑战 id +
    ``stage_index`` + 三套 ``lineup_tierce_list``），服务端回 ``scene`` +
    ``cur_tierce_challenge``。节点数据见 :func:`tierce_nodes`。
    """
    runtime = session.runtime
    challenge = runtime.endgame.challenge(challenge_id)
    stage = runtime.endgame.tierce_stage(challenge_id)
    if challenge is None and stage is None:
        raise ValueError(f"unknown challenge id {challenge_id}")
    phases = _tierce_phases(session, challenge_id, [list(team) for team in (teams or [])])
    if not phases:
        raise ValueError(f"unknown challenge id {challenge_id}")
    mode = challenge.mode if challenge is not None else MOC
    buff = 0
    if challenge is not None:
        buff = challenge.buff_id
    elif stage is not None and stage.pre_challenge_id:
        pre = runtime.endgame.challenge(stage.pre_challenge_id)
        buff = pre.buff_id if pre is not None else 0
    state = ChallengeState(
        challenge_id=int(challenge_id),
        mode=mode,
        phases=phases,
        index=min(max(int(stage_index), 0), len(phases) - 1),
        buff_ids=list(buff_ids if buff_ids is not None else ([buff] if buff else [])),
        rounds_left=DEFAULT_ROUNDS,
    )
    state.phase_start_rounds = int(state.rounds_left)
    state.phase_start_dead_avatars = int(state.dead_avatars)
    _remember_return(session, state)
    session.challenge = state
    scene = _apply_phase_scene(session, state)
    runtime.logger.info(
        "challenge tierce entered: id=%d mode=%s stage_index=%d/%d teams=%s"
        " scene=%d/%d entry=%d stage=%d monster=%d",
        state.challenge_id,
        state.mode,
        state.index,
        len(phases),
        [phase.team for phase in phases],
        state.node.plane_id,
        state.node.floor_id,
        state.node.entrance_id,
        state.stage_id,
        state.monster_entity_id,
    )
    return state, scene


def start_direct(
    session, challenge_id: int, *, second: bool = False
) -> pb.SceneInfo | None:
    """Start a challenge without the 忘却之庭 lobby (debug / 模拟器入口).

    Mirrors pearl-sr's custom stage selection: the server is told the challenge
    id (``GET /debug/challenge?id=...``) and pushes the arena scene to the
    client.  Needed because the lobby floor hard-locks the 4.6.54 beta client
    (``MapNPC:* -400001`` has no ``ConfigEntityPath``).

    ``second=True`` starts **directly on the 下半** (phase 2 arena, second team).
    2026-11-11 真机探针：下半的战斗在“换半场之后”打不出来（卡在进战斗的动画），
    用这个开关可以把下半当成**第一场**战斗来打，从而分清是“下半的战斗包不对”
    还是“换半场那一下把客户端状态弄坏了”。
    """
    challenge = session.runtime.endgame.challenge(challenge_id)
    tierce_stage = session.runtime.endgame.tierce_stage(challenge_id)
    if challenge is None and tierce_stage is None:
        return None
    team = [slot for slot in session.lineup.slots if slot]
    if challenge is None:
        state, scene = enter_tierce(session, challenge_id, stage_index=0, teams=[team])
        state.direct = True
        session.runtime.logger.info(
            "challenge direct start (tierce): id=%d stage=%d team=%s",
            challenge_id,
            state.stage_id,
            team,
        )
        return scene
    state, scene = enter(
        session,
        challenge.id,
        avatars=team,
        first_avatars=team,
        second=second,
    )
    state.direct = True
    session.runtime.logger.info(
        "challenge direct start: id=%d (%s) phase=%d/%d stage=%d group=%d team=%s",
        challenge.id,
        challenge.mode,
        state.index + 1,
        len(state.phases),
        state.stage_id,
        state.node.maze_group_id,
        state.current.team,
    )
    return scene


def clear(session) -> None:
    """Drop the active challenge (battle included)."""
    session.challenge = None
    session.battle_info = None


# ------------------------------------------------------------------- lineups
def lineup_info(session, second: bool | None = None, *, index: int | None = None) -> pb.LineupInfo:
    """Challenge ``LineupInfo`` for one phase (``extra_lineup_type`` = phase slot).

    ``index`` picks the phase explicitly; ``second`` is kept for older callers.
    Reference shape: extra lineups carry ``extra_lineup_type`` and ``index = 0``.
    """
    state = getattr(session, "challenge", None)
    runtime = session.runtime
    roster = runtime.roster
    if state is not None:
        if index is None:
            index = state.index if second is None else (1 if second else 0)
        index = min(max(int(index), 0), len(state.phases) - 1)
        phase = state.phases[index]
        avatars = list(phase.team)
        extra_lineup_type = phase.lineup_type
        if not avatars:
            avatars = [slot for slot in session.lineup.slots if slot]
    else:
        avatars = [slot for slot in session.lineup.slots if slot]
        extra_lineup_type = (
            pb.ExtraLineupType.ExtraLineupType_LineupChallenge2
            if second
            else pb.ExtraLineupType.ExtraLineupType_LineupChallenge
        )
    lineup = pb.LineupInfo(
        name="",
        index=0,
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
    """All phase lineups for ``StartChallenge``/``GetCurChallenge`` responses.

    ``StageNum >= 2`` carries **both** teams (DanhengServer/HyacineCore and
    LunarCore do the same): with only the current team the client has nothing to
    render the other half with.
    """
    state = getattr(session, "challenge", None)
    if state is None:
        return [lineup_info(session)]
    return [lineup_info(session, index=index) for index in range(len(state.phases))]


def normal_lineup_notify(session) -> pb.SyncLineupNotify:
    """``SyncLineupNotify`` for the open-world (non-extra) lineup.

    Sent when a challenge ends so the client stops using ``LineupChallenge``/
    ``LineupChallenge2`` for the next open-world fight.
    """
    return pb.SyncLineupNotify(lineup=lineup_service.build_lineup(session))


# --------------------------------------------------------------- cur challenge
def cur_challenge(session, *, finished: bool | None = None) -> pb.CurChallenge:
    """``CurChallenge`` for Start/GetCur/Settle responses."""
    state = session.challenge
    if finished is None:
        status = pb.ChallengeStatus.CHALLENGE_DOING
    else:
        status = (
            pb.ChallengeStatus.CHALLENGE_FINISH
            if finished
            else pb.ChallengeStatus.CHALLENGE_FAILED
        )
    countdown = _rounds_for(state.mode, 0)
    if state.phases:
        challenge = session.runtime.endgame.challenge(state.challenge_id)
        if challenge is not None:
            countdown = _rounds_for(state.mode, challenge.countdown)
    cur = pb.CurChallenge(
        challenge_id=state.challenge_id,
        status=status,
        extra_lineup_type=state.lineup_type,
        score_id=PF_SCORE_ID if state.mode == PF else 0,
        score_two=0,
        round_count=max(0, countdown - int(state.rounds_left)),
        dead_avatar_num=int(state.dead_avatars),
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
    challenge = session.runtime.endgame.challenge(state.challenge_id)
    # ``floor`` 是「第几层」（ChallengeMazeConfig.Floor，只有轮换组才有）；
    # 常驻组（100/900）没有这一列，就不发 max_level，别拿 floor_id 顶上。
    floor = int(getattr(challenge, "floor", 0) or 0) if challenge is not None else 0
    notify = pb.ChallengeSettleNotify(
        challenge_id=state.challenge_id,
        is_win=win,
        star=max(0, int(stars)) if win else 0,
        challenge_score=0,
        score_two=0,
        cur_challenge=cur_challenge(session, finished=win),
    )
    if floor:
        notify.max_level.CopyFrom(
            pb.ChallengeHistoryMaxLevel(level=floor, reward_display_type=101212)
        )
    return notify


# ----------------------------------------------------------------- transitions
def can_advance(session) -> bool:
    """True when the attempt still has an unplayed phase."""
    state = getattr(session, "challenge", None)
    return bool(state is not None and state.has_next)


def _advance_inplace(session, state: ChallengeState, phase: Phase) -> Transition:
    """Reference phase switch: lineups + avatars (before ack) + groups (after)."""
    previous = state.current
    old_entities = [
        entity
        for group in session.scene.extra_groups
        for entity in group.entity_list
    ]
    new_entities, monster_entity_id = scene_service.challenge_group_entities(
        session,
        plane_id=phase.node.plane_id,
        group_id=phase.node.maze_group_id,
        monster_id=phase.node.monster_id,
        stage_id=phase.stage_id,
        entry_id=phase.node.entrance_id,
        # 换实体 id 段：旧组刚删掉的 id 不重复用（legacy 刷新路径对此敏感）。
        base=scene_service.CHALLENGE_ENTITY_BASE + 10 * phase.index,
    )
    before: list = [pb.ChallengeLineupNotify(extra_lineup_type=phase.lineup_type)]
    avatars = scene_service.avatar_refresh_notify(
        session,
        old_avatars=previous.team,
        new_avatars=phase.team,
    )
    if avatars is not None:
        before.append(avatars)
    after: list = []
    if previous.node.maze_group_id != phase.node.maze_group_id:
        # 4.7.0 真机 22:56：用 `refresh_type=LOADED` 发“卸旧组”（逐实体 delete）
        # 会被客户端当成“激活这个组” → `Reload ServerGroup Error, Already Exists!!!
        # ServerGroupID: 9` → `MapDef._ActiveServerHoyoGroup` NRE → 客户端卡在黑屏/
        # 近战，且**再也不会发 SceneCastSkill**。
        # 所以默认不卸旧组，只加载新组（旧组的怪在上半已经被打死，视觉上没有残留）；
        # 需要真卸载时 `[endgame] unload_old_group = true` 走 `refresh_type=UNLOAD`。
        if bool(getattr(session.runtime.cfg.endgame, "unload_old_group", False)):
            after.append(
                scene_service.unload_group_notify(
                    session,
                    previous.node.maze_group_id,
                    old_entities,
                    refresh_type=scene_service.GROUP_REFRESH_UNLOAD,
                )
            )
        after.append(
            scene_service.load_group_notify(
                session, phase.node.maze_group_id, new_entities
            )
        )
        session.scene.extra_groups = [
            pb.SceneEntityGroupInfo(
                group_id=int(phase.node.maze_group_id),
                state=1,
                entity_list=new_entities,
            )
        ]
    state.monster_entity_id = monster_entity_id
    # GetCurSceneInfo / 场景重建要按新队伍装配场上角色，否则会把上一队发回来。
    session.scene.team = list(phase.team)
    return Transition(before=before, after=after)


def _advance_sync(session, state: ChallengeState, phase: Phase) -> Transition:
    """Reference phase switch: tell the client the server scene changed.

    LunarCore ``ChallengeInstance.advanceStage`` (and every 4.x server that gets
    下半 to work) does **not** patch maze groups entity-by-entity.  It sends

    1. ``ChallengeLineupNotify`` for the new extra lineup,
    2. ``SyncServerSceneChangeNotify`` — the client drops its arena state and
       re-runs its own phase entry (4.7.0 真机：advance 之后客户端确实会自己
       重载 stage，`AdventurePhase._EnterStep2` → `Map._LoadStage`),
    3. a full ``SceneInfo`` through ``EnterSceneByServerScNotify`` whose
       ``entity_group_list`` declares the new half's maze group.

    Patching groups with ``SceneGroupRefreshScNotify`` is what broke 4.7.0: the
    client runs ``MapDef._ActiveServerHoyoGroup`` on a group it never declared
    (``_OnSceneGroupRefreshScNotify`` → NRE), so the arena keeps the visual NPC
    but never becomes attackable and no ``SceneCastSkillCsReq`` is sent.
    """
    state.index = phase.index
    scene = _apply_phase_scene(session, state)
    lineup = lineup_info(session)
    # 阵容包在结果包之前（客户端还停在战斗态时就会处理，和场景模式一致）；
    # 场景切换包在结果包之后，顺序与 LunarCore 的 advanceStage 一致。
    before = [
        pb.ChallengeLineupNotify(extra_lineup_type=phase.lineup_type),
        pb.SyncLineupNotify(lineup=lineup),
    ]
    after = [
        pb.SyncServerSceneChangeNotify(),
        pb.EnterSceneByServerScNotify(
            reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
            lineup=lineup,
            scene=scene,
        ),
    ]
    session.runtime.logger.info(
        "challenge advance (sync): id=%d phase=%d->%d scene=%d/%d entry=%d"
        " group=%d stage=%d team=%s",
        state.challenge_id,
        phase.index,
        phase.index + 1,
        phase.node.plane_id,
        phase.node.floor_id,
        phase.node.entrance_id,
        phase.node.maze_group_id,
        phase.stage_id,
        phase.team,
    )
    return Transition(before=before, after=after, new_scene=True)


def _advance_declare(session, state: ChallengeState, phase: Phase) -> Transition:
    """Phase switch **in place** by declaring the next maze group.

    Same scene, no ``SyncServerSceneChangeNotify`` and therefore no client-side
    stage reload (4.7 真机：发那个通知后客户端会 `AdventurePhase._EnterStep2` 重载
    整个 stage，紧跟着进战斗就会卡在进场动画上）。组不是逐实体 patch，而是用
    ``GroupRefreshInfo.group_info``（和 `SceneInfo.entity_group_list` 同一个
    message）**声明**，也就是上半进场景时客户端认的那条路径。
    """
    previous = state.current
    new_entities, monster_entity_id = scene_service.challenge_group_entities(
        session,
        plane_id=phase.node.plane_id,
        group_id=phase.node.maze_group_id,
        monster_id=phase.node.monster_id,
        stage_id=phase.stage_id,
        entry_id=phase.node.entrance_id,
        base=scene_service.CHALLENGE_ENTITY_BASE + 10 * phase.index,
    )
    before: list = [pb.ChallengeLineupNotify(extra_lineup_type=phase.lineup_type)]
    avatars = scene_service.avatar_refresh_notify(
        session,
        old_avatars=previous.team,
        new_avatars=phase.team,
    )
    if avatars is not None:
        before.append(avatars)
    after: list = [
        scene_service.declared_group_notify(
            session, phase.node.maze_group_id, new_entities
        )
    ]
    state.index = phase.index
    session.scene.extra_groups = [
        pb.SceneEntityGroupInfo(
            group_id=int(phase.node.maze_group_id),
            state=1,
            entity_list=new_entities,
        )
    ]
    session.scene.team = list(phase.team)
    state.monster_entity_id = monster_entity_id
    state.stage_id = phase.stage_id
    state.battle_id = 0
    state.battle_ids.clear()
    session.battle_info = None
    state.phase_start_rounds = int(state.rounds_left)
    state.phase_start_dead_avatars = int(state.dead_avatars)
    session.runtime.logger.info(
        "challenge advance (declare): id=%d phase=%d->%d group=%d entities=%d"
        " stage=%d team=%s",
        state.challenge_id,
        phase.index,
        phase.index + 1,
        phase.node.maze_group_id,
        len(new_entities),
        phase.stage_id,
        phase.team,
    )
    return Transition(before=before, after=after)


def advance(session, *, mode: str | None = None) -> Transition:
    """Switch the attempt to its next phase; returns the packets to send.

    ``mode`` defaults to ``[endgame] advance``.  ``client`` returns an empty
    transition and changes nothing: the client is expected to re-send
    ``StartChallenge`` (handled by :func:`enter_second_half`).
    """
    state = getattr(session, "challenge", None)
    if state is None or not state.has_next:
        return Transition()
    mode = (mode or session.runtime.cfg.endgame.advance or "sync").lower()
    if mode in ("client", "none"):
        session.runtime.logger.info(
            "challenge advance: waiting for client (advance=%s)", mode
        )
        return Transition()

    phase = state.phases[state.index + 1]
    if mode == "declare":
        return _advance_declare(session, state, phase)
    if mode == "sync":
        return _advance_sync(session, state, phase)
    same_scene = (
        int(phase.node.plane_id) == int(session.scene.plane_id)
        and int(phase.node.floor_id) == int(session.scene.floor_id)
        and int(phase.node.entrance_id) == int(session.scene.entry_id)
    )
    if mode == "scene" or not same_scene:
        # 重建 SceneInfo 的老路：客户端重进 arena。保留做 A/B 与「换层」兜底。
        # 阵容切换先发（客户端还在战斗态就会处理），场景包放在结果之后。
        state.index = phase.index
        scene = _apply_phase_scene(session, state)
        before = [pb.ChallengeLineupNotify(extra_lineup_type=phase.lineup_type)]
        after = [
            pb.SyncLineupNotify(lineup=lineup_info(session)),
            pb.EnterSceneByServerScNotify(
                reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
                lineup=lineup_info(session),
                scene=scene,
            ),
        ]
        session.runtime.logger.info(
            "challenge advance (scene): id=%d phase=%d scene=%d/%d entry=%d"
            " stage=%d team=%s",
            state.challenge_id,
            phase.index + 1,
            phase.node.plane_id,
            phase.node.floor_id,
            phase.node.entrance_id,
            phase.stage_id,
            phase.team,
        )
        return Transition(before=before, after=after, new_scene=True)

    transition = _advance_inplace(session, state, phase)
    state.index = phase.index
    # 新一场战斗：清掉上一阶段的 battle id，否则迟到的结果会被当成有效结果。
    state.battle_id = 0
    state.battle_ids.clear()
    state.stage_id = phase.stage_id
    session.battle_info = None
    # 新半场的轮次预算 = 此刻剩余（跨半场共用）；重打本半场时回退到这里。
    state.phase_start_rounds = int(state.rounds_left)
    state.phase_start_dead_avatars = int(state.dead_avatars)
    session.runtime.logger.info(
        "challenge advance (inplace): id=%d phase=%d->%d group=%d stage=%d team=%s",
        state.challenge_id,
        state.index,
        phase.index + 1,
        phase.node.maze_group_id,
        phase.stage_id,
        phase.team,
    )
    return transition


def enter_second_half(session) -> pb.SceneInfo:
    """Client-driven 下半: the client re-sent ``StartChallenge`` (pearl flow).

    Only used when ``[endgame] advance = "client"``; returns the fresh
    ``SceneInfo`` for the phase the client asked for.
    """
    state = session.challenge
    if state.has_next:
        state.index += 1
    scene = _apply_phase_scene(session, state)
    session.runtime.logger.info(
        "challenge enter second half: id=%d phase=%d group=%d stage=%d team=%s",
        state.challenge_id,
        state.index + 1,
        state.node.maze_group_id,
        state.stage_id,
        state.current.team,
    )
    return scene


def apply_result_stats(session, req) -> None:
    """Fold one ``PVEBattleResultCsReq`` into round/death counters.

    ``CurChallenge.round_count`` / ``dead_avatar_num`` feed both the HUD (剩余轮次)
    and the settlement page; the old implementation never wrote them, so every
    fight showed "剩余轮 30" and the star count was a constant.
    """
    state = getattr(session, "challenge", None)
    if state is None:
        return
    stats = getattr(req, "stt", None)
    if stats is None:
        return
    rounds = int(getattr(stats, "round_cnt", 0) or 0)
    if rounds > 0:
        state.rounds_left = max(0, int(state.rounds_left) - rounds)
    for avatar in getattr(stats, "battle_avatar_list", []):
        if int(getattr(avatar, "die_times", 0) or 0) > 0:
            state.dead_avatars += 1


# ------------------------------------------------------------------- battles
def rounds_limit(mode: str) -> int:
    """Fallback round limit when the config row has no ``ChallengeCountDown``.

    PF is a 4-round mode; MoC/AS use their own countdown (usually 30, 常驻回忆 40).
    """
    return 4 if mode == PF else DEFAULT_ROUNDS


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
    # 轮次预算跨半场共用（参考实现 `battle.RoundLimit = Data.Memory.RoundsLeft`）：
    # 上半用掉的轮次不能在下半再给一遍，否则下半 HUD 的「剩余轮次」会跳回满值。
    # remaining 已是 0 时也必须保持 0/1，不能回退到 rounds_limit(mode) 的满值。
    remaining = max(0, int(state.rounds_left or 0))
    info = pb.SceneBattleInfo(
        battle_id=battle_id,
        world_level=runtime.cfg.player.world_level,
        stage_id=state.stage_id,
        rounds_limit=max(1, remaining),
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
    runtime.logger.info(
        "challenge battle waves: stage=%d waves=%s",
        state.stage_id,
        [list(wave) for wave in stage.waves],
    )
    runtime.logger.info(
        "challenge battle info: id=%d challenge=%d mode=%s phase=%d stage=%d"
        " waves=%d avatars=%s buffs=%d rounds=%d",
        battle_id,
        state.challenge_id,
        state.mode,
        state.index + 1,
        state.stage_id,
        len(info.monster_wave_list),
        [int(avatar.id) for avatar in info.battle_avatar_list],
        len(info.buff_list),
        info.rounds_limit,
    )
    return info
