"""Fake HoYo SDK + dispatch gateway.

Only the endpoints in the real 4.6.54 login chain are implemented. Everything is
plain HTTP on localhost; the real client reaches us through the existing proxy
(firefly-go-proxy / Cultivation), which rewrites the login domains.

Chain (docs/design/01 §1-2): ``query_dispatch`` -> ``query_gateway`` -> SDK JSON ->
``exchange`` -> UDP handshake on the game port.  Two hops are load-bearing: a
404/405 on ``query_gateway`` or ``exchange`` aborts the login before the game
server is ever contacted.  Everything else the client tolerates being missing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from hsr import compat
from hsr.dispatch import official
from hsr.dispatch.hotfix import HotfixStore, is_local, localize
from hsr.dispatch.local_cdn import LocalCdn
from hsr.game.services import freesr_sync
from hsr.protocol.gen import StarRail_pb2 as pb

if TYPE_CHECKING:
    from hsr.runtime import Runtime


def b64_proto(msg) -> PlainTextResponse:
    return PlainTextResponse(base64.b64encode(msg.SerializeToString()))


# 浏览器里的 SR Tools（https://srtools.punklorde.org）是跨域 POST 到
# http://localhost:21000/srtools，所以这几个头必须原样回；OPTIONS 预检也要单独
# 处理（FastAPI 默认 405）。只对本机 127.0.0.1 监听，不需要鉴权。
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Vary": "origin, access-control-request-method, access-control-request-headers",
}


def with_cors(response: Response) -> Response:
    response.headers.update(_CORS_HEADERS)
    return response


def srtools_error(message: str, status: int = 400) -> JSONResponse:
    return with_cors(
        JSONResponse({"message": message, "status": status}, status_code=status)
    )


def truthy(value: object) -> bool:
    """``?force=1`` / ``"force": true`` style flag parsing."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "force"}
    return False


def build_get_config(mode: str = "disabled") -> dict:
    """SDK ``combo/granter/api/getConfig`` payload.

    The login-time popup is the SDK announcement panel: when the announcement
    fields are absent the SDK falls back to its own notice page and opens it
    full-screen after loading.  ``disabled`` mirrors pearl-sr (empty
    ``announce_url`` + ``enable_announce_pic_popup=false``); ``serve`` keeps the
    old payload for A/B; ``notfound`` is handled by the route (HTTP 404).
    """
    if mode == "serve":
        return {
            "protocol": True,
            "qr_enabled": False,
            "log_level": "INFO",
            "push_alias_type": 0,
            "disable_ysdk_guard": False,
            "protocol_version": "",
            "protocol_version_new": False,
        }
    return {
        "protocol": True,
        "qr_enabled": False,
        "log_level": "INFO",
        "announce_url": "",
        "enable_announce_pic_popup": False,
        "disable_ysdk_guard": True,
        "push_alias_type": 0,
        "protocol_version": "",
        "protocol_version_new": False,
    }


def build_dispatch(rt: Runtime) -> pb.Dispatch:
    cfg = rt.cfg
    return pb.Dispatch(
        retcode=0,
        msg="OK",
        # 字段名里的 "sever" 是 proto 里的拼写，不要"修正"；env_type="9" 是
        # 参考实现共同的取值（docs/design/01 §2.1）。
        top_sever_region_name="hsr-local",
        region_list=[
            pb.RegionInfo(
                name="Local",
                title="HSR Local",
                display_name="HSR Local",
                env_type="9",
                dispatch_url=f"http://{cfg.server.listen}:{cfg.server.http_port}/query_gateway",
            )
        ],
    )


def local_cdn_base(cfg) -> str:
    """本地 CDN 回放的根地址；``/cdn`` 之后的路径由 hotfix 条目自带。"""
    return f"http://{cfg.server.listen}:{cfg.server.http_port}/cdn"


def build_gateway(rt: Runtime, hotfix: HotfixStore, version: str) -> pb.GateServer:
    cfg = rt.cfg
    rsp = pb.GateServer(
        ip=cfg.server.listen,
        port=cfg.server.game_port,
        # 4.6.5x 的 8 个含义未知的 bool，全部置 true 是已知最宽松的取值；
        # 置 false 会让客户端在进入游戏服前就走别的分支（docs/design/01 §2.1）。
        unk1=True,
        unk2=True,
        unk3=True,
        unk4=True,
        unk5=True,
        unk6=True,
        unk7=True,
        unk8=True,
    )
    if cfg.features.send_hotfix:
        hf = hotfix.for_version(version)
        if is_local(hf):
            if cfg.features.local_cdn:
                # LOCAL_CDN 是占位符，换成这台机器上的 /cdn 路由。
                hf = localize(hf, local_cdn_base(cfg))
            else:
                rt.logger.error(
                    "hotfix %s is local-only; set features.local_cdn=true "
                    "or the client fails its resource check",
                    version,
                )
        rsp.asset_bundle_url = hf["asset_bundle_url"]
        # Field names differ by build; the profile owns the alias.
        aliases = rt.profile.field_aliases if getattr(rt, "profile", None) else {}
        compat.set_field(rsp, "asset_bundle_url_b", hf["asset_bundle_url_b"], aliases)
        rsp.ex_resource_url = hf["ex_resource_url"]
        rsp.lua_url = hf["lua_url"]
        rsp.ifix_url = hf["ifix_url"]
        compat.set_field(rsp, "lua_version", hf["lua_version"], aliases)
        rsp.ifix_version = hf["ifix_version"]
    return rsp


def create_app(rt: Runtime) -> FastAPI:
    """Build the FastAPI app: one route per step of the client login chain.

    Routes are grouped in the order the client calls them, not by importance:
    dispatch -> gateway -> shield API -> granter API -> passport/session API.
    ``{region}`` stays a path variable so ``/hkrpg_cn/...`` and
    ``/hkrpg_global/...`` both hit the same handler (docs/design/01 §2.2).

    Every token below is the constant ``"local-token"`` on purpose: the game
    server never validates the token the client carries into
    ``PlayerGetTokenCsReq``, so any stable string works and makes packet logs
    easy to correlate.
    """
    app = FastAPI(title="hsr-local dispatch", docs_url=None, redoc_url=None)
    hotfix = HotfixStore(rt.cfg.hotfix_path)
    cfg = rt.cfg
    cdn: LocalCdn | None = None
    if cfg.features.local_cdn:
        if not cfg.paths.client_dir:
            rt.logger.error("features.local_cdn is on but paths.client_dir is empty")
        else:
            candidate = LocalCdn(cfg.paths.resolve(cfg.paths.client_dir))
            if candidate.available():
                cdn = candidate
            else:
                rt.logger.error(
                    "local CDN: %s has no StarRail_Data/", candidate.client_dir
                )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        rt.logger.debug(
            "%s %s -> %d (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
        )
        return response

    @app.get("/")
    async def root():
        return {
            "name": "hsr-local",
            "version": rt.profile.version if rt.profile else cfg.server.version,
            "profile": cfg.server.profile,
            "game_port": cfg.server.game_port,
            "srtools": "/srtools",
        }

    # ------------------------------------------------ 模拟器口径：直接选关开打
    # 忘却之庭大厅在 4.6.54 beta 客户端上会卡死（MapNPC:NNN-400001 没有
    # ConfigEntityPath），参考实现（pearl-sr 的 /id）本来就不走大厅：直接把
    # challenge id 告诉服务端。这里用 HTTP 做同一件事：
    #   GET /debug/challenge            列出当前赛季的层和 challenge id
    #   GET /debug/challenge?id=5512    把玩家直接送进那一层的 arena
    @app.get("/debug/challenge")
    async def debug_challenge(id: int = 0):  # noqa: A002 - query parameter name
        from hsr.game.services import challenge as challenge_service

        sessions = rt.sessions()
        if not sessions:
            return JSONResponse(
                {"ok": False, "error": "no live session (log in first)"}, status_code=409
            )
        session = sessions[-1]
        endgame = rt.endgame
        group_id = int(cfg.endgame.moc_group_id or 0) or endgame.active_group("MOC")
        if not id:
            season = endgame.season(group_id)
            floors = sorted(
                (challenge.id, challenge.floor)
                for challenge in endgame.challenges.values()
                if challenge.group_id == group_id
            )
            return {
                "ok": True,
                "active_group": group_id,
                "season": season.name if season else "",
                "floors": [{"id": cid, "floor": floor} for cid, floor in floors],
            }
        scene = challenge_service.start_direct(session, id)
        if scene is None:
            return JSONResponse(
                {"ok": False, "error": f"unknown challenge {id}"}, status_code=404
            )
        await session.send(
            pb.EnterSceneByServerScNotify(
                reason=pb.EnterSceneReason.Value("ENTER_SCENE_REASON_NONE"),
                lineup=challenge_service.lineup_info(session),
                scene=scene,
            )
        )
        state = session.challenge
        rt.logger.info(
            "debug challenge: pushed arena for id=%d stage=%d", id, state.stage_id
        )
        return {
            "ok": True,
            "challenge": id,
            "stage": state.stage_id,
            "plane": scene.plane_id,
            "floor": scene.floor_id,
        }

    # ------------------------------------------------ SR Tools freesr bridge
    # 「连接设置 → 连接类型 RobinSR → 连接 PS」：浏览器 POST 到本机的 /srtools，
    # body = {"data": <freesr json>, "username": ..., "password": ...}（ppSR 约定）。
    # 收到后热更新 roster 并把新状态推给正在游戏的客户端，不重启服务端。
    @app.options("/srtools")
    async def srtools_options():
        return with_cors(Response(status_code=200))

    @app.get("/srtools")
    async def srtools_status():
        # 浏览器直接打开就能确认服务端在线；不写文件、不改数据。
        roster = rt.roster
        return with_cors(
            JSONResponse(
                {
                    "message": "OK",
                    "status": 200,
                    "avatars": len(roster.avatars),
                    "relics": len(roster.data.relics),
                    "lightcones": len(roster.data.lightcones),
                    "sessions": len(rt.sessions()),
                    "hot_reload": cfg.features.freesr_hot_reload,
                    "file": str(rt.freesr_path),
                    "export": "/srtools/export",
                }
            )
        )

    @app.get("/srtools/export")
    async def srtools_export():
        # 网页「加载数据」需要一份文件；导出服务端当前配置，避免用网页空状态反清空。
        path = rt.freesr_path
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            text = json.dumps(rt.roster.data.model_dump(), indent=4, ensure_ascii=False)
        return with_cors(
            Response(
                content=text,
                media_type="application/json",
                headers={
                    "Content-Disposition": 'attachment; filename="freesr-data.json"'
                },
            )
        )

    @app.post("/srtools")
    async def srtools(request: Request):
        body = await request.body()
        try:
            payload = json.loads(body) if body else None
        except ValueError as exc:
            return srtools_error(f"invalid JSON body: {exc}")
        if payload is None:
            return srtools_error("empty body")
        if not freesr_sync.has_document(payload):
            # 「连接 PS」握手：没有 data，只确认服务端在线（ppSR 也是 200）。
            rt.logger.info("srtools probe from %s", request.client.host if request.client else "?")
            return with_cors(
                JSONResponse(
                    {
                        "message": "OK",
                        "status": 200,
                        "probe": True,
                        "avatars": len(rt.roster.avatars),
                        "relics": len(rt.roster.data.relics),
                        "lightcones": len(rt.roster.data.lightcones),
                        "sessions": len(rt.sessions()),
                    }
                )
            )
        # ?force=1 / {"force": true}：允许把遗器/光锥清空（默认拒绝，见 _check_item_wipe）。
        force = truthy(request.query_params.get("force"))
        if isinstance(payload, dict) and truthy(payload.get("force")):
            force = True
        try:
            result = await freesr_sync.apply(rt, payload, source="srtools", force=force)
        except freesr_sync.FreesrPayloadError as exc:
            rt.logger.warning("srtools rejected: %s", exc)
            return srtools_error(str(exc))
        return with_cors(JSONResponse({"message": "OK", "status": 200, **result.as_dict()}))

    @app.post("/admin/freesr/reload")
    async def freesr_reload():
        # 手动覆盖 data/freesr-data.json 后，不想等文件监视也能立刻生效。
        try:
            result = await freesr_sync.reload_from_disk(rt, source="manual")
        except freesr_sync.FreesrPayloadError as exc:
            rt.logger.warning("freesr reload rejected: %s", exc)
            return srtools_error(str(exc))
        return with_cors(JSONResponse({"message": "OK", "status": 200, **result.as_dict()}))

    @app.get("/query_dispatch")
    async def query_dispatch():
        return b64_proto(build_dispatch(rt))

    @app.api_route("/query_gateway", methods=["GET", "HEAD"])
    async def query_gateway(request: Request):
        # The client probes with HEAD first; a 405 here aborts the whole login.
        if request.method == "HEAD" or "version" not in request.query_params:
            return Response(status_code=200)
        version = request.query_params["version"]
        if rt.profile and version != rt.profile.version:
            rt.logger.warning(
                "client asks for %s but active profile is %s (%s)",
                version,
                rt.profile.name,
                rt.profile.version,
            )
        if cfg.features.hotfix_auto and not hotfix.has(version):
            # AGENTS.md §10 唯一例外：本地缺条目时向官方网关取一次，成功即落盘。
            entry = await asyncio.to_thread(
                official.fetch_hotfix,
                version,
                dict(request.query_params),
                rt.profile.field_aliases if rt.profile else {},
                proxy=cfg.features.hotfix_proxy,
            )
            if entry is not None and hotfix.save(version, entry):
                rt.logger.info("hotfix cached for %s: %s", version, entry["asset_bundle_url"])
        return b64_proto(build_gateway(rt, hotfix, version))

    # ------------------------------------------- local CDN replay (4.7.0 CN)
    # hotfix 里的 LOCAL_CDN 条目指向这里：按文件名回放客户端自己安装里的资源，
    # 版本天然一致，不联网、不依赖社区表（docs/reference/05 §3）。
    served: set[str] = set()

    @app.api_route("/cdn/{rest:path}", methods=["GET", "HEAD"])
    async def cdn_replay(rest: str):
        if cdn is None:
            return PlainTextResponse("local CDN replay is disabled", status_code=404)
        target = cdn.lookup(rest)
        if target is None:
            rt.logger.warning("local CDN miss: %s", rest)
            return PlainTextResponse("not found", status_code=404)
        name = rest.replace("\\", "/").rsplit("/", 1)[-1]
        if name not in served:
            served.add(name)
            rt.logger.info("local CDN serve: %s", name)
        if isinstance(target, bytes):
            return Response(content=target, media_type="application/octet-stream")
        return FileResponse(target, media_type="application/octet-stream")

    # ------------------------------------------------------------- SDK JSON
    def account() -> dict:
        # 客户端只检查字段是否存在，不校验邮箱/地区/验证状态；"**" 和本地假邮箱
        # 是从参考实现沿用下来的，换版本时不必改。
        return {
            "area_code": "**",
            "email": "local@hsr.local",
            "country": "CN",
            "is_email_verify": "1",
            "token": "local-token",
            "uid": str(cfg.player.uid),
        }

    @app.post("/hkrpg_{region}/mdk/shield/api/{action}")
    @app.get("/hkrpg_{region}/mdk/shield/api/{action}")
    async def shield_api(region: str, action: str):
        # login / verify -> 账号信息；loadConfig 等 -> 客户端配置项。两者靠 action
        # 分流，不要按 region 分（两个 region 共用同一套）。
        if action in ("login", "verify"):
            data = {
                "account": account(),
                "device_grant_required": False,
                "reactivate_required": False,
                "realperson_required": False,
                "safe_mobile_required": False,
            }
        else:  # loadConfig and friends
            data = {
                "id": 3,
                "game_key": f"hkrpg_{region}",
                "client": "PC",
                "identity": "",
                "guest": False,
                "independent": False,
            }
        return JSONResponse({"data": data, "message": "OK", "retcode": 0})

    @app.get("/hkrpg_{region}/combo/granter/api/getConfig")
    async def granter_get_config(region: str):
        mode = cfg.experiments.popup_sdk_config
        if mode == "notfound":
            return Response(status_code=404)
        return JSONResponse({"data": build_get_config(mode), "message": "OK", "retcode": 0})

    @app.post("/hkrpg_{region}/combo/granter/api/compareProtocolVersion")
    async def compare_protocol_version(region: str):
        return JSONResponse(
            {
                "data": {"protocol_version": "", "protocol_version_new": False},
                "message": "OK",
                "retcode": 0,
            }
        )

    @app.post("/hkrpg_{region}/combo/granter/login/v2/login")
    async def granter_login(region: str):
        return JSONResponse(
            {
                "data": {
                    "account_type": 1,
                    "combo_id": str(cfg.player.uid),
                    "combo_token": "local-token",
                    "data": '{"guest":false}',
                    "heartbeat": False,
                    "open_id": str(cfg.player.uid),
                },
                "message": "OK",
                "retcode": 0,
            }
        )

    @app.post("/hkrpg_{region}/combo/red_dot/list")
    async def red_dot_list(region: str):
        return JSONResponse({"data": {}, "message": "OK", "retcode": 0})

    @app.post("/account/risky/api/check")
    async def risky_check():
        return JSONResponse(
            {
                "data": {"id": "local", "action": "ACTION_NONE", "geetest": None},
                "message": "OK",
                "retcode": 0,
            }
        )

    @app.post("/account/ma-cn-passport/app/loginByPassword")
    @app.post("/hkrpg_global/account/ma-passport/api/appLoginByPassword")
    async def login_by_password():
        # 密码登录：客户端不校验密码本身，只要 data.token.token 和 user_info 齐全。
        return JSONResponse(
            {
                "data": {
                    "bind_email_action_ticket": "",
                    "ext_user_info": {"birth": "0", "guardian_email": ""},
                    "reactivate_action_token": "",
                    "token": {"token": "local-token", "token_type": 1},
                    "user_info": {
                        "account_name": "hsr-local",
                        "aid": str(cfg.player.uid),
                        "mid": str(cfg.player.uid),
                        "token": "local-token",
                        "area_code": "**",
                        "country": "CN",
                        "is_email_verify": "1",
                        "is_adult": 1,
                        "email": "local@hsr.local",
                    },
                },
                "message": "OK",
                "retcode": 0,
            }
        )

    @app.post("/account/ma-cn-session/app/verify")
    async def verify_token():
        return JSONResponse(
            {
                "data": {
                    "tokens": [{"token": "local-token", "token_type": 1}],
                    "token": "local-token",
                    "uid": str(cfg.player.uid),
                    "account_type": 1,
                    "user_info": {
                        "aid": str(cfg.player.uid),
                        "mid": str(cfg.player.uid),
                        "is_email_verify": 1,
                        "area_code": "**",
                        "country": "CN",
                        "is_adult": 1,
                        "email": "local@hsr.local",
                    },
                },
                "message": "OK",
                "retcode": 0,
            }
        )

    @app.post("/account/ma-cn-session/app/getTokenByGameToken")
    async def get_token_by_game_token():
        return JSONResponse(
            {
                "data": {
                    "uid": str(cfg.player.uid),
                    "token": {"token": "local-token", "token_type": 1},
                    "game_token": "local-token",
                    "account_type": 1,
                },
                "message": "OK",
                "retcode": 0,
            }
        )

    @app.post("/account/ma-cn-session/app/exchange")
    async def exchange():
        # 4.6.54 在 query_gateway 之后调这个；404 会导致拿不到 session_key，
        # 在连游戏服之前就中断（docs/design/01 §2.2 关键点 4）。
        return JSONResponse(
            {
                "data": {
                    "uid": str(cfg.player.uid),
                    "token": "local-token",
                    "session_key": "local-token",
                    "game_token": "local-token",
                    "account_type": 1,
                    "tokens": [{"token": "local-token", "token_type": 1}],
                },
                "message": "OK",
                "retcode": 0,
            }
        )

    return app


async def serve(rt: Runtime) -> None:
    app = create_app(rt)
    cfg = uvicorn.Config(
        app,
        host=rt.cfg.server.listen,
        port=rt.cfg.server.http_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(cfg)
    rt.logger.info(
        "dispatch listening on http://%s:%d", rt.cfg.server.listen, rt.cfg.server.http_port
    )
    await server.serve()
