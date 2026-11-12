#!/usr/bin/env python
"""Summarise one game_test run: did each challenge half actually get fought?

    python scripts/analyze_run.py logs/test/test-20261112-185701.server.log
    python scripts/analyze_run.py logs/test            # newest run in that dir
    python scripts/analyze_run.py --all logs/test      # every run in that dir

Exit code 0 when every challenge played its halves for real, 1 otherwise (so the
PowerShell wrapper can just forward it).

The judgement this prints is the one the 下半 investigation needs:

* 换半场（challenge advance）有没有发生、是哪个模式、发了几个包；
* advance 之后客户端有没有再发 SceneCastSkillCsReq（对下半的怪出手）；
* 每半场的 battle stats：turns > 0 = 真打，turns=0 + 固定 ~9 s =
  战斗场景没加载完、客户端超时判胜。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TS = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3})\s+\w+\s+hsr: (.*)$")
ADVANCE = re.compile(
    r"challenge advance \((\w+)\): id=(\d+) .*phase=(\d+)->(\d+) .*packets=(\d+)"
)
BATTLE = re.compile(
    r"challenge battle info: id=(\d+) challenge=(\d+) mode=(\w+) phase=(\d+) stage=(\d+)"
)
STATS = re.compile(
    r"battle stats: battle=(\d+) .*turns=(\d+) auto_turns=(\d+) cost=(\d+)s"
)
SETTLE = re.compile(r"challenge settle: challenge=(\d+) phase=(\d+)/(\d+) win=(\w+)")
CAST = re.compile(r"-> SceneCastSkillCsReq")

# 换半场之后客户端「超时判胜」的时长上限（抓包/真机都是 9 s 左右）。
TIMEOUT_COST = 15


@dataclass
class Half:
    phase: int
    stage: int = 0
    battle_id: int = 0
    turns: int | None = None
    cost: int | None = None

    @property
    def fought(self) -> bool:
        return bool(self.turns)

    def text(self) -> str:
        if self.turns is None:
            return f"phase={self.phase} stage={self.stage} 未开打"
        mark = "✓" if self.fought else "✗"
        return (
            f"phase={self.phase} stage={self.stage} turns={self.turns}"
            f" cost={self.cost}s {mark}"
        )


@dataclass
class Run:
    mode: str = ""
    halves: dict[int, Half] = field(default_factory=dict)
    advance_ts: str = ""
    advance_packets: int = 0
    casts_after: int = 0
    cast_delay_ms: int | None = None
    settled: bool = False

    @property
    def lower(self) -> Half | None:
        return self.halves.get(2)


def find_logs(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(target.glob("*.server.log"))
    if target.suffix == ".log":
        return [target]
    return [target.with_suffix(".server.log")]


def ms(stamp: str) -> int:
    hh, mm, rest = stamp.split(":")
    ss, msec = rest.split(".")
    return ((int(hh) * 60 + int(mm)) * 60 + int(ss)) * 1000 + int(msec)


def parse(server_log: Path) -> dict[int, Run]:
    """Group the run by challenge id (a session can clear several floors)."""
    text = server_log.read_text(encoding="utf-8", errors="replace").splitlines()
    runs: dict[int, Run] = {}
    battles: dict[int, tuple[int, int]] = {}  # battle id -> (challenge, phase)
    casts: list[int] = []
    settle_ids: set[int] = set()

    for line in text:
        m = TS.match(line)
        if not m:
            continue
        stamp, body = m.group(1), m.group(2)
        a = ADVANCE.search(body)
        if a:
            run = runs.setdefault(int(a.group(2)), Run())
            run.mode = a.group(1)
            run.advance_ts = stamp
            run.advance_packets = int(a.group(5))
            continue
        b = BATTLE.search(body)
        if b:
            run = runs.setdefault(int(b.group(2)), Run())
            half = run.halves.get(int(b.group(4))) or Half(phase=int(b.group(4)))
            half.stage = int(b.group(5))
            half.battle_id = int(b.group(1))
            run.halves[half.phase] = half
            battles[int(b.group(1))] = (int(b.group(2)), half.phase)
            continue
        s = STATS.search(body)
        if s:
            owner = battles.get(int(s.group(1)))
            if owner is None:
                continue
            run = runs.setdefault(owner[0], Run())
            half = run.halves.get(owner[1])
            if half is not None:
                half.turns = int(s.group(2))
                half.cost = int(s.group(4))
            continue
        t = SETTLE.search(body)
        if t:
            settle_ids.add(int(t.group(1)))
            continue

    packets = server_log.with_name(
        server_log.name.replace(".server.log", ".packets.log")
    )
    if packets.exists():
        for line in packets.read_text(encoding="utf-8", errors="replace").splitlines():
            m = TS.match(line)
            if m and CAST.search(m.group(2)):
                casts.append(ms(m.group(1)))

    for cid, run in runs.items():
        if run.advance_ts:
            later = [c for c in casts if c >= ms(run.advance_ts)]
            run.casts_after = len(later)
            if later:
                run.cast_delay_ms = later[0] - ms(run.advance_ts)
        run.settled = cid in settle_ids
    return runs


def verdict(run: Run) -> tuple[bool, str]:
    if not run.advance_ts:
        return False, "没有换半场（日志里没有 challenge advance）"
    if not run.lower:
        return False, "只打了上半，下半没开打"
    lower = run.lower
    if run.casts_after == 0:
        return False, "换半场之后客户端没有再 cast（没对下半的怪出手）"
    if lower.fought:
        return True, f"下半真打了（turns={lower.turns} cost={lower.cost}s）"
    if lower.cost is not None and lower.cost <= TIMEOUT_COST:
        return False, f"下半战斗没起来（turns=0，{lower.cost}s 后自己判胜 = 场景加载超时）"
    return False, "下半战斗没起来（turns=0）"


def report(server_log: Path) -> bool:
    runs = parse(server_log)
    print(f"运行：{server_log.stem.replace('.server', '')}")
    if not runs:
        print("  没有解析到挑战事件（日志被截断？这局没打挑战？）")
        return False
    bad = 0
    for cid in sorted(runs):
        run = runs[cid]
        halves = " / ".join(run.halves[p].text() for p in sorted(run.halves)) or "无"
        print(f"\n挑战 {cid}：{halves}")
        if run.advance_ts:
            delay = f"（+{run.cast_delay_ms} ms）" if run.cast_delay_ms is not None else ""
            print(
                f"  换半场 {run.advance_ts} mode={run.mode}"
                f" packets={run.advance_packets}；之后 cast {run.casts_after} 次{delay}"
            )
        else:
            print("  换半场：没有发生")
        if run.settled:
            print("  结算：已结算" + ("" if run.lower and run.lower.fought else "（无真打）"))
        ok, why = verdict(run)
        print(f"  判定：{'PASS' if ok else 'FAIL'} — {why}")
        bad += 0 if ok else 1
    return bad == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a *.server.log file, a run prefix, or a directory")
    ap.add_argument("--all", action="store_true", help="analyse every run in the directory")
    args = ap.parse_args(argv)
    logs = find_logs(Path(args.target))
    if not logs:
        print(f"没有找到 *.server.log：{args.target}", file=sys.stderr)
        return 2
    if not args.all:
        logs = logs[-1:]
    ok = True
    for log in logs:
        ok = report(log) and ok
    print()
    print("总判定：" + ("PASS" if ok else "FAIL"))
    if not ok:
        print(
            "参考：换半场对了但下半 turns=0 → 看截图 *-battle-info+4.png 是否全黑；"
            "全黑 = 战斗场景没加载完。两个层都这样就照「第二场战斗」查，"
            "只有某一层这样就是那一层 stage 的数据/资源问题。"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
