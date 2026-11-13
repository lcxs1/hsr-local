"""Account progress: missions, quests and the activity schedule.

The client asks for all three at login:

    GetMissionDataCsReq           (1274) -> main mission list  / finished ids
    GetQuestDataCsReq             (974)  -> quest list
    GetActivityScheduleConfigCsReq (2605) -> which activity module is running when

Until now every one of them got an empty response.  An empty answer does not
mean "a complete account": the client happily believes that a lot of content is
still untouched and re-opens its introduction panels on every login.  That is
one of the candidates for the ``「无尽膨胀奇物」位面规则`` popup (P0).

The id lists are exported from pearl-sr's resources by
``scripts/export_progress.py``; loading them here is cheap (~120 KB JSON) and
keeps the big upstream files out of the server.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from hsr.log import get_logger
from hsr.protocol import pb

log = get_logger("hsr.progress")

FINISHED = pb.MissionStatus.MissionStatus_MissionFinish

# Tutorial quests that pearl-sr reports as finished; the rest are closed.
FINISHED_QUEST_IDS = frozenset({2200503, 2200504, 2200505, 2200506})

# 2022-09-28 and 2025-01-01: a window that is unambiguously over.
ENDED_BEGIN = 1664308800
ENDED_END = 1735689600
# 2022-09-28 .. 2100-01-01: "running now" for a sandbox.
ACTIVE_END = 4102444800

ActivityMode = Literal["off", "ended", "active"]


@dataclass
class ActivityEntry:
    activity_id: int
    panel_id: int
    module_ids: list[int] = field(default_factory=list)


@dataclass
class ProgressBook:
    missions: list[int] = field(default_factory=list)
    quests: list[int] = field(default_factory=list)
    activities: list[ActivityEntry] = field(default_factory=list)
    source: str = ""

    @classmethod
    def load(cls, path: Path) -> ProgressBook:
        book = cls()
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            book.missions = [int(x) for x in raw.get("missions", [])]
            book.quests = [int(x) for x in raw.get("quests", [])]
            book.activities = [
                ActivityEntry(
                    activity_id=int(a["id"]),
                    panel_id=int(a.get("panel_id") or a["id"]),
                    module_ids=[int(x) for x in a.get("module_ids", [])],
                )
                for a in raw.get("activities", [])
            ]
            book.source = str(raw.get("source", ""))
            log.info(
                "progress book loaded: %d missions, %d quests, %d activities",
                len(book.missions),
                len(book.quests),
                len(book.activities),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("failed to load progress data (%s): %s", path, exc)
        return book

    # ------------------------------------------------------------- missions
    def mission_data_rsp(self) -> pb.GetMissionDataScRsp:
        """Every main mission is finished and none is tracked."""
        return pb.GetMissionDataScRsp(
            main_mission_list=[
                pb.MainMission(id=mission_id, status=FINISHED) for mission_id in self.missions
            ],
            finished_main_mission_id_list=list(self.missions),
        )

    def mission_list_for(self, mission_ids: list[int]) -> list[pb.MainMission]:
        known = set(self.missions)
        return [
            pb.MainMission(id=mission_id, status=FINISHED)
            for mission_id in mission_ids
            if mission_id in known
        ]

    # ---------------------------------------------------------------- quests
    def quest_data_rsp(self) -> pb.GetQuestDataScRsp:
        """Pearl-sr's "nothing to do" shape.

        2200503..2200506 are the tutorial quests pearl marks finished; every
        other quest is closed (hidden from the quest log) instead of pretending
        that 6000 rewards were collected.
        """
        return pb.GetQuestDataScRsp(
            quest_list=[
                pb.Quest(
                    id=quest_id,
                    status=(
                        pb.QuestStatus.QuestStatus_QuestFinish
                        if quest_id in FINISHED_QUEST_IDS
                        else pb.QuestStatus.QuestStatus_QuestClose
                    ),
                    progress=200,
                )
                for quest_id in self.quests
            ]
        )

    # ------------------------------------------------------------ activities
    def activity_schedule(
        self, mode: ActivityMode, now: int | None = None
    ) -> list[pb.ActivityScheduleData]:
        if mode == "off":
            return []
        now = int(time.time()) if now is None else now
        begin, end = (ENDED_BEGIN, ENDED_END) if mode == "ended" else (ENDED_BEGIN, ACTIVE_END)
        schedule: list[pb.ActivityScheduleData] = []
        for activity in self.activities:
            for module_id in activity.module_ids:
                schedule.append(
                    pb.ActivityScheduleData(
                        activity_id=module_id,
                        panel_id=activity.panel_id,
                        begin_time=begin,
                        end_time=end,
                    )
                )
        log.debug(
            "activity schedule: mode=%s now=%d entries=%d (window %d..%d)",
            mode,
            now,
            len(schedule),
            begin,
            end,
        )
        return schedule
