from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hsr.config import AppConfig
from hsr.game.handlers import login as login_handlers
from hsr.game.handlers import progress as progress_handlers
from hsr.game.services.progress import ENDED_END, ProgressBook
from hsr.protocol.gen import StarRail_pb2 as pb

MISSIONS = [1000101, 1000201, 4340525]
QUESTS = [1001711, 2200503, 6091028]
ACTIVITIES = [
    {"id": 10014, "panel_id": 10014, "module_ids": [1001402, 1001401]},
    {"id": 10018, "panel_id": 10018, "module_ids": [1001801]},
]


class _Session:
    def __init__(self, cfg: AppConfig, progress: ProgressBook):
        self.runtime = SimpleNamespace(
            cfg=cfg, progress=progress, logger=logging.getLogger("hsr.test")
        )


class ProgressTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = AppConfig.load()
        self.cfg.experiments.popup_mission = False
        self.cfg.experiments.popup_quest = False
        self.cfg.experiments.popup_activity = "off"
        self.cfg.experiments.popup_basic_info = "keep"
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "progress.json"
        path.write_text(
            json.dumps({"missions": MISSIONS, "quests": QUESTS, "activities": ACTIVITIES}),
            encoding="utf-8",
        )
        self.progress = ProgressBook.load(path)
        self.session = _Session(self.cfg, self.progress)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load(self):
        self.assertEqual(self.progress.missions, MISSIONS)
        self.assertEqual(self.progress.quests, QUESTS)
        self.assertEqual(len(self.progress.activities), 2)

    async def test_mission_data_off_is_empty(self):
        rsp = await progress_handlers.on_mission_data(self.session, pb.GetMissionDataCsReq())
        # handlers are async; the default experiment returns the empty message
        self.assertEqual(len(rsp.main_mission_list), 0)

    async def test_mission_data_full(self):
        self.cfg.experiments.popup_mission = True
        rsp = await progress_handlers.on_mission_data(self.session, pb.GetMissionDataCsReq())
        self.assertEqual([m.id for m in rsp.main_mission_list], MISSIONS)
        self.assertEqual(list(rsp.finished_main_mission_id_list), MISSIONS)
        self.assertTrue(
            all(
                m.status == pb.MissionStatus.MissionStatus_MissionFinish
                for m in rsp.main_mission_list
            )
        )
        self.assertEqual(rsp.track_mission_id, 0)

    async def test_mission_status_full(self):
        self.cfg.experiments.popup_mission = True
        req = pb.GetMissionStatusCsReq(main_mission_id_list=[1000101], sub_mission_id_list=[1])
        rsp = await progress_handlers.on_mission_status(self.session, req)
        self.assertEqual(list(rsp.finished_main_mission_id_list), MISSIONS)
        self.assertEqual(
            rsp.sub_mission_status_list[0].status, pb.MissionStatus.MissionStatus_MissionFinish
        )

    async def test_main_mission_custom_value(self):
        self.cfg.experiments.popup_mission = True
        req = pb.GetMainMissionCustomValueCsReq(main_mission_id_list=[1000201, 999])
        rsp = await progress_handlers.on_main_mission_custom_value(self.session, req)
        self.assertEqual([m.id for m in rsp.main_mission_list], [1000201])

    async def test_quest_data_full(self):
        self.cfg.experiments.popup_quest = True
        rsp = await progress_handlers.on_quest_data(self.session, pb.GetQuestDataCsReq())
        self.assertEqual([q.id for q in rsp.quest_list], QUESTS)
        status = {q.id: q.status for q in rsp.quest_list}
        self.assertEqual(status[2200503], pb.QuestStatus.QuestStatus_QuestFinish)
        self.assertEqual(status[1001711], pb.QuestStatus.QuestStatus_QuestClose)
        self.assertTrue(all(q.progress == 200 for q in rsp.quest_list))

    async def test_activity_schedule_modes(self):
        req = pb.GetActivityScheduleConfigCsReq()
        self.cfg.experiments.popup_activity = "off"
        rsp = await progress_handlers.on_activity_schedule(self.session, req)
        self.assertEqual(len(rsp.schedule_data), 0)

        self.cfg.experiments.popup_activity = "ended"
        rsp = await progress_handlers.on_activity_schedule(self.session, req)
        self.assertEqual(len(rsp.schedule_data), 3)
        self.assertEqual({d.activity_id for d in rsp.schedule_data}, {1001401, 1001402, 1001801})
        self.assertTrue(all(d.end_time == ENDED_END for d in rsp.schedule_data))
        self.assertEqual(
            {d.activity_id: d.panel_id for d in rsp.schedule_data},
            {1001401: 10014, 1001402: 10014, 1001801: 10018},
        )

        self.cfg.experiments.popup_activity = "active"
        rsp = await progress_handlers.on_activity_schedule(self.session, req)
        self.assertTrue(all(d.end_time > ENDED_END for d in rsp.schedule_data))

    async def test_basic_info_modes(self):
        req = pb.GetBasicInfoCsReq()
        self.cfg.experiments.popup_basic_info = "keep"
        rsp = await login_handlers.on_basic_info(self.session, req)
        self.assertTrue(rsp.HasField("player_setting_info"))
        self.assertFalse(rsp.player_setting_info.HasField("OJPAODIHAJE"))

        self.cfg.experiments.popup_basic_info = "omit"
        rsp = await login_handlers.on_basic_info(self.session, req)
        self.assertFalse(rsp.HasField("player_setting_info"))

        self.cfg.experiments.popup_basic_info = "nested"
        rsp = await login_handlers.on_basic_info(self.session, req)
        self.assertTrue(rsp.player_setting_info.HasField("OJPAODIHAJE"))
        self.assertTrue(rsp.player_setting_info.HasField("ANKIDJJINEI"))
        self.assertTrue(rsp.player_setting_info.HasField("CMFCOOEEECB"))
        self.assertTrue(rsp.is_gender_set)


if __name__ == "__main__":
    unittest.main()
