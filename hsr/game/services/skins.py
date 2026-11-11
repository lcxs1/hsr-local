"""Owned avatar skins.

The client already ships the skin prefabs/configs; the server only needs to say
which skin ids exist and which one each avatar is currently wearing.  The list
comes from the local resource dump (``AvatarSkin.json``) and is kept small on
purpose: every entry is treated as owned.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("hsr.skins")


@dataclass(frozen=True)
class SkinInfo:
    id: int
    avatar_id: int


class SkinBook:
    def __init__(self, skins: dict[int, SkinInfo] | None = None):
        self.skins = skins or {}

    @classmethod
    def load(cls, path: str | Path, logger: logging.Logger | None = None) -> SkinBook:
        log_ = logger or log
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            items = raw.get("skins", []) if isinstance(raw, dict) else raw
            skins = {
                int(item["id"]): SkinInfo(id=int(item["id"]), avatar_id=int(item["avatar_id"]))
                for item in items
            }
            log_.info("skins loaded: %d", len(skins))
            return cls(skins)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log_.warning("failed to load skins (%s): %s", path, exc)
            return cls()

    def all_ids(self) -> list[int]:
        return sorted(self.skins)

    def for_avatar(self, avatar_id: int) -> list[int]:
        return sorted(s.id for s in self.skins.values() if s.avatar_id == avatar_id)

    def valid(self, avatar_id: int, skin_id: int) -> bool:
        if skin_id == 0:
            return True
        skin = self.skins.get(skin_id)
        return skin is not None and skin.avatar_id == avatar_id
