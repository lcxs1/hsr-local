"""End-to-end smoke test with a headless client (no StarRail, no proxy).

Starts a throw-away server (own ports + own save directory), connects the bot
from hsr/tools/bot.py over real UDP/KCP, and drives the login -> scene -> lineup
-> cocoon battle chain.  With --challenge and --profile 4.6.54 it also drives
the full challenge chain (start -> upper -> switch -> lower -> settle -> leave).

Usage:
    .venv/Scripts/python.exe scripts/bot_smoke.py
    .venv/Scripts/python.exe scripts/bot_smoke.py --profile 4.6.54 --challenge
    .venv/Scripts/python.exe scripts/bot_smoke.py --no-spawn --port 23301
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hsr.tools.bot import Bot, BotError  # noqa: E402
from hsr.runtime import Runtime  # noqa: E402

TMP = ROOT / ".tmp" / "bot-smoke"


def free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_config(game_port: int, http_port: int) -> pathlib.Path:
    TMP.mkdir(parents=True, exist_ok=True)
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    replacements = [
        (r"(?m)^game_port = \d+", f"game_port = {game_port}"),
        (r"(?m)^http_port = \d+", f"http_port = {http_port}"),
        (r'(?m)^save_dir = ".*"', 'save_dir = ".tmp/bot-smoke/saves"'),
        (r'(?m)^log_dir = ".*"', 'log_dir = ".tmp/bot-smoke/logs"'),
        (r'(?m)^lua_dir = ".*"', 'lua_dir = ".tmp/bot-smoke/lua"'),
    ]
    for pattern, value in replacements:
        text, count = re.subn(pattern, value, text)
        if count != 1:
            raise SystemExit(f"config.toml: expected one match for {pattern}, got {count}")
    # paths.save / paths.prefs are pydantic defaults, not keys in config.toml:
    # save_dir alone does NOT move player.json.  Insert explicit overrides.
    text = re.sub(r'(?m)^(save|prefs) = ".*"\r?\n', "", text)
    text = re.sub(
        r"(?m)^\[paths\]\r?$",
        '[paths]\nsave = ".tmp/bot-smoke/saves/player.json"\n'
        'prefs = ".tmp/bot-smoke/saves/server_prefs.json"',
        text,
        count=1,
    )
    (TMP / "saves").mkdir(parents=True, exist_ok=True)
    path = TMP / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def wait_http(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise SystemExit(f"server did not open http://127.0.0.1:{port} within {timeout}s")


class SkipStep(Exception):
    """A step that the active profile cannot run (a command id is missing)."""


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (name, PASS/SKIP/FAIL, detail)

    def run(self, name: str, fn) -> None:
        try:
            fn()
        except SkipStep as exc:
            self.rows.append((name, "SKIP", str(exc)))
        except Exception as exc:  # noqa: BLE001 - a failed step is data, not a crash
            self.rows.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))
        else:
            self.rows.append((name, "PASS", ""))

    @staticmethod
    def check(cond: bool, message: str) -> None:
        if not cond:
            raise AssertionError(message)


def _msg(bot: Bot, req_name: str):
    cls = bot.registry.message_cls(req_name)
    if cls is None:
        raise BotError(f"no message class for {req_name}")
    return cls()


def run_core(bot: Bot, rt: Runtime, checks: Checks, shared: dict) -> None:
    reg = rt.registry

    def step(name: str, fn) -> None:
        checks.run(name, fn)

    def token() -> None:
        pkt = bot.call_empty("PlayerGetTokenCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no token response")
        checks.check(pkt.msg.uid == rt.cfg.player.uid, f"uid {pkt.msg.uid} != {rt.cfg.player.uid}")

    def login() -> None:
        pkt = bot.call_empty("PlayerLoginCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no login response")
        checks.check(
            pkt.msg.basic_info.nickname == rt.cfg.player.name,
            f"nickname {pkt.msg.basic_info.nickname!r} != {rt.cfg.player.name!r}",
        )

    def basic_info() -> None:
        pkt = bot.call_empty("GetBasicInfoCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no basic info response")
        checks.check(bool(pkt.msg.is_gender_set), "is_gender_set is false")

    def avatars() -> None:
        pkt = bot.call_empty("GetAvatarDataCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no avatar response")
        checks.check(bool(pkt.msg.is_get_all), "avatar sync is not full")
        checks.check(
            any(avatar.base_avatar_id == 8001 for avatar in pkt.msg.avatar_list),
            "Trailblazer 8001 missing from avatar list",
        )
        checks.check(
            any(8001 <= path.avatar_id <= 8010 for path in pkt.msg.avatar_path_data_info_list),
            "Trailblazer path data missing",
        )

    def scene() -> None:
        pkt = bot.call_empty("GetCurSceneInfoCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no scene response")
        checks.check(
            pkt.msg.scene.floor_id == rt.cfg.world.floor_id,
            f"floor {pkt.msg.scene.floor_id} != {rt.cfg.world.floor_id}",
        )

    def lineup() -> None:
        pkt = bot.call_empty("GetAllLineupDataCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no lineup response")
        checks.check(len(pkt.msg.lineup_list) >= 1, "no lineup returned")
        shared["lineup"] = [a.id for a in pkt.msg.lineup_list[0].avatar_list]

    def replace() -> None:
        if not reg.has("ReplaceLineupCsReq") or reg.message_cls("LineupSlotData") is None:
            raise SkipStep("ReplaceLineupCsReq missing from this profile")
        slots = [8001, 1506, 1501, 1502]
        slot_cls = reg.message_cls("LineupSlotData")
        req = _msg(bot, "ReplaceLineupCsReq")
        req.leader_slot = 0
        for index, avatar_id in enumerate(slots):
            req.lineup_slot_list.append(slot_cls(slot=index, id=avatar_id))
        pkt = bot.call(req, "ReplaceLineupCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no replace response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")
        shared["team"] = slots

    def path_switch() -> None:
        if not reg.has("SetAvatarPathCsReq"):
            raise BotError("SetAvatarPathCsReq missing from this profile")
        target = 8003 if rt.cfg.player.gender == 1 else 8004
        req = _msg(bot, "SetAvatarPathCsReq")
        req.avatar_id = target
        pkt = bot.call(req, "SetAvatarPathCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no path response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")
        shared["path"] = target

    def cocoon() -> None:
        req = _msg(bot, "StartCocoonStageCsReq")
        req.cocoon_id = 1014
        req.wave = 0
        req.prop_entity_id = 1337
        pkt = bot.call(req, "StartCocoonStageCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no cocoon response")
        checks.check(pkt.msg.HasField("battle_info"), "response has no battle_info")
        checks.check(pkt.msg.battle_info.stage_id == 1052106, f"stage {pkt.msg.battle_info.stage_id}")
        shared["battle_id"] = pkt.msg.battle_info.battle_id
        shared["stage_id"] = pkt.msg.battle_info.stage_id

    def cur_battle() -> None:
        pkt = bot.call_empty("GetCurBattleInfoCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no battle info response")
        checks.check(pkt.msg.HasField("battle_info"), "active battle not exposed")

    def settle() -> None:
        req = _msg(bot, "PVEBattleResultCsReq")
        req.battle_id = shared["battle_id"]
        req.stage_id = shared["stage_id"]
        req.end_status = getattr(rt.registry.pb.BattleEndStatus, "BATTLE_END_WIN", 1)
        pkt = bot.call(req, "PVEBattleResultCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no result response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")

    def retry() -> None:
        req = _msg(bot, "StartCocoonStageCsReq")
        req.cocoon_id = 1014
        req.wave = 0
        req.prop_entity_id = 1337
        pkt = bot.call(req, "StartCocoonStageCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no retry response")
        new_id = pkt.msg.battle_info.battle_id
        checks.check(new_id != shared["battle_id"], "battle id was reused (retry will stall)")

    def quit_battle() -> None:
        if not reg.has("QuitBattleCsReq"):
            raise SkipStep("QuitBattleCsReq missing from this profile")
        pkt = bot.call_empty("QuitBattleCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no quit response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")

    def heartbeat() -> None:
        pkt = bot.call_empty("PlayerHeartBeatCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no heartbeat response")
        stamp = getattr(pkt.msg, "server_time_ms", 0) or getattr(pkt.msg, "server_timestamp_ms", 0)
        checks.check(bool(stamp), "heartbeat response has no server time")

    step("connect (UDP handshake + KCP)", lambda: None)
    step("PlayerGetTokenCsReq", token)
    step("PlayerLoginCsReq", login)
    step("GetBasicInfoCsReq", basic_info)
    step("GetAvatarDataCsReq", avatars)
    step("GetCurSceneInfoCsReq", scene)
    step("GetAllLineupDataCsReq", lineup)
    step("ReplaceLineupCsReq", replace)
    step("SetAvatarPathCsReq", path_switch)
    step("StartCocoonStageCsReq", cocoon)
    step("GetCurBattleInfoCsReq", cur_battle)
    step("PVEBattleResultCsReq (win)", settle)
    step("StartCocoonStageCsReq (again)", retry)
    step("QuitBattleCsReq", quit_battle)
    step("PlayerHeartBeatCsReq", heartbeat)


def run_challenge(bot: Bot, rt: Runtime, checks: Checks, shared: dict) -> None:
    reg = rt.registry
    if not reg.has("StartChallengeCsReq") or not reg.has("SceneCastSkillCsReq"):
        checks.rows.append(("challenge chain", "SKIP", "profile has no challenge commands"))
        return

    data = rt.endgame
    group = data.active_group("MOC") or 100
    candidates = [
        entry for entry in data.challenges.values()
        if entry.group_id == group and entry.node2 is not None
    ]
    if not candidates:
        checks.rows.append(("challenge chain", "SKIP", "no two-half MOC challenge in data"))
        return
    challenge = min(candidates, key=lambda entry: entry.id)

    def start() -> None:
        base = [rt.roster.base_id(a) for a in shared.get("team", [8001, 1506, 1501, 1502])]
        first, second = base[:4], list(reversed(base[:4]))
        avatar_cls = reg.message_cls("AvatarLineup")
        req = _msg(bot, "StartChallengeCsReq")
        req.challenge_id = challenge.id
        for avatar_id in first:
            req.avatar_lineup_first.append(avatar_cls(id=avatar_id))
        for avatar_id in second:
            req.avatar_lineup_second.append(avatar_cls(id=avatar_id))
        pkt = bot.call(req, "StartChallengeCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no start response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")
        checks.check(len(pkt.msg.lineup_list) == 2, "StageNum>=2 must carry two lineups")
        checks.check(pkt.msg.scene.entry_id == challenge.node1.entrance_id, "upper scene mismatch")

    def cast(stage_id: int) -> None:
        req = _msg(bot, "SceneCastSkillCsReq")
        req.cast_entity_id = 1
        pkt = bot.call(req, "SceneCastSkillCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no cast response")
        checks.check(pkt.msg.HasField("battle_info"), "cast returned no battle")
        checks.check(
            pkt.msg.battle_info.stage_id == stage_id,
            f"stage {pkt.msg.battle_info.stage_id} != {stage_id}",
        )
        shared["cbattle"] = pkt.msg.battle_info

    def finish() -> None:
        info = shared["cbattle"]
        req = _msg(bot, "PVEBattleResultCsReq")
        req.battle_id = info.battle_id
        req.stage_id = info.stage_id
        req.end_status = getattr(rt.registry.pb.BattleEndStatus, "BATTLE_END_WIN", 1)
        pkt = bot.call(req, "PVEBattleResultCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no result response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")

    def advance() -> None:
        bot.drain(0.5)
        names = [p.name for p in bot.pending]
        checks.check(
            any(name in ("ChallengeLineupNotify", "EnterSceneByServerScNotify") for name in names),
            f"no phase-switch notify after upper half: {names}",
        )

    def settle() -> None:
        bot.drain(1.0)
        names = [p.name for p in bot.pending]
        checks.check("ChallengeSettleNotify" in names, f"no settle notify: {names}")

    def leave() -> None:
        pkt = bot.call_empty("LeaveChallengeCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no leave response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")

    checks.run(f"StartChallengeCsReq (id={challenge.id})", start)
    checks.run("SceneCastSkillCsReq (upper)", lambda: cast(challenge.node1.stage_id))
    checks.run("PVEBattleResultCsReq (upper win)", finish)
    checks.run("phase switch notifies", advance)
    checks.run("SceneCastSkillCsReq (lower)", lambda: cast(challenge.node2.stage_id))
    checks.run("PVEBattleResultCsReq (lower win)", finish)
    checks.run("ChallengeSettleNotify", settle)
    checks.run("LeaveChallengeCsReq", leave)


def run_peak(bot: Bot, rt: Runtime, checks: Checks, shared: dict) -> None:
    """异相仲裁 chain: data -> start (scene notify) -> cast -> settle -> confirm -> leave."""
    reg = rt.registry
    if not reg.has("StartChallengePeakCsReq") or not reg.has("GetChallengePeakDataCsReq"):
        checks.rows.append(("peak chain", "SKIP", "profile has no peak commands"))
        return

    def data() -> None:
        pkt = bot.call_empty("GetChallengePeakDataCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no peak data response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")
        groups = list(pkt.msg.challenge_peak_groups)
        checks.check(len(groups) == 11, f"{len(groups)} peak groups")
        peaks = [entry.peak_id for entry in groups[0].peaks]
        checks.check(peaks == [101, 102, 103, 104], f"group 1 peaks {peaks}")
        checks.check(groups[0].count_of_peaks == 3, f"count_of_peaks {groups[0].count_of_peaks}")
        shared["peak_group"] = groups[0].peak_group_id

    def start() -> None:
        req = _msg(bot, "StartChallengePeakCsReq")
        req.peak_id = 101
        team = [rt.roster.base_id(a) for a in shared.get("team", [8001, 1506, 1501, 1502])]
        req.peak_avatar_id_list.extend(team)
        pkt = bot.call(req, "StartChallengePeakCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no peak start response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")
        # StartChallengePeakScRsp 里没有 scene，场景只能由这个 Notify 推。
        scene = bot.wait_for("EnterSceneByServerScNotify", timeout=5)
        checks.check(scene.msg.scene.floor_id > 0, "no scene in the peak notify")
        shared["peak_entry"] = scene.msg.scene.entry_id

    def cast() -> None:
        req = _msg(bot, "SceneCastSkillCsReq")
        req.cast_entity_id = 1
        pkt = bot.call(req, "SceneCastSkillCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no cast response")
        checks.check(pkt.msg.HasField("battle_info"), "cast returned no battle")
        checks.check(pkt.msg.battle_info.stage_id == 30501011, f"stage {pkt.msg.battle_info.stage_id}")
        shared["peak_battle"] = pkt.msg.battle_info

    def finish() -> None:
        info = shared["peak_battle"]
        req = _msg(bot, "PVEBattleResultCsReq")
        req.battle_id = info.battle_id
        req.stage_id = info.stage_id
        req.end_status = getattr(reg.pb.BattleEndStatus, "BATTLE_END_WIN", 1)
        req.stt.round_cnt = 4
        pkt = bot.call(req, "PVEBattleResultCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no result response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")

    def settle() -> None:
        settle_pkt = bot.wait_for("ChallengePeakSettleScNotify", timeout=5)
        checks.check(settle_pkt.msg.is_win, "peak settle is_win is false")
        checks.check(settle_pkt.msg.cycles_used == 4, f"cycles {settle_pkt.msg.cycles_used}")
        # is_first_pass 取决于 .tmp/bot-smoke/saves 里有没有上一次的通关记录，
        # 所以只断言“这次是胜利 + 组数据里 101 已过”。
        update = bot.wait_for("ChallengePeakGroupDataUpdateScNotify", timeout=5)
        group = update.msg.challenge_peak_group
        checks.check(group.obtained_stars == 1, f"obtained_stars {group.obtained_stars}")
        passed = [entry.peak_id for entry in group.peaks if entry.has_passed]
        checks.check(passed == [101], f"passed peaks {passed}")

    def confirm() -> None:
        req = _msg(bot, "ConfirmChallengePeakSettleCsReq")
        req.peak_id = 101
        pkt = bot.call(req, "ConfirmChallengePeakSettleCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no confirm response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")

    def leave() -> None:
        pkt = bot.call_empty("LeaveChallengePeakCsReq")
        checks.check(pkt is not None and pkt.msg is not None, "no leave response")
        checks.check(pkt.msg.retcode == 0, f"retcode {pkt.msg.retcode}")

    checks.run("GetChallengePeakDataCsReq", data)
    checks.run("StartChallengePeakCsReq (peak=101)", start)
    checks.run("SceneCastSkillCsReq (peak)", cast)
    checks.run("PVEBattleResultCsReq (peak win)", finish)
    checks.run("ChallengePeakSettleScNotify", settle)
    checks.run("ConfirmChallengePeakSettleCsReq", confirm)
    checks.run("LeaveChallengePeakCsReq", leave)

def main() -> int:
    parser = argparse.ArgumentParser(description="headless hsr-local smoke test")
    parser.add_argument("--profile", default=None, help="server profile (default: config.toml)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="game port (default: spawn on a free port)")
    parser.add_argument("--no-spawn", action="store_true", help="connect to an already running server")
    parser.add_argument("--challenge", action="store_true", help="also drive the MOC two-half chain")
    parser.add_argument("--peak", action="store_true", help="also drive the 异相仲裁 (Peak) chain")
    parser.add_argument("--keep", action="store_true", help="leave the spawned server running")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args()

    profile = args.profile
    rt = Runtime.load(profile=profile)
    profile = rt.profile.name if rt.profile else rt.cfg.server.profile
    checks = Checks()
    shared: dict = {}
    proc = None
    game_port = args.port or free_port(socket.SOCK_DGRAM)
    http_port = free_port(socket.SOCK_STREAM)
    log_path = None

    try:
        if not args.no_spawn:
            config_path = make_config(game_port, http_port)
            log_path = TMP / "server.log"
            handle = log_path.open("w", encoding="utf-8")
            cmd = [sys.executable, "-u", "-m", "hsr", "--config", str(config_path), "--profile", profile]
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            handle.close()
            wait_http(http_port)

        bot = Bot(rt.registry, host=args.host, port=game_port)
        try:
            bot.connect()
            run_core(bot, rt, checks, shared)
            if args.challenge:
                run_challenge(bot, rt, checks, shared)
            if args.peak:
                run_peak(bot, rt, checks, shared)
        finally:
            bot.close()
    finally:
        if proc is not None and not args.keep:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    failed = [row for row in checks.rows if row[1] == "FAIL"]
    passed = sum(1 for row in checks.rows if row[1] == "PASS")
    skipped = sum(1 for row in checks.rows if row[1] == "SKIP")
    if args.json:
        print(json.dumps({
            "profile": profile,
            "game_port": game_port,
            "ok": not failed,
            "steps": [{"name": n, "status": status, "detail": d} for n, status, d in checks.rows],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"profile={profile} game_port={game_port}")
        for name, status, detail in checks.rows:
            print(f"  [{status}] {name}" + (f"  -- {detail}" if detail else ""))
        print(f"\n{passed} passed, {skipped} skipped, {len(failed)} failed")
        if failed and log_path is not None:
            print(f"server log: {log_path}")
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            print("\n".join(tail))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
