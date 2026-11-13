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

from hsr.protocol import pb


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
    # 挑战场景里“场上这一队”的基座 id（上半 / 下半 / 星启节点各不相同）。
    # 为空 = 用 session.lineup（野外阵容）。挑战结束后必须清掉，否则 GetCurSceneInfo
    # 会把上一队的阵容当成野外阵容发出去。
    team: list[int] | None = None
    # 场上角色实体的起始 id（``avatar_entities`` 用 ``avatar_base + slot``）。
    # 官方换半场时换了一批新 id（1048579-82 → 1048593-96），而不是删掉再加回同一批；
    # ``advance=official`` 跟着换，其余路径都保持 1。
    avatar_base: int = 1

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


def leader_entity_id(session) -> int:
    """Entity id of the controlled slot, honouring a bumped ``avatar_base``.

    ``world.leader_entity_id`` is a *slot* id (default 1 = slot 0); the entity
    id moves with the base, so a challenge that renumbers its avatars (see
    ``advance=official``) keeps ``SceneInfo.leader_entity_id`` and movement
    handling pointing at the right entity.
    """
    return int(session.scene.avatar_base) + int(session.runtime.cfg.world.leader_entity_id) - 1


# ------------------------------------------------------------------ builders
def avatar_entities(
    session, avatars: list[int] | None = None
) -> list[pb.SceneEntityInfo]:
    """The player's team as scene entities, one per non-empty lineup slot.

    ``avatars`` lets a challenge show the team the client picked in
    ``StartChallengeCsReq`` instead of the open-world lineup.

    ``entity_id`` is ``scene.avatar_base + slot`` (base 1 by default, so ids are
    1..4); :func:`leader_entity_id` derives the controlled slot from the same
    base.  ``base_avatar_id`` is the id stored in the lineup (base id, not form id).
    """
    entities: list[pb.SceneEntityInfo] = []
    uid = _uid(session)
    world = session.runtime.cfg.world
    if avatars is not None:
        slots = list(avatars)
    elif session.scene.team:
        # 挑战场景：场上这一队由 ChallengeState 决定，不是野外阵容。
        slots = list(session.scene.team)
    else:
        slots = list(session.lineup.slots)
    for slot, avatar_id in enumerate(slots[: world.team_size]):
        if not avatar_id:
            continue
        entities.append(
            pb.SceneEntityInfo(
                entity_id=session.scene.avatar_base + slot,
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
    fields = pb.SceneInfo.DESCRIPTOR.fields_by_name
    values = {
        "plane_id": state.plane_id,
        "floor_id": state.floor_id,
        "entry_id": state.entry_id,
        "dimension_id": state.dimension_id,
        "game_mode_type": state.game_mode_type,
        "world_id": state.world_id,
        "leader_entity_id": leader_entity_id(session),
        "client_pos_version": state.client_pos_version,
        "scene_identifier": state.identifier,
        "entity_group_list": entity_groups(session),
        "interact_id": state.interact_id,
        "floor_saved_data": dict(state.saved_data),
    }
    # 4.8 replaced the `floor_saved_data` map with repeated CustomSaveData; the
    # client rebuilds the floor from scene tables, so just omit the field there.
    return pb.SceneInfo(**{k: v for k, v in values.items() if k in fields})


def spawn_crystals(session) -> list[pb.SceneEntityGroupInfo]:
    """把三块终局挑战水晶放在出生点旁边（合成组）。

    大厅 entry 没有导出水晶道具时（例如 `[endgame] lobby_entry` 指到另一节
    车厢的探针），客户端就没有可点的水晶。这里按大厅同款 prop id
    （`100083` 混沌回忆 / `101028` 虚构叙事 / `100018` 末日幻影）在玩家脚下
    摆三个，位置沿用 `/debug/chrystal` 探针的做法。

    返回 ``[SceneEntityGroupInfo]``（一个合成组）。
    """
    state = session.scene
    base = state.position
    group_id = LOBBY_CRYSTAL_GROUP_ID
    crystals = ((100083, "混沌回忆"), (101028, "虚构叙事"), (100018, "末日幻影"))
    entities: list[pb.SceneEntityInfo] = []
    for index, (prop_id, _label) in enumerate(crystals):
        entities.append(
            _prop_entity(
                group_id=group_id,
                inst_id=LOBBY_CRYSTAL_INST_BASE + index,
                entity_id=LOBBY_CRYSTAL_ENTITY_BASE + index,
                prop_id=prop_id,
                prop_state=1,
                pos=(base[0] + (index - 1) * 300, base[1], base[2] + 300),
                rot=(0, 180000, 0),
            )
        )
    return [pb.SceneEntityGroupInfo(group_id=group_id, state=1, entity_list=entities)]


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
    if props is not None:
        groups = lobby_groups(
            props, mode=getattr(runtime.cfg.endgame, "lobby_props", "all")
        )
        if not any(int(prop.prop_id) >= LOBBY_CRYSTAL_PROP_MIN for prop in props):
            # 这节车厢/入口没有挑战水晶（比如换 floor 的探针）：放到出生点旁边，
            # 否则客户端里没有任何可点的东西。
            groups.extend(spawn_crystals(session))
        state.extra_groups = groups
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
# 合成水晶（出生点旁边）用的分组/实例/实体 id 段，避开大厅导出道具的 id。
LOBBY_CRYSTAL_GROUP_ID = 990001
LOBBY_CRYSTAL_INST_BASE = 400001
LOBBY_CRYSTAL_ENTITY_BASE = LOBBY_ENTITY_BASE + 500
# 大厅里“可交互展台/水晶”的道具 id 都 >= 100000；小于它的是车厢装饰（会触发
# 4.6.54 客户端里 ConfigEntityPath 为空的 MapNPC:227-400001，传送卡死）。
LOBBY_CRYSTAL_PROP_MIN = 100000


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


def lobby_groups(props, mode: str = "all") -> list[pb.SceneEntityGroupInfo]:
    """Turn a lobby entry's exported props into loaded scene groups.

    One entry spans several maze groups (the 忘却之庭 lobby entry 100000104 has
    groups 2/3/30/47/66/142/153/276), and the client keys challenge crystals off
    ``group_id``, so the props are re-grouped here.  Entity ids start above the
    avatar ids (1..4) and the challenge arena base (1000) so all three can coexist.

    ``mode`` controls which groups are reported as loaded:

    * ``all``      — every exported prop (scenery + crystals).
    * ``crystals`` — only the interactive stands (``prop_id >= 100000``).  The
      client creates a *map NPC* for each loaded group; ``MapNPC:227-400001`` has
      no ``ConfigEntityPath`` in the 4.6.54 client and NREs inside
      ``AdventurePhase._EnterStep2``, which hard-locks the teleport loading bar.
      Decor groups carry that NPC, the challenge stands do not.
    * ``none``     — no lobby groups at all.
    """
    if mode == "none":
        return []
    grouped: dict[int, list[pb.SceneEntityInfo]] = {}
    entity_id = LOBBY_ENTITY_BASE
    for prop in props:
        if mode == "crystals" and int(prop.prop_id) < LOBBY_CRYSTAL_PROP_MIN:
            continue
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


def _challenge_entities(
    session,
    *,
    group,
    group_id: int,
    monster_id: int,
    stage_id: int,
    pos: tuple[int, int, int],
    rot: tuple[int, int, int],
    base: int = CHALLENGE_ENTITY_BASE,
) -> tuple[list[pb.SceneEntityInfo], int]:
    """NPC monster + props of one challenge maze group.

    The config's monster/event ids override the **first** NPC in the group (that
    is the trigger monster; see pearl-sr ``addChallengeEntities``).  When the
    group has no monster (rare maps), one NPC is placed at ``pos`` so the player
    always has something to hit.  Returns ``(entities, monster_entity_id)``.
    """
    world_level = int(session.runtime.cfg.player.world_level)
    entities: list[pb.SceneEntityInfo] = []
    monster_entity_id = 0
    next_entity_id = base

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
    return entities, monster_entity_id


# 4.6.54/4.7.0 的 ``SceneGroupRefreshScNotify`` 必须按参考实现的**最小形状**发：
# 只填 ``group_id`` / ``refresh_type=LOADED`` / ``refresh_entity(add_entity|
# delete_entity)``，一个组一条 ``GroupRefreshInfo``。
#
# 之前的实现额外填了 ``state=0/1``、把「卸旧组 + 装新组」塞进同一个 notify，还在
# 里面混了角色组刷新——4.6.54 客户端在 ``_RefreshGroup_Legacy`` 里直接 NRE
# （见 guide/03 坑 35）。参考实现（HyacineCore 4.0 ``PacketSceneGroupRefreshScNotify``，
# 同日 DanhengServer 同文件）一个字段都不多设。
GROUP_REFRESH_LOADED = pb.SceneGroupRefreshType.SCENE_GROUP_REFRESH_TYPE_LOADED
# 官方 4.6.54/4.7.0 换半场全程用 3（客户端枚举名 ``AFIBFMAFNCC`）：语义是
# “删/改这个组里的实体”，纯删和“删了再加”都用它。抓包证据见 §8 的 #595/#597/#681。
GROUP_REFRESH_REFRESH = pb.SceneGroupRefreshType.SCENE_GROUP_REFRESH_TYPE_AFIBFMAFNCC
# 4.7.0 的语义：LOADED = 激活这个组（已激活时会在 _ActiveServerHoyoGroup 里 NRE），
# UNLOAD = 整组卸载。卸载**必须**用 UNLOAD。
GROUP_REFRESH_UNLOAD = pb.SceneGroupRefreshType.SCENE_GROUP_REFRESH_TYPE_UNLOAD


def group_refresh_notify(
    session,
    *,
    add: list[pb.SceneEntityInfo] | None = None,
    remove: list[pb.SceneEntityInfo] | None = None,
    group_ids: list[int] | None = None,
    refresh_type: int = GROUP_REFRESH_LOADED,
) -> pb.SceneGroupRefreshScNotify:
    """One ``SceneGroupRefreshScNotify`` with per-group add/delete lists.

    ``remove`` entries only need ``group_id`` + ``entity_id``; ``add`` entries are
    full ``SceneEntityInfo`` messages.  Resulting ``GroupRefreshInfo`` keeps the
    reference shape (no ``state``, no ``group_info``, no combined groups).

    ``refresh_type`` matters on 4.7.0: ``LOADED`` means **activate this group**
    (the client logs ``Reload ServerGroup Error, Already Exists`` + NREs in
    ``MapDef._ActiveServerHoyoGroup`` when the group is already active), so it
    must never be used to unload a group.  Use ``UNLOAD`` for that.
    """
    state = session.scene
    buckets: dict[int, pb.GroupRefreshInfo] = {}

    def bucket(group_id: int) -> pb.GroupRefreshInfo:
        info = buckets.get(int(group_id))
        if info is None:
            info = pb.GroupRefreshInfo(
                group_id=int(group_id),
                refresh_type=refresh_type,
            )
            buckets[int(group_id)] = info
        return info

    for entity in remove or []:
        bucket(entity.group_id).refresh_entity.append(
            pb.SceneEntityRefreshInfo(delete_entity=entity.entity_id)
        )
    for entity in add or []:
        bucket(entity.group_id).refresh_entity.append(
            pb.SceneEntityRefreshInfo(add_entity=entity)
        )
    for group_id in group_ids or []:
        bucket(group_id)
    return pb.SceneGroupRefreshScNotify(
        floor_id=state.floor_id,
        dimension_id=state.dimension_id,
        group_refresh_list=list(buckets.values()),
    )


def unload_group_notify(
    session,
    group_id: int,
    entities: list[pb.SceneEntityInfo],
    *,
    refresh_type: int = GROUP_REFRESH_UNLOAD,
) -> pb.SceneGroupRefreshScNotify:
    """Unload one maze group.

    Default ``refresh_type=UNLOAD`` (整组卸载)；``refresh_type=LOADED`` 保留旧的
    “LOaded + 逐实体 delete” 写法，只用于对照（4.7.0 上会 NRE，见
    :func:`group_refresh_notify`）。
    """
    if refresh_type == GROUP_REFRESH_UNLOAD:
        return group_refresh_notify(
            session,
            group_ids=[int(group_id)],
            refresh_type=GROUP_REFRESH_UNLOAD,
        )
    return group_refresh_notify(
        session,
        remove=[
            pb.SceneEntityInfo(
                group_id=int(group_id),
                entity_id=entity.entity_id,
            )
            for entity in entities
        ],
        refresh_type=refresh_type,
    )


def load_group_notify(
    session, group_id: int, entities: list[pb.SceneEntityInfo]
) -> pb.SceneGroupRefreshScNotify:
    """Load one maze group (add every entity, ids must not collide)."""
    return group_refresh_notify(session, add=list(entities))


def declared_group_notify(
    session,
    group_id: int,
    entities: list[pb.SceneEntityInfo],
    *,
    refresh_type: int = GROUP_REFRESH_LOADED,
    state: int = 1,
) -> pb.SceneGroupRefreshScNotify:
    """Load one maze group the way 4.7 declares server groups.

    ``SceneInfo.entity_group_list`` (scene entry, which works) and
    ``GroupRefreshInfo.group_info`` (this packet) are the **same** message
    (``SceneEntityGroupInfo``), so a mid-scene group can be declared instead of
    patched: the client builds the group from the entity list instead of running
    ``MapDef._ActiveServerHoyoGroup`` on a group it has never seen (which NREs,
    see :func:`group_refresh_notify`).

    ``state`` is left unset (``0``) by the official half-switch; the older
    declare path keeps ``1``.
    """
    scene = session.scene
    return pb.SceneGroupRefreshScNotify(
        floor_id=scene.floor_id,
        dimension_id=scene.dimension_id,
        group_refresh_list=[
            pb.GroupRefreshInfo(
                group_id=int(group_id),
                refresh_type=refresh_type,
                group_info=pb.SceneEntityGroupInfo(
                    group_id=int(group_id),
                    state=int(state),
                    entity_list=list(entities),
                ),
            )
        ],
    )


def avatar_refresh_notify(
    session,
    *,
    old_avatars: list[int],
    new_avatars: list[int],
) -> pb.SceneGroupRefreshScNotify | None:
    """Re-sync the field team after a lineup switch (``Scene.SyncLineup``).

    参考实现换半场时会把场上角色实体删掉再按新队加回来（Danheng
    ``SceneInstance.SyncLineup``），否则客户端 3D 场景里还站着上半那队。
    两队完全相同时返回 ``None``（不用发）。
    """
    old_ids = [int(avatar_id) for avatar_id in old_avatars if avatar_id]
    new_ids = [int(avatar_id) for avatar_id in new_avatars if avatar_id]
    if old_ids == new_ids:
        return None
    group_id = session.runtime.cfg.world.avatar_group_id
    remove = [
        pb.SceneEntityInfo(group_id=group_id, entity_id=slot + 1)
        for slot in range(len(old_ids))
    ]
    add = avatar_entities(session, new_ids)
    return group_refresh_notify(session, add=add, remove=remove)


def challenge_spawn(
    session, *, entry_id: int, group, fallback: tuple[int, int, int] = (0, 0, 0)
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """``(pos, rot)`` where the challenge arena puts the player.

    Anchor row first (``Anchor.json`` covers most challenge entrances), then the
    group's first NPC, then the caller's fallback.
    """
    anchor = session.runtime.endgame.anchor(entry_id)
    if anchor:
        return (
            (int(anchor.get("x", 0)), int(anchor.get("y", 0)), int(anchor.get("z", 0))),
            (int(anchor.get("rx", 0)), int(anchor.get("ry", 0)), int(anchor.get("rz", 0))),
        )
    if group is not None and group.monsters:
        return group.monsters[0].pos, (0, 0, 0)
    return fallback, (0, 0, 0)


def challenge_group_entities(
    session,
    *,
    plane_id: int,
    group_id: int,
    monster_id: int,
    stage_id: int,
    entry_id: int,
    base: int = CHALLENGE_ENTITY_BASE,
) -> tuple[list[pb.SceneEntityInfo], int]:
    """Build (not apply) one challenge maze group's entities + trigger entity id.

    Used by both the initial ``SceneInfo`` and the mid-run group swap, so the two
    paths can never disagree about entity ids or the NPC override.
    """
    spec = session.runtime.endgame.scene(plane_id)
    group = spec.groups.get(int(group_id)) if spec is not None else None
    pos, rot = challenge_spawn(
        session,
        entry_id=entry_id,
        group=group,
        fallback=session.scene.position,
    )
    return _challenge_entities(
        session,
        group=group,
        group_id=int(group_id),
        monster_id=monster_id,
        stage_id=stage_id,
        pos=pos,
        rot=rot,
        base=base,
    )


def move_notify(
    session, avatars: list[int] | None = None, *, entity_id: int | None = None
) -> pb.SceneEntityMoveScNotify:
    """``SceneEntityMoveScNotify`` holding the player's current position.

    Danheng ``MoveTo`` sends this after a phase switch so the client snaps the
    whole team to the new arena start.  ``entity_id=None`` uses the current
    leader; the official half-switch leaves the field unset (``0``).
    """
    state = session.scene
    if entity_id is None:
        entity_id = leader_entity_id(session)
    return pb.SceneEntityMoveScNotify(
        entity_id=entity_id,
        entry_id=state.entry_id,
        client_pos_version=state.client_pos_version,
        motion=_motion(session),
    )


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
    pos, _rot = challenge_spawn(session, entry_id=entry_id, group=group)

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
    # 整份新 SceneInfo 会把场上的角色实体重新声明成 1..4，基座跟着回到 1。
    state.avatar_base = 1
    state.team = list(avatars) if avatars else list(session.lineup.slots)
    state.saved_data = {
        str(item["name"]): int(item["value"])
        for item in endgame.floors.get(int(floor_id), [])
        if item.get("name")
    }

    entities, monster_entity_id = _challenge_entities(
        session,
        group=group,
        group_id=int(group_id),
        monster_id=monster_id,
        stage_id=stage_id,
        pos=pos,
        rot=_rot,
    )

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
        leader_entity_id=leader_entity_id(session),
        client_pos_version=state.client_pos_version,
        scene_identifier=state.identifier,
        entity_group_list=groups,
        interact_id=state.interact_id,
        floor_saved_data=dict(state.saved_data),
    )
    return scene, monster_entity_id
