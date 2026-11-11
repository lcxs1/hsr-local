"""Export tutorial / tutorial-guide ids from pearl-sr's resources.

Tutorials are *server driven*: the client shows a tutorial popup when the server
lists it as unlocked, so a server that answers `GetTutorialScRsp` with an empty
list leaves the client free to nag about every tutorial it meets.  For a
"everything already done" account we want the opposite: list every tutorial as
finished.

    python scripts/export_tutorials.py
    python scripts/export_tutorials.py --pearl E:\\LCXS\\run\\pearl-sr\\resources
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PEARL = Path(r"E:\LCXS\run\pearl-sr\resources")


def _load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return next(iter(data.values()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pearl", type=Path, default=DEFAULT_PEARL)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "tutorials.json")
    args = ap.parse_args()

    tutorials = sorted({int(x["TutorialID"]) for x in _load(args.pearl / "TutorialData.json")})

    guides: list[dict] = []
    guide_ids: list[int] = []
    seen: set[int] = set()
    seen_items: set[int] = set()
    for group in _load(args.pearl / "TutorialGuideGroup.json"):
        # The client's UnlockTutorialGuideCsReq carries the *group* id (1101),
        # not the individual guide id (110101). GetTutorialGuideScRsp must use
        # the same id space, otherwise the client re-unlocks every group on
        # every login (and shows its introduction popup again).
        group_id = int(group["GroupID"])
        if group_id in seen:
            continue
        seen.add(group_id)
        # The client's UnlockTutorialGuideCsReq always carries
        # TutorialGuideType_TutorialGuideDefault (0) for group-level guides; the
        # config's TutorialType is a *different* enum (1..5) and must NOT be
        # copied into TutorialGuide.type.  A mismatch makes the client think
        # the group was never finished, so it re-sends UnlockTutorialGuide every
        # login and re-shows the guide hint ("G 多层韧性").
        guides.append({"id": group_id, "type": 0})
        for item_id in group.get("TutorialGuideIDList", []):
            item_id = int(item_id)
            if item_id not in seen_items:
                seen_items.add(item_id)
                guide_ids.append(item_id)
    guides.sort(key=lambda g: g["id"])
    guide_ids.sort()

    payload = {"tutorials": tutorials, "guides": guides, "guide_ids": guide_ids}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(
        f"wrote {args.out}: {len(tutorials)} tutorials, "
        f"{len(guides)} guide groups, {len(guide_ids)} guide items"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
