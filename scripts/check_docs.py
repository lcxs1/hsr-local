"""文档自检：链接、旧路径、编号、元信息。

用法::

    python scripts/check_docs.py            # 检查
    python scripts/check_docs.py --verbose  # 打印每个通过的文件

检查项：

1. 相对 markdown 链接的目标存在（外链/纯锚点跳过）；
2. 不出现迁移前的旧路径和已弃用项目名；
3. 同一目录内文档编号不重复；
4. 非归档文档在开头 20 行内有「最后修订」，归档文档有「已归档」。

退出码：0 = 通过；1 = 有 error（warning 不阻断）。
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
NUMBER_RE = re.compile(r"^(\d{2})-")

# 迁移前的位置/已弃用项目；出现在现行文档里会误导读者。
BANNED = {
    "E:\\LCXS\\hsr-local": "项目已移到 E:\\LCXS\\run\\hsr-local",
    "E:\\LCXS\\docs": "文档已并入项目 docs/",
    "run/hsr-local/docs": "文档链接应相对当前文件",
    "..\\docs\\": "文档链接应相对当前文件",
    "hsr-server": "该参考工程已删除弃用，不再作为参考",
}


def md_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    files += sorted(DOCS.rglob("*.md"))
    unique = {p.resolve(): p for p in files if p.exists()}
    return [unique[key] for key in sorted(unique, key=lambda p: str(p))]


def link_target(raw: str) -> str | None:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if not target or target.startswith("#"):
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return None  # http:, https:, mailto: ...
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return None
    return urllib.parse.unquote(target)


def check_links(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for match in LINK_RE.finditer(text):
        target = link_target(match.group(1))
        if target is None:
            continue
        if re.match(r"^[A-Za-z]:[\\/]", target):
            errors.append(f"{rel(path)}: 链接不要用绝对路径 -> {match.group(1)}")
            continue
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            errors.append(f"{rel(path)}: 链接目标不存在 -> {match.group(1)}")
    return errors


def check_text(path: Path) -> list[str]:
    errors: list[str] = []
    # 归档文档保留历史原文；索引页要举例说明旧路径。
    if path.parent == ARCHIVE or path == DOCS / "README.md":
        return []
    text = path.read_text(encoding="utf-8")
    for bad, hint in BANNED.items():
        if bad in text:
            errors.append(f"{rel(path)}: 出现旧路径/弃用名 `{bad}`（{hint}）")
    return errors


def check_meta(path: Path) -> list[str]:
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:20])
    if path.parent == ARCHIVE:
        return [] if "已归档" in head else [f"{rel(path)}: 归档文档缺少「已归档」说明"]
    if path.name == "README.md" or DOCS / "data" in path.parents:
        return []
    return [] if "最后修订" in head else [f"{rel(path)}: 开头缺少「最后修订」元信息"]


def check_numbers() -> list[str]:
    seen: dict[tuple[Path, str], Path] = {}
    errors: list[str] = []
    for path in sorted(DOCS.rglob("*.md")):
        match = NUMBER_RE.match(path.name)
        if not match:
            continue
        key = (path.parent, match.group(1))
        if key in seen:
            errors.append(
                f"编号重复 {match.group(1)}: "
                f"{rel(seen[key])} / {rel(path)}"
            )
        seen[key] = path
    return errors


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description="check docs links and metadata")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    files = md_files()
    checks = {
        "links": check_links,
        "path": check_text,
        "meta": check_meta,
    }
    for path in files:
        file_errors: list[str] = []
        for check in checks.values():
            file_errors += check(path)
        if args.verbose and not file_errors:
            print(f"ok  {rel(path)}")
        errors += file_errors

    errors += check_numbers()

    if errors:
        print(f"check_docs: {len(errors)} problem(s) in {len(files)} file(s)")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"check_docs: ok ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
