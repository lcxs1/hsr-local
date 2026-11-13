#!/usr/bin/env python
"""异相仲裁（Peak）真机日志判定：这局到底有没有真的进 Peak、开打、结算。

    python scripts/analyze_peak.py logs/test/test-20261113-210000.server.log
    python scripts/analyze_peak.py logs/test          # 最新一轮

Exit code 0 = Peak 链真跑通（进场 → 战斗包 → 结算），1 = 没跑通，2 = 找不到日志。

判定依据（服务端日志）：

* ``peak data: N groups``                  客户端拉过 Peak 面板数据；
* ``start peak: peak=101``                 客户端发过 StartChallengePeak；
* ``challenge battle info ... mode=PEAK``  战斗包发出去过（stage 一起打出来）；
* ``peak settle: peak=101 win=True``       ChallengePeakSettleScNotify 已发；
* ``confirm peak settle``                  客户端确认结算；
* ``leave peak``                           退出并回场景。

``debug peak: pushed arena`` 表示走了 /debug/peak（没有 HUD 的探针路线）：
这条路线只要能开出战斗包就算“场景/战斗包没问题”，不算完整闭环。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TS = re.compile(r"^(\d\d:\d\d:\d\d\.\d{3})\s+\w+\s+hsr: (.*)$")
DATA = re.compile(r"peak data: (\d+) groups")
START = re.compile(r"start peak: peak=(\d+) group=(\d+) boss=(\w+)")
ENTERED = re.compile(r"peak entered: peak=(\d+)")
DEBUG = re.compile(r"debug peak: pushed arena for peak=(\d+)")
INFO = re.compile(
    r"challenge battle info: id=(\d+) challenge=(\d+) mode=PEAK phase=(\d+) stage=(\d+)"
)
SETTLE = re.compile(
    r"peak settle: peak=(\d+) group=(\d+) boss=(\w+) win=(\w+) cycles=(\d+)"
    r" first=(\w+) hard=(\w+)"
)
CONFIRM = re.compile(r"confirm peak settle: peak=(\d+)")
LEAVE = re.compile(r"leave peak: back to scene (\d+)/(\d+)")
HARD = re.compile(r"peak hard mode: group=(\d+) hard=(\w+)")
REWARD = re.compile(r"peak reward: group=(\d+)")
FAILED = re.compile(r"handler .* failed")
REQ = re.compile(r"-> (\w+CsReq)")

PEAK_REQUESTS = (
    "GetChallengePeakDataCsReq",
    "GetCurChallengePeakCsReq",
    "StartChallengePeakCsReq",
    "ReStartChallengePeakCsReq",
    "ConfirmChallengePeakSettleCsReq",
    "LeaveChallengePeakCsReq",
    "TakeChallengePeakRewardCsReq",
    "SetChallengePeakBossHardModeCsReq",
    "SetChallengePeakMobLineupAvatarCsReq",
)


@dataclass
class PeakRun:
    peak_id: int = 0
    group: int = 0
    boss: bool = False
    entered: bool = False
    debug: bool = False
    battle_id: int = 0
    stage: int = 0
    settle: str = ""
    win: bool = False
    cycles: int = 0
    first: bool = False
    hard: bool = False
    confirm: bool = False
    leave: bool = False


@dataclass
class PeakLog:
    runs: dict[int, PeakRun] = field(default_factory=dict)
    handler_failures: list[str] = field(default_factory=list)
    unknown_commands: list[str] = field(default_factory=list)
    peak_stats: dict[str, int] = field(default_factory=dict)
    hard_modes: list[int] = field(default_factory=list)
    rewards: int = 0
    data_groups: int = 0
    leave_scene: tuple[int, int] | None = None


def _run(log: PeakLog, peak_id: int) -> PeakRun:
    run = log.runs.get(peak_id)
    if run is None:
        run = PeakRun(peak_id=peak_id)
        log.runs[peak_id] = run
    return run


def parse(server_log: Path, packets_log: Path | None = None) -> PeakLog:
    log = PeakLog()
    for raw in server_log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = TS.match(raw)
        if not match:
            continue
        stamp, body = match.group(1), match.group(2)
        hit = DATA.search(body)
        if hit:
            log.data_groups = int(hit.group(1))
            continue
        hit = ENTERED.search(body)
        if hit:
            _run(log, int(hit.group(1))).entered = True
            continue
        hit = DEBUG.search(body)
        if hit:
            _run(log, int(hit.group(1))).debug = True
            continue
        hit = START.search(body)
        if hit:
            run = _run(log, int(hit.group(1)))
            run.group = int(hit.group(2))
            run.boss = hit.group(3) == "True"
            run.entered = True
            continue
        hit = INFO.search(body)
        if hit:
            run = _run(log, int(hit.group(2)))
            run.battle_id = int(hit.group(1))
            run.stage = int(hit.group(4))
            continue
        hit = SETTLE.search(body)
        if hit:
            run = _run(log, int(hit.group(1)))
            run.group = int(hit.group(2))
            run.boss = hit.group(3) == "True"
            run.win = hit.group(4) == "True"
            run.cycles = int(hit.group(5))
            run.first = hit.group(6) == "True"
            run.hard = hit.group(7) == "True"
            run.settle = stamp
            continue
        hit = CONFIRM.search(body)
        if hit:
            _run(log, int(hit.group(1))).confirm = True
            continue
        hit = LEAVE.search(body)
        if hit:
            for run in log.runs.values():
                run.leave = True
            log.leave_scene = (int(hit.group(1)), int(hit.group(2)))
            continue
        hit = HARD.search(body)
        if hit:
            log.hard_modes.append(int(hit.group(1)))
            continue
        if REWARD.search(body):
            log.rewards += 1
            continue
        if FAILED.search(body):
            log.handler_failures.append(body)
            continue
        if "unhandled command:" in body:
            log.unknown_commands.append(body)
    if packets_log is not None and packets_log.exists():
        for line in packets_log.read_text(encoding="utf-8", errors="replace").splitlines():
            hit = REQ.search(line)
            if not hit:
                continue
            name = hit.group(1)
            if name in PEAK_REQUESTS:
                log.peak_stats[name] = log.peak_stats.get(name, 0) + 1
    return log


def verdict(log: PeakLog) -> tuple[bool, str]:
    runs = [run for run in log.runs.values() if run.entered or run.debug]
    if not runs:
        return False, "日志里没有任何 Peak 进场记录（start peak / debug peak）"
    bad: list[str] = []
    full_loop = False
    for run in runs:
        if not run.battle_id:
            bad.append(f"peak {run.peak_id}：进了场景但没有战斗包（SceneCastSkill 没出去）")
            continue
        if not run.settle:
            bad.append(f"peak {run.peak_id}：有战斗包但没有 ChallengePeakSettleScNotify")
            continue
        if not run.win:
            bad.append(f"peak {run.peak_id}：结算 win=False")
            continue
        if run.confirm and run.leave:
            full_loop = True
    if log.handler_failures:
        bad.append(f"服务端 handler 异常 {len(log.handler_failures)} 条")
    if bad:
        return False, "；".join(bad)
    if full_loop:
        return True, "Peak 全链通过（进场 → 战斗包 → 结算 → 确认 → 离开）"
    return True, "Peak 战斗+结算通过（没看到 Confirm/Leave，可能是 /debug 探针或提前退出）"


def report(server_log: Path) -> bool:
    packets = server_log.with_name(server_log.stem.replace(".server", "") + ".packets.log")
    log = parse(server_log, packets)
    print(f"运行：{server_log.stem.replace('.server', '')}")
    if not log.runs:
        print("  没有解析到 Peak 事件（这局没开 Peak / 日志被截断）")
        return False
    print(f"  Peak 面板数据：{log.data_groups} 个组")
    for peak_id in sorted(log.runs):
        run = log.runs[peak_id]
        flags = ["BOSS" if run.boss else "小关"]
        if run.debug:
            flags.append("debug 直进")
        if run.hard:
            flags.append("困难")
        print(f"\nPeak {peak_id}（group {run.group or '?'}，{'/'.join(flags)}）：")
        print(f"  场景：{'进了' if run.entered else '没进'}")
        print(f"  战斗包：{'stage=' + str(run.stage) if run.battle_id else '没有'}")
        if run.settle:
            print(
                f"  结算：{run.settle} win={run.win} cycles={run.cycles}"
                f" first={run.first}"
            )
        else:
            print("  结算：没有")
        print(f"  确认/离开：{'有' if run.confirm else '无'} / {'有' if run.leave else '无'}")
    if log.peak_stats:
        print("\nPeak 请求（packets.log）：")
        for name in PEAK_REQUESTS:
            if name in log.peak_stats:
                print(f"  {name:38s} {log.peak_stats[name]}")
    ok, why = verdict(log)
    print(f"\n判定：{'PASS' if ok else 'FAIL'} — {why}")
    if log.handler_failures:
        print("\nhandler 异常（前 5 条）：")
        for line in log.handler_failures[:5]:
            print(f"  {line}")
    if log.unknown_commands:
        print(f"\n未注册命令：{len(log.unknown_commands)} 条（命令表缺号时正常）")
    return ok


def find_logs(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(target.glob("*.server.log"))
    if target.exists():
        return [target]
    candidate = target.with_name(target.name + ".server.log")
    return [candidate] if candidate.exists() else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="*.server.log、运行前缀，或 logs\\test 目录")
    ap.add_argument("--all", action="store_true", help="分析目录里的每一轮")
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
        print("参考：没进场 → 看界面有没有异相仲裁入口；进场没战斗包 → 看截图是否黑屏；")
        print("      有战斗包没结算 → 查 report.md 的 handler 异常；win=False → 队伍/关卡数据。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
