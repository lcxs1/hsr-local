"""Offline replay of the five minimal-playable chains.

The real client is still the source of truth, but this harness drives the same
handlers a client would hit, in the same order, without UDP/KCP.  It is used by
``scripts/replay_flows.py`` and by the unit tests so a regression in login,
lineup, path switching, battle start or battle retry fails locally first.

The replay deliberately writes to a temporary save file; the player's real
``saves/player.json`` is copied in and never modified.
"""

from __future__ import annotations

import copy
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from hsr.game.handlers import avatar as avatar_handlers
from hsr.game.handlers import battle as battle_handlers
from hsr.game.handlers import challenge as challenge_handlers
from hsr.game.handlers import lineup as lineup_handlers
from hsr.game.handlers import login as login_handlers
from hsr.game.handlers import scene as scene_handlers
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.game.services import snapshot
from hsr.game.services.lineup import LineupState
from hsr.game.services.save import SaveStore
from hsr.net.session import Session
from hsr.protocol.gen import StarRail_pb2 as pb


class ReplayError(RuntimeError):
    """The replay produced a result the real client would reject."""


# 官方抓包（docs/notes/2026-11-12-正式服抓包方案.md §8）换半场的包序：
# 删旧怪 → 换角色实体 → 3×阵容 → 挑战槽位 → 声明新组 → 移动通知。
# ``PVEBattleResultScRsp`` 不在表里：官方永远把它放在最后。
OFFICIAL_SWITCH = [
    "SyncLineupNotify",
    "ScenePlaneEventScNotify",
    "SceneGroupRefreshScNotify",  # group 9: 删上半的怪（refresh_type=3）
    "SceneGroupRefreshScNotify",  # 换场上角色实体（删旧 id + 加新 id）
    "SyncLineupNotify",
    "SyncLineupNotify",
    "SyncLineupNotify",
    "ChallengeLineupNotify",
    "SceneGroupRefreshScNotify",  # group 10: 声明下半的组（group_info + LOADED）
    "SceneEntityMoveScNotify",
]


@dataclass
class ReplayResult:
    battle_ids: list[int] = field(default_factory=list)
    lineup_base: list[int] = field(default_factory=list)
    avatar_form: int = 0
    events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": True,
            "battle_ids": self.battle_ids,
            "lineup_base": self.lineup_base,
            "avatar_form": self.avatar_form,
            "events": self.events,
        }


class ReplaySession:
    """The subset of ``Session`` used by the handlers, with send capture."""

    def __init__(self, runtime):
        self.runtime = runtime
        saved = runtime.save.player
        wanted = (
            saved.lineup or runtime.cfg.player.default_lineup or runtime.roster.default_lineup()
        )
        slots = runtime.roster.repair_lineup(wanted, runtime.cfg.world.team_size)
        leader = int(saved.leader)
        if not 0 <= leader < len(slots):
            leader = 0

        self.lineup = LineupState(slots=slots, leader=leader)
        self.tb_multi_path = Session._resolve_tb_path(
            saved.tb_multi_path,
            runtime.cfg.player.tb_multi_path,
            runtime.roster,
        )
        self.march_multi_path = Session._resolve_path(
            saved.march_multi_path,
            runtime.cfg.player.march_multi_path,
            runtime.roster,
            (1001, 1224),
        )
        self.scene = scene_service.SceneState.from_config(runtime.cfg.world)
        if runtime.cfg.world.persist_position and len(saved.pos) == 3:
            self.scene.position = (saved.pos[0], saved.pos[1], saved.pos[2])
        self.battle_seq = 0
        self.battle_info = None
        self.sent: list[object] = []

    async def send(self, msg, name: str | None = None) -> None:
        self.sent.append(msg)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ReplayError(message)


async def run_core_flow(rt=None, *, strict: bool = False) -> ReplayResult:
    """Run login → scene → lineup → path → battle → retry.

    ``rt`` is optional so the script can reuse a loaded Runtime; tests may pass
    one as well.  A temporary save is always used.
    """
    if rt is None:
        from hsr.runtime import Runtime

        rt = Runtime.load(strict=strict)

    with tempfile.TemporaryDirectory(prefix="hsr-replay-") as tmp:
        save = SaveStore(
            path=Path(tmp) / "player.json",
            player=copy.deepcopy(rt.save.player),
        )
        local = replace(rt, save=save)
        session = ReplaySession(local)
        result = ReplayResult()

        # 1) login / basic info / avatar data / current scene
        login = await login_handlers.on_login(session, pb.PlayerLoginCsReq())
        _check(login.basic_info.nickname == local.cfg.player.name, "login nickname mismatch")
        result.events.append("login")

        basic = await login_handlers.on_basic_info(session, pb.GetBasicInfoCsReq())
        _check(basic.is_gender_set, "basic info gender missing")
        result.events.append("basic_info")

        avatars = await avatar_handlers.on_get_avatar_data(session, pb.GetAvatarDataCsReq())
        _check(avatars.is_get_all, "avatar sync is not full")
        _check(any(a.base_avatar_id == 8001 for a in avatars.avatar_list), "Trailblazer missing")
        _check(
            any(8001 <= p.avatar_id <= 8010 for p in avatars.avatar_path_data_info_list),
            "Trailblazer path data missing",
        )
        _check(1100101 in avatars.skin_list, "March skin is not advertised")
        result.events.append("avatar_data")

        scene = await scene_handlers.on_get_cur_scene(session, pb.GetCurSceneInfoCsReq())
        _check(scene.scene.floor_id == local.cfg.world.floor_id, "scene floor mismatch")
        result.events.append("scene")

        # 2) replace lineup with the Trailblazer present
        slots = [8001, 1506, 1501, 1502]
        _check(all(local.roster.has_base(a) for a in slots), "replay lineup has unknown avatar")
        req = pb.ReplaceLineupCsReq(
            leader_slot=0,
            lineup_slot_list=[
                pb.LineupSlotData(slot=index, id=avatar_id)
                for index, avatar_id in enumerate(slots)
            ],
        )
        rsp = await lineup_handlers.on_replace(session, req)
        _check(rsp.retcode == 0, "replace lineup failed")
        _check(session.lineup.slots[:4] == slots, "lineup did not persist")
        result.events.append("lineup_replace")

        # 3) switch the Trailblazer path and verify the lineup uses the form id
        target_path = 8003 if local.cfg.player.gender == 1 else 8004
        path_rsp = await avatar_handlers.on_set_avatar_path(
            session, pb.SetAvatarPathCsReq(avatar_id=target_path)
        )
        _check(path_rsp.retcode == 0, "path switch failed")
        _check(session.tb_multi_path == target_path, "path did not update")
        _check(save.player.tb_multi_path == target_path, "path was not persisted")
        wire = lineup_service.build_lineup(session)
        _check(wire.avatar_list[0].id == target_path, "lineup does not use the form id")
        result.avatar_form = target_path
        result.lineup_base = list(session.lineup.slots[:4])
        result.events.append("avatar_path")

        # 3b) dress/remove a skin (March 7th's skin is present in skins.json)
        if local.skins.for_avatar(1001):
            skin_id = local.skins.for_avatar(1001)[0]
            skin_rsp = await avatar_handlers.on_dress_avatar_skin(
                session, pb.DressAvatarSkinCsReq(avatar_id=1001, skin_id=skin_id)
            )
            _check(skin_rsp.retcode == 0, "dress skin failed")
            _check(save.player.skins.get(1001) == skin_id, "skin was not persisted")
            sync = snapshot.avatar_sync(session)
            path = next(p for p in sync.avatar_path_data_info_list if p.avatar_id == 1001)
            _check(path.dressed_skin_id == skin_id, "skin was not synced")
            result.events.append("skin_dress")

            off_rsp = await avatar_handlers.on_take_off_avatar_skin(
                session, pb.TakeOffAvatarSkinCsReq(avatar_id=1001)
            )
            _check(off_rsp.retcode == 0, "take off skin failed")
            _check(1001 not in save.player.skins, "skin was not removed")
            result.events.append("skin_takeoff")

        # 3c) the team-prep HP table must cover the lineup's form ids
        hp_table = await lineup_handlers.on_get_avatar_data(
            session, pb.GetLineupAvatarDataCsReq()
        )
        by_id = {entry.id: entry for entry in hp_table.avatar_data_list}
        _check(target_path in by_id, "hp table misses the Trailblazer form id")
        _check(set(local.roster.forms) <= set(by_id), "hp table misses owned avatars")
        _check(
            by_id[target_path].hp == lineup_service.FULL_HP,
            "hp table has a wrong hp value",
        )
        result.events.append("lineup_hp")

        # 4) start the cocoon, settle it, and read the active battle back
        start = await battle_handlers.on_start_cocoon(
            session,
            pb.StartCocoonStageCsReq(cocoon_id=1014, wave=0, prop_entity_id=1337),
        )
        _check(start.battle_info.stage_id == 1052106, "cocoon mapped to the wrong stage")
        first_id = start.battle_info.battle_id
        result.battle_ids.append(first_id)
        active = await battle_handlers.on_get_cur_battle(
            session, pb.GetCurBattleInfoCsReq()
        )
        _check(active.HasField("battle_info"), "active battle was not exposed")
        result.events.append("battle_start")

        settled = await battle_handlers.on_pve_result(
            session,
            pb.PVEBattleResultCsReq(
                battle_id=first_id,
                stage_id=1052106,
                end_status=pb.BattleEndStatus.BATTLE_END_WIN,
            ),
        )
        _check(settled.retcode == 0, "battle result failed")
        _check(session.battle_info is None, "battle was not cleared")
        result.events.append("battle_result")

        # 5) "battle again": a second start must get a new battle id
        second = await battle_handlers.on_start_cocoon(
            session,
            pb.StartCocoonStageCsReq(cocoon_id=1014, wave=0, prop_entity_id=1337),
        )
        second_id = second.battle_info.battle_id
        result.battle_ids.append(second_id)
        _check(second_id != first_id, "battle id was reused; retry will stall")
        result.events.append("battle_retry")

        await battle_handlers.on_quit_battle(session, pb.QuitBattleCsReq())
        _check(session.battle_info is None, "quit did not clear the active battle")
        result.events.append("battle_quit")

    return result


async def run_challenge_flow(
    rt=None,
    *,
    strict: bool = False,
    challenge_id: int | None = None,
    advance: str = "sync",
) -> ReplayResult:
    """Run 混沌回忆 two-half: start → 上半 → 换组 → 下半 → 结算 → 退出.

    This is the chain that kept regressing (下半黑屏).  It asserts the reference
    packet sequence of the phase switch, the two teams, and that only the last
    phase settles.
    """
    if rt is None:
        from hsr.runtime import Runtime

        rt = Runtime.load(strict=strict)

    with tempfile.TemporaryDirectory(prefix="hsr-replay-") as tmp:
        save = SaveStore(path=Path(tmp) / "player.json", player=copy.deepcopy(rt.save.player))
        local = replace(rt, save=save, cfg=copy.deepcopy(rt.cfg))
        local.cfg.endgame.advance = advance
        local.cfg.endgame.avatar_swap = "renumber"  # 回放固定按官方做法，不受 config.toml 探针影响
        session = ReplaySession(local)
        result = ReplayResult()

        data = local.endgame
        challenge = None
        if challenge_id is not None:
            challenge = data.challenge(challenge_id)
        else:
            group = data.active_group("MOC") or 100
            candidates = [
                entry
                for entry in data.challenges.values()
                if entry.group_id == group and entry.node2 is not None
            ]
            challenge = min(candidates, key=lambda entry: entry.id) if candidates else None
        if challenge is None or challenge.node2 is None:
            raise ReplayError("no two-half MOC challenge in the data pack")

        first = [entry for entry in session.lineup.slots if entry][:4]
        second = list(reversed(first))
        start = await challenge_handlers.on_start_challenge(
            session,
            pb.StartChallengeCsReq(
                challenge_id=challenge.id,
                avatar_lineup_first=[pb.AvatarLineup(id=int(a)) for a in first],
                avatar_lineup_second=[pb.AvatarLineup(id=int(a)) for a in second],
            ),
        )
        _check(start.retcode == 0, "start challenge failed")
        _check(
            len(start.lineup_list) == 2,
            "StageNum >= 2 must carry both phase lineups",
        )
        _check(
            start.lineup_list[0].extra_lineup_type
            == pb.ExtraLineupType.ExtraLineupType_LineupChallenge,
            "first lineup is not LineupChallenge",
        )
        _check(
            start.lineup_list[1].extra_lineup_type
            == pb.ExtraLineupType.ExtraLineupType_LineupChallenge2,
            "second lineup is not LineupChallenge2",
        )
        _check(start.scene.entry_id == challenge.node1.entrance_id, "上半 scene mismatch")
        session.sent.clear()
        result.events.append("challenge_start")

        async def fight() -> pb.SceneCastSkillScRsp:
            cast = await scene_handlers.on_cast_skill(
                session, pb.SceneCastSkillCsReq(cast_entity_id=1)
            )
            _check(cast.HasField("battle_info"), "challenge cast returned no battle")
            _check(
                cast.battle_info.stage_id == session.challenge.stage_id,
                "battle stage does not match the phase",
            )
            return cast

        async def finish(cast: pb.SceneCastSkillScRsp) -> None:
            await battle_handlers.on_pve_result(
                session,
                pb.PVEBattleResultCsReq(
                    battle_id=cast.battle_info.battle_id,
                    stage_id=cast.battle_info.stage_id,
                    end_status=pb.BattleEndStatus.BATTLE_END_WIN,
                ),
            )

        upper = await fight()
        _check(
            [avatar.id for avatar in upper.battle_info.battle_avatar_list]
            == [local.roster.base_id(a) for a in first],
            "上半 battle uses the wrong team",
        )
        result.events.append("challenge_battle_1")

        session.sent.clear()
        await finish(upper)
        names = [type(message).__name__ for message in session.sent]
        _check("ChallengeSettleNotify" not in names, "上半 must not settle the run")
        if advance == "official":
            # 官方：整段换半场包在战斗态里就发完，结果包最后才到。
            _check(
                names == [*OFFICIAL_SWITCH, "PVEBattleResultScRsp"],
                f"official switch sequence mismatch: {names}",
            )
            _check(
                session.sent[-1].stage_id == challenge.node1.stage_id,
                "official switch must ack the upper half last",
            )
            declared = session.sent[OFFICIAL_SWITCH.index("ChallengeLineupNotify") + 1]
            _check(
                declared.group_refresh_list[0].group_id
                == challenge.node2.maze_group_id
                and declared.group_refresh_list[0].refresh_type
                == pb.SceneGroupRefreshType.SCENE_GROUP_REFRESH_TYPE_LOADED,
                "official switch did not declare the lower arena group",
            )
            _check(
                [group.group_id for group in session.scene.extra_groups]
                == [challenge.node2.maze_group_id],
                "official switch left the old arena group behind",
            )
        else:
            try:
                rsp_index = names.index("PVEBattleResultScRsp")
            except ValueError as exc:
                raise ReplayError(
                    f"advance did not ack the battle result: {names}"
                ) from exc
            _check(
                names[:rsp_index] == ["ChallengeLineupNotify", "SyncLineupNotify"],
                f"lineup switch must precede the result ack: {names}",
            )
            _check(
                names[rsp_index + 1 :]
                == ["SyncServerSceneChangeNotify", "EnterSceneByServerScNotify"],
                f"scene-change sequence mismatch: {names}",
            )
            scene = session.sent[-1].scene
            groups = [group.group_id for group in scene.entity_group_list]
            _check(
                challenge.node2.maze_group_id in groups,
                "new arena group was not declared in the scene",
            )
            _check(
                challenge.node1.maze_group_id not in groups,
                "old arena group must not stay in the new scene",
            )
        result.events.append("challenge_advance")

        lower = await fight()
        _check(lower.battle_info.stage_id == challenge.node2.stage_id, "下半 stage mismatch")
        _check(
            [avatar.id for avatar in lower.battle_info.battle_avatar_list]
            == [local.roster.base_id(a) for a in second],
            "下半 battle uses the wrong team",
        )
        result.battle_ids.extend([upper.battle_info.battle_id, lower.battle_info.battle_id])
        result.events.append("challenge_battle_2")

        session.sent.clear()
        await finish(lower)
        names = [type(message).__name__ for message in session.sent]
        _check("ChallengeSettleNotify" in names, "下半 did not settle the run")
        settle = [message for message in session.sent if type(message).__name__ == "ChallengeSettleNotify"][0]
        _check(settle.is_win and settle.star == 3, "settle payload is wrong")
        _check(
            settle.cur_challenge.status == pb.ChallengeStatus.CHALLENGE_FINISH,
            "settle did not mark the run finished",
        )
        result.events.append("challenge_settle")

        session.sent.clear()
        await challenge_handlers.on_leave_challenge(session, pb.LeaveChallengeCsReq())
        names = [type(message).__name__ for message in session.sent]
        _check("ChallengeLineupNotify" in names, "leave did not reset the extra lineup")
        _check(
            session.sent[0].extra_lineup_type
            == pb.ExtraLineupType.ExtraLineupType_LineupNone,
            "leave did not switch the lineup back to LineupNone",
        )
        _check(session.challenge is None, "challenge state survived leave")
        result.events.append("challenge_leave")

    return result
