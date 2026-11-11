"""Shared test fixtures (not a test module: `unittest discover` ignores it)."""

from __future__ import annotations

import logging
from pathlib import Path

from hsr.config import ROOT, AppConfig
from hsr.game.handlers import register_all
from hsr.game.lua import LuaConsole
from hsr.game.router import Router
from hsr.game.services.battle import BattleData
from hsr.game.services.endgame import EndgameData
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
        endgame=EndgameData.load(cfg.paths.resolve(cfg.paths.endgame), log),
        skins=SkinBook.load(cfg.paths.resolve(cfg.paths.skins), log),
    )


def build_session(tmp: Path) -> Session:
    rt = build_runtime(tmp)
    return Session(rt, _Transport(), ("127.0.0.1", 5555), b"\x00" * 8, 1, 1)
