"""Mission / quest / activity handlers (P0 experiment surface).

All three commands used to fall through to the generic "unknown -> empty ScRsp"
path.  The client asked, got an empty answer and believed the account was
untouched, which is one of the suspects for the ``位面规则`` popup.

``[experiments]`` in ``config.toml`` decides whether a handler answers with a
complete account (every mission/quest finished, an activity schedule) or keeps
the old empty response.  Defaults are the old behaviour, so only the experiment
runner changes what the game sees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.protocol import pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


async def on_mission_data(session: Session, req: pb.GetMissionDataCsReq) -> pb.GetMissionDataScRsp:
    if not session.runtime.cfg.experiments.popup_mission:
        return pb.GetMissionDataScRsp()
    return session.runtime.progress.mission_data_rsp()


async def on_mission_status(
    session: Session, req: pb.GetMissionStatusCsReq
) -> pb.GetMissionStatusScRsp:
    finished = list(req.main_mission_id_list)
    if session.runtime.cfg.experiments.popup_mission:
        finished = list(session.runtime.progress.missions)
    return pb.GetMissionStatusScRsp(
        finished_main_mission_id_list=finished,
        curversion_finished_main_mission_id_list=finished,
        sub_mission_status_list=[
            pb.Mission(
                id=mission_id,
                progress=1,
                status=pb.MissionStatus.MissionStatus_MissionFinish,
            )
            for mission_id in req.sub_mission_id_list
        ],
    )


async def on_main_mission_custom_value(
    session: Session, req: pb.GetMainMissionCustomValueCsReq
) -> pb.GetMainMissionCustomValueScRsp:
    if not session.runtime.cfg.experiments.popup_mission:
        return pb.GetMainMissionCustomValueScRsp()
    return pb.GetMainMissionCustomValueScRsp(
        main_mission_list=session.runtime.progress.mission_list_for(list(req.main_mission_id_list))
    )


async def on_track_mission(
    session: Session, req: pb.UpdateTrackMainMissionCsReq
) -> pb.UpdateTrackMainMissionScRsp:
    # Nothing is left to track in a "complete account".
    return pb.UpdateTrackMainMissionScRsp(
        track_mission_id=req.track_mission_id,
        prev_track_mission_id=req.track_mission_id,
    )


async def on_quest_data(session: Session, req: pb.GetQuestDataCsReq) -> pb.GetQuestDataScRsp:
    if not session.runtime.cfg.experiments.popup_quest:
        return pb.GetQuestDataScRsp()
    return session.runtime.progress.quest_data_rsp()


async def on_quest_record(session: Session, req: pb.GetQuestRecordCsReq) -> pb.GetQuestRecordScRsp:
    return pb.GetQuestRecordScRsp()


async def on_activity_schedule(
    session: Session, req: pb.GetActivityScheduleConfigCsReq
) -> pb.GetActivityScheduleConfigScRsp:
    schedule = session.runtime.progress.activity_schedule(
        session.runtime.cfg.experiments.popup_activity
    )
    return pb.GetActivityScheduleConfigScRsp(schedule_data=schedule)


async def on_first_talk_npc(
    session: Session, req: pb.GetFirstTalkNpcCsReq
) -> pb.GetFirstTalkNpcScRsp:
    return pb.GetFirstTalkNpcScRsp()


async def on_npc_status(session: Session, req: pb.GetNpcStatusCsReq) -> pb.GetNpcStatusScRsp:
    return pb.GetNpcStatusScRsp()


def register(router: Router) -> None:
    router.add("GetMissionStatusCsReq", on_mission_status)
    router.add("GetMissionDataCsReq", on_mission_data)
    router.add("GetMainMissionCustomValueCsReq", on_main_mission_custom_value)
    router.add("UpdateTrackMainMissionCsReq", on_track_mission)
    router.add("GetQuestDataCsReq", on_quest_data)
    router.add("GetQuestRecordCsReq", on_quest_record)
    router.add("GetActivityScheduleConfigCsReq", on_activity_schedule)
    router.add("GetFirstTalkNpcCsReq", on_first_talk_npc)
    router.add("GetNpcStatusCsReq", on_npc_status)
