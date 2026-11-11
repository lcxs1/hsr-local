from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.game.handlers import scene as scene_handlers
from hsr.game.services import scene as scene_service
from hsr.game.services.lineup import LineupState
from hsr.game.services.save import SaveStore
from hsr.game.services.scene import SceneState
from hsr.protocol.gen import StarRail_pb2 as pb


class _Session:
    def __init__(self, cfg: AppConfig, save: SaveStore):
        self.runtime = SimpleNamespace(
            cfg=cfg,
            logger=logging.getLogger("hsr.test"),
            save=save,
            # build_lineup() 只在补点时被调用（SyncLineupNotify），这里给个最小 roster
            roster=SimpleNamespace(lineup_id=lambda avatar_id, **_: avatar_id),
        )
        self.lineup = LineupState(
            slots=[1505, 1502, 1503, 1414],
            leader=0,
            mp=cfg.gameplay.technique_points_max,
        )
        self.scene = SceneState.from_config(cfg.world)
        self.sent: list[object] = []

    async def send(self, msg, name: str | None = None) -> None:
        self.sent.append(msg)


class SceneTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = AppConfig.load()
        self.tmp = tempfile.TemporaryDirectory()
        self.save = SaveStore.load(Path(self.tmp.name) / "player.json")
        self.session = _Session(self.cfg, self.save)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scene_shape(self):
        scene = scene_service.build_scene(self.session)
        self.assertEqual(scene.plane_id, self.cfg.world.plane_id)
        self.assertEqual(scene.floor_id, self.cfg.world.floor_id)
        self.assertEqual(scene.entry_id, self.cfg.world.entry_id)
        self.assertEqual(scene.leader_entity_id, self.cfg.world.leader_entity_id)
        self.assertNotEqual(scene.interact_id, 0)
        self.assertEqual(len(scene.entity_group_list), 2)
        avatars = scene.entity_group_list[0].entity_list
        self.assertEqual([e.entity_id for e in avatars], [1, 2, 3, 4])
        self.assertEqual([e.actor.base_avatar_id for e in avatars], [1505, 1502, 1503, 1414])
        calyx = scene.entity_group_list[1].entity_list[0]
        self.assertEqual(calyx.prop.prop_id, self.cfg.world.calyx_prop_id)
        self.assertEqual(calyx.entity_id, self.cfg.world.calyx_entity_id)

    async def test_move_tracks_leader(self):
        req = pb.SceneEntityMoveCsReq(
            entity_motion_list=[
                pb.EntityMotion(
                    entity_id=1,
                    motion=pb.MotionInfo(pos_index=pb.Vector(x=1, y=2, z=3)),
                ),
                pb.EntityMotion(
                    entity_id=2,
                    motion=pb.MotionInfo(pos_index=pb.Vector(x=9, y=9, z=9)),
                ),
            ]
        )
        rsp = await scene_handlers.on_entity_move(self.session, req)
        self.assertEqual(self.session.scene.position, (1, 2, 3))
        self.assertEqual(rsp.entity_motion_list, [])

    async def test_teleport_updates_position(self):
        req = pb.SceneEntityTeleportCsReq(
            entity_motion=pb.EntityMotion(
                entity_id=1,
                motion=pb.MotionInfo(pos_index=pb.Vector(x=10, y=20, z=30)),
            )
        )
        rsp = await scene_handlers.on_entity_teleport(self.session, req)
        self.assertEqual(self.session.scene.position, (10, 20, 30))
        self.assertEqual(rsp.entity_motion.motion.pos_index.x, 10)

    async def test_map_info_contains_calyx(self):
        rsp = await scene_handlers.on_scene_map_info(self.session, pb.GetSceneMapInfoCsReq())
        self.assertEqual(len(rsp.scene_map_info), 1)
        info = rsp.scene_map_info[0]
        self.assertEqual(info.floor_id, self.cfg.world.floor_id)
        self.assertEqual(info.entry_id, self.cfg.world.entry_id)
        self.assertIn(0, info.lighten_section_list)
        self.assertIn(30020, info.lighten_section_list)
        self.assertNotIn(10051, info.lighten_section_list)
        self.assertIn(self.cfg.world.calyx_prop_id, [p.config_id for p in info.maze_prop_list])
        self.assertIn(self.cfg.world.calyx_group_id, [g.group_id for g in info.maze_group_list])

    async def test_cast_skill_cost_mp_echoes_caster_and_pushes_points(self):
        rsp = await scene_handlers.on_cast_skill_cost_mp(
            self.session,
            pb.SceneCastSkillCostMpCsReq(skill_index=2, cast_entity_id=7),
        )
        self.assertEqual(rsp.retcode, 0)
        self.assertEqual(rsp.cast_entity_id, 7)
        # 客户端发完包就已经扣掉一点；unlimited 下要先用 1448 报点数，再用
        # SyncLineupNotify 刷阵容（实测客户端只认后者能把点数加回去）。
        kinds = [type(m).__name__ for m in self.session.sent]
        self.assertEqual(kinds, ["SceneCastSkillMpUpdateScNotify", "SyncLineupNotify"])
        notify, sync = self.session.sent
        self.assertEqual(notify.cast_entity_id, 7)
        self.assertEqual(notify.mp, self.cfg.gameplay.technique_points_max)
        self.assertEqual(sync.lineup.mp, self.cfg.gameplay.technique_points_max)
        self.assertEqual(len(sync.lineup.avatar_list), 4)

    async def test_entered_scene(self):
        rsp = await scene_handlers.on_get_entered(self.session, pb.GetEnteredSceneCsReq())
        self.assertEqual(len(rsp.entered_scene_info_list), 1)
        self.assertEqual(rsp.entered_scene_info_list[0].plane_id, self.cfg.world.plane_id)

    async def test_lineup_refresh_keeps_position(self):
        self.session.scene.move_to((100, 200, 300))
        refresh = scene_service.build_refresh(self.session)
        entities = refresh.group_refresh_list[0].refresh_entity
        self.assertEqual(len(entities), 4)
        self.assertEqual(entities[0].add_entity.motion.pos_index.x, 100)


if __name__ == "__main__":
    unittest.main()
