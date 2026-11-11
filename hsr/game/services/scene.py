"""Static world state + protobuf builders (Phase 1).

Plane ``20313`` (筑梦边境 / Dream's Edge) is the map both reference servers use:
a clean, flat Penacony courtyard with a single calyx and no monsters or NPCs.
That makes it the safest place to validate "enter the world, walk around, open
the map" before any battle code exists.  A data-driven map is a later phase.

Everything the client needs to render the world comes from three messages:

    GetCurSceneInfoScRsp    -> SceneInfo (who is where, which groups are loaded)
    GetSceneMapInfoScRsp    -> SceneMapInfo (map props, teleports, lightened sections)
    SceneEntityMoveScRsp    -> acknowledgement for client-authoritative movement

The server is *not* authoritative for position: the client moves the leader and
tells us where it went.  We record it so that a lineup refresh or a fresh
GetCurSceneInfo puts the avatars back where the player actually is instead of
teleporting them to the spawn point.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from hsr.protocol.gen import StarRail_pb2 as pb


def _lighten_sections(world) -> list[int]:
    sections: list[int] = []
    for start, end in world.lighten_section_ranges:
        if end < start:
            start, end = end, start
        sections.extend(range(start, end + 1))
    return sections


@dataclass
class SceneState:
    """Per-session world state (position, interact id, loaded props)."""

    plane_id: int
    floor_id: int
    entry_id: int
    map_layer: int
    position: tuple[int, int, int]
    interact_id: int
    dimension_id: int = 0
    section_id: int = 0
    client_pos_version: int = 1
    # 普通世界 = 1；挑战场景 = 4（pearl-sr 的 ChallengeSceneManager 用 4）。
    game_mode_type: int = 1
    # 挑战场景才有意义（MazePlane.WorldID）；普通世界保持 0。
    world_id: int = 0
    # 挑战场景需要下发的 floor saved values（SceneInfo.floor_saved_data）。
    saved_data: dict[str, int] = field(default_factory=dict)
    # 场景道具（目前只有凝滞虚影）的交互状态。注意：**当前没有任何 handler 写入
    # 它**，所有读取处都会回落到默认值 1；留在这里是为了以后接 prop 状态同步时
    # 有地方落数据。战斗走的是 cocoon 流程，不依赖这个字段。
    prop_states: dict[int, int] = field(default_factory=dict)
    # 挑战场景的非 avatar 实体组（NPC 怪 + props）。普通世界为空，走 calyx 组。
    extra_groups: list[pb.SceneEntityGroupInfo] = field(default_factory=list)

    @classmethod
    def from_config(cls, world) -> SceneState:
        """Build the initial per-session world state from data/world.json.

        ``floor_id``/``entry_id`` fall back to ``plane_id * 1000 + 1`` and
        ``plane_id * 100 + 1`` (20313 -> 20313001 / 2031301), which is what the
        client uses on this plane; world.json normally sets them explicitly and
        config_check warns when it does not.

        ``interact_id`` is an opaque handle the client echoes back; world.json
        leaves it 0, so a random 63-bit value is generated per session.  Only
        stability within one session matters.
        """
        return cls(
            plane_id=world.plane_id,
            floor_id=world.floor_id or world.plane_id * 1000 + 1,
            entry_id=world.entry_id or world.plane_id * 100 + 1,
            map_layer=world.map_layer,
            position=(world.x, world.y, world.z),
            interact_id=world.interact_id or secrets.randbits(63),
            dimension_id=world.dimension_id,
        )

    def move_to(self, pos: tuple[int, int, int]) -> None:
        self.position = pos
        self.client_pos_version += 1

    @property
    def identifier(self) -> pb.SceneIdentifier:
        return pb.SceneIdentifier(floor_id=self.floor_id)


# --------------------------------------------------------------- primitives
def _vector(x: int, y: int, z: int) -> pb.Vector:
    return pb.Vector(x=x, y=y, z=z)


def _uid(session) -> int:
    return session.runtime.cfg.player.uid


def _pos(session) -> tuple[int, int, int]:
    return session.scene.position


def _motion(session) -> pb.MotionInfo:
    return pb.MotionInfo(pos_index=_vector(*_pos(session)), rot_index=pb.Vector())


# ------------------------------------------------------------------ builders
def avatar_entities(
    session, avatars: list[int] | None = None
) -> list[pb.SceneEntityInfo]:
    """The player's team as scene entities, one per non-empty lineup slot.

    ``avatars`` lets a challenge show the team the client picked in
    ``StartChallengeCsReq`` instead of the open-world lineup.

    ``entity_id`` is ``slot + 1``, so it must stay aligned with
    ``world.leader_entity_id`` (default 1 = slot 0).  ``base_avatar_id`` is the
    id stored in the lineup (base id, not form id).
    """
    entities: list[pb.SceneEntityInfo] = []
    uid = _uid(session)
    world = session.runtime.cfg.world
    slots = list(avatars) if avatars is not None else list(session.lineup.slots)
    for slot, avatar_id in enumerate(slots[: world.team_size]):
        if not avatar_id:
            continue
        entities.append(
            pb.SceneEntityInfo(
                entity_id=slot + 1,
                actor=pb.SceneActorInfo(
                    base_avatar_id=avatar_id,
                    avatar_type=pb.AvatarType.AvatarType_AvatarFormalType,
                    uid=uid,
                    map_layer=session.scene.map_layer,
                ),
                motion=_motion(session),
            )
        )
    return entities


def calyx_entity(session) -> pb.SceneEntityInfo | None:
    world = session.runtime.cfg.world
    if not world.calyx_prop_id or not world.calyx_entity_id:
        return None
    return pb.SceneEntityInfo(
        group_id=world.calyx_group_id,
        inst_id=world.calyx_inst_id,
        entity_id=world.calyx_entity_id,
        prop=pb.ScenePropInfo(
            prop_state=session.scene.prop_states.get(world.calyx_entity_id, 1),
            prop_id=world.calyx_prop_id,
        ),
        motion=pb.MotionInfo(
            pos_index=_vector(world.calyx_x, world.calyx_y, world.calyx_z),
            rot_index=pb.Vector(),
        ),
    )


def entity_groups(session) -> list[pb.SceneEntityGroupInfo]:
    world = session.runtime.cfg.world
    groups = [
        pb.SceneEntityGroupInfo(
            group_id=world.avatar_group_id,
            state=0,
            entity_list=avatar_entities(session),
        )
    ]
    if session.scene.extra_groups:
        # Challenge scene: the NPC/prop groups were assembled by
        # build_challenge_scene; reuse them so GetCurSceneInfo / lineup refresh
        # do not leak the calyx group into the arena.
        groups.extend(session.scene.extra_groups)
        return groups
    calyx = calyx_entity(session)
    if calyx is not None and session.scene.plane_id == world.plane_id:
        groups.append(
            pb.SceneEntityGroupInfo(
                group_id=session.runtime.cfg.world.calyx_group_id,
                state=1,
                entity_list=[calyx],
            )
        )
    return groups


def build_scene(session) -> pb.SceneInfo:
    """SceneInfo for GetCurSceneInfoScRsp / login.

    ``game_mode_type=1`` is the normal single-player world mode; the entity groups
    are the avatar group at ``state=0`` plus the calyx group at ``state=1``
    (loaded with its prop).  ``leader_entity_id`` must point at one of the avatar
    entity ids built by :func:`avatar_entities`.
    """
    state = session.scene
    world = session.runtime.cfg.world
    return pb.SceneInfo(
        plane_id=state.plane_id,
        floor_id=state.floor_id,
        entry_id=state.entry_id,
        dimension_id=state.dimension_id,
        game_mode_type=state.game_mode_type,
        world_id=state.world_id,
        leader_entity_id=world.leader_entity_id,
        client_pos_version=state.client_pos_version,
        scene_identifier=state.identifier,
        entity_group_list=entity_groups(session),
        interact_id=state.interact_id,
        floor_saved_data=dict(state.saved_data),
    )


def enter_scene(session, *, plane_id: int, floor_id: int, entry_id: int) -> pb.SceneInfo:
    """Generic scene switch for ``EnterSceneCsReq`` (e.g. 忘却之庭 lobby).

    The client only sends the entrance id (plus a floor identifier); the plane
    comes from ``MapEntrance`` (exported into ``data/endgame.json``).  Outside
    the fixed open world there is no calyx group, and the spawn point is the
    Anchor row when the entrance has one.

    For lobby planes with exported entry data (``EndgameData.lobby_entry``) the
    entry's own props become ``extra_groups`` so the client renders the real
    scene groups (challenge crystals) instead of the two world defaults.
    """
    runtime = session.runtime
    previous = (session.scene.plane_id, session.scene.floor_id, session.scene.entry_id)
    state = SceneState.from_config(runtime.cfg.world)

    state.plane_id = int(plane_id)
    state.floor_id = int(floor_id)
    state.entry_id = int(entry_id)
    state.game_mode_type = 1
    state.world_id = 0
    anchor = runtime.endgame.anchor(entry_id)
    if anchor:
        state.position = (
            int(anchor.get("x", 0)),
            int(anchor.get("y", 0)),
            int(anchor.get("z", 0)),
        )
    state.saved_data = {
        str(item["name"]): int(item["value"])
        for item in runtime.endgame.floors.get(int(floor_id), [])
        if item.get("name")
    }
    props = runtime.endgame.lobby_entry(plane_id, entry_id)
    if props:
        state.extra_groups = lobby_groups(props)
        # 记住"来大厅之前在哪"：打完整局挑战后要回那个世界场景，而不是留在车厢里。
        if int(previous[0]) != int(plane_id):
            session.lobby_return = previous
    session.scene = state
    return build_scene(session)




def build_refresh(session) -> pb.SceneGroupRefreshScNotify:
    """Reload the avatar group after a lineup change."""
    entities = [pb.SceneEntityRefreshInfo(add_entity=entity) for entity in avatar_entities(session)]
    return pb.SceneGroupRefreshScNotify(
        floor_id=session.scene.floor_id,
        group_refresh_list=[
            pb.GroupRefreshInfo(
                group_id=session.runtime.cfg.world.avatar_group_id,
                state=0,
                refresh_type=pb.SceneGroupRefreshType.SCENE_GROUP_REFRESH_TYPE_LOADED,
                refresh_entity=entities,
            )
        ],
    )


def build_map_info(session) -> pb.SceneMapInfo:
    """Map screen data: teleport anchors, lit sections and the calyx prop.

    The client refuses to show a floor it has no map entry for, and it builds
    the map from three lists: unlocked teleports, lit sections and maze props.
    ``lighten_section_list`` matters even for a single-room floor: an empty list
    renders the whole map black.
    """
    world = session.runtime.cfg.world
    state = session.scene
    if state.game_mode_type != 1:
        # Challenge arena: no open-world teleports/calyx. Report the arena floor
        # and its loaded groups; the challenge map is mostly decorative.
        info = pb.SceneMapInfo(
            floor_id=state.floor_id,
            entry_id=state.entry_id,
            dimension_id=state.dimension_id,
            scene_identifier=state.identifier,
            unlocked_teleport_list=[state.entry_id],
        )
        for group in entity_groups(session):
            info.maze_group_list.append(pb.MazeGroup(group_id=group.group_id))
        return info
    if state.plane_id != world.plane_id:
        # Teleported out of the fixed open world (e.g. 忘却之庭 lobby).  When the
        # export has this lobby entry we must report **its** maze groups and props:
        # the client builds the 关卡组 crystals from them, and answering with the
        # world's avatar/calyx groups is what made the lobby show only two default
        # groups (4.6.54 real machine, 2026-11-11).
        info = pb.SceneMapInfo(
            floor_id=state.floor_id,
            entry_id=state.entry_id,
            dimension_id=state.dimension_id,
            scene_identifier=state.identifier,
            unlocked_teleport_list=[state.entry_id],
        )
        if state.extra_groups:
            for group in state.extra_groups:
                info.maze_group_list.append(pb.MazeGroup(group_id=group.group_id))
                for entity in group.entity_list:
                    info.maze_prop_list.append(
                        pb.MazePropState(
                            group_id=group.group_id,
                            config_id=entity.prop.prop_id,
                            state=entity.prop.prop_state,
                        )
                    )
            return info
        info.maze_group_list.append(pb.MazeGroup(group_id=world.avatar_group_id))
        return info

    info = pb.SceneMapInfo(
        floor_id=state.floor_id,
        entry_id=state.entry_id,
        dimension_id=state.dimension_id,
        scene_identifier=state.identifier,
        unlocked_teleport_list=world.teleport_ids or [state.entry_id],
        lighten_section_list=_lighten_sections(world),
    )
    info.maze_group_list.append(pb.MazeGroup(group_id=world.avatar_group_id))
    if world.calyx_group_id:
        info.maze_group_list.append(pb.MazeGroup(group_id=world.calyx_group_id))
        info.maze_prop_list.append(
            pb.MazePropState(
                group_id=world.calyx_group_id,
                config_id=world.calyx_prop_id,
                state=session.scene.prop_states.get(world.calyx_entity_id, 1),
            )
        )
    return info


def build_entered(session) -> pb.GetEnteredSceneScRsp:
    state = session.scene
    return pb.GetEnteredSceneScRsp(
        entered_scene_info_list=[
            pb.EnteredSceneInfo(plane_id=state.plane_id, floor_id=state.floor_id)
        ]
    )


def build_teleport_rsp(session, motion: pb.EntityMotion) -> pb.SceneEntityTeleportScRsp:
    return pb.SceneEntityTeleportScRsp(
        client_pos_version=session.scene.client_pos_version,
        entity_motion=motion,
    )


# ------------------------------------------------------------- challenge scene
# Challenge/Peak scenes use game_mode_type=4 and a dedicated maze group; entity ids
# start above the avatar ids (1..4) so both can coexist in one SceneInfo.
CHALLENGE_GAME_MODE = 4
CHALLENGE_ENTITY_BASE = 1000
CHALLENGE_FALLBACK_INST = 200001
# Lobby props (忘却之庭 challenge crystals) sit between the arena ids and the
# fallback so no two entity groups in one SceneInfo share an id.
LOBBY_ENTITY_BASE = 2000


def _npc_entity(
    *,
    group_id: int,
    inst_id: int,
    entity_id: int,
    monster_id: int,
    event_id: int,
    world_level: int,
    pos: tuple[int, int, int],
    rot: tuple[int, int, int],
) -> pb.SceneEntityInfo:
    return pb.SceneEntityInfo(
        group_id=group_id,
        inst_id=inst_id,
        entity_id=entity_id,
        npc_monster=pb.SceneNpcMonsterInfo(
            world_level=world_level,
            monster_id=monster_id,
            event_id=event_id,
        ),
        motion=pb.MotionInfo(pos_index=_vector(*pos), rot_index=_vector(*rot)),
    )


def _prop_entity(
    *,
    group_id: int,
    inst_id: int,
    entity_id: int,
    prop_id: int,
    prop_state: int,
    pos: tuple[int, int, int],
    rot: tuple[int, int, int],
) -> pb.SceneEntityInfo:
    return pb.SceneEntityInfo(
        group_id=group_id,
        inst_id=inst_id,
        entity_id=entity_id,
        prop=pb.ScenePropInfo(prop_id=prop_id, prop_state=prop_state),
        motion=pb.MotionInfo(pos_index=_vector(*pos), rot_index=_vector(*rot)),
    )


def lobby_groups(props) -> list[pb.SceneEntityGroupInfo]:
    """Turn a lobby entry's exported props into loaded scene groups.

    One entry spans several maze groups (the 忘却之庭 lobby entry 100000104 has
    groups 2/3/30/47/66/142/153/276), and the client keys challenge crystals off
    ``group_id``, so the props are re-grouped here.  Entity ids start above the
    avatar ids (1..4) and the challenge arena base (1000) so all three can coexist.
    """
    grouped: dict[int, list[pb.SceneEntityInfo]] = {}
    entity_id = LOBBY_ENTITY_BASE
    for prop in props:
        group_id = int(prop.group_id)
        grouped.setdefault(group_id, []).append(
            _prop_entity(
                group_id=group_id,
                inst_id=prop.inst_id,
                entity_id=entity_id,
                prop_id=prop.prop_id,
                prop_state=prop.prop_state,
                pos=prop.pos,
                rot=prop.rot,
            )
        )
        entity_id += 1
    return [
        pb.SceneEntityGroupInfo(group_id=group_id, state=1, entity_list=entities)
        for group_id, entities in sorted(grouped.items())
    ]


def build_challenge_scene(
    session,
    *,
    plane_id: int,
    floor_id: int,
    entry_id: int,
    world_id: int = 0,
    group_id: int = 0,
    monster_id: int = 0,
    stage_id: int = 0,
    avatars: list[int] | None = None,
) -> tuple[pb.SceneInfo, int]:
    """Assemble a challenge ``SceneInfo`` and switch the session's scene state.

    This is a pure builder for now (S1): no handler calls it yet, so normal
    world/login behaviour is unchanged.  Data comes from ``runtime.endgame``:

    * ``anchor(entry_id)``          -> spawn position (``Anchor.json``)
    * ``scene(plane_id).groups[group_id]`` -> NPC monster + props coordinates
    * ``floors[floor_id]``          -> ``SceneInfo.floor_saved_data``

    The challenge config's monster/event ids override the **first** NPC in the
    group (that is the trigger monster; see pearl-sr ``addChallengeEntities``).
    When the scene group has no monster (rare maps), one NPC is placed at the
    anchor so the player always has something to hit.

    Returns ``(scene_info, monster_entity_id)``; the entity id is what
    ``SceneCastSkillCsReq.hit_target_entity_id_list`` will contain.
    """
    runtime = session.runtime
    endgame = runtime.endgame
    state = session.scene

    spec = endgame.scene(plane_id)
    group = spec.groups.get(int(group_id)) if spec is not None else None

    # Anchor coverage is partial (most challenge entrances are virtual and have no
    # Anchor.json row), so fall back to the arena: the first NPC monster, then
    # origin.  The client is authoritative for movement and corrects us on the
    # first SceneEntityMove anyway.
    anchor = endgame.anchor(entry_id)
    if anchor:
        pos = (int(anchor.get("x", 0)), int(anchor.get("y", 0)), int(anchor.get("z", 0)))
        rot = (int(anchor.get("rx", 0)), int(anchor.get("ry", 0)), int(anchor.get("rz", 0)))
    elif group is not None and group.monsters:
        pos = group.monsters[0].pos
        rot = (0, 0, 0)
    else:
        pos = (0, 0, 0)
        rot = (0, 0, 0)

    state.plane_id = int(plane_id)
    state.floor_id = int(floor_id)
    state.entry_id = int(entry_id)
    state.world_id = int(world_id)
    state.game_mode_type = CHALLENGE_GAME_MODE
    state.dimension_id = 0
    state.section_id = 0
    state.position = pos
    state.client_pos_version += 1
    state.prop_states = {}
    state.saved_data = {
        str(item["name"]): int(item["value"])
        for item in endgame.floors.get(int(floor_id), [])
        if item.get("name")
    }

    world_level = int(runtime.cfg.player.world_level)
    entities: list[pb.SceneEntityInfo] = []
    monster_entity_id = 0
    next_entity_id = CHALLENGE_ENTITY_BASE

    if group is not None:
        for index, monster in enumerate(group.monsters):
            entities.append(
                _npc_entity(
                    group_id=int(group_id),
                    inst_id=monster.inst_id,
                    entity_id=next_entity_id,
                    monster_id=monster_id if index == 0 and monster_id else monster.monster_id,
                    event_id=stage_id if index == 0 and stage_id else monster.event_id,
                    world_level=world_level,
                    pos=monster.pos,
                    rot=monster.rot,
                )
            )
            if index == 0:
                monster_entity_id = next_entity_id
            next_entity_id += 1
        for prop in group.props:
            entities.append(
                _prop_entity(
                    group_id=int(group_id),
                    inst_id=prop.inst_id,
                    entity_id=next_entity_id,
                    prop_id=prop.prop_id,
                    prop_state=prop.prop_state,
                    pos=prop.pos,
                    rot=prop.rot,
                )
            )
            next_entity_id += 1

    if not monster_entity_id:
        # Fallback: keep the config's monster at the anchor so the fight is still
        # reachable even if res.json does not describe this maze group.
        entities.insert(
            0,
            _npc_entity(
                group_id=int(group_id),
                inst_id=CHALLENGE_FALLBACK_INST,
                entity_id=next_entity_id,
                monster_id=monster_id,
                event_id=stage_id,
                world_level=world_level,
                pos=pos,
                rot=rot,
            ),
        )
        monster_entity_id = next_entity_id
        next_entity_id += 1

    groups = [
        pb.SceneEntityGroupInfo(
            group_id=session.runtime.cfg.world.avatar_group_id,
            state=0,
            entity_list=avatar_entities(session, avatars),
        )
    ]
    if entities:
        groups.append(
            pb.SceneEntityGroupInfo(
                group_id=int(group_id),
                state=1,
                entity_list=entities,
            )
        )
    state.extra_groups = groups[1:]

    scene = pb.SceneInfo(
        plane_id=state.plane_id,
        floor_id=state.floor_id,
        entry_id=state.entry_id,
        dimension_id=state.dimension_id,
        game_mode_type=state.game_mode_type,
        world_id=state.world_id,
        leader_entity_id=session.runtime.cfg.world.leader_entity_id,
        client_pos_version=state.client_pos_version,
        scene_identifier=state.identifier,
        entity_group_list=groups,
        interact_id=state.interact_id,
        floor_saved_data=dict(state.saved_data),
    )
    return scene, monster_entity_id
