"""Run the offline five-chain replay.

Usage:
    python scripts/replay_flows.py
    python scripts/replay_flows.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hsr.replay import ReplayError, run_core_flow


async def _main(json_output: bool) -> int:
    try:
        result = await run_core_flow(strict=True)
    except ReplayError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if json_output:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("PASS: login -> scene -> lineup -> avatar path -> battle -> retry")
        print(f"  battle ids: {result.battle_ids}")
        print(f"  lineup base: {result.lineup_base}")
        print(f"  avatar form: {result.avatar_form}")
        print(f"  events: {', '.join(result.events)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="hsr-local offline core-flow replay")
    parser.add_argument("--json", action="store_true", help="print a JSON result")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.json)))


if __name__ == "__main__":
    main()
