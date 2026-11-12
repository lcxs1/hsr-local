"""Lineup state and protobuf builders."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hsr.protocol.gen import StarRail_pb2 as pb

# Out-of-combat HP is a display value only: hsr-local has no HP simulation, so
# every snapshot (LineupInfo and GetLineupAvatarDataScRsp) reports full HP.
FULL_HP = 10000

# 秘技点：LineupInfo.mp/max_mp（不是 MP 值，是队伍共享的秘技点）。默认 5 点，
# 与 pearl 的 lineup_mgr.zig（mp=5/max_mp=5）和丹恒的 GetMaxMp()=5 一致。
DEFAULT_MAX_MP = 5


@dataclass
class LineupState:
    slots: list[int] = field(default_factory=list)
    leader: int = 0
    # 当前秘技点；None = 用 gameplay.technique_points_max（Session 登录时会填）
    mp: int | None = None
    # 客户端那份计数的估计值：客户端发 1498 前已经自己扣过，服务端加点时它并不听
    # SceneCastSkillMpUpdateScNotify(1448)（真机实测），所以要靠 SyncLineupNotify
    # 整体刷新阵容来覆盖它。这个字段只用来判断"什么时候值得刷一次"。
    mp_client: int | None = None
    # 上次变动/恢复的时间（time.monotonic），心跳驱动的定时恢复用它计时
    mp_updated_at: float = 0.0

    def set_slot(self, slot: int, avatar_id: int, team_size: int = 4) -> None:
        # slot 直接来自客户端包，必须钳制：负值会静默写 self.slots[-1]，
        # 超大值会让 while 循环 append 数十亿个 0（OOM）。team_size 由 world.team_size 提供。
        if not 0 <= slot < team_size:
            raise ValueError(f"slot {slot} out of range [0, {team_size})")
        while len(self.slots) <= slot:
            self.slots.append(0)
        self.slots[slot] = avatar_id


def max_mp(session) -> int:
    return max(1, int(getattr(session.runtime.cfg.gameplay, "technique_points_max", DEFAULT_MAX_MP)))


def current_mp(session) -> int:
    """秘技点，夹在 [0, max] 之间；未初始化时按满点算。"""
    cap = max_mp(session)
    value = session.lineup.mp
    if value is None:
        return cap
    return max(0, min(int(value), cap))


def _fill(session, value: int) -> None:
    session.lineup.mp = max(0, min(value, max_mp(session)))


def _sync_lineup(session) -> pb.SyncLineupNotify:
    """客户端唯一认的"加点"方式：整份阵容（含 mp）重发。"""
    if session.lineup.mp_client is not None:
        session.lineup.mp_client = current_mp(session)
    return pb.SyncLineupNotify(lineup=build_lineup(session))


def spend_mp(session, cast_entity_id: int = 0) -> list[object]:
    """放一次秘技：按 gameplay.technique_points 结算，返回要推给客户端的消息。

    客户端在发包前就已经把自己那份点数扣掉了，所以回包（ScRsp）永远只是 ack；
    点数要靠 1448 或 SyncLineupNotify 推回去。实测客户端**只认后者**，所以只在
    "服务端点数高于客户端那份"时才补一次阵容刷新，正常扣点不刷、避免每放一次就
    重建角色。
    """
    gameplay = session.runtime.cfg.gameplay
    mode = gameplay.technique_points
    cap = max_mp(session)
    lineup = session.lineup

    # 客户端自己先扣的那一点
    if lineup.mp_client is None:
        lineup.mp_client = current_mp(session)
    lineup.mp_client = max(0, lineup.mp_client - 1)

    if mode == "unlimited":
        # 永远是满点：1448 客户端只认"扣"，所以必须每放一次就刷一次阵容
        # （这是真机上唯一能把点数写回去的包），代价是多一个 60 字节左右的 Notify。
        value = cap
    else:
        value = max(0, current_mp(session) - 1)
        if mode == "on_empty" and value == 0:
            value = cap

    _fill(session, value)
    lineup.mp_updated_at = time.monotonic()

    messages: list[object] = [
        pb.SceneCastSkillMpUpdateScNotify(
            mp=current_mp(session), cast_entity_id=cast_entity_id
        )
    ]
    if current_mp(session) > lineup.mp_client:
        messages.append(_sync_lineup(session))
    return messages


def tick_mp(session, now: float | None = None) -> list[object] | None:
    """心跳驱动的定时恢复；只在 ``technique_points = "refill"`` 时生效。

    返回要推送的消息（没有变化就是 None）。放在心跳里跑，等价于"每 5 秒
    检查一次"，不需要额外任务/定时器，客户端断开后自然停止。
    """
    gameplay = session.runtime.cfg.gameplay
    if gameplay.technique_points != "refill":
        return None
    if now is None:
        now = time.monotonic()
    cap = max_mp(session)
    if current_mp(session) >= cap:
        session.lineup.mp_updated_at = now
        return None
    interval = float(gameplay.technique_refill_seconds)
    if now - session.lineup.mp_updated_at < interval:
        return None
    _fill(session, current_mp(session) + 1)
    session.lineup.mp_updated_at = now

    messages: list[object] = [
        pb.SceneCastSkillMpUpdateScNotify(mp=current_mp(session), cast_entity_id=0)
    ]
    if session.lineup.mp_client is None or current_mp(session) > session.lineup.mp_client:
        messages.append(_sync_lineup(session))
    return messages


def build_lineup(session, *, extra: int = 0) -> pb.LineupInfo:
    lineup = pb.LineupInfo(
        name="Squad 1",
        mp=current_mp(session),
        max_mp=max_mp(session),
        leader_slot=session.lineup.leader,
        index=0,
        plane_id=0,
        is_virtual=False,
        extra_lineup_type=pb.ExtraLineupType.ExtraLineupType_LineupNone,
    )
    team_size = getattr(session.runtime.cfg.world, "team_size", 4)
    roster = session.runtime.roster
    for slot, avatar_id in enumerate(session.lineup.slots[:team_size]):
        if not avatar_id:
            continue
        lineup.avatar_list.append(
            pb.LineupAvatar(
                avatar_type=pb.AvatarType.AvatarType_AvatarFormalType,
                sp_bar=pb.SpBarInfo(cur_sp=10000, max_sp=10000),
                hp=FULL_HP,
                satiety=100,
                id=roster.lineup_id(
                    avatar_id,
                    tb_path=getattr(session, "tb_multi_path", None),
                    march_path=getattr(session, "march_multi_path", None),
                ),
                slot=slot,
            )
        )
    return lineup


def build_avatar_data(session) -> list[pb.LineupAvatarData]:
    """HP table for the team-prep screen (``GetLineupAvatarDataScRsp``).

    The response is a map keyed by avatar id (no slot/order field), so two id
    granularities are both emitted to survive either client lookup:
    * the *base* id of every owned character — Danheng sends exactly this
      (``AvatarData.FormalAvatars`` -> ``AvatarId``);
    * the *form* id the lineup uses for multi-path characters (8001..8010,
      1001/1224), because ``LineupInfo.avatar_list`` lists them by form.

    The client is asked right before a cocoon starts (``SetClientPaused`` ->
    this -> ``StartCocoonStage``), i.e. to paint the team's HP bars.
    """
    roster = session.runtime.roster
    ids: set[int] = set(roster.forms)  # owned base ids
    for slot_avatar in session.lineup.slots:
        if slot_avatar:
            ids.add(
                roster.lineup_id(
                    roster.base_id(slot_avatar),
                    tb_path=getattr(session, "tb_multi_path", None),
                    march_path=getattr(session, "march_multi_path", None),
                )
            )
    return [
        pb.LineupAvatarData(
            id=avatar_id,
            hp=FULL_HP,
            avatar_type=pb.AvatarType.AvatarType_AvatarFormalType,
        )
        for avatar_id in sorted(ids)
    ]


async def notify(session) -> None:
    """Push scene entities + lineup after a lineup change."""
    from hsr.game.services import scene as scene_service

    await session.send(scene_service.build_refresh(session))
    await session.send(pb.SyncLineupNotify(lineup=build_lineup(session)))
