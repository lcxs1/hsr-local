r"""Import the live challenge tables from the public srtools data API.

Source (public, no login): ``https://srtools.punklorde.org/api/data/<name>``
with ``<name>`` in ``changelog / moc / pf / as / peak``.

Why this exists: ``data/endgame.json`` was exported from the 4.6.54 client
tables (pearl-sr) and carries neither rotation windows nor human-readable
season names — only ``ChallengeMazeConfig`` ids.  The srtools datasets are the
*resolved* tables (group -> floors -> stages -> waves/levels, plus
``BeginTime`` / ``EndTime``), which is what the server needs to pick the
**active** season instead of hard-coding a group id.

Output (``data/vendor/srtools-<YYYYMMDD>/``)::

    endgame_index.json   groups -> floors -> halves -> stages (id/level/waves)
    PROVENANCE.json      url, fetched_at, bytes, sha256 of every raw file

Usage::

    .\.venv\Scripts\python.exe scripts\import_srtools.py
    .\.venv\Scripts\python.exe scripts\import_srtools.py --at 2026-11-11
    .\.venv\Scripts\python.exe scripts\import_srtools.py --raw E:\LCXS\.ego\srtools-20261111
    .\.venv\Scripts\python.exe scripts\import_srtools.py --compare data\endgame.json

``--raw`` reuses already downloaded ``<name>.json`` files instead of fetching
(they are verified against ``PROVENANCE.json`` when it exists).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path

API = "https://srtools.punklorde.org/api/data/{name}"
NAMES = ("changelog", "moc", "pf", "as", "peak")
USER_AGENT = "hsr-local-data-import/1.0 (+local single-player server)"
DATE_FMT = "%Y-%m-%d %H:%M:%S"


# --------------------------------------------------------------------------- io
def fetch(name: str) -> bytes:
    request = urllib.request.Request(API.format(name=name), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 200:
            raise SystemExit(f"GET {name}: HTTP {response.status}")
        return response.read()


def load(name: str, raw_dir: Path | None) -> tuple[dict, dict]:
    """Return ``(parsed, provenance)`` for one dataset."""
    if raw_dir is not None:
        path = raw_dir / f"{name}.json"
        if not path.exists():
            raise SystemExit(f"missing raw file: {path}")
        data = path.read_bytes()
        source = str(path)
    else:
        data = fetch(name)
        source = API.format(name=name)
    try:
        parsed = json.loads(data)
    except ValueError as exc:
        raise SystemExit(f"{name}: not JSON ({exc})") from exc
    meta = {
        "name": name,
        "source": API.format(name=name),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if raw_dir is not None:
        meta["local"] = str(source)
    return parsed, meta


# ----------------------------------------------------------------------- helpers
def _text(value: dict | None) -> tuple[str, str]:
    value = value or {}
    return str(value.get("cn", "")), str(value.get("en", ""))


def _waves(monster_list) -> list[list[int]]:
    waves = []
    for wave in monster_list or []:
        ids = [int(mid) for mid in wave if int(mid)]
        if ids:
            waves.append(ids)
    return waves


def _stages(events) -> list[dict]:
    out = []
    for event in events or []:
        out.append(
            {
                "stage_id": int(event.get("ID", 0)),
                "level": int(event.get("Level", 0) or 0),
                "waves": _waves(event.get("MonsterList")),
                "monster": _text(event.get("Name"))[0],
            }
        )
    return out


def _targets(targets) -> list[list]:
    out = []
    for target in targets or []:
        out.append([int(target.get("ID", 0)), _text(target.get("Name"))[0]])
    return out


def _buff_ids(buffs) -> list[int]:
    out = []
    for buff in buffs or []:
        if isinstance(buff, int):
            out.append(buff)
        elif isinstance(buff, dict) and buff.get("ID"):
            out.append(int(buff["ID"]))
    return out


def _window(group: dict) -> tuple[str | None, str | None]:
    begin = group.get("BeginTime") or None
    end = group.get("EndTime") or None
    return begin, end


def _floor(level: dict) -> dict:
    floor = {
        "id": int(level.get("ID", 0)),
        "floor": int(level.get("Floor", 0) or 0),
        "name": _text(level.get("Name"))[0],
        "name_en": _text(level.get("Name"))[1],
        "stage_num": int(level.get("StageNum", 1) or 1),
        "turn_limit": int(level.get("TurnLimit", 0) or 0),
        "targets": _targets(level.get("Target")),
        "buffs": _buff_ids(level.get("MazeBuff")),
        "halves": [
            {
                "events": _stages(level.get("EventList1")),
                "stages": [int(e.get("ID", 0)) for e in level.get("EventList1") or []],
            },
            {
                "events": _stages(level.get("EventList2")),
                "stages": [int(e.get("ID", 0)) for e in level.get("EventList2") or []],
            },
        ],
    }
    if "ClearScore" in level:
        floor["clear_score"] = int(level.get("ClearScore", 0) or 0)
    if "BattleTarget" in level:
        floor["score_targets"] = _targets(level.get("BattleTarget"))
    return floor


def group_entry(group: dict) -> dict:
    begin, end = _window(group)
    entry = {
        "id": int(group.get("ID", 0)),
        "name": _text(group.get("Name"))[0],
        "name_en": _text(group.get("Name"))[1],
        "type": str(group.get("ChallengeGroupType", "")),
        "begin": begin,
        "end": end,
        "floors": [_floor(level) for level in group.get("Level") or []],
    }
    if group.get("Option"):
        entry["options"] = _buff_ids(group.get("Option"))
    if group.get("SubOption"):
        entry["sub_options"] = _buff_ids(group.get("SubOption"))
    for key in ("BuffList1", "BuffList2", "BuffList3"):
        if group.get(key):
            entry[key.lower()] = _buff_ids(group.get(key))
    tierce = group.get("Tierce") or {}
    if tierce.get("EventList"):
        entry["tierce"] = {
            "stages": [int(e.get("ID", 0)) for e in tierce["EventList"]],
            "damage_types": [str(t) for t in tierce.get("DamageType") or []],
        }
    return entry


def _peak_level(level: dict) -> dict:
    return {
        "id": int(level.get("ID", 0) or 0),
        "name": _text(level.get("Name"))[0],
        "name_en": _text(level.get("Name"))[1],
        "turn_limit": int(level.get("TurnLimit", 0) or 0),
        "damage_types": [str(t) for t in level.get("DamageType") or []],
        "targets": _targets(level.get("BattleTarget")),
        "stages": [int(e.get("ID", 0)) for e in level.get("EventList") or []],
    }


def peak_entry(group: dict) -> dict:
    entry = {
        "id": int(group.get("ID", 0)),
        "name": _text(group.get("Name"))[0],
        "name_en": _text(group.get("Name"))[1],
        "type": str(group.get("ChallengeGroupType", "")),
        "pre_levels": [_peak_level(level) for level in group.get("PreLevel") or []],
    }
    if group.get("BossLevel"):
        entry["boss"] = _peak_level(group["BossLevel"])
    if group.get("BossConfig"):
        entry["hard"] = _peak_level(group["BossConfig"])
    return entry


# ------------------------------------------------------------------- reporting
def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, DATE_FMT)
    except ValueError:
        return None


def active_groups(index: dict, at: dt.datetime) -> list[tuple[str, dict]]:
    out = []
    for mode, block in index["modes"].items():
        for group in block["groups"]:
            begin, end = _parse_time(group["begin"]), _parse_time(group["end"])
            if begin and end and begin <= at <= end:
                out.append((mode, group))
    return out


def compare(index: dict, pack_path: Path) -> None:
    pack = json.loads(pack_path.read_text(encoding="utf-8-sig"))
    ours = {int(key): value for key, value in pack["challenges"].items()}
    site = {}
    for block in index["modes"].values():
        for group in block["groups"]:
            for floor in group["floors"]:
                site[int(floor["id"])] = (group["id"], floor)
    only_ours = sorted(set(ours) - set(site))
    only_site = sorted(set(site) - set(ours))
    mismatch = []
    for cid in sorted(set(ours) & set(site)):
        stages = site[cid][1]["halves"][0]["stages"]
        mine = int(ours[cid]["node1"]["stage_id"])
        if stages and mine not in stages:
            mismatch.append((cid, mine, stages))
    print(f"compare with {pack_path}:")
    print(f"  challenges ours/site: {len(ours)} / {len(site)}")
    print(f"  only ours: {len(only_ours)} {only_ours[:6]}")
    print(f"  only site: {len(only_site)} {only_site[:6]}")
    print(f"  node1 stage id not among site events: {len(mismatch)}")
    for cid, mine, stages in mismatch[:5]:
        print(f"    id={cid} ours={mine} site={stages}")


# ------------------------------------------------------------------------ main
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, help="directory holding <name>.json files")
    parser.add_argument("--out", type=Path, help="output dir (default data/vendor/srtools-<today>)")
    parser.add_argument("--compare", type=Path, default=Path("data/endgame.json"))
    parser.add_argument("--at", help="report the active season at this local time")
    args = parser.parse_args()

    today = dt.datetime.now().strftime("%Y%m%d")
    out_dir = args.out or Path("data/vendor") / f"srtools-{today}"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = {name: load(name, args.raw) for name in NAMES}
    provenance = [meta for _, meta in raw.values()]
    changelog = raw["changelog"][0]
    latest = changelog[0] if changelog else {}

    index = {
        "source": "srtools.punklorde.org /api/data",
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "site_changelog_latest": {
            "version": latest.get("version"),
            "date": latest.get("date"),
        },
        "notes": (
            "resolved challenge tables: group begin/end windows, floors, "
            "per-half stage ids with monster waves and levels. "
            "One floor may hold more than one stage per half "
            "(EventList has 2 entries); the client fights them in order."
        ),
        "modes": {},
        "peak": [peak_entry(group) for group in raw["peak"][0].values()],
    }
    for mode, key in (("MOC", "moc"), ("PF", "pf"), ("AS", "as")):
        groups = [group_entry(group) for group in raw[key][0].values()]
        groups.sort(key=lambda g: g["id"])
        index["modes"][mode] = {
            "groups": groups,
            "floors": sum(len(g["floors"]) for g in groups),
        }
    index["counts"] = {
        "groups": sum(len(block["groups"]) for block in index["modes"].values()),
        "floors": sum(block["floors"] for block in index["modes"].values()),
        "peak": len(index["peak"]),
    }

    index_path = out_dir / "endgame_index.json"
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "PROVENANCE.json").write_text(
        json.dumps(
            {
                "api": "https://srtools.punklorde.org/api/data",
                "fetched_at": index["fetched_at"],
                "site_changelog_latest": index["site_changelog_latest"],
                "files": provenance,
                "sha256_index": hashlib.sha256(index_path.read_bytes()).hexdigest(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"wrote {index_path} ({index_path.stat().st_size / 1024:.0f} KiB)")
    print(
        "groups {groups}, floors {floors}, peak {peak}".format(**index["counts"]),
        "| site version:",
        index["site_changelog_latest"]["version"],
    )
    for mode, block in index["modes"].items():
        print(f"  {mode}: {len(block['groups'])} groups / {block['floors']} floors")

    at = dt.datetime.strptime(args.at, "%Y-%m-%d") if args.at else dt.datetime.now()
    for mode, group in active_groups(index, at):
        print(
            f"active @ {at:%Y-%m-%d}: {mode} {group['id']} {group['name']} "
            f"[{group['begin']} .. {group['end']}] {len(group['floors'])} floors"
        )
    if args.compare and args.compare.exists():
        compare(index, args.compare)


if __name__ == "__main__":
    main()

