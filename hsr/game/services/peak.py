"""异相仲裁 (ChallengePeak) — 组内 3 个小关 + 1 个 BOSS，独立于 MOC/PF/AS。

数据来自 ``data/endgame.json`` 的 ``peak`` 段（``scripts/export_endgame.py`` 从
4.6.54 客户端表导出）：

* 11 个组：``pre_level_ids``（3 个 peak）+ ``boss_level_id``（1 个 BOSS peak）；
* 44 个 peak stage：场景 / 迷宫组 / 怪物 / 战斗 stage 与挑战节点同构；
* 11 个 BOSS：``buff_ids``（3 选 1）、``hard_event_ids``、``hard_target``。

协议链（对照 pearl-sr ``services/challenge.zig`` 的 Peak 分支）：

    GetChallengePeakDataCsReq           -> 组列表（peaks + boss + 星数 + 领奖）
    StartChallengePeakCsReq(peak)       -> EnterSceneByServerScNotify（ScRsp 本身没有 scene）
    SceneCastSkillCsReq                 -> 复用挑战战斗（mode=PEAK，一套阵容一个节点）
    PVEBattleResultCsReq                -> ChallengePeakSettleScNotify + GroupDataUpdateScNotify
    ConfirmChallengePeakSettleCsReq     -> 清掉本次 Peak（历史已经落库）
    GetCurChallengePeakCsReq            -> 当前 Peak（没有就只回 retcode）
    ReStartChallengePeakCsReq           -> 重推当前 Peak 场景
    LeaveChallengePeakCsReq             -> 回进入前的场景
    TakeChallengePeakRewardCsReq        -> 记下领奖（没有奖励管线，只做状态）
    SetChallengePeakBossHardModeCsReq   -> 记住困难模式开关
    SetChallengePeakMobLineupAvatarCsReq-> 回执 + 组数据更新

``cycles``（用掉的轮次）与 ``stars``（是否通关）按 pearl-sr + 客户端表推断，
**未真机验证**；真机先看 ``GetChallengePeakData`` 的组列表能不能渲染。
"""

from __future__ import annotations

from dataclasses import dataclass

from hsr.game.services import challenge as challenge_service
from hsr.game.services.endgame import ChallengeNode, PeakBoss, PeakGroup, PeakStage
from hsr.protocol import pb

PEAK = challenge_service.PEAK
DEFAULT_ROUNDS = challenge_service.DEFAULT_ROUNDS


@dataclass
class PeakTarget:
    """One startable Peak node: its group, its stage row and (for a BOSS) its table."""

    group: PeakGroup
    stage: PeakStage
    boss: PeakBoss | None = None
    is_boss: bool = False

    @property
    def peak_id(self) -> int:
        return int(self.stage.id)


# ------------------------------------------------------------------ discovery
def groups(session) -> list[PeakGroup]:
    """Every Peak group, sorted by id (1..11 in the 4.6.54 table)."""
    data = session.runtime.endgame
    return [data.peak_groups[gid] for gid in sorted(data.peak_groups)]


def peaks_of(session, group: PeakGroup) -> list[PeakStage]:
    """The group's peaks in panel order: 3 小关 first, then the BOSS."""
    data = session.runtime.endgame
    out = [data.peak_stages[pid] for pid in group.pre_level_ids if pid in data.peak_stages]
    boss = data.peak_stages.get(int(group.boss_level_id))
    if boss is not None:
        out.append(boss)
    return out


def group_of(session, peak_id: int) -> PeakGroup | None:
    for group in groups(session):
        if int(peak_id) == int(group.boss_level_id) or int(peak_id) in group.pre_level_ids:
            return group
    return None


def resolve(session, peak_id: int) -> PeakTarget | None:
    data = session.runtime.endgame
    stage = data.peak_stages.get(int(peak_id))
    if stage is None:
        return None
    group = group_of(session, peak_id)
    if group is None:
        return None
    is_boss = int(group.boss_level_id) == int(peak_id)
    return PeakTarget(
        group=group,
        stage=stage,
        boss=data.peak_boss(group.boss_level_id) if is_boss else None,
        is_boss=is_boss,
    )


def node_of(stage: PeakStage) -> ChallengeNode:
    """``ChallengeNode`` of one Peak row (same six scene fields as a challenge)."""
    return ChallengeNode(
        entrance_id=int(stage.entrance_id),
        plane_id=int(stage.plane_id),
        floor_id=int(stage.floor_id),
        world_id=int(stage.world_id),
        maze_group_id=int(stage.maze_group_id),
        monster_id=int(stage.monster_id),
        stage_id=int(stage.stage_id),
    )


def default_buff(session, ref: PeakTarget) -> int:
    """The BOSS's first blessing when the client did not pick one."""
    if ref.boss is not None and ref.boss.buff_ids:
        return int(ref.boss.buff_ids[0])
    return 0


def current_group(session) -> int:
    """Group the panel should open on: the highest one with progress, else the first."""
    save = session.runtime.save
    best = 0
    for group in groups(session):
        passed_peak = any(save.peak_record(pid).get("passed") for pid in group.pre_level_ids)
        if passed_peak or save.peak_record(group.boss_level_id).get("passed"):
            best = max(best, int(group.id))
    if best:
        return best
    return int(groups(session)[0].id) if groups(session) else 0


def hard_mode(session, group_id: int) -> bool:
    return bool(session.runtime.save.peak_group_record(group_id).get("hard"))


# -------------------------------------------------------------------- protos
def peak_proto(session, stage: PeakStage) -> pb.ChallengePeak:
    record = session.runtime.save.peak_record(stage.id)
    return pb.ChallengePeak(
        peak_id=int(stage.id),
        has_passed=bool(record.get("passed")),
        cycles_used=int(record.get("cycles") or 0),
        finished_target_list=[int(x) for x in (record.get("targets") or [])],
    )


def _clearance(record: dict, *, buff_id: int = 0) -> pb.ChallengePeakBossClearance:
    return pb.ChallengePeakBossClearance(
        has_passed=bool(record.get("passed")),
        best_cycle_count=int(record.get("cycles") or 0),
        buff_id=int(buff_id),
    )


def boss_proto(session, group: PeakGroup) -> pb.ChallengePeakBoss:
    save = session.runtime.save
    boss = session.runtime.endgame.peak_boss(group.boss_level_id)
    group_record = save.peak_group_record(group.id)
    easy = save.peak_record(group.boss_level_id)
    buff = int(boss.buff_ids[0]) if boss is not None and boss.buff_ids else 0
    hard_clear = {"passed": group_record.get("hard_passed"), "cycles": group_record.get("hard_cycles")}
    return pb.ChallengePeakBoss(
        easy_mode=_clearance(easy, buff_id=buff),
        hard_mode=_clearance(hard_clear),
        hard_mode_has_passed=bool(group_record.get("hard_passed")),
    )


def group_proto(session, group: PeakGroup) -> pb.ChallengePeakGroup:
    save = session.runtime.save
    stages = peaks_of(session, group)
    peaks: list[pb.ChallengePeak] = []
    stars = 0
    for stage in stages:
        proto = peak_proto(session, stage)
        peaks.append(proto)
        if proto.has_passed:
            stars += 1
    record = save.peak_group_record(group.id)
    return pb.ChallengePeakGroup(
        peak_group_id=int(group.id),
        peaks=peaks,
        peak_boss=boss_proto(session, group),
        obtained_stars=stars,
        taken_star_rewards=[int(x) for x in (record.get("taken") or [])],
        count_of_peaks=len([ps for ps in stages if int(ps.id) != int(group.boss_level_id)]),
        disable_hard_mode=False,
    )


def data_response(session) -> pb.GetChallengePeakDataScRsp:
    rsp = pb.GetChallengePeakDataScRsp(
        retcode=0,
        current_peak_group_id=current_group(session),
        challenge_peak_groups=[group_proto(session, group) for group in groups(session)],
    )
    return rsp


def cur_response(session) -> pb.GetCurChallengePeakScRsp:
    state = getattr(session, "challenge", None)
    if state is None or state.mode != PEAK:
        return pb.GetCurChallengePeakScRsp(retcode=0)
    return pb.GetCurChallengePeakScRsp(
        retcode=0,
        peak_id=int(state.peak_id),
        has_passed=bool(getattr(session, "peak_settled", False)),
        boss_buff_id=int(state.buff_ids[0]) if state.buff_ids else 0,
    )


# ---------------------------------------------------------------------- flow
def enter(session, ref: PeakTarget, *, avatars: list[int] | None = None, buff_id: int = 0):
    """Enter one Peak node; returns ``(state, scene)`` like ``challenge.enter``."""
    return challenge_service.enter_peak(
        session,
        peak_id=ref.peak_id,
        node=node_of(ref.stage),
        avatars=list(avatars or []),
        buff_ids=[int(buff_id)] if buff_id else [],
        group_id=int(ref.group.id),
        boss=ref.is_boss,
    )


async def settle(session, *, win: bool) -> None:
    """Settlement after ``PVEBattleResult``: notify + history + group update.

    The client keeps the Peak until ``ConfirmChallengePeakSettleCsReq``, so the
    state is only recorded here (``session.peak_settled``); the confirm handler
    clears it.
    """
    state = getattr(session, "challenge", None)
    if state is None:
        return
    save = session.runtime.save
    ref = resolve(session, int(state.peak_id))
    cycles = max(0, DEFAULT_ROUNDS - int(state.rounds_left))
    hard = bool(state.peak_hard) or hard_mode(session, int(state.peak_group_id))
    first_pass = False
    if win:
        first_pass = save.record_peak(int(state.peak_id), cycles=cycles, stars=1)
        if hard and ref is not None:
            save.record_peak_hard_clear(int(ref.group.id))
    notify = pb.ChallengePeakSettleScNotify(
        is_win=bool(win),
        peak_id=int(state.peak_id),
        cycles_used=cycles,
        turn_left=int(state.rounds_left),
        is_first_pass=bool(first_pass),
        hard_mode_has_passed=bool(hard and win),
        is_unlock_easy_boss=bool(win and first_pass and ref is not None and ref.is_boss),
        is_boss_target_better=False,
        is_wait_confirm=False,
    )
    session.peak_settled = bool(win)
    await session.send(notify)
    if ref is not None:
        await session.send(
            pb.ChallengePeakGroupDataUpdateScNotify(
                challenge_peak_group=group_proto(session, ref.group)
            )
        )
    session.runtime.logger.info(
        "peak settle: peak=%d group=%d boss=%s win=%s cycles=%d first=%s hard=%s",
        state.peak_id,
        state.peak_group_id,
        state.peak_boss,
        win,
        cycles,
        first_pass,
        hard,
    )
