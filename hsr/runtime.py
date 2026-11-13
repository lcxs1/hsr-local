"""Runtime context: config + registry + router + persists stores."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from hsr.config import ROOT, AppConfig
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
from hsr.log import setup_logging
from hsr.protocol.registry import Registry
from hsr.version import VersionProfile


@dataclass
class Runtime:
    cfg: AppConfig
    registry: Registry
    router: Router
    roster: Roster
    lua: LuaConsole
    logger: logging.Logger
    prefs: PrefsStore
    save: SaveStore
    tutorials: TutorialBook
    progress: ProgressBook
    battle: BattleData
    endgame: EndgameData = field(default_factory=EndgameData)
    # Kept optional so tests can build a Runtime without touching the filesystem.
    profile: VersionProfile | None = None
    skins: SkinBook = field(default_factory=SkinBook)
    # Serialises /srtools + file-watch reloads so two pushes cannot interleave.
    freesr_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # mtime/size of the last applied freesr file; the watcher ignores our own write.
    freesr_signature: tuple[int, int] | None = None
    # Set by hsr.net.server.start: the GameProtocol that owns the live sessions.
    protocol: object | None = None

    @property
    def freesr_path(self) -> Path:
        """Where ``paths.freesr`` points (the hot-reload target)."""
        return self.cfg.paths.resolve(self.cfg.paths.freesr)

    def sessions(self) -> list:
        """Live game sessions, newest last (empty before the UDP server runs)."""
        protocol = self.protocol
        sessions = getattr(protocol, "sessions", None) or {}
        return [s for s in sessions.values() if not getattr(s, "closed", False)]

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        strict: bool = False,
        profile: str | None = None,
    ) -> Runtime:
        cfg = AppConfig.load(config_path)
        if profile:
            cfg.server.profile = profile
        logger = setup_logging(cfg.server.log_level, cfg.log_dir)
        version_profile = VersionProfile.load(ROOT, cfg.server.profile)
        if (
            cfg.server.version != version_profile.version
            or cfg.server.client_build != version_profile.client_build
        ):
            logger.warning(
                "config.toml version/build (%s/%s) differs from profile %s (%s/%s)",
                cfg.server.version,
                cfg.server.client_build,
                version_profile.name,
                version_profile.version,
                version_profile.client_build,
            )
        registry = Registry(
            version_profile.proto_dir, strict=not version_profile.partial
        )

        router = Router(cfg, registry, logger)

        from hsr.game.handlers import register_all

        register_all(router)
        roster = Roster.load(cfg.paths.resolve(cfg.paths.freesr), cfg.player, logger)

        lua = LuaConsole(cfg.paths.resolve(getattr(cfg.paths, "lua_dir", "lua")))
        if cfg.features.lua_console:
            lua.ensure()

        prefs = PrefsStore.load(cfg.paths.resolve(cfg.paths.prefs))
        save = SaveStore.load(cfg.paths.resolve(cfg.paths.save))
        tutorials = TutorialBook.load(cfg.paths.resolve(cfg.paths.tutorials))
        progress = ProgressBook.load(cfg.paths.resolve(cfg.paths.progress))
        battle = BattleData.load(
            cfg.paths.resolve(cfg.paths.battle),
            cfg.paths.resolve(cfg.paths.stages),
        )
        endgame = EndgameData.load(
            cfg.paths.resolve(cfg.paths.endgame),
            logger,
            seasons_path=latest_index(cfg.data_dir),
        )
        skins = SkinBook.load(cfg.paths.resolve(cfg.paths.skins), logger)

        logger.info(
            "runtime ready: %d handlers, profile=%s (%s/%s), proto=%s",
            len(router.handlers),
            version_profile.name,
            version_profile.version,
            version_profile.client_build,
            version_profile.proto_dir,
        )
        if router.skipped:
            logger.info(
                "partial cmd table: %d handlers skipped (no command id yet)",
                len(router.skipped),
            )
        rt = cls(
            cfg=cfg,
            registry=registry,
            router=router,
            roster=roster,
            lua=lua,
            logger=logger,
            prefs=prefs,
            save=save,
            tutorials=tutorials,
            progress=progress,
            battle=battle,
            endgame=endgame,
            profile=version_profile,
            skins=skins,
        )

        # config_check 放在最后：它要读 roster / 全部 store / battle，前面任何一个没
        # 加载完都会误报。errors 只在 strict 时才抛（见 config_check.validate）。
        from hsr.config_check import ConfigError, validate

        report = validate(rt)
        for warning in report.warnings:
            logger.warning("config check: %s", warning)
        for error in report.errors:
            logger.error("config check: %s", error)
        if report.ok:
            logger.info("config check ok (%d warnings)", len(report.warnings))
        elif strict:
            raise ConfigError("; ".join(report.errors))
        return rt
