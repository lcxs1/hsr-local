"""Session-scoped protobuf snapshots shared by handlers and the hot reload.

Both ``GetAvatarDataCsReq`` and the freesr hot reload must build the *same*
``AvatarSync`` (owned characters + per-path data + worn skins).  Keeping the
builder here avoids the hot reload importing a handler module, and guarantees
the incremental push and the full response can never drift apart.
"""

from __future__ import annotations

from hsr.protocol.gen import StarRail_pb2 as pb


def avatar_sync(session) -> pb.AvatarSync:
    """Full ``AvatarSync`` for the session's current paths and skins.

    ``session.runtime.save.player.skins`` is filtered through ``SkinBook`` so a
    skin id removed from ``data/skins.json`` never reaches the client.
    """
    runtime = session.runtime
    book = getattr(runtime, "skins", None)
    skins = {
        avatar_id: skin_id
        for avatar_id, skin_id in runtime.save.player.skins.items()
        if book is not None and book.valid(avatar_id, skin_id)
    }
    return runtime.roster.build_avatar_sync(
        tb_path=getattr(session, "tb_multi_path", None),
        march_path=getattr(session, "march_multi_path", None),
        skins=skins,
    )


def bag_lists(session) -> tuple[list[pb.Relic], list[pb.Equipment]]:
    """Relic and lightcone lists for the bag / ``PlayerSyncScNotify``."""
    bag = session.runtime.roster.build_bag()
    return list(bag.relic_list), list(bag.equipment_list)
