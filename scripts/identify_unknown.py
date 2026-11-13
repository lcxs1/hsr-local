"""Identify dumped unknown command bodies against a protocol snapshot.

The router writes the first body of every unhandled command to logs/unknown/.
This tool parses each body with every message of the snapshot: a candidate is
accepted when decoding + re-encoding reproduces the exact bytes, and the best
candidates (most fields populated) are printed. Use it to recover name -> id
for a new client version (docs/reference/06 section 10).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from google.protobuf import descriptor_pb2

from hsr.protocol.registry import Registry


def message_names(descriptor_set: Path) -> list[str]:
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(descriptor_set.read_bytes())
    names: list[str] = []

    def walk(messages):
        for message in messages:
            names.append(message.name)
            walk(message.nested_type)

    for file_proto in fds.file:
        walk(file_proto.message_type)
    return names


def identify(registry, body, names, top):
    hits = []
    for name in names:
        cls = registry.message_cls(name)
        if cls is None:
            continue
        msg = cls()
        try:
            msg.ParseFromString(body)
        except Exception:
            continue
        if msg.SerializeToString() != body:
            continue
        hits.append((len(msg.ListFields()), name, msg))
    hits.sort(key=lambda item: -item[0])
    return hits[:top]


def main() -> int:
    parser = argparse.ArgumentParser(description="identify unknown cmd bodies")
    parser.add_argument("--profile", default="4.7.51")
    parser.add_argument("--dir", default=None, help="default logs/unknown")
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    from hsr.version import VersionProfile

    profile = VersionProfile.load(ROOT, args.profile)
    registry = Registry(profile.proto_dir, strict=False)
    names = message_names(profile.proto_dir / "classes.desc")
    dump_dir = Path(args.dir) if args.dir else ROOT / "logs" / "unknown"
    files = sorted(dump_dir.glob("*.bin"), key=lambda p: int(p.stem.split("_")[0]))
    print(f"snapshot {profile.proto_dir.name}: {len(names)} messages, {len(files)} dumps")
    for path in files:
        body = path.read_bytes()
        cmd, size = path.stem.split("_")
        print(f"\ncmd {cmd} ({size} B):")
        for fields, name, msg in identify(registry, body, names, args.top):
            print(f"  {name:40s} fields={fields}")
            text = str(msg).strip().splitlines()
            for line in text[:4]:
                print(f"      {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
