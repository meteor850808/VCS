"""docs 索引文档的链接与覆盖守卫（不需要数据库）。

背景（2026-11-12）：`docs/README.md` 曾长期漏掉最新 revision 与 ADR-0004–0006；
`docs/docker/README.md`、`docs/deploy/README.md` 的路径少了一层 `../`（这两个文件原本
按「住在 `docker/`、`deploy/` 目录里」写，被放进 `docs/` 下后未改链接）。两类问题都不会
被其它测试发现，只能靠人工点开链接。本文件把它们变成红灯。

修好后的相对路径在 monorepo（`空间质检/docs/...`）与 root（`docs/...`）两种布局下**完全相同**，
因为相对链接只取决于两个文件之间的关系，与仓库根位置无关——本守卫因此在两种布局下都成立。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

INDEX_FILES = [
    DOCS / "README.md",
    DOCS / "docker" / "README.md",
    DOCS / "deploy" / "README.md",
]

# [文本](目标) —— markdown 行内链接
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# `../xxx` / `../../xxx` —— 反引号里以父目录起头的路径（表格里用文本而非链接描述的路径）
_BACKTICK_REL = re.compile(r"`(\.\.?/[^`\s]+)`")
_SKIP_PREFIX = ("http://", "https://", "mailto:", "#", "tel:")


def _relative_links(path: Path) -> list[str]:
    out = []
    for raw in _LINK.findall(path.read_text(encoding="utf-8")):
        if raw.startswith(_SKIP_PREFIX):
            continue
        target = raw.split("#", 1)[0]   # 去掉锚点
        if target:
            out.append(target)
    return out


def _backticked_relative_paths(path: Path) -> list[str]:
    out = []
    for raw in _BACKTICK_REL.findall(path.read_text(encoding="utf-8")):
        target = raw.split("#", 1)[0]
        if target:
            out.append(target)
    return out


def test_index_files_exist():
    """三个索引文件必须存在——它们只在 monorepo 侧写过一次，root 侧曾是缺口。"""
    missing = [str(p.relative_to(ROOT)) for p in INDEX_FILES if not p.exists()]
    assert not missing, f"缺少 docs 索引文件: {missing}"


def test_docs_index_links_resolve():
    """索引里的每个 markdown 相对链接都必须指向真实存在的文件或目录。"""
    broken = []
    for path in INDEX_FILES:
        for target in _relative_links(path):
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "docs 索引存在失效链接:\n" + "\n".join(broken)


def test_docs_index_backticked_paths_resolve():
    """表格里用反引号描述的相对路径也必须可解析。

    本次真正的 bug 就在这里：`docs/docker/README.md` 的 `` `../Dockerfile` `` 与
    `docs/deploy/README.md` 的 `` `../deploy_windows.bat` `` 都少一层 `../`，
    但它们不是 markdown 链接，`test_docs_index_links_resolve` 抓不到。
    """
    broken = []
    for path in INDEX_FILES:
        for target in _backticked_relative_paths(path):
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "docs 索引存在失效的反引号路径:\n" + "\n".join(broken)


def test_docs_index_covers_all_adrs():
    """每个 ADR 文件都必须在 docs/README.md 的 ADR 表里出现（防再次漏条目）。"""
    adr_dir = DOCS / "adr"
    if not adr_dir.is_dir():
        return
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    missing = sorted(p.name for p in adr_dir.glob("ADR-*.md") if p.name not in text)
    assert not missing, f"docs/README.md 未索引这些 ADR: {missing}"


def test_docs_index_lists_current_version_revision():
    """当前版本的发版说明必须被 docs/README.md 索引。

    版本号从 app/main.py 读取，避免每次发版改测试；根因是 v2.25.0 发版后索引一直
    停在 v2.24.0。
    """
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'version="(\d+\.\d+\.\d+)"', main_py)
    assert match, 'app/main.py 未找到 version="X.Y.Z" 字段'
    version = match.group(1)
    note = f"revision_v{version.replace('.', '_')}.md"
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    assert note in text, f"docs/README.md 未索引当前版本发版说明 {note}"
