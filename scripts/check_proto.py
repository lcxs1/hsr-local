"""Pre-flight check for a new proto/build snapshot.

Run this after replacing proto/ and versions/<build>.toml.  It checks that every
registered handler still resolves to a command/message in the new snapshot and
that the handful of core response fields still exist.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hsr.config import AppConfig
from hsr.game.handlers import register_all
from hsr.game.router import Router
from hsr.protocol.registry import Registry
from hsr.version import VersionProfile

log = logging.getLogger("hsr.check_proto")

CORE_FIELDS = {
    "GateServer": [
        "ip",
        "port",
        "asset_bundle_url",
        "ex_resource_url",
        "lua_url",
        "ifix_url",
    ],
    "PlayerLoginScRsp": ["basic_info", "server_timestamp_ms"],
    "GetCurSceneInfoScRsp": ["scene"],
    "StartCocoonStageScRsp": ["cocoon_id", "wave", "battle_info"],
    "PVEBattleResultScRsp": ["battle_id", "stage_id", "end_status"],
    "DressAvatarSkinScRsp": ["retcode"],
    "TakeOffAvatarSkinScRsp": ["retcode"],
}

NO_RESPONSE = {"PlayerLogoutCsReq"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None, help="versions/<name>.toml (default: config.toml)")
    ap.add_argument("--verbose", action="store_true", help="list skipped handlers")
    args = ap.parse_args()

    cfg = AppConfig.load()
    name = args.profile or cfg.server.profile
    profile = VersionProfile.load(ROOT, name)
    registry = Registry(profile.proto_dir, strict=not profile.partial)
    router = Router(cfg, registry, logging.getLogger("hsr"))
    register_all(router)

    errors: list[str] = []
    for cmd, (req_name, _fn) in sorted(router.handlers.items()):
        if registry.message_cls(req_name) is None:
            errors.append(f"{req_name} (cmd {cmd}) has no protobuf message")
        rsp = registry.rsp_name_of(req_name)
        if rsp is None and req_name not in NO_RESPONSE:
            errors.append(f"{req_name} (cmd {cmd}) has no ScRsp mapping")
        elif rsp is not None and registry.message_cls(rsp) is None:
            errors.append(f"{req_name} -> {rsp} has no protobuf message")

    for msg_name, fields in CORE_FIELDS.items():
        cls = registry.message_cls(msg_name)
        if cls is None:
            errors.append(f"core message missing: {msg_name}")
            continue
        for field in fields:
            if field not in cls.DESCRIPTOR.fields_by_name:
                errors.append(f"{msg_name} field missing: {field}")

    gate_cls = registry.message_cls("GateServer")
    if gate_cls is not None:
        gate_fields = gate_cls.DESCRIPTOR.fields_by_name
        for semantic, alias in profile.field_aliases.items():
            if semantic not in gate_fields and alias not in gate_fields:
                errors.append(f"gateway field alias {semantic}->{alias} matches neither name")

    print(f"profile: {profile.name} ({profile.version}/{profile.client_build})")
    print(f"handlers: {len(router.handlers)}")
    if router.skipped:
        # Partial tables are normal while a new client is being ported; the id is
        # simply not recovered yet, so this is a note, not a failure.
        print(f"skipped (no command id yet): {len(router.skipped)}")
        if args.verbose:
            for name in router.skipped:
                print(f"  skip {name}")
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    if errors:
        print(f"FAILED: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    print("OK: handlers and core fields resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
