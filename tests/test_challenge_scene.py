from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hsr.game.handlers import scene as scene_handlers
from hsr.game.services import scene as scene_service
from hsr.protocol.gen import StarRail_pb2 as pb
from tests.support import build_session


class ChallengeSceneTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = build_session(Path(self.tmp.name))
        self.rt = self.session.runtime

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    def _build(self, challenge_id: int, second: bool = False):
        challenge = self.rt.endgame.challenge(challenge_id)
        self.assertIsNotNone(challenge)
        node = challenge.node(second)
        scene, monster_entity_id = scene_service.build_challenge_scene(
            self.session,
            plane_id=node.plane_id,
            floor_id=node.floor_id,
            entry_id=node.entrance_id,
            world_id=node.world_id,
            group_id=node.maze_group_id,
            monster_id=node.monster_id,
            stage_id=node.stage_id,
        )
        return challenge, node, scene, monster_entity_id

    def _npc(self, scene, entity_id: int):
        for group in scene.entity_group_list:
            for entity in group.entity_list:
                if entity.entity_id == entity_id:
                    return entity
        return None

    async def test_moc_scene(self) -> None:
        challenge, node, scene, monster_entity_id = self._build(1101)
        self.assertEqual(scene.plane_id, node.plane_id)
        self.assertEqual(scene.floor_id, node.floor_id)
        self.assertEqual(scene.entry_id, node.entrance_id)
        self.assertEqual(scene.world_id, node.world_id)
        self.assertEqual(scene.game_mode_type, scene_service.CHALLENGE_GAME_MODE)

        state = self.session.scene
        self.assertEqual((state.plane_id, state.floor_id, state.entry_id), (
            node.plane_id,
            node.floor_id,
            node.entrance_id,
        ))
        group = self.rt.endgame.scene_group(node.plane_id, node.maze_group_id)
        anchor = self.rt.endgame.anchor(node.entrance_id)
        if anchor:
            expected_pos = (anchor["x"], anchor["y"], anchor["z"])
        else:
            expected_pos = group.monsters[0].pos
        self.assertEqual(state.position, expected_pos)

        # group 0 = avatars, group N = the challenge's maze group
        self.assertEqual(scene.entity_group_list[0].state, 0)
        npc_group = next(
            g for g in scene.entity_group_list if g.group_id == node.maze_group_id
        )
        self.assertEqual(npc_group.state, 1)
        npc = self._npc(scene, monster_entity_id)
        self.assertIsNotNone(npc)
        self.assertEqual(npc.group_id, node.maze_group_id)
        self.assertEqual(npc.npc_monster.monster_id, node.monster_id)
        self.assertEqual(npc.npc_monster.event_id, node.stage_id)
        self.assertGreater(npc.npc_monster.world_level, 0)

        # NPC coordinates come from the exported scene group
        group = self.rt.endgame.scene_group(node.plane_id, node.maze_group_id)
        self.assertIsNotNone(group)
        first = group.monsters[0]
        self.assertEqual(npc.inst_id, first.inst_id)
        self.assertEqual(
            (npc.motion.pos_index.x, npc.motion.pos_index.y, npc.motion.pos_index.z),
            first.pos,
        )
        props = [e for e in npc_group.entity_list if e.HasField("prop")]
        self.assertEqual(len(props), len(group.props))

        # floor saved values are pushed with the scene
        expected = {
            str(item["name"]): item["value"]
            for item in self.rt.endgame.floors.get(node.floor_id, [])
        }
        self.assertEqual(dict(scene.floor_saved_data), expected)

    async def test_pf_scene(self) -> None:
        challenge, node, scene, monster_entity_id = self._build(20111)
        self.assertEqual(challenge.mode, "PF")
        npc = self._npc(scene, monster_entity_id)
        self.assertIsNotNone(npc)
        self.assertEqual(npc.npc_monster.event_id, node.stage_id)
        self.assertTrue(scene.floor_saved_data)

    async def test_as_scene(self) -> None:
        challenge, node, scene, monster_entity_id = self._build(30011)
        self.assertEqual(challenge.mode, "AS")
        self.assertEqual(scene.plane_id, 30124)
        npc = self._npc(scene, monster_entity_id)
        self.assertIsNotNone(npc)
        self.assertEqual(npc.npc_monster.monster_id, node.monster_id)

    async def test_unknown_group_falls_back_to_anchor(self) -> None:
        node = self.rt.endgame.challenge(1101).node1
        scene, monster_entity_id = scene_service.build_challenge_scene(
            self.session,
            plane_id=node.plane_id,
            floor_id=node.floor_id,
            entry_id=node.entrance_id,
            world_id=node.world_id,
            group_id=999999,
            monster_id=1234,
            stage_id=5678,
        )
        npc = self._npc(scene, monster_entity_id)
        self.assertIsNotNone(npc)
        self.assertEqual(npc.npc_monster.monster_id, 1234)
        self.assertEqual(npc.npc_monster.event_id, 5678)
        anchor = self.rt.endgame.anchor(node.entrance_id)
        expected = (
            (anchor["x"], anchor["y"], anchor["z"]) if anchor else (0, 0, 0)
        )
        self.assertEqual(
            (npc.motion.pos_index.x, npc.motion.pos_index.y, npc.motion.pos_index.z),
            expected,
        )
        self.assertEqual(len(scene.entity_group_list), 2)

    async def test_normal_scene_is_unchanged(self) -> None:
        scene = scene_service.build_scene(self.session)
        self.assertEqual(scene.game_mode_type, 1)
        self.assertEqual(scene.world_id, 0)
        self.assertEqual(len(scene.floor_saved_data), 0)
        self.assertEqual(scene.plane_id, self.rt.cfg.world.plane_id)


class LobbySceneTest(unittest.IsolatedAsyncioTestCase):
    """忘却之庭 lobby must render the exported entry groups, not world defaults.

    4.6.54 real machine 2026-11-11: the lobby showed only two default groups
    (avatar + calyx) because ``enter_scene`` fell back to the open-world
    assembly and ``data/endgame.json`` had no plane 10000 data.
    """

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = build_session(Path(self.tmp.name))
        self.rt = self.session.runtime
        self.rt.cfg.endgame.lobby_props = "all"
        self.props = self.rt.endgame.lobby_entry(10000, 100000104)
        self.assertIsNotNone(self.props)

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    async def test_lobby_entry_is_exported(self) -> None:
        self.assertEqual(len(self.props), 13)
        self.assertEqual(
            sorted({prop.group_id for prop in self.props}),
            [2, 3, 30, 47, 66, 142, 153, 276],
        )
        self.assertIsNone(self.rt.endgame.lobby_entry(10000, 123456))
        self.assertIsNone(self.rt.endgame.lobby_entry(30101, 30101001))

    async def test_enter_scene_uses_lobby_groups(self) -> None:
        scene = scene_service.enter_scene(
            self.session, plane_id=10000, floor_id=10000000, entry_id=100000104
        )
        self.assertEqual(scene.plane_id, 10000)
        self.assertEqual(scene.floor_id, 10000000)
        # avatar group first, then every exported lobby group with its props
        self.assertEqual(
            scene.entity_group_list[0].group_id, self.rt.cfg.world.avatar_group_id
        )
        lobby_groups = scene.entity_group_list[1:]
        self.assertEqual(
            [group.group_id for group in lobby_groups],
            [2, 3, 30, 47, 66, 142, 153, 276],
        )
        entities = [entity for group in lobby_groups for entity in group.entity_list]
        self.assertEqual(len(entities), len(self.props))
        self.assertTrue(all(entity.HasField("prop") for entity in entities))
        # entity ids must not collide with avatars (1..4) or the arena (1000+)
        self.assertGreaterEqual(
            min(entity.entity_id for entity in entities),
            scene_service.LOBBY_ENTITY_BASE,
        )
        # the calyx group belongs to the fixed open world, never to the lobby
        calyx_group = self.rt.cfg.world.calyx_group_id
        self.assertNotIn(
            calyx_group,
            [group.group_id for group in scene.entity_group_list],
        )

    async def test_lobby_crystals_only_skips_decor_groups(self) -> None:
        """crystals 模式只报展台（prop_id >= 100000），避开客户端的坏 MapNPC。"""
        self.rt.cfg.endgame.lobby_props = "crystals"
        scene = scene_service.enter_scene(
            self.session, plane_id=10000, floor_id=10000000, entry_id=100000104
        )
        groups = scene.entity_group_list[1:]
        self.assertEqual([group.group_id for group in groups], [142, 153, 276])
        props = [entity for group in groups for entity in group.entity_list]
        self.assertEqual(len(props), 3)
        self.assertTrue(
            all(p.prop.prop_id >= scene_service.LOBBY_CRYSTAL_PROP_MIN for p in props)
        )

    async def test_lobby_entry_override(self) -> None:
        """[endgame] lobby_entry 把 100000104 的传送改到参考实现的 100000352。"""
        self.rt.cfg.endgame.lobby_entry = 100000352
        try:
            await scene_handlers.on_enter_scene(
                self.session,
                pb.EnterSceneCsReq(
                    entry_id=100000104,
                    scene_identifier=pb.SceneIdentifier(floor_id=10000000),
                ),
            )
        finally:
            self.rt.cfg.endgame.lobby_entry = 0
        self.assertEqual(self.session.scene.entry_id, 100000352)
        self.assertEqual(self.session.scene.floor_id, 10000003)

    async def test_lobby_props_none(self) -> None:
        self.rt.cfg.endgame.lobby_props = "none"
        scene = scene_service.enter_scene(
            self.session, plane_id=10000, floor_id=10000000, entry_id=100000104
        )
        self.assertEqual(len(scene.entity_group_list), 1)

    async def test_lobby_map_info_reports_entry_groups(self) -> None:
        scene_service.enter_scene(
            self.session, plane_id=10000, floor_id=10000000, entry_id=100000104
        )
        info = scene_service.build_map_info(self.session)
        self.assertEqual(info.entry_id, 100000104)
        self.assertEqual(
            [group.group_id for group in info.maze_group_list],
            [2, 3, 30, 47, 66, 142, 153, 276],
        )
        self.assertEqual(len(info.maze_prop_list), len(self.props))
        self.assertTrue(
            all(prop.config_id for prop in info.maze_prop_list),
            "every lobby prop must carry its prop id as config_id",
        )

    async def test_unknown_lobby_entry_keeps_world_group(self) -> None:
        """A plane/entry without exported lobby data keeps the old fallback."""
        scene = scene_service.enter_scene(
            self.session, plane_id=10000, floor_id=10000000, entry_id=100000999
        )
        self.assertEqual(len(scene.entity_group_list), 1)
        self.assertEqual(
            scene.entity_group_list[0].group_id, self.rt.cfg.world.avatar_group_id
        )



class LobbyGraceTest(unittest.IsolatedAsyncioTestCase):
    """登录后马上传送要压后场景推送（4.7.0 真机三次采样的时间差）。"""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = build_session(Path(self.tmp.name))
        self.rt = self.session.runtime
        self.sent: list = []

        async def record(msg, name=None) -> None:  # noqa: ANN001
            self.sent.append(msg)

        self.session.send = record  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    async def test_delay_math(self) -> None:
        import time as _time

        self.rt.cfg.endgame.lobby_grace_seconds = 30.0
        self.session.created_at = _time.monotonic()
        self.assertGreater(scene_handlers.scene_push_delay(self.session), 29.0)
        self.session.created_at = _time.monotonic() - 60.0
        self.assertEqual(scene_handlers.scene_push_delay(self.session), 0.0)
        self.rt.cfg.endgame.lobby_grace_seconds = 0.0
        self.assertEqual(scene_handlers.scene_push_delay(self.session), 0.0)

    async def test_scene_push_is_held_then_sent(self) -> None:
        import asyncio

        self.rt.cfg.endgame.lobby_grace_seconds = 0.05
        await scene_handlers.on_enter_scene(
            self.session,
            pb.EnterSceneCsReq(entry_id=100000104, is_close_map=False),
        )
        names = [type(message).__name__ for message in self.sent]
        self.assertIn("EnterSceneScRsp", names)
        self.assertNotIn("EnterSceneByServerScNotify", names)
        await asyncio.sleep(0.15)
        names = [type(message).__name__ for message in self.sent]
        self.assertIn("EnterSceneByServerScNotify", names)


if __name__ == "__main__":
    unittest.main()


