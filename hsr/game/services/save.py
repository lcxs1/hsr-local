"""Player save: lineup, world position and the "already finished" tutorial ids.

Single-player server, so there is exactly one save file: ``saves/player.json``.
Writes are atomic (tmp + replace) and throttled so a 10 Hz movement stream does
not hammer the disk.

The tutorial ids captured here are *additional* to `data/tutorials.json`: if the
client explicitly finishes something the book does not know about, we remember it
and replay it as finished on the next login.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from hsr.log import get_logger

log = get_logger("hsr.save")


@dataclass
class PlayerSave:
    schema: int = 1
    lineup: list[int] = field(default_factory=list)
    leader: int = 0
    tb_multi_path: int = 0
    march_multi_path: int = 0
    # avatar base id -> skin id (0/absent = default model)
    skins: dict[int, int] = field(default_factory=dict)
    plane_id: int = 0
    pos: list[int] = field(default_factory=list)
    finished_tutorials: list[int] = field(default_factory=list)
    finished_guides: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "lineup": self.lineup,
            "leader": self.leader,
            "tb_multi_path": self.tb_multi_path,
            "march_multi_path": self.march_multi_path,
            "skins": {str(k): v for k, v in self.skins.items()},
            "plane_id": self.plane_id,
            "pos": self.pos,
            "finished_tutorials": self.finished_tutorials,
            "finished_guides": self.finished_guides,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> PlayerSave:
        skins_raw = raw.get("skins") or {}
        if not isinstance(skins_raw, dict):
            skins_raw = {}
        return cls(
            schema=int(raw.get("schema", 1)),
            lineup=[int(x) for x in raw.get("lineup", [])],
            leader=int(raw.get("leader", 0)),
            tb_multi_path=int(raw.get("tb_multi_path", 0)),
            march_multi_path=int(raw.get("march_multi_path", 0)),
            skins={int(k): int(v) for k, v in skins_raw.items() if int(v)},
            plane_id=int(raw.get("plane_id", 0)),
            pos=[int(x) for x in raw.get("pos", [])],
            finished_tutorials=[int(x) for x in raw.get("finished_tutorials", [])],
            finished_guides=[int(x) for x in raw.get("finished_guides", [])],
        )


@dataclass
class SaveStore:
    path: Path
    player: PlayerSave = field(default_factory=PlayerSave)
    interval: float = 5.0
    _last_write: float = 0.0

    @classmethod
    def load(cls, path: Path, interval: float = 5.0) -> SaveStore:
        store = cls(path=Path(path), interval=interval)
        if not store.path.exists():
            return store
        try:
            store.player = PlayerSave.from_dict(json.loads(store.path.read_text(encoding="utf-8")))
            log.info("player save loaded: %s", store.path)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("failed to load player save (%s): %s", path, exc)
        return store

    # ---------------------------------------------------------------- writes
    def flush(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_write) < self.interval:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = json.dumps(self.player.to_dict(), indent=2, ensure_ascii=False)
            # 先写临时文件并 fsync，再原子 replace：既不会写坏档，断电也不丢已写数据。
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.path)
            # 只在真正写成功之后才推进节流时间戳；否则写失败后的 5 s 内会被
            # 节流跳过，内存里的最新状态静默不落盘。
            self._last_write = now
        except OSError as exc:
            log.warning("failed to write player save: %s", exc)

    # ------------------------------------------------------------- mutations
    def set_lineup(self, slots: list[int], leader: int) -> None:
        self.player.lineup = list(slots)
        self.player.leader = int(leader)
        self.flush(force=True)

    def set_paths(self, *, tb_multi_path: int, march_multi_path: int) -> None:
        self.player.tb_multi_path = int(tb_multi_path)
        self.player.march_multi_path = int(march_multi_path)
        self.flush(force=True)

    def set_skin(self, avatar_id: int, skin_id: int) -> None:
        if skin_id:
            self.player.skins[int(avatar_id)] = int(skin_id)
        else:
            self.player.skins.pop(int(avatar_id), None)
        self.flush(force=True)

    def set_position(self, plane_id: int, pos: tuple[int, int, int]) -> None:
        self.player.plane_id = int(plane_id)
        self.player.pos = [int(pos[0]), int(pos[1]), int(pos[2])]
        self.flush()

    def add_tutorial(self, tutorial_id: int) -> None:
        if tutorial_id and tutorial_id not in self.player.finished_tutorials:
            self.player.finished_tutorials.append(int(tutorial_id))
            self.flush()

    def add_guide(self, guide_id: int) -> None:
        if guide_id and guide_id not in self.player.finished_guides:
            self.player.finished_guides.append(int(guide_id))
            self.flush()
