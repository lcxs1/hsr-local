"""Turn scripts/probe_cmd_ids.py output into CmdId.json entries.

The probe sends one invalid byte under every candidate id; the client logs the
protobuf type it failed to parse, which is the authoritative 4.8 message name.
Handler names from the NullReferenceException stack (e.g. _OnCmdPlayerLoginScRsp)
are the fallback for ids whose snapshot name is obfuscated.

Usage:  .venv/Scripts/python.exe scripts/probe_to_cmdid.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

from google.protobuf import descriptor_pb2

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROTO = ROOT / "proto/4.7.51"
PROBE = ROOT / "logs/probe_ids.json"


def norm(text: str) -> str:
    plain = re.sub(r"[^a-z0-9]", "", text.lower())
    # The client prefixes some types with "Proto." when it reports a parse error.
    if plain.startswith("proto") and len(plain) > 5:
        return plain[5:]
    return plain


def snapshot_names() -> dict[str, str]:
    """normalised name -> real message name for every message in the snapshot."""
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


def resolve(info: dict, names: dict[str, str]) -> str | None:
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

    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    names = snapshot_names()
    cmd_path = PROTO / "CmdId.json"
    table = json.loads(cmd_path.read_text(encoding="utf-8"))
    known = {name: int(cmd) for name, cmd in table.items()}

    added: list[tuple[int, str]] = []
    unresolved: list[tuple[int, list[str], list[str]]] = []
    for key, info in sorted(probe.items(), key=lambda kv: int(kv[0])):
        cmd = int(key)
        name = resolve(info, names)
        if name is None:
            unresolved.append((cmd, info.get("handlers", []), info.get("proto", [])))
            continue
        if known.get(name) == cmd:
            continue
        added.append((cmd, name))

    for cmd, name in added:
        table[name] = cmd

    print(f"probed: {len(probe)}  new pairs: {len(added)}  unresolved: {len(unresolved)}")
    for cmd, name in sorted(added):
        print(f"  {cmd:6d} {name}")
    if unresolved:
        print("unresolved (obfuscated snapshot name):")
        for cmd, handlers, proto in unresolved:
            print(f"  {cmd:6d} handlers={handlers} proto={proto}")
    if args.dry_run:
        print("dry run: CmdId.json untouched")
        return 0
    cmd_path.write_text(json.dumps(dict(sorted(table.items())), indent=1), encoding="utf-8")
    print(f"wrote {cmd_path} ({len(table)} commands)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
