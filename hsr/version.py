"""Version profile: the single place that knows which client build we serve.

One server process serves exactly one client build.  Updating the client means
adding a new ``versions/<name>.toml`` + a new ``proto/`` snapshot, then switching
``[server].profile`` in ``config.toml`` -- no handler edits for the build number.

The profile also owns the few proto field renames that differ between builds
(e.g. ``asset_bundle_url_b`` became ``asset_bundle_url_android`` in 4.6.54).
Keep that map small: only fields the core login/scene/battle flow touches.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from hsr.log import get_logger

log = get_logger("hsr.version")

DEFAULT_PROFILE = "4.7.51"


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class VersionProfile:
    name: str
    version: str
    client_build: int
    hotfix_key: str
    proto_dir: Path
    content_dir: Path
    field_aliases: dict[str, str] = field(default_factory=dict)
    # True when proto_dir only lists the command ids that are known so far, so
    # the router must skip handlers whose id has not been recovered yet.
    partial: bool = False

    @classmethod
    def load(cls, root: str | Path, name: str = DEFAULT_PROFILE) -> VersionProfile:
        root = Path(root)
        path = root / "versions" / f"{name}.toml"
        if not path.exists():
            raise ProfileError(f"version profile not found: {path}")
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

        proto_dir = Path(str(raw.get("proto_dir", "proto")))
        content_dir = Path(str(raw.get("content_dir", "data")))
        if not proto_dir.is_absolute():
            proto_dir = root / proto_dir
        if not content_dir.is_absolute():
            content_dir = root / content_dir

        aliases = raw.get("field_aliases", {})
        if not isinstance(aliases, dict):
            aliases = {}
        aliases = {str(k): str(v) for k, v in aliases.items() if v}

        profile = cls(
            name=str(raw.get("name", name)),
            version=str(raw["version"]),
            client_build=int(raw["client_build"]),
            hotfix_key=str(raw.get("hotfix_key", raw["version"])),
            proto_dir=proto_dir,
            content_dir=content_dir,
            field_aliases=aliases,
            partial=raw.get("cmd_table") == "partial",
        )
        return profile

    def field_name(self, semantic: str) -> str:
        """Return the proto field name for a semantic name (identity by default)."""
        return self.field_aliases.get(semantic, semantic)
