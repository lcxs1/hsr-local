"""Handler registration.

Every module exposes `register(router)`; nothing is registered by import side
effects. Add new modules here explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import (
    archive,
    avatar,
    battle,
    challenge,
    item,
    lineup,
    login,
    mission,
    peak,
    player,
    prefs,
    progress,
    scene,
)

if TYPE_CHECKING:
    from hsr.game.router import Router


def register_all(router: Router) -> None:
    for module in (
        login,
        player,
        avatar,
        archive,
        item,
        lineup,
        scene,
        challenge,
        peak,
        prefs,
        mission,
        progress,
        battle,
    ):
        module.register(router)
