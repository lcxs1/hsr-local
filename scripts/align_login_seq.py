"""Pair 4.8 request ids with command names by aligning two login traces.

The client issues the same login requests in the same order on every version, so
a 4.6 trace (command names + body sizes) can be aligned with a 4.8 trace (ids +
body sizes).  Ids that already exist in CmdId.json act as anchors; inside each
anchor-to-anchor segment the two sequences are paired one-to-one when the segment
lengths agree (high confidence) and fuzzy-matched on body size otherwise.

Usage:
    .venv/Scripts/python.exe scripts/align_login_seq.py \
        --ref logs/packets.log.1 --ref-window 14:54:36-14:55:02 \
        --target logs/packets.log --target-window 18:34:56-18:35:02
"""

from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REQ = re.compile(
    r"(?:-> (\S+) \(cmd (\d+)\)|unhandled (?:command: )?(?:UNKNOWN_(\d+)|(\S+)) \(cmd (\d+)\)) (\d+) bytes"
)


def parse(path: pathlib.Path, start: str, stop: str) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    for line in path.read_text("utf-8", "replace").splitlines():
        match = REQ.search(line)
        if not match:
            continue
        stamp = line.split(" ", 1)[0]
        if stamp < start or stamp > stop:
            continue
        name = match.group(1) or match.group(4) or ""
        cmd = int(match.group(2) or match.group(5) or 0)
        out.append((cmd, name, int(match.group(6))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="logs/packets.log.1")
    ap.add_argument("--ref-window", default="14:54:36-14:55:02")
    ap.add_argument("--target", default="logs/packets.log")
    ap.add_argument("--target-window", default="18:34:56-18:35:02")
    ap.add_argument("--out", default="logs/cs_ids.json")
    args = ap.parse_args()

    ref = parse(ROOT / args.ref, *args.ref_window.split("-", 1))
    tgt = parse(ROOT / args.target, *args.target_window.split("-", 1))
    table = json.loads((ROOT / "proto/4.7.51/CmdId.json").read_text(encoding="utf-8"))
    id_name = {int(cmd): name for name, cmd in table.items()}

    anchors = [i for i, entry in enumerate(tgt) if entry[0] in id_name]
    segments: list[tuple[int, int, int, int, str | None]] = []
    prev_r = prev_t = 0
    for i in anchors:
        name = id_name[tgt[i][0]]
        try:
            j = next(k for k in range(prev_r, len(ref)) if ref[k][1] == name)
        except StopIteration:
            continue
        segments.append((prev_r, j, prev_t, i, name))
        prev_r, prev_t = j + 1, i + 1
    segments.append((prev_r, len(ref), prev_t, len(tgt), None))

    result: dict[str, dict] = {}
    for r1, r2, t1, t2, anchor in segments:
        rs, ts = ref[r1:r2], tgt[t1:t2]
        if len(rs) == len(ts):
            for (cmd, _n, size), (_rc, rname, rsize) in zip(ts, rs):
                result[str(cmd)] = {"name": rname, "conf": "exact", "size": [size, rsize], "anchor": anchor}
            continue
        matcher = difflib.SequenceMatcher(
            a=[size for _c, _n, size in rs], b=[size for _c, _n, size in ts], autojunk=False
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag not in ("equal", "replace"):
                continue
            for k in range(min(i2 - i1, j2 - j1)):
                cmd, _n, size = ts[j1 + k]
                _rc, rname, rsize = rs[i1 + k]
                conf = "fuzzy" if tag == "replace" or size != rsize else "order"
                result[str(cmd)] = {"name": rname, "conf": conf, "size": [size, rsize], "anchor": anchor}

    out_path = ROOT / args.out
    out_path.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    counts: dict[str, int] = {}
    for info in result.values():
        counts[info["conf"]] = counts.get(info["conf"], 0) + 1
    print(f"ref={len(ref)} target={len(tgt)} paired={len(result)} {counts}")
    for cmd, info in sorted(result.items(), key=lambda kv: int(kv[0])):
        print(f"  {int(cmd):6d} {info['name']:38s} {info['conf']:5s} {info['size']}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
