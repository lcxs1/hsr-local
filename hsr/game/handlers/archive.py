"""档案（角色图鉴）handler。

4.7.0 客户端解析 ``GetArchiveDataScRsp`` 时会直接解引用 ``archive_data``：
回空 body 会让 ``RPG.Client.AvatarArchiveData.Sync`` 抛 NullReferenceException，
然后 ``ArchiveModule`` 停止发包、心跳 15 秒后断会话（docs/reference/05 §4）。

未知命令的默认回包也是空 body，所以这里必须给“空但存在”的子消息。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.game.router import Router
    from hsr.net.session import Session


def _empty_archive() -> pb.ArchiveData:
    return pb.ArchiveData()


async def on_get_archive_data(
    session: Session, req: pb.GetArchiveDataCsReq
) -> pb.GetArchiveDataScRsp:
    session.runtime.logger.debug("archive data requested")
    return pb.GetArchiveDataScRsp(archive_data=_empty_archive(), retcode=0)


async def on_get_updated_archive_data(
    session: Session, req: pb.GetUpdatedArchiveDataCsReq
) -> pb.GetUpdatedArchiveDataScRsp:
    session.runtime.logger.debug("archive data refresh requested")
    return pb.GetUpdatedArchiveDataScRsp(archive_data=_empty_archive(), retcode=0)


def register(router: Router) -> None:
    router.add("GetArchiveDataCsReq", on_get_archive_data)
    router.add("GetUpdatedArchiveDataCsReq", on_get_updated_archive_data)
