"""ServerPrefs: the client's tiny key/value store for "already seen" UI flags.

The client keeps things like "did the player already read this announcement"
here and asks for them at login (``GetAllServerPrefsDataCsReq``).  If the server
always answers with an empty list, every login looks like a first login and the
client happily re-opens the same intro/promo windows.  Persisting the blobs is
therefore both correct and the cheapest way to keep those popups away.

Stored as ``saves/server_prefs.json``: ``{ "prefs_id": "base64 bytes" }``.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from hsr.log import get_logger
from hsr.protocol.gen import StarRail_pb2 as pb

log = get_logger("hsr.prefs")


@dataclass
class PrefsStore:
    path: Path
    values: dict[int, bytes] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> PrefsStore:
        store = cls(path=Path(path))
        if not store.path.exists():
            return store
        try:
            raw = json.loads(store.path.read_text(encoding="utf-8"))
            store.values = {int(k): base64.b64decode(v) for k, v in raw.items()}
            log.info("server prefs loaded: %d entries", len(store.values))
        except (OSError, ValueError) as exc:
            log.warning("failed to load server prefs (%s): %s", path, exc)
        return store

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            raw = {str(k): base64.b64encode(v).decode("ascii") for k, v in self.values.items()}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("failed to save server prefs: %s", exc)

    def set(self, prefs_id: int, data: bytes) -> None:
        self.values[prefs_id] = bytes(data)
        self.save()

    def get(self, prefs_id: int) -> bytes | None:
        return self.values.get(prefs_id)

    def to_proto(self) -> list[pb.ServerPrefs]:
        return [
            pb.ServerPrefs(server_prefs_id=prefs_id, data=data)
            for prefs_id, data in sorted(self.values.items())
        ]
