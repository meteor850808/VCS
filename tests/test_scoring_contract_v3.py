"""评分口径 v3 的源码契约（不需要数据库）。"""
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
