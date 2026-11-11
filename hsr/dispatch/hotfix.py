"""Per-build hotfix CDN addresses.

The client refuses to continue if the addresses do not match its build ("resource
download failed" before the game connection). We keep a local table and fall back to
the closest cached build, then to the 4.6.53 CN constants.

A table entry may use the ``LOCAL_CDN`` prefix instead of a real CDN host (4.7.0 CN
has no public entry yet): ``build_gateway`` swaps it for this server's ``/cdn``
route, which replays the client's own installed files (``docs/reference/05`` §3).
"""

from __future__ import annotations

import json
from pathlib import Path

from hsr.log import get_logger

log = get_logger("hsr.hotfix")

# 占位前缀：``LOCAL_CDN/asb/Live/output_<...>``。只有 features.local_cdn 打开时
# 才会被换成 http://<listen>:<http_port>/cdn，否则原样保留（客户端会报资源失败）。
LOCAL_CDN_PREFIX = "LOCAL_CDN"

URL_KEYS = (
    "asset_bundle_url",
    "asset_bundle_url_b",
    "ex_resource_url",
    "lua_url",
    "ifix_url",
)

FALLBACK = {
    "asset_bundle_url": "https://autopatchcn.bhsr.com/asb/BetaLive/output_16818953_d4345bbd54a2_93c4aa5dfb978d",
    "asset_bundle_url_b": "https://autopatchcn.bhsr.com/asb/BetaLive/output_16818953_d4345bbd54a2_93c4aa5dfb978d",
    "ex_resource_url": "https://autopatchcn.bhsr.com/design_data/BetaLive/output_16833202_959c316ef547_ac20bef3513343",
    "lua_url": "https://autopatchcn.bhsr.com/lua/BetaLive/output_16819203_679727d45bd7_91ccc9b73f52b7",
    "ifix_url": "https://autopatchcn.bhsr.com/ifix/BetaLive/output_0_40d2ce0253_c61ba99f70b885",
    "lua_version": "16819203",
    "ifix_version": "0",
}


def _prefix(version: str) -> str:
    return version.rstrip("0123456789.")


def is_local(entry: dict[str, str]) -> bool:
    """该条目是否指向本地回放（而不是真实 CDN）。"""
    return any(str(entry.get(key, "")).startswith(LOCAL_CDN_PREFIX) for key in URL_KEYS)


def localize(entry: dict[str, str], base: str) -> dict[str, str]:
    """把 ``LOCAL_CDN/...`` 换成 ``<base>/...``，其它字段原样保留。"""
    out = dict(entry)
    for key in URL_KEYS:
        value = str(out.get(key, ""))
        if value.startswith(LOCAL_CDN_PREFIX):
            out[key] = base + value[len(LOCAL_CDN_PREFIX) :]
    return out


class HotfixStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = raw
        except (OSError, ValueError) as exc:
            log.warning("could not read %s: %s", self.path, exc)

    def has(self, version: str) -> bool:
        return version in self.data

    def save(self, version: str, entry: dict[str, str]) -> bool:
        """缓存一条 hotfix（官方网关取到的结果）；失败只告警，不影响本次登录。"""
        self.data[version] = dict(entry)
        try:
            text = json.dumps(self.data, indent=4, ensure_ascii=False) + "\n"
            self.path.write_text(text, encoding="utf-8")
            return True
        except OSError as exc:
            log.warning("could not write %s: %s", self.path, exc)
            return False

    def for_version(self, version: str) -> dict[str, str]:
        if version in self.data:
            return self._normalize(self.data[version])

        prefix = _prefix(version)
        same = [key for key in self.data if _prefix(key) == prefix]
        if same:
            chosen = max(same)
            log.warning("no hotfix for %s, using closest cached %s", version, chosen)
            return self._normalize(self.data[chosen])

        log.error("no hotfix for %s, falling back to 4.6.53 CN addresses", version)
        return dict(FALLBACK)

    @staticmethod
    def _normalize(entry: dict[str, str]) -> dict[str, str]:
        out = dict(FALLBACK)
        out.update({k: v for k, v in entry.items() if isinstance(v, str)})
        # older tables use different key names
        out["asset_bundle_url_b"] = out.get("asset_bundle_url_b") or out.get("asset_bundle_url", "")
        out["lua_version"] = out.get("lua_version") or out.get("mdk_res_version", "")
        return out
