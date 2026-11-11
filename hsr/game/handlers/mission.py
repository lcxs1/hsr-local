"""Tutorial / tutorial-guide handlers: mark everything requested as finished.

Mission/quest/activity state lives in ``handlers/progress.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.log import get_logger
from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session

log = get_logger("hsr.mission")


async def on_finish_tutorial(
    session: Session, req: pb.FinishTutorialCsReq
) -> pb.FinishTutorialScRsp:
    log.info("finish tutorial: id=%d", req.tutorial_id)
    session.runtime.save.add_tutorial(req.tutorial_id)
    return pb.FinishTutorialScRsp(
        tutorial=pb.Tutorial(
            id=req.tutorial_id, status=pb.TutorialStatus.TutorialStatus_TutorialFinish
        )
    )


async def on_unlock_tutorial(
    session: Session, req: pb.UnlockTutorialCsReq
) -> pb.UnlockTutorialScRsp:
    log.info("unlock tutorial: id=%d", req.tutorial_id)
    session.runtime.save.add_tutorial(req.tutorial_id)
    return pb.UnlockTutorialScRsp(
        tutorial=pb.Tutorial(
            id=req.tutorial_id, status=pb.TutorialStatus.TutorialStatus_TutorialFinish
        )
    )


async def on_finish_guide(
    session: Session, req: pb.FinishTutorialGuideCsReq
) -> pb.FinishTutorialGuideScRsp:
    log.info("finish guide: group=%d type=%d", req.group_id, req.type)
    session.runtime.save.add_guide(req.group_id)
    return pb.FinishTutorialGuideScRsp(
        tutorial_guide=pb.TutorialGuide(
            id=req.group_id,
            type=req.type,
            status=pb.TutorialStatus.TutorialStatus_TutorialFinish,
        )
    )


async def on_unlock_guide(
    session: Session, req: pb.UnlockTutorialGuideCsReq
) -> pb.UnlockTutorialGuideScRsp:
    log.info("unlock guide: group=%d type=%d", req.group_id, req.type)
    session.runtime.save.add_guide(req.group_id)
    return pb.UnlockTutorialGuideScRsp(
        tutorial_guide=pb.TutorialGuide(
            id=req.group_id,
            type=req.type,
            status=pb.TutorialStatus.TutorialStatus_TutorialFinish,
        )
    )


async def on_get_tutorials(session: Session, req: pb.GetTutorialCsReq) -> pb.GetTutorialScRsp:
    """Everything is finished: the client then has nothing to pop up."""
    book = session.runtime.tutorials
    known = list(book.tutorials)
    extra = [tid for tid in session.runtime.save.player.finished_tutorials if tid not in set(known)]
    return pb.GetTutorialScRsp(
        tutorial_list=[
            pb.Tutorial(id=tid, status=pb.TutorialStatus.TutorialStatus_TutorialFinish)
            for tid in known + extra
        ]
    )


async def on_get_guides(
    session: Session, req: pb.GetTutorialGuideCsReq
) -> pb.GetTutorialGuideScRsp:
    book = session.runtime.tutorials
    known_ids = {gid for gid, _ in book.guides}
    guides = list(book.guide_list())
    for gid in session.runtime.save.player.finished_guides:
        if gid not in known_ids:
            guides.append(
                pb.TutorialGuide(
                    id=gid,
                    # group-level guides always use the default type; see
                    # scripts/export_tutorials.py
                    type=pb.TutorialGuideType.TutorialGuideType_TutorialGuideDefault,
                    status=pb.TutorialStatus.TutorialStatus_TutorialFinish,
                )
            )
    return pb.GetTutorialGuideScRsp(tutorial_guide_list=guides)


def register(router: Router) -> None:
    router.add("FinishTutorialCsReq", on_finish_tutorial)
    router.add("UnlockTutorialCsReq", on_unlock_tutorial)
    router.add("FinishTutorialGuideCsReq", on_finish_guide)
    router.add("UnlockTutorialGuideCsReq", on_unlock_guide)
    router.add("GetTutorialCsReq", on_get_tutorials)
    router.add("GetTutorialGuideCsReq", on_get_guides)
