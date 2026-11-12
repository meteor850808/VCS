"""评分口径 v3 的源码契约（不需要数据库）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_docs_call_fifth_dimension_topology():
    """第五维在文档中必须叫「拓扑」，不得残留「及时性」。"""
    assert "及时性" not in _src("README.md")
    assert "及时性" not in _src("BASELINE.md")
    assert "拓扑(0.15)" in _src("README.md")


# ── P1：前端/HTML 渲染口径收敛 ────────────────────────────────────────────────

def test_frontend_has_single_score_renderer():
    src = _src("app/frontend/app.js")
    assert "function scoreView(" in src
    assert "function dimCard(" in src
    assert src.count("scoreView(") >= 4      # 定义处 + 至少三处调用
    assert "未评价" in src


def test_frontend_has_no_threshold_literals():
    src = _src("app/frontend/app.js")
    for lit in (">=.85", ">=.6", ">=85", ">=60"):
        assert lit not in src, f"前端残留阈值字面量: {lit}"


def test_frontend_has_no_completeness_fallback():
    src = _src("app/frontend/app.js")
    assert "d.completeness_score" not in src
    assert "r.completeness_score" not in src


def test_backend_html_has_no_threshold_literals():
    src = _src("app/api/v1/quality.py")
    assert ">= 85" not in src and ">= 60" not in src


# ── P4：发版说明与版本号 ──────────────────────────────────────────────────────

def test_release_note_exists_and_states_incompatibility():
    note = _src("docs/revision_v2_25_0.md")
    assert "v3" in note and "v2" in note
    assert "不可" in note          # 不可直接比较
    assert "T06" in note           # 诊断项变化必须写明
    assert "5000" in note          # T11 截断口径必须写明


def _app_version() -> str:
    """从 app/main.py 的 FastAPI version 字段读取当前版本号。"""
    match = re.search(r'version="(\d+\.\d+\.\d+)"', _src("app/main.py"))
    assert match, "app/main.py 未找到 version=\"X.Y.Z\" 字段"
    return match.group(1)


def test_context_version_bumped():
    """CONTEXT.md 的版本号必须与 app/main.py 一致（发版时同步）。"""
    assert f"v{_app_version()}" in _src("CONTEXT.md")


def test_release_positions_are_consistent():
    """CLAUDE.md §4 版本发布清单：6 个位点 + revision 文档必须同版本。

    漏同步任何一个位点这里就红，不再靠人工核对——v2.25.0 发版时正是漏了
    BASELINE §15 / app/main.py / 部署脚本且无人发现。
    """
    version = _app_version()
    baseline = _src("BASELINE.md")
    assert f"**app/main.py**: {version}" in baseline, "BASELINE §15 未同步 app/main.py 版本"
    assert f"**CONTEXT.md**: v{version}" in baseline, "BASELINE §15 未同步 CONTEXT.md 版本"
    assert f"VCS v{version} One-Click Deploy (Linux)" in _src("deploy_linux.sh"), (
        "deploy_linux.sh 标题版本号未同步"
    )
    assert f"title VCS v{version} Restart" in _src("restart.bat"), (
        "restart.bat 标题版本号未同步"
    )
    note = f"docs/revision_v{version.replace('.', '_')}.md"
    assert (ROOT / note).exists(), f"缺少修订记录 {note}"