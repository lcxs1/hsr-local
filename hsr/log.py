"""Logging setup.

One console logger for humans, one optional file for packet traces. Keep it boring:
no third-party log framework, no colors that break on old Windows consoles.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


class _Formatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802 - stdlib signature
        import datetime as _dt

        ts = _dt.datetime.fromtimestamp(record.created)
        return ts.strftime("%H:%M:%S.") + f"{int(record.msecs):03d}"


def setup_logging(
    level: str = "INFO", log_dir: Path | None = None, packet_log: bool = True
) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("hsr")
    if _CONFIGURED:
        return logger
    _CONFIGURED = True

    # The logger itself must stay at DEBUG so the file handler can capture packet
    # traces; the console handler applies the configured level instead.
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(_Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s"))
    logger.addHandler(console)

    if log_dir is not None and packet_log:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "packets.log",
            maxBytes=8 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(_Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s"))
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)

    # uvicorn's access log is noise for a local server
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    return logger


def get_logger(name: str = "hsr") -> logging.Logger:
    return logging.getLogger(name)
