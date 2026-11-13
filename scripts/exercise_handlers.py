"""Run every registered handler against a maximally populated request.

A handler that only works for an empty request still breaks the client the
moment a real packet reaches it: 4.8 renamed or renumbered a field and the
handler touches `.some_old_name`, which raises AttributeError mid-way and the
client never gets its response.  This script fills every scalar/message field of
the request (bounded depth) so those branches actually execute.

Usage:  .venv/Scripts/python.exe scripts/exercise_handlers.py [--profile 4.7.51]
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hsr.net.session import Session
from hsr.protocol import pb
from hsr.runtime import Runtime

MAX_DEPTH = 3
MAX_NODES = 200


class _Counter:
    def __init__(self) -> None:
        self.n = 0


def populate(msg, depth: int = 0, budget: _Counter | None = None) -> None:
    """Fill scalar fields with 1/True/"x" and recurse into one level of messages."""
    if budget is None:
        budget = _Counter()
    if depth > MAX_DEPTH:
        return
    for field in msg.DESCRIPTOR.fields:
        if budget.n > MAX_NODES:
            return
        budget.n += 1
        try:
            if field.type == field.TYPE_MESSAGE:
                if depth >= MAX_DEPTH:
                    continue
                if getattr(field, "is_repeated", False):
                    child = getattr(msg, field.name).add()
                else:
                    child = getattr(msg, field.name)
                populate(child, depth + 1, budget)
            elif field.type == field.TYPE_ENUM:
                continue  # keep 0: a made-up enum number angers validation
            elif field.type == field.TYPE_BOOL:
                setattr(msg, field.name, True)
            elif field.type in (field.TYPE_STRING, field.TYPE_BYTES):
                if field.type == field.TYPE_STRING:
                    setattr(msg, field.name, "x")
            else:
                setattr(msg, field.name, 1)
        except Exception:  # noqa: BLE001 - population is best effort
            continue


class _Sink:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendto(self, data, addr=None) -> None:  # noqa: ANN001
        self.sent.append(data)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="4.7.51")
    args = ap.parse_args()

    rt = Runtime.load(profile=args.profile)
    failures = 0
    sink = _Sink()
    sess = Session(rt, sink, ("127.0.0.1", 5555), b"\0" * 8, 1, 1)
    with pb.using(rt.registry.pb):
        for cmd, (name, fn) in sorted(rt.router.handlers.items()):
            cls = rt.registry.message_cls(name)
            req = cls()
            populate(req)
            try:
                rsp = await fn(sess, req)
                size = 0 if rsp is None else len(rsp.SerializeToString())
                print(f"OK   {cmd:5d} {name:34s} rsp={type(rsp).__name__ if rsp is not None else None} {size}B")
            except Exception as exc:  # noqa: BLE001 - this is the point of the script
                failures += 1
                print(f"FAIL {cmd:5d} {name:34s} {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=2)
    await sess.close("done")
    print(f"\n{len(rt.router.handlers) - failures}/{len(rt.router.handlers)} handlers survived")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
