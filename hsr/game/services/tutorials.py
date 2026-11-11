"""Tutorial book: every tutorial / tutorial guide marked as finished.

The client asks `GetTutorialCsReq` at login and then shows popups for whatever is
not finished yet.  For a "complete account" server the right answer is therefore
"all of them are finished" — that also stops the `UnlockTutorial*` /
`FinishTutorial*` storm and clears the red dots on 教学目录 / 指南.

The id list is generated from pearl-sr's resources by `scripts/export_tutorials.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from hsr.log import get_logger
from hsr.protocol.gen import StarRail_pb2 as pb

log = get_logger("hsr.tutorials")

FINISH = pb.TutorialStatus.TutorialStatus_TutorialFinish


@dataclass
class TutorialBook:
    tutorials: list[int] = field(default_factory=list)
    guides: list[tuple[int, int]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> TutorialBook:
        book = cls()
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            book.tutorials = [int(x) for x in raw.get("tutorials", [])]
            book.guides = [(int(g["id"]), int(g.get("type", 0))) for g in raw.get("guides", [])]
            log.info(
                "tutorial book loaded: %d tutorials, %d guides",
                len(book.tutorials),
                len(book.guides),
            )
        except (OSError, ValueError, KeyError) as exc:
            log.warning("failed to load tutorials (%s): %s", path, exc)
        return book

    def tutorial_list(self) -> list[pb.Tutorial]:
        return [pb.Tutorial(id=tid, status=FINISH) for tid in self.tutorials]

    def guide_list(self) -> list[pb.TutorialGuide]:
        return [pb.TutorialGuide(id=gid, type=gtype, status=FINISH) for gid, gtype in self.guides]
