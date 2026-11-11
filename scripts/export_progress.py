"""Export mission / quest / activity ids from pearl-sr's resources.

`GetMissionData`, `GetQuestData` and `GetActivityScheduleConfig` are asked at
login.  For a "everything already done" sandbox account the cheapest correct
answer is a complete id list, so we compile one here instead of loading pearl's
multi-megabyte json files at runtime.

    python scripts/export_progress.py
    python scripts/export_progress.py --pearl E:\\LCXS\\run\\pearl-sr\\resources
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


def export(pearl: Path) -> dict:
    missions = sorted({int(m["MainMissionID"]) for m in _load(pearl / "MainMission.json")})
    quests = sorted({int(q["QuestID"]) for q in _load(pearl / "QuestData.json")})

    activities: list[dict] = []
    for activity in _load(pearl / "ActivityConfig.json"):
        activity_id = int(activity["ActivityID"])
        module_ids = sorted({int(x) for x in activity.get("ActivityModuleIDList", [])})
        # Resident modules have no schedule window; the client asks for the
        # scheduled ones here, so skip activities that only have residents.
        if not module_ids:
            continue
        panel_id = int(activity.get("ActivityPanelID") or activity_id)
        activities.append(
            {
                "id": activity_id,
                "panel_id": panel_id,
                "module_ids": module_ids,
            }
        )
    activities.sort(key=lambda a: a["id"])

    return {
        "source": str(pearl / "MainMission.json"),
        "missions": missions,
        "quests": quests,
        "activities": activities,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pearl", type=Path, default=DEFAULT_PEARL)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "progress.json")
    args = ap.parse_args()

    payload = export(args.pearl)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"wrote {args.out}: {len(payload['missions'])} missions, "
        f"{len(payload['quests'])} quests, {len(payload['activities'])} activities"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
