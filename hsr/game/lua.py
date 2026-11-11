"""Lua console over the heartbeat channel.

The client executes the bytes in `PlayerHeartBeatScRsp.download_data` as Lua.
Format of `lua/main.lua`:

    -- exec=XY
    ...lua code...

    X = 't' -> execute this heartbeat
    Y = 't' -> reset X to 'f' after one execution (exec=tt = run once)
"""

from __future__ import annotations

from pathlib import Path

from hsr.log import get_logger

log = get_logger("hsr.lua")

MARKER = "-- exec="


class LuaConsole:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "main.lua"

    def ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(
                "-- exec=ft\n"
                "-- 改第一行为 '-- exec=tt' 即可在下一次心跳执行一次\n"
                "-- 示例：CS.UnityEngine.Debug.Log(\"[dsh] hello\")\n",
                encoding="utf-8",
            )

    def take(self) -> bytes | None:
        """Return script bytes if it should run now; rewrite the marker if needed."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return None

        pos = text.find(MARKER)
        if pos < 0:
            return None
        flag_pos = pos + len(MARKER)
        if len(text) < flag_pos + 2:
            return None
        should_execute = text[flag_pos] == "t"
        reset_after = text[flag_pos + 1] == "t"
        if not should_execute:
            return None

        if reset_after:
            try:
                self.path.write_text(text[:flag_pos] + "f" + text[flag_pos + 1 :], encoding="utf-8")
            except OSError as exc:
                log.warning("failed to rewrite lua marker: %s", exc)
        return text.encode("utf-8")

    def set(self, script: str, *, run_once: bool = True) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        marker = "-- exec=tt" if run_once else "-- exec=tf"
        if not script.startswith("-- exec="):
            script = marker + "\n" + script
        self.path.write_text(script, encoding="utf-8")
