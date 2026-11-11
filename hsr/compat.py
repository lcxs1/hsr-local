"""Small proto compatibility helpers.

The generated classes use the field names of one client build.  A handful of
fields were renamed between builds (see ``versions/<name>.toml``); route those
through here instead of scattering ``hasattr``/``getattr`` in handlers.
"""

from __future__ import annotations

from typing import Any

from hsr.log import get_logger

log = get_logger("hsr.compat")


def set_field(message: Any, semantic: str, value: Any, aliases: dict[str, str]) -> str | None:
    """Set a field by semantic name, trying the profile alias first.

    Returns the field name that worked, or ``None`` if the build has neither.
    """
    candidates = [aliases.get(semantic), semantic]
    for name in candidates:
        if not name or not hasattr(message, name):
            continue
        try:
            setattr(message, name, value)
            return name
        except (AttributeError, TypeError, ValueError) as exc:
            log.warning("could not set %s.%s: %s", type(message).__name__, name, exc)
    log.warning(
        "field %s missing on %s (tried %s)",
        semantic,
        type(message).__name__,
        ", ".join(str(c) for c in candidates if c),
    )
    return None
