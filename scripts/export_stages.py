"""Export stagnant-shadow (凝滞虚影) stages from pearl-sr's StageConfig.

The calyx in plane 20313 is a stagnant shadow.  A battle response needs the
stage's monster waves, and loading the 5.5 MB upstream file at runtime would be
silly, so only the ``1052xxx`` stages are compiled into ``data/stages.json``.

    python scripts/export_stages.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PEARL = Path(r"E:\LCXS\run\pearl-sr\resources")

# 1052xxx = stagnant shadow (凝滞虚影); 1032xxx = the older calyx family.
PREFIXES = ("1052", "1032")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pearl", type=Path, default=DEFAULT_PEARL)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "stages.json")
    args = ap.parse_args()

    raw = json.loads((args.pearl / "StageConfig.json").read_text(encoding="utf-8"))
    stages = {}
    for entry in next(iter(raw.values())):
        stage_id = int(entry["StageID"])
        if not str(stage_id).startswith(PREFIXES):
            continue
        stages[str(stage_id)] = {
            "level": int(entry.get("Level", 80)),
            "waves": [[int(m) for m in wave] for wave in entry.get("MonsterList", [])],
        }

    payload = {"stages": stages}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}: {len(stages)} stages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
