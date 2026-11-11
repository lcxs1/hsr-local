"""Scene handlers (Phase 1: static world).

Movement is client-authoritative: we only record where the leader went so the
next ``GetCurSceneInfo`` / lineup refresh does not snap the team back to spawn.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from hsr.game.services import challenge as challenge_service
from hsr.game.services import lineup as lineup_service
from hsr.game.services import scene as scene_service
from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


async def on_get_cur_scene(
    session: Session, req: pb.GetCurSceneInfoCsReq
) -> pb.GetCurSceneInfoScRsp:
    return pb.GetCurSceneInfoScRsp(scene=scene_service.build_scene(session))


async def on_get_entered(session: Session, req: pb.GetEnteredSceneCsReq) -> pb.GetEnteredSceneScRsp:
    return scene_service.build_entered(session)


async def on_scene_map_info(
    session: Session, req: pb.GetSceneMapInfoCsReq
) -> pb.GetSceneMapInfoScRsp:
    # The client may ask about several floors at once (e.g. the map screen);
    # we only have one, so answer with the same floor for every identifier.
    count = max(1, len(req.scene_identifiers))
    return pb.GetSceneMapInfoScRsp(
        scene_map_info=[scene_service.build_map_info(session) for _ in range(count)]
    )


async def on_enter_section(session: Session, req: pb.EnterSectionCsReq) -> pb.EnterSectionScRsp:
    session.scene.section_id = req.section_id
    session.runtime.logger.debug("entered section %d", req.section_id)
    return pb.EnterSectionScRsp()


async def on_entity_move(session: Session, req: pb.SceneEntityMoveCsReq) -> pb.SceneEntityMoveScRsp:
    if req.entity_motion_list:
        # The client sends the whole team in one packet; the controlled slot is
        # not always entity 1 after a lineup change, so prefer it when present
        # and fall back to the first entry.
        leader = next(
            (
                m
                for m in req.entity_motion_list
                if m.entity_id == session.runtime.cfg.world.leader_entity_id
            ),
            req.entity_motion_list[0],
        )
        pos = leader.motion.pos_index
        session.scene.move_to((pos.x, pos.y, pos.z))
        if session.runtime.cfg.world.persist_position:
            session.runtime.save.set_position(session.scene.plane_id, session.scene.position)
    # Deliberately empty: echoing movement makes the client rubber-band when it
    # has already moved on.  The ack alone keeps the send window healthy.
    return pb.SceneEntityMoveScRsp()


async def on_entity_teleport(
    session: Session, req: pb.SceneEntityTeleportCsReq
) -> pb.SceneEntityTeleportScRsp:
    pos = req.entity_motion.motion.pos_index
    if req.entity_motion.entity_id:
        session.scene.move_to((pos.x, pos.y, pos.z))
        if session.runtime.cfg.world.persist_position:
            session.runtime.save.set_position(session.scene.plane_id, session.scene.position)
    return scene_service.build_teleport_rsp(session, req.entity_motion)


async def on_interact_prop(session: Session, req: pb.InteractPropCsReq) -> pb.InteractPropScRsp:
    state = session.scene.prop_states.get(req.prop_entity_id, 1)
    return pb.InteractPropScRsp(
        prop_entity_id=req.prop_entity_id,
        prop_state=state,
    )


async def on_cast_skill(session: Session, req: pb.SceneCastSkillCsReq) -> pb.SceneCastSkillScRsp:
    """Scene skill cast.

    Inside an endgame challenge this is the actual "start the fight" packet:
    the client expects ``monster_battle_info`` for the monster it hit plus the
    full ``SceneBattleInfo``.  In the normal world there are no monsters, so we
    keep acknowledging the cast (unchanged behaviour).
    """
    challenge = getattr(session, "challenge", None)
    if challenge is None:
        return pb.SceneCastSkillScRsp(cast_entity_id=req.cast_entity_id)

    targets = [int(entity_id) for entity_id in req.hit_target_entity_id_list]
    if not targets:
        targets = [challenge.monster_entity_id]
    targets = [entity_id for entity_id in targets if entity_id]
    # 换半场后场上怪物的实体 id 会变（1000 -> 1010）；客户端偶尔还拿旧 id 施放，
    # 这时回一个已经删掉的实体 id 会让它开不了战斗。只认当前场景里存在的实体。
    known = {
        int(entity.entity_id)
        for group in session.scene.extra_groups
        for entity in group.entity_list
    }
    if known:
        stale = [entity_id for entity_id in targets if entity_id not in known]
        if stale:
            session.runtime.logger.info(
                "scene cast skill: stale target(s) %s, using current monster %d",
                stale,
                challenge.monster_entity_id,
            )
        targets = [entity_id for entity_id in targets if entity_id in known]
    if not targets:
        targets = [challenge.monster_entity_id]
    info = challenge_service.battle_info(session)
    session.runtime.logger.info(
        "scene cast skill -> challenge battle: challenge=%d mode=%s stage=%d targets=%s",
        challenge.challenge_id,
        challenge.mode,
        challenge.stage_id,
        targets,
    )
    return pb.SceneCastSkillScRsp(
        cast_entity_id=req.cast_entity_id,
        monster_battle_info=[
            pb.HitMonsterBattleInfo(
                target_monster_entity_id=entity_id,
                monster_battle_type=challenge_service.TRIGGER_BATTLE,
            )
            for entity_id in targets
        ],
        battle_info=info,
    )


async def on_cast_skill_cost_mp(
    session: Session, req: pb.SceneCastSkillCostMpCsReq
) -> pb.SceneCastSkillCostMpScRsp:
    # 秘技消耗：客户端在发这个包之前已经把自己那份秘技点扣掉了，而回包没有点数字段，
    # 所以要点数回推。实测 1448 只能"扣"，加回去客户端不理（会一路扣到 0 然后拒绝
    # 施放，5 次即耗尽），所以补点必须用 SyncLineupNotify 重发整份阵容。
    # 策略见 config.toml 的 [gameplay].technique_points。
    messages = lineup_service.spend_mp(session, cast_entity_id=req.cast_entity_id)
    for message in messages:
        await session.send(message)
    session.runtime.logger.debug(
        "technique cast: entity=%d mp=%d/%d sync=%s (skill_index=%d)",
        req.cast_entity_id,
        lineup_service.current_mp(session),
        lineup_service.max_mp(session),
        len(messages) > 1,
        req.skill_index,
    )
    return pb.SceneCastSkillCostMpScRsp(cast_entity_id=req.cast_entity_id)


def scene_push_delay(session) -> float:
    """登录后多少秒内点「传送」要把场景推送压后（0 = 不用压）。

    4.7.0 真机：登录后 18 s / 14 s 两次传送都卡在客户端
    ``MapNPC:*-400001 / MapNpcDef.get_ConfigEntityPath``；113 s 那次正常。
    参数见 ``EndgameSettings.lobby_grace_seconds``。
    """
    grace = float(
        getattr(session.runtime.cfg.endgame, "lobby_grace_seconds", 0) or 0
    )
    if grace <= 0:
        return 0.0
    elapsed = time.monotonic() - float(getattr(session, "created_at", 0.0) or 0.0)
    return max(0.0, grace - elapsed)


def lobby_teleport_blocked(session) -> bool:
    """「传送」进忘却之庭大厅会不会把客户端卡死。

    4.6.54 / 4.7.0 **两个客户端**都在加载 plane 10000 的 floor 时挂在一个
    资源/配置残缺的地图 NPC 上（`[MapNPC:227-400001]`，换到 100000352 变成
    `[MapNPC:103-400001]`，都报 `MapNpcDef.get_ConfigEntityPath` NRE，随后
    SDK 弹确认框、会话被关）。这条链服务端补不了（客户端自己的 floor 配置），
    所以默认**不切场景**：回一个 ``EnterSceneScRsp`` + 当前场景，客户端重载后
    留在原地，不会硬卡；混沌回忆请从「星际和平指南」面板进。

    ``[endgame] lobby_teleport``：
      * ``auto``（默认）= 4.7.0 忽略、4.6.54 照旧（老客户端的大厅偶尔能过，
        15 层闭环就是走大厅打通的）
      * ``ignore`` = 都忽略
      * ``scene``  = 都切场景（对照实验用，会卡）
    """
    mode = str(getattr(session.runtime.cfg.endgame, "lobby_teleport", "auto")).lower()
    if mode == "scene":
        return False
    if mode == "ignore":
        return True
    profile = getattr(getattr(session.runtime, "profile", None), "name", "")
    profile = profile or str(getattr(session.runtime.cfg.server, "profile", ""))
    return profile.startswith("4.7")


async def on_enter_scene(session: Session, req: pb.EnterSceneCsReq) -> pb.EnterSceneScRsp | None:
    """``EnterSceneCsReq``: teleport / scene switch (guide 传送, challenge lobby).

    The client sends an entrance id + a floor identifier.  When the entrance is
    known (``MapEntrance`` in ``data/endgame.json``) we switch the session scene
    and answer like pearl-sr: ``EnterSceneScRsp`` first, then
    ``EnterSceneByServerScNotify`` with the new ``SceneInfo``.  Unknown ids keep
    the old single-scene behaviour instead of dumping the player into nothing.

    Teleports into an exported **lobby plane** are ignored when
    :func:`lobby_teleport_blocked` says so (default on 4.7.0): those floors
    hard-lock the client on a broken map NPC, and the challenge panel does not
    need the lobby at all.
    """
    state = session.scene
    entry_id = int(req.entry_id) or state.entry_id
    runtime = session.runtime
    override_entry = int(getattr(runtime.cfg.endgame, "lobby_entry", 0) or 0)
    requested = runtime.endgame.entrance(entry_id)
    overridden = False
    if (
        override_entry
        and requested is not None
        and int(requested["plane_id"]) == 10000
    ):
        entry_id = override_entry
        requested = runtime.endgame.entrance(entry_id)
        overridden = True
    entrance = requested
    if entrance is None:
        if entry_id != state.entry_id:
            runtime.logger.info(
                "teleport to unknown entry %d ignored (staying on %d)",
                entry_id,
                state.entry_id,
            )
        return pb.EnterSceneScRsp(
            is_close_map=req.is_close_map,
            scene_identifier=state.identifier,
        )
    lobby_plane = int(entrance["plane_id"]) in runtime.endgame.lobbies
    if lobby_plane and not overridden and lobby_teleport_blocked(session):
        runtime.logger.warning(
            "lobby teleport ignored: entry=%d plane=%d (client hard-locks on"
            " MapNPC:*-400001; open 混沌回忆 from the 星际和平指南 panel)",
            entry_id,
            int(entrance["plane_id"]),
        )
        await session.send(
            pb.EnterSceneScRsp(
                retcode=0,
                is_over_map=True,
                is_close_map=req.is_close_map,
                scene_identifier=state.identifier,
            )
        )
        await session.send(
            pb.EnterSceneByServerScNotify(
                reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
                lineup=lineup_service.build_lineup(session),
                scene=scene_service.build_scene(session),
            )
        )
        return None
    if entry_id == state.entry_id:
        # 同一个入口再点一次「传送」（例如回到大厅后还想再传一次）不能让按钮失效：
        # 服务端要把场景重新下发一遍客户端才会重载。2026-11-11 真机踩到。
        runtime.logger.info("enter scene (re-enter): entry=%d", entry_id)
    floor_id = (
        0
        if overridden
        else (int(req.scene_identifier.floor_id) if req.HasField("scene_identifier") else 0)
    ) or int(entrance["floor_id"])
    scene = scene_service.enter_scene(
        session,
        plane_id=entrance["plane_id"],
        floor_id=floor_id,
        entry_id=entry_id,
    )
    identifier = (
        req.scene_identifier
        if req.HasField("scene_identifier")
        else pb.SceneIdentifier(floor_id=floor_id)
    )
    await session.send(
        pb.EnterSceneScRsp(
            retcode=0,
            is_over_map=True,
            is_close_map=req.is_close_map,
            scene_identifier=identifier,
        )
    )
    notify = pb.EnterSceneByServerScNotify(
        reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
        lineup=lineup_service.build_lineup(session),
        scene=scene,
    )
    delay = scene_push_delay(session)
    if delay > 0:
        # 客户端刚登录时自己的场景加载态还没就绪，这时候推场景会在
        # `AdventurePhase._EnterStep2` 里撞上 `MapNPC:*-400001` 的 NRE 硬卡。
        # 回包立刻给（客户端进度条不至于超时），场景晚点推。
        runtime.logger.info(
            "enter scene: hold scene push %.1fs (client still loading after login)",
            delay,
        )

        async def _push_later() -> None:
            await asyncio.sleep(delay)
            if not session.closed:
                await session.send(notify)

        asyncio.create_task(_push_later())
    else:
        await session.send(notify)
    runtime.logger.info(
        "enter scene: entry=%d plane=%d floor=%d",
        entry_id,
        entrance["plane_id"],
        floor_id,
    )
    return None


    return pb.EnterSceneScRsp(
        is_close_map=req.is_close_map,
        scene_identifier=state.identifier,
    )


async def on_unlock_teleport(
    session: Session, req: pb.GetUnlockTeleportCsReq
) -> pb.GetUnlockTeleportScRsp:
    world = session.runtime.cfg.world
    known = set(req.entry_id_list) | set(world.teleport_ids) | {session.scene.entry_id}
    return pb.GetUnlockTeleportScRsp(unlocked_teleport_list=sorted(known))


async def on_group_state_change(
    session: Session, req: pb.GroupStateChangeCsReq
) -> pb.GroupStateChangeScRsp:
    return pb.GroupStateChangeScRsp(group_state_info=req.group_state_info)


async def on_update_group_property(
    session: Session, req: pb.UpdateGroupPropertyCsReq
) -> pb.UpdateGroupPropertyScRsp:
    return pb.UpdateGroupPropertyScRsp(
        group_id=req.group_id,
        floor_id=req.floor_id or session.scene.floor_id,
        dimension_id=req.dimension_id,
        HMELFPDPCFC=req.HMELFPDPCFC,
    )


async def on_refresh_trigger(
    session: Session, req: pb.RefreshTriggerByClientCsReq
) -> pb.RefreshTriggerByClientScRsp:
    return pb.RefreshTriggerByClientScRsp(
        trigger_entity_id=req.trigger_entity_id,
        trigger_name=req.trigger_name,
        refresh_trigger=False,
    )


def register(router: Router) -> None:
    router.add("GetCurSceneInfoCsReq", on_get_cur_scene)
    router.add("GetEnteredSceneCsReq", on_get_entered)
    router.add("GetSceneMapInfoCsReq", on_scene_map_info)
    router.add("EnterSectionCsReq", on_enter_section)
    router.add("SceneEntityMoveCsReq", on_entity_move)
    router.add("SceneEntityTeleportCsReq", on_entity_teleport)
    router.add("InteractPropCsReq", on_interact_prop)
    router.add("SceneCastSkillCsReq", on_cast_skill)
    router.add("SceneCastSkillCostMpCsReq", on_cast_skill_cost_mp)
    router.add("EnterSceneCsReq", on_enter_scene)
    router.add("GetUnlockTeleportCsReq", on_unlock_teleport)
    router.add("GroupStateChangeCsReq", on_group_state_change)
    router.add("UpdateGroupPropertyCsReq", on_update_group_property)
    router.add("RefreshTriggerByClientCsReq", on_refresh_trigger)
