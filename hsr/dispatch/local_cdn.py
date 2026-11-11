"""按文件名回放客户端自己的资源清单（docs/reference/05 §3）。

4.7.0 CN 客户端在 ``query_gateway`` 之后、连游戏服之前，会拿 hotfix 里的 CDN
地址去校验安装时那份资源清单：地址对应不上就重新下载（写进 ``Persistent``），
地址为空则直接「资源下载失败」。社区表没有新版本条目时，最省事的过法是让
hotfix 指向本机，再把客户端**自己安装里的**文件按文件名回给它——版本天然一致，
不需要托管几十 GB 资源，也不碰官方 CDN。

两个 4.7.0 实测踩过的点：

* ``StreamingAssets/DesignData/Windows/M_Design_ArchiveV.bytes`` 指向安装期
  stub ``DesignV_9482…``（1.3 KB，缺中文 textmap），客户端读它会先
  ``TextmapExcelTable`` NRE 再 ``DesignDataBundleIndexInfo`` 报错；真正可用的是
  ``Persistent/DesignData/Windows`` 里那份 2.3 MB 的 ``DesignV_2de2…``。
  所以这一个清单在运行时改写 ContentHash/FileSize，指向实际安装的包。
* 客户端会自己往资源目录写 ``*_cache.bytes``；本模块只读，不创建也不改文件。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from hsr.log import get_logger

log = get_logger("hsr.local_cdn")

# 清单是「一行一个 JSON 对象」，客户端自己 StreamingAssets 里的文件就是 CRLF 分隔。
# 不要用 ``..`` 连接：4.7.0 的版本更新模块用严格 JSON 解析整份清单，尾随的 ``..``
# 会报 "Additional text encountered after finished reading JSON content"，
# 于是判定设计档案需要重下，弹出 27.9 GB 的「任务资源管理」（2026-11-11 真机日志）。
_SEPARATOR = "\r\n"
_DESIGN_MANIFEST = "M_Design_ArchiveV.bytes"
_DESIGN_BUNDLE = re.compile(r"^DesignV_([0-9a-f]{32})\.bytes$")
# stub 只有 1.3 KB；任何真实设计包都远大于此。
_MIN_DESIGN_BYTES = 64 * 1024


class LocalCdn:
    """按文件名索引 ``StarRail_Data``，并合成设计清单。"""

    def __init__(self, client_dir: str | Path):
        self.client_dir = Path(client_dir)
        self.data_dir = self.client_dir / "StarRail_Data"
        self._roots = (
            self.data_dir / "Persistent",
            self.data_dir / "StreamingAssets",
        )
        self._index: dict[str, Path] | None = None

    def available(self) -> bool:
        return self.data_dir.is_dir()

    def lookup(self, request_path: str) -> Path | bytes | None:
        """把 ``/cdn/...`` 的剩余路径换成文件，未知文件返回 ``None``。

        只按 basename 查索引，不拼接客户端给的路径，天然挡掉目录穿越。
        """
        name = request_path.replace("\\", "/").rsplit("/", 1)[-1]
        if not name or name in {".", ".."}:
            return None
        if name == _DESIGN_MANIFEST:
            manifest = self._design_manifest()
            if manifest is not None:
                return manifest
        return self.index.get(name)

    # ------------------------------------------------------------------ index
    @property
    def index(self) -> dict[str, Path]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def _build_index(self) -> dict[str, Path]:
        index: dict[str, Path] = {}
        for root in self._roots:
            if not root.is_dir():
                continue
            # 不只 ``*.bytes``：客户端也可能按名字取别的后缀（.block 之类），
            # 索引全量文件最省心，数量级只有几千。
            for path in root.rglob("*"):
                if path.is_file():
                    # Persistent 优先：StreamingAssets 里常有同名的安装期 stub。
                    index.setdefault(path.name, path)
        log.info(
            "local CDN index: %d files under %s",
            len(index),
            self.data_dir,
        )
        return index

    # ---------------------------------------------------------- design bundle
    def real_design_bundle(self) -> Path | None:
        """``Persistent`` 里最大的 ``DesignV_*.bytes``（安装后的真实设计包）。"""
        best: Path | None = None
        for root in self._roots:
            folder = root / "DesignData" / "Windows"
            if not folder.is_dir():
                continue
            for path in folder.glob("DesignV_*.bytes"):
                if not _DESIGN_BUNDLE.match(path.name):
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size < _MIN_DESIGN_BYTES:
                    continue
                if best is None or size > best.stat().st_size:
                    best = path
        return best

    def _design_manifest(self) -> bytes | None:
        source = (
            self.data_dir
            / "StreamingAssets"
            / "DesignData"
            / "Windows"
            / _DESIGN_MANIFEST
        )
        if not source.is_file():
            return None
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("could not read %s: %s", source, exc)
            return None
        entries = [
            json.loads(match.group(0)) for match in re.finditer(r"\{[^{}]*\}", text)
        ]
        real = self.real_design_bundle()
        if real is None:
            log.warning("no installed DesignV_*.bytes under %s", self.data_dir)
            return None
        match = _DESIGN_BUNDLE.match(real.name)
        if match is None or not entries:
            return None
        patched = False
        for entry in entries:
            if entry.get("FileName") != "M_DesignV":
                continue
            entry["ContentHash"] = match.group(1)
            entry["FileSize"] = real.stat().st_size
            patched = True
        if not patched:
            return None
        log.info(
            "design manifest: M_DesignV -> %s (%d bytes)",
            real.name,
            real.stat().st_size,
        )
        body = _SEPARATOR.join(
            json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            for entry in entries
        )
        return (body + _SEPARATOR).encode("utf-8")
