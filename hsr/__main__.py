"""Entry point: one asyncio loop hosting the UDP game server and the HTTP dispatch."""

from __future__ import annotations

import argparse
import asyncio

from hsr.dispatch import app as dispatch_app
from hsr.game.services import freesr_sync
from hsr.net import server as net_server
from hsr.runtime import Runtime


async def _run(rt: Runtime) -> None:
    protocol = await net_server.start(rt)
    watcher: asyncio.Task | None = None
    features = rt.cfg.features
    if features.freesr_hot_reload and features.freesr_watch:
        # 文件监视：手动复制 data/freesr-data.json 也能即时生效；
        # /srtools 推送后 apply() 会回写文件，watch 会看到一次同内容 mtime 变化，
        # 但内容相同所以只多一次无害的 reload。
        watcher = asyncio.create_task(freesr_sync.watch(rt), name="freesr-watch")
    try:
        await dispatch_app.serve(rt)
    finally:
        if watcher is not None:
            watcher.cancel()
        if protocol.transport is not None:
            protocol.transport.close()
        rt.router.log_unknown_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description="hsr-local server (4.6.54)")
    parser.add_argument("--config", default=None, help="path to config.toml")
    parser.add_argument("--profile", default=None, help="override versions/<name>.toml")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="abort on config errors (bad freesr/team/stage/world ids)",
    )
    args = parser.parse_args()

    rt = Runtime.load(args.config, strict=args.strict, profile=args.profile)
    cfg = rt.cfg
    version = rt.profile.version if rt.profile else cfg.server.version
    print("=" * 64)
    print(f"  hsr-local · {version} · profile={cfg.server.profile} · uid={cfg.player.uid}")
    print(f"  game   udp://{cfg.server.listen}:{cfg.server.game_port}")
    if cfg.server.game_bind_host and cfg.server.game_bind_host != cfg.server.listen:
        print(f"  bind   udp://{cfg.server.game_bind_host}:{cfg.server.game_port} (tap in front)")
    print(f"  sdk    http://{cfg.server.listen}:{cfg.server.http_port}")
    if cfg.features.freesr_hot_reload:
        watch = "watch=on" if cfg.features.freesr_watch else "watch=off"
        print(
            f"  srtools POST http://{cfg.server.listen}:{cfg.server.http_port}/srtools"
            f"  (网页「连接设置 → RobinSR → 连接 PS」，{watch})"
        )
    print(f"  kcp    {cfg.server.kcp_conv_mode}  unknown={cfg.server.unknown_mode}")
    if not (
        cfg.server.listen.startswith("127.")
        or cfg.server.listen in ("::1", "localhost")
    ):
        print("  !! 非回环监听：/srtools、/admin、/debug 无鉴权，会被局域网暴露")
    print("=" * 64)
    print("  启动游戏前确认代理已把登录域名指向 127.0.0.1:21000")
    print("  Ctrl+C 退出")
    print("=" * 64)

    try:
        asyncio.run(_run(rt))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
