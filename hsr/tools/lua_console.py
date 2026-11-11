"""Push a Lua script to the client through the heartbeat channel.

    python -m hsr.tools.lua_console push probe.lua
    python -m hsr.tools.lua_console push probe.lua --always
    python -m hsr.tools.lua_console status

The server picks the file up on the next heartbeat (~5s). Client output goes to
`%USERPROFILE%/AppData/LocalLow/Cognosphere/Star Rail/Player.log`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hsr.config import ROOT
from hsr.game.lua import LuaConsole


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="manage lua/main.lua")
    sub = ap.add_subparsers(dest="cmd", required=True)

    push = sub.add_parser("push", help="replace lua/main.lua")
    push.add_argument("file", help="lua source file")
    push.add_argument("--always", action="store_true", help="run on every heartbeat")

    sub.add_parser("status", help="show the current marker")

    args = ap.parse_args(argv)
    console = LuaConsole(ROOT / "lua")

    if args.cmd == "push":
        script = Path(args.file).read_text(encoding="utf-8")
        console.set(script, run_once=not args.always)
        print(f"wrote {console.path} ({'every heartbeat' if args.always else 'once'})")
        return 0

    if not console.path.exists():
        print("lua/main.lua does not exist")
        return 1
    text = console.path.read_text(encoding="utf-8")
    marker = next((line for line in text.splitlines() if line.startswith("-- exec=")), "(none)")
    print(f"{console.path}: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
