"""Shared test fixtures (not a test module: `unittest discover` ignores it)."""

from __future__ import annotations

import logging
from pathlib import Path

from hsr.config import ROOT, AppConfig
from hsr.game.handlers import register_all
from hsr.game.lua import LuaConsole
from hsr.game.router import Router
from hsr.game.services.battle import BattleData
from hsr.game.services.endgame import EndgameData, latest_index
from hsr.game.services.prefs import PrefsStore
from hsr.game.services.progress import ProgressBook
from hsr.game.services.roster import Roster
from hsr.game.services.save import SaveStore
from hsr.game.services.skins import SkinBook
from hsr.game.services.tutorials import TutorialBook
from hsr.net.session import Session
from hsr.protocol.registry import Registry
from hsr.runtime import Runtime

log = logging.getLogger("hsr.test")


class _Transport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendto(self, data: bytes, addr=None) -> None:  # noqa: ANN001
        self.sent.append(data)


def build_runtime(tmp: Path) -> Runtime:
    """Runtime with the real config/freesr/endgame data and temp save/prefs."""
    cfg = AppConfig.load()
    # 单测不跟随 config.toml 里的真机探针开关（每次探针都会改它们）。
    cfg.endgame.advance = "inplace"
    cfg.endgame.avatar_swap = "renumber"  # 真机 A/B 探针不参与单测
    cfg.endgame.battle_stage_override = 0
    cfg.endgame.lobby_entry = 0
    cfg.endgame.lobby_props = "all"
    cfg.endgame.lobby_grace_seconds = 0.0
    cfg.endgame.send_max_level = False
    registry = Registry(ROOT / "proto")
    router = Router(cfg, registry, log)
    register_all(router)
    return Runtime(
        cfg=cfg,
        registry=registry,
        router=router,
        roster=Roster.load(cfg.paths.resolve(cfg.paths.freesr), cfg.player, log),
        lua=LuaConsole(tmp),
        logger=log,
        prefs=PrefsStore.load(tmp / "server_prefs.json"),
        save=SaveStore.load(tmp / "player.json"),
        tutorials=TutorialBook(),
        progress=ProgressBook(),
        battle=BattleData.load(
            cfg.paths.resolve(cfg.paths.battle),
            cfg.paths.resolve(cfg.paths.stages),
        ),
        endgame=EndgameData.load(
            cfg.paths.resolve(cfg.paths.endgame),
            log,
            seasons_path=latest_index(cfg.data_dir),
        ),
        skins=SkinBook.load(cfg.paths.resolve(cfg.paths.skins), log),
    )


def build_session(tmp: Path) -> Session:
    rt = build_runtime(tmp)
    return Session(rt, _Transport(), ("127.0.0.1", 5555), b"\x00" * 8, 1, 1)
