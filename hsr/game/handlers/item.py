"""Inventory handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


async def on_get_bag(session: Session, req: pb.GetBagCsReq) -> pb.GetBagScRsp:
    return session.runtime.roster.build_bag()


async def on_get_mark_item_list(
    session: Session, req: pb.GetMarkItemListCsReq
) -> pb.GetMarkItemListScRsp:
    # The "marked items" list (the bag's filter/favourite set) is empty because
    # nothing in hsr-local stars an item yet: MarkItemCsReq (568) still falls
    # through to unknown_mode=empty_rsp and CancelMarkItemNotify is a Notify, so
    # an item starred in the client is never recorded.  Answering with an empty
    # list is what pearl-sr does too (dummy pair in handlers.zig).  The repeated
    # field (tag 14, name obfuscated in the 4.6.54 descriptor) is only read by
    # the filter UI, so empty is safe.
    return pb.GetMarkItemListScRsp()


def register(router: Router) -> None:
    router.add("GetBagCsReq", on_get_bag)
    router.add("GetMarkItemListCsReq", on_get_mark_item_list)
