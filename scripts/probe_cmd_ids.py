"""Recover client command ids by feeding garbage response bodies.

The 4.8/4.7.51 protocol renumbers every command id, and the client never tells us
the mapping.  It does, however, *log* what happens when a body fails to parse:
the deserialiser throws and the client still invokes the command handler with a
null payload, so the resulting NullReferenceException stack trace names
RPG.Client.<Module>._On<Command>ScRsp.  Sending one byte of garbage under a
candidate id therefore reveals which command that id belongs to.

Usage:
    .venv/Scripts/python.exe scripts/probe_cmd_ids.py --ids 100-199,700-799
    .venv/Scripts/python.exe scripts/probe_cmd_ids.py --ids 1400-1499 --delay 0.2
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

LOG = pathlib.Path(os.environ.get("USERPROFILE", pathlib.Path.home())) / (
    "AppData/LocalLow/Cognosphere/Star Rail/Player.log"
)
URL = "http://127.0.0.1:21000/debug/send?cmd={cmd}&hex={body}"
HANDLER = re.compile(r"RPG\.Client\.[A-Za-z0-9_]+?\._On([A-Za-z0-9_]+?)(ScRsp|CsReq|Notify)\b")
DESERIALIZE = re.compile(r"Deserialize\s+(\S+)\s+meet exception")


def parse_ids(spec: str) -> list[int]:
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(chunk))
    return out


class Tail:
    """Incremental reader for a log the game keeps appending to."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.offset = path.stat().st_size if path.exists() else 0

    def read(self) -> str:
        if not self.path.exists():
            return ""
        size = self.path.stat().st_size
        if size < self.offset:  # truncated or rotated
            self.offset = 0
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            data = fh.read()
        self.offset += len(data)
        return data.decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help="e.g. 100-199,700-799")
    ap.add_argument("--hex", default="ff", help="garbage body (default 0xff)")
    ap.add_argument("--delay", type=float, default=0.05, help="pause between probes")
    ap.add_argument("--wait", type=float, default=0.3, help="max wait for the client to log")
    ap.add_argument("--out", default="logs/probe_ids.json")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument(
        "--skip-known",
        action="store_true",
        help="skip ids already in proto/4.7.51/CmdId.json (probing a live id such as",
    )
    ap.add_argument("--skip", default="", help="extra ids to skip, e.g. 18,96")
    args = ap.parse_args()

    out_path = pathlib.Path(args.out)
    table: dict[str, dict] = {}
    if args.resume and out_path.exists():
        table = json.loads(out_path.read_text(encoding="utf-8"))

    known: set[int] = set()
    if args.skip_known:
        table_path = pathlib.Path(__file__).resolve().parents[1] / "proto/4.7.51/CmdId.json"
        if table_path.exists():
            known = {int(cmd) for cmd in json.loads(table_path.read_text(encoding="utf-8")).values()}
    skip = {int(item) for item in args.skip.split(",") if item.strip()}

    tail = Tail(LOG)
    started = time.time()
    for cmd in parse_ids(args.ids):
        key = str(cmd)
        if key in table or cmd in known or cmd in skip:
            continue
        tail.read()  # drop anything still buffered
        try:
            with urllib.request.urlopen(URL.format(cmd=cmd, body=args.hex), timeout=5) as rsp:
                rsp.read()
        except Exception as exc:  # noqa: BLE001 - the server may have no session
            print(f"send failed for {cmd}: {exc}", file=sys.stderr)
            time.sleep(args.delay)
            continue
        # Poll rather than sleeping a fixed amount: most ids answer within ~30 ms
        # and a fixed 250 ms wait dominated the whole sweep.
        text = ""
        deadline = time.time() + args.wait
        while time.time() < deadline:
            time.sleep(0.03)
            chunk = tail.read()
            if chunk:
                text += chunk
                time.sleep(0.06)  # the NRE stack follows the deserialize error
                text += tail.read()
                break
        handlers = sorted({f"{m.group(1)}{m.group(2)}" for m in HANDLER.finditer(text)})
        proto = [m.group(1) for m in DESERIALIZE.finditer(text)]
        if handlers or proto:
            table[key] = {"handlers": handlers, "proto": proto}
            print(f"{cmd:6d} -> {', '.join(handlers) or '?'}  proto={','.join(proto) or '-'}")
        time.sleep(args.delay)
        if int(time.time() - started) % 30 == 0:
            out_path.write_text(json.dumps(table, indent=1, sort_keys=True), encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(table, indent=1, sort_keys=True), encoding="utf-8")
    print(f"wrote {out_path} ({len(table)} ids)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
