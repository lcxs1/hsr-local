"""Hot reload of ``data/freesr-data.json`` while the server is running.

Two entry points share one code path:

* ``POST /srtools`` — the bridge used by https://srtools.punklorde.org
  (「连接设置」→ 连接类型 ``RobinSR`` → 连接 PS).  The site posts
  ``{"data": <freesr json>, "username": ..., "password": ...}`` to
  ``http://localhost:21000/srtools``, exactly like ppSR / sr-recover-new.
* the mtime watcher — copies the file by hand and the running server picks it
  up on its own (``features.freesr_watch``).

Both end in :func:`apply`: validate the document, write it back (so the file and
the memory can never disagree), swap ``runtime.roster``, re-resolve every live
session's multi-path forms and lineup, then push the new state to the client.

The push order is copied from sr-recover-new ``avatar.sync()``: delete stale
item ids first, then relics, lightcones and the avatar sync, so the client never
shows an item whose ``unique_id`` no longer exists.  A repaired lineup also gets
``SyncLineupNotify`` + scene refresh through ``lineup.notify``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hsr.game.services import freesr, snapshot
from hsr.game.services import lineup as lineup_service
from hsr.game.services.roster import (
    MARCH_BASE,
    MARCH_PATHS,
    TB_BASE,
    Roster,
    lightcone_uid,
    relic_uid,
)
from hsr.protocol.gen import StarRail_pb2 as pb

# Keys the page merges next to ``data`` in the POST body (its ``extraData``).
# ``multi_path`` is only sent when "允许修改命途" is on; ``lua`` is never applied.
_EXTRA_KEYS = ("multi_path", "lua")


class FreesrPayloadError(ValueError):
    """A posted / watched document cannot be used as a roster."""


@dataclass
class ApplyResult:
    """What one hot reload actually changed (also the HTTP response body)."""

    avatars: int = 0
    relics: int = 0
    lightcones: int = 0
    written: bool = False
    source: str = ""
    sessions: int = 0
    lineup_changed: bool = False
    stale_relics: int = 0
    stale_lightcones: int = 0
    path_changes: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "avatars": self.avatars,
            "relics": self.relics,
            "lightcones": self.lightcones,
            "written": self.written,
            "source": self.source,
            "sessions": self.sessions,
            "lineup_changed": self.lineup_changed,
            "stale_relics": self.stale_relics,
            "stale_lightcones": self.stale_lightcones,
            "path_changes": self.path_changes,
        }

    def summary(self) -> str:
        return (
            f"{self.avatars} avatars / {self.relics} relics / {self.lightcones} lightcones"
            f" from {self.source}"
            f"{', written' if self.written else ''}"
            f", pushed to {self.sessions} session(s)"
            f"{', lineup repaired' if self.lineup_changed else ''}"
        )


# --------------------------------------------------------------------- payload
def has_document(raw: Any) -> bool:
    """True when a body carries a freesr document (not just a probe).

    The site's 「连接 PS」 button first POSTs ``{"username": ..., "password": ...}``
    with no data to check the server is alive; ppSR answers that with a plain
    200 OK and no data change.  Keep that behaviour so the UI flips to 已连接.
    """
    if not isinstance(raw, dict):
        return False
    document = raw.get("data", raw)
    if isinstance(document, str):
        return True
    return isinstance(document, dict) and ("avatars" in document or "relics" in document)


def unwrap_payload(raw: Any) -> tuple[dict, dict]:
    """Return ``(freesr document, extras)`` from a site body or a raw file.

    Accepts all three shapes seen in the wild:

    * ``{"data": {...}, "username": "x", "password": "y"}`` (ppSR / the site)
    * ``{"data": "<json string>"}`` (some builds stringify the document)
    * a bare freesr document (``{"avatars": ..., "relics": ...}``)
    """
    if not isinstance(raw, dict):
        raise FreesrPayloadError("body must be a JSON object")

    extras: dict[str, Any] = {k: raw[k] for k in _EXTRA_KEYS if k in raw}
    for key in ("extra_data", "extraData", "extra"):
        value = raw.get(key)
        if isinstance(value, dict):
            extras.update(value)

    document: Any = raw.get("data", raw)
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as exc:
            raise FreesrPayloadError(f"data is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise FreesrPayloadError("data must be a JSON object")
    if "avatars" not in document and "relics" not in document:
        raise FreesrPayloadError("data has neither 'avatars' nor 'relics'")
    return document, extras


def _validate(document: dict) -> freesr.FreesrData:
    try:
        data = freesr.FreesrData.model_validate(document)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = first.get("msg", "invalid freesr document")
        raise FreesrPayloadError(f"{location}: {message}" if location else message) from exc
    if not data.avatars:
        raise FreesrPayloadError("document has no avatars")
    return data


# ----------------------------------------------------------------------- disk
def _write(path: Path, document: dict) -> None:
    """Write with the same 4-space layout the site produces (atomic replace)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(document, indent=4, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise FreesrPayloadError(f"failed to write {path}: {exc}") from exc


def _relic_uids(roster: Roster) -> set[int]:
    return {relic_uid(relic) for relic in roster.data.relics}


def _lightcone_uids(roster: Roster) -> set[int]:
    return {lightcone_uid(lc) for lc in roster.data.lightcones}


def _stat(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


# ---------------------------------------------------------------------- extra
def _iter_path_pairs(multi: Any, roster: Roster):
    """Yield ``(base, form)`` from the page's ``multi_path`` in any shape."""
    if isinstance(multi, dict):
        for key, value in multi.items():
            try:
                form = int(value)
            except (TypeError, ValueError):
                continue
            try:
                base = int(key)
            except (TypeError, ValueError):
                base = roster.base_id(form)
            yield base, form
    elif isinstance(multi, list):
        for item in multi:
            if isinstance(item, dict):
                raw_form = item.get("path") or item.get("multi_path") or item.get("avatar_path")
                try:
                    form = int(raw_form)
                except (TypeError, ValueError):
                    continue
                try:
                    base = int(item.get("avatar_id") or item.get("base_avatar_id"))
                except (TypeError, ValueError):
                    base = roster.base_id(form)
                yield base, form
            else:
                try:
                    form = int(item)
                except (TypeError, ValueError):
                    continue
                yield roster.base_id(form), form


def _apply_extra(rt, extras: dict, roster: Roster) -> tuple[int | None, int | None, list[int]]:
    """Best-effort apply of the page's ``extraData``.

    Only the two multi-path bases are configurable in hsr-local.  ``lua``
    (the site's Lua console payload) is intentionally ignored: scripts are
    delivered through the heartbeat channel (``lua/main.lua``) instead.
    Returns ``(tb_path, march_path, changed_forms)``.
    """
    multi = extras.get("multi_path")
    if multi is None:
        return None, None, []

    tb_path: int | None = None
    march_path: int | None = None
    changed: list[int] = []
    for base, form in _iter_path_pairs(multi, roster):
        if not roster.has_form(form):
            rt.logger.warning("multi_path ignored: form %d is not in the new freesr", form)
            continue
        expected = roster.base_id(form)
        if expected == TB_BASE:
            tb_path = form
            changed.append(form)
        elif expected == MARCH_BASE and form in MARCH_PATHS:
            march_path = form
            changed.append(form)
        else:
            rt.logger.warning(
                "multi_path ignored: %d is not a Trailblazer/March 7th form (base %d)",
                form,
                base,
            )
    if tb_path is not None and tb_path % 2 != (0 if rt.cfg.player.gender == 2 else 1):
        rt.logger.warning(
            "multi_path %d does not match player.gender=%d; the session will pick the"
            " same-gender form instead",
            tb_path,
            rt.cfg.player.gender,
        )
    return tb_path, march_path, changed


# ---------------------------------------------------------------------- guards
_FORCE_HINT = "确认要清空请用 POST /srtools?force=1（或 payload 里 force=true）"


def _check_item_wipe(old: Roster, new: freesr.FreesrData) -> None:
    """Refuse a push that drops all relics/lightcones.

    The SR Tools page starts from its own default state; if the user did not
    「加载数据」first, its ``relics``/``lightcones`` are ``[]`` and one 「同步」
    would silently empty the account (seen on 2026-11-10).  A real clear-out is
    rare and can be forced explicitly, so the default is to reject.
    """
    checks = (
        ("遗器", len(old.data.relics), len(new.relics)),
        ("光锥", len(old.data.lightcones), len(new.lightcones)),
    )
    for label, before, after in checks:
        if before and not after:
            raise FreesrPayloadError(
                f"拒绝清空{label}：服务端现有 {before} 件，新配置是 0 件。"
                f"多半是网页没有先「加载数据」（可用 GET /srtools/export 取回当前配置）。"
                f"{_FORCE_HINT}"
            )


# ---------------------------------------------------------------------- apply
async def apply(
    rt,
    raw: Any,
    *,
    write: bool = True,
    source: str = "srtools",
    guard: bool | None = None,
    force: bool = False,
) -> ApplyResult:
    """Validate, persist, swap and push one freesr document.

    ``guard`` defaults to on for ``source="srtools"`` (the web push) and off for
    file-triggered reloads, where replacing the file is already an explicit act.
    Raises :class:`FreesrPayloadError` for anything unusable; the running
    roster is left untouched when validation fails.
    """
    if not rt.cfg.features.freesr_hot_reload:
        raise FreesrPayloadError(
            "freesr hot reload is disabled (features.freesr_hot_reload = false)"
        )

    async with rt.freesr_lock:
        document, extras = unwrap_payload(raw)
        data = _validate(document)
        old = rt.roster
        if (source == "srtools" if guard is None else guard) and not force:
            _check_item_wipe(old, data)

        new_relics = {relic_uid(relic) for relic in data.relics}
        new_lightcones = {lightcone_uid(lc) for lc in data.lightcones}
        stale_relics = sorted(_relic_uids(old) - new_relics)
        stale_lightcones = sorted(_lightcone_uids(old) - new_lightcones)

        written = False
        if write:
            _write(rt.freesr_path, document)
            written = True
            # Keep the watcher from treating our own write as an external change.
            rt.freesr_signature = _stat(rt.freesr_path)

        roster = Roster(data, rt.cfg.player)
        roster.source = str(rt.freesr_path)
        roster.load_error = None
        rt.roster = roster

        tb_path, march_path, path_changes = _apply_extra(rt, extras, roster)

        result = ApplyResult(
            avatars=len(data.avatars),
            relics=len(data.relics),
            lightcones=len(data.lightcones),
            written=written,
            source=source,
            stale_relics=len(stale_relics),
            stale_lightcones=len(stale_lightcones),
            path_changes=path_changes,
        )
        await _push_sessions(rt, result, stale_relics, stale_lightcones, tb_path, march_path)
        rt.logger.info("freesr hot reload: %s", result.summary())
        return result


async def _push_sessions(
    rt,
    result: ApplyResult,
    stale_relics: list[int],
    stale_lightcones: list[int],
    tb_path: int | None,
    march_path: int | None,
) -> None:
    sessions = rt.sessions()
    result.sessions = len(sessions)
    for session in sessions:
        lineup_changed = False
        try:
            lineup_changed = session.refresh_freesr(tb_path=tb_path, march_path=march_path)
            await push_session(session, stale_relics, stale_lightcones, lineup_changed=lineup_changed)
        except Exception:  # noqa: BLE001 - one broken session must not abort the reload
            rt.logger.exception("freesr push failed for %s", getattr(session, "addr", "?"))
        result.lineup_changed = result.lineup_changed or lineup_changed


async def push_session(
    session,
    stale_relics: list[int] | None = None,
    stale_lightcones: list[int] | None = None,
    *,
    lineup_changed: bool = False,
) -> None:
    """Push the reloaded roster to one client (see module docstring for order)."""
    if getattr(session, "closed", False):
        return
    stale_relics = list(stale_relics or [])
    stale_lightcones = list(stale_lightcones or [])
    if stale_relics or stale_lightcones:
        await session.send(
            pb.PlayerSyncScNotify(
                del_relic_list=stale_relics,
                del_equipment_list=stale_lightcones,
            )
        )
    relics, equipment = snapshot.bag_lists(session)
    await session.send(pb.PlayerSyncScNotify(relic_list=relics))
    await session.send(pb.PlayerSyncScNotify(equipment_list=equipment))
    await session.send(pb.PlayerSyncScNotify(avatar_sync=snapshot.avatar_sync(session)))
    if lineup_changed:
        await lineup_service.notify(session)


# ---------------------------------------------------------------------- watch
async def reload_from_disk(rt, *, source: str = "file") -> ApplyResult:
    """Re-read the configured file (used by ``--strict``-free manual replacement)."""
    path = rt.freesr_path
    try:
        # utf-8-sig: a hand-copied file may carry a BOM (PowerShell, site export).
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise FreesrPayloadError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise FreesrPayloadError(f"{path} is not valid JSON: {exc}") from exc
    return await apply(rt, raw, write=False, source=source)


async def watch(rt, interval: float | None = None) -> None:
    """Poll the freesr file and reload on mtime/size change.

    ``apply`` rewrites the file after a /srtools push, so the signature is
    refreshed right after each reload to avoid a second, no-op reload.
    """
    path = rt.freesr_path
    delay = float(interval if interval is not None else rt.cfg.features.freesr_watch_interval)
    signature = _stat(path)
    rt.freesr_signature = signature
    while True:
        await asyncio.sleep(delay)
        current = _stat(path)
        if current is None or current == rt.freesr_signature:
            continue
        rt.freesr_signature = current
        try:
            await reload_from_disk(rt, source="file-watch")
        except FreesrPayloadError as exc:
            rt.logger.warning("freesr watch reload failed: %s", exc)
            continue
        except Exception:  # noqa: BLE001 - a watcher crash must not stop the server
            rt.logger.exception("freesr watch reload crashed")
            continue
