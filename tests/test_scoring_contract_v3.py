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
