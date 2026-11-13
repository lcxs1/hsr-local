"""Merge probed response ids and aligned request ids into CmdId.json.

Inputs:
    logs/probe_ids.json   id -> {handlers, proto}   (scripts/probe_cmd_ids.py)
    logs/cs_ids.json      id -> {name, conf}        (scripts/align_login_seq.py)

A request id is only accepted with confidence "exact" or "order" (segment
lengths agreed), and a request/response pair is only added when both halves are
known.  Existing entries are never overwritten: they come from the reference
server (ppSR) and take precedence.

Usage:  .venv/Scripts/python.exe scripts/merge_cmd_ids.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

from google.protobuf import descriptor_pb2

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROTO = ROOT / "proto/4.7.51"


def norm(text: str) -> str:
    plain = re.sub(r"[^a-z0-9]", "", text.lower())
    if plain.startswith("proto") and len(plain) > 5:
        return plain[5:]
    return plain


def snapshot_names() -> dict[str, str]:
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString((PROTO / "classes.desc").read_bytes())
    out: dict[str, str] = {}

    def walk(messages):
        for message in messages:
            out[norm(message.name)] = message.name
            walk(message.nested_type)

    for file_proto in fds.file:
        walk(file_proto.message_type)
    return out


def resolve_probe(info: dict, names: dict[str, str]) -> str | None:
    for proto_name in info.get("proto", []):
        hit = names.get(norm(proto_name))
        if hit is not None:
            return hit
    for handler in info.get("handlers", []):
        for candidate in (handler, handler[3:] if handler.startswith("Cmd") else handler):
            hit = names.get(norm(candidate))
            if hit is not None:
                return hit
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    names = snapshot_names()
    cmd_path = PROTO / "CmdId.json"
    table: dict[str, int] = json.loads(cmd_path.read_text(encoding="utf-8"))

    rsp_ids: dict[str, int] = {}
    probe_path = ROOT / "logs/probe_ids.json"
    if probe_path.exists():
        for key, info in json.loads(probe_path.read_text(encoding="utf-8")).items():
            name = resolve_probe(info, names)
            if name is not None:
                rsp_ids[name] = int(key)

    cs_path = ROOT / "logs/cs_ids.json"
    cs: dict[str, dict] = json.loads(cs_path.read_text(encoding="utf-8")) if cs_path.exists() else {}

    added: list[tuple[int, str]] = []
    conflicts: list[str] = []
    for key, info in sorted(cs.items(), key=lambda kv: int(kv[0])):
        cmd, name, conf = int(key), info["name"], info["conf"]
        if conf not in ("exact", "order"):
            continue
        if not name.endswith("CsReq"):
            continue
        if name in table:
            if table[name] != cmd:
                conflicts.append(f"{name}: table {table[name]} vs aligned {cmd}")
            continue
        pair = name[:-5] + "ScRsp"
        rsp_id = rsp_ids.get(pair, table.get(pair))
        if rsp_id is None:
            continue
        added.append((cmd, name))
        if pair not in table:
            added.append((rsp_id, pair))

    for name, value in rsp_ids.items():
        if name not in table and name.endswith("ScRsp"):
            added.append((value, name))

    for cmd, name in added:
        table[name] = cmd

    print(f"probed rsp names: {len(rsp_ids)}  aligned cs ids: {len(cs)}")
    print(f"new entries: {len(added)}  conflicts: {len(conflicts)}")
    for cmd, name in sorted(added):
        print(f"  {cmd:6d} {name}")
    for line in conflicts:
        print(f"  CONFLICT {line}")

    if args.dry_run:
        print("dry run: CmdId.json untouched")
        return 0
    cmd_path.write_text(json.dumps(dict(sorted(table.items())), indent=1), encoding="utf-8")
    print(f"wrote {cmd_path} ({len(table)} commands)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
