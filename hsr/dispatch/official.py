"""缺 hotfix 条目时向官方网关取一次。

移植自 sr-recover-new ``src/dispatch/dispatchserver.py::check_hotfix``：按版本串
前缀选官方域名，把客户端**自己**的 ``query_gateway`` 查询串原样转发过去，解析
返回的 ``GateServer``，取 7 个 CDN 字段缓存进 ``data/hotfix.json``。

本模块负责执行这些约束：

* 只 ``GET /query_gateway``，只发客户端上报过的参数，不带账号凭据、不伪造 uid；
* 直连并绕过本机代理：Clash TUN 会把域名解析成 fake-ip，走代理必失败；
* 单次超时 5 s、不重试、不后台轮询（调用方只在本地缺条目时调用一次）；
* 每次尝试都留日志：目标 host / 结果 / 是否落盘。
"""

from __future__ import annotations

import base64
import urllib.parse
import urllib.request

from hsr.log import get_logger
from hsr.protocol.gen import StarRail_pb2 as pb

log = get_logger("hsr.official")

# 顺序有意义：先匹配更具体的 CNPROD/CNBETA，再匹配 OSPROD/OSBETA。
HOSTS: tuple[tuple[str, str], ...] = (
    ("CNPROD", "prod-gf-cn-dp01.bhsr.com"),
    ("CNBETA", "beta-release01-cn.bhsr.com"),
    ("OSPROD", "prod-official-eur-dp01.starrails.com"),
    ("OSBETA", "beta-release01-asia.starrails.com"),
)

# 只允许复述这些 key；其余（尤其 account_*/token）一律丢弃。
QUERY_KEYS: tuple[str, ...] = (
    "version",
    "dispatch_seed",
    "channel_id",
    "sub_channel_id",
    "platform_type",
    "language_type",
    "is_need_url",
    "account_type",
    "region",
    "t",
)

TIMEOUT = 5.0

# 与 versions/*.toml 的 [field_aliases] 同义：写入 hotfix.json 时用语义名。
CDN_FIELDS: tuple[str, ...] = (
    "asset_bundle_url",
    "asset_bundle_url_b",
    "ex_resource_url",
    "lua_url",
    "ifix_url",
    "lua_version",
    "ifix_version",
)


def host_for(version: str) -> str | None:
    for prefix, host in HOSTS:
        if prefix in version:
            return host
    return None


def _gateway_url(version: str, params: dict[str, str], host: str) -> str:
    query = {
        key: str(params[key])
        for key in QUERY_KEYS
        if key in params and str(params[key]).strip()
    }
    return f"https://{host}/query_gateway?{urllib.parse.urlencode(query, safe='')}"


def _read_field(msg, semantic: str, aliases: dict[str, str]) -> str:
    for name in (aliases.get(semantic), semantic):
        if name and hasattr(msg, name):
            try:
                return str(getattr(msg, name))
            except (TypeError, ValueError):
                continue
    return ""


def _decode(body: bytes) -> pb.GateServer | None:
    text = body.strip()
    if not text:
        return None
    try:
        raw = base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        raw = text
    msg = pb.GateServer()
    try:
        msg.ParseFromString(raw)
    except Exception:  # noqa: BLE001 - 官方返回形状变了也不该崩登录
        return None
    return msg


def fetch_hotfix(
    version: str,
    params: dict[str, str],
    aliases: dict[str, str] | None = None,
    *,
    timeout: float = TIMEOUT,
    proxy: str = "",
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, str] | None:
    """取一次 hotfix；失败返回 ``None``（调用方静默回退本地表）。

    ``proxy`` 只在直连必然失败时才由配置打开；``opener`` 只给测试注入假网关用。
    """
    aliases = aliases or {}
    host = host_for(version)
    if host is None:
        log.warning("hotfix fetch: no official host for version %s", version)
        return None
    if not str(params.get("dispatch_seed", "")).strip():
        # 官方网关对空 seed 有概率回空包；参考实现都要求非空，这里直接放弃。
        log.warning("hotfix fetch: %s has no dispatch_seed, skip", version)
        return None

    url = _gateway_url(version, params, host)
    if opener is None:
        handler = urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy} if proxy else {}
        )
        # ProxyHandler({}) 覆盖环境变量里的 http(s)_proxy，等价 sr-recover 的 proxies=None。
        opener = urllib.request.build_opener(handler)
    try:
        with opener.open(url, timeout=timeout) as response:
            body = response.read()
        msg = _decode(body)
        if msg is None:
            log.warning("hotfix fetch %s: could not parse GateServer", host)
            return None
        entry = {name: _read_field(msg, name, aliases) for name in CDN_FIELDS}
        if not entry["asset_bundle_url"]:
            log.warning("hotfix fetch %s: empty asset_bundle_url", host)
            return None
        log.info(
            "hotfix fetch ok: version=%s host=%s asset_bundle_url=%s",
            version,
            host,
            entry["asset_bundle_url"],
        )
        return entry
    except Exception as exc:  # noqa: BLE001 - 官方不可达时不能拖垮登录
        log.warning("hotfix fetch %s failed: %s", host, exc)
        return None
