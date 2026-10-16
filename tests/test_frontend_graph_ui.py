# -*- coding: utf-8 -*-
"""知识图谱（A 档纯视图）前端内容契约。

守卫六类事实：
  1. 弹层结构完整（modal/标题/统计条/画布），无重复 DOM id；
  2. 标准卡片有 🕸 入口且真实调用 v2OpenGraph；
  3. 前后端端点契约一致（/graph 摘要 + /graph/nodes/{id} 懒加载）；
  4. 折叠契约：摘要渲染只吃 field_count/item_count 徽章，明细只在展开
     后经 details 缓存使用 —— 防止有人"顺手"把明细塞进摘要载荷；
  5. 零新依赖：不引 Cytoscape/ECharts/D3（项目规矩：不依赖 CDN，全部内联），
     SVG 手写渲染且用 CSS 变量保深色主题；
  6. 孤儿 DOM id / 未定义 CSS 变量守卫（getElementById 静默 null、
     未声明变量静默回落继承色，肉眼极难发现）。
"""

import re
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_HTML = _ROOT / "app" / "frontend" / "index.html"
_JS = _ROOT / "app" / "frontend" / "app.js"
_GRAPH_API = _ROOT / "app" / "api" / "v1" / "data_standard_v2" / "graph.py"


@pytest.fixture(scope="module")
def html() -> str:
    return _HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return _JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def graph_modal(html: str) -> str:
    start = html.index('<div id="v2GraphModal"')
    nxt = html.find("<!-- ═══", start + 10)
    return html[start: nxt if nxt != -1 else len(html)]


@pytest.fixture(scope="module")
def graph_js(js: str) -> str:
    start = js.index("const V2G =")
    end = js.index("// ── Bootstrap", start)
    return js[start:end]


# ══════════════════════════════════════════════════════════════════════
# 1. 弹层结构
# ══════════════════════════════════════════════════════════════════════

class TestGraphModalStructure:
    def test_modal_ids_exist(self, graph_modal: str):
        for eid in ("v2GraphModal", "v2GraphTitle", "v2GraphStats",
                    "v2GraphHint", "v2GraphBody"):
            assert f'id="{eid}"' in graph_modal, f"缺少弹层锚点: {eid}"

    def test_no_duplicate_ids_in_html(self, html: str):
        ids = re.findall(r'id="([^"]+)"', html)
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert not dupes, f"重复 DOM id（getElementById 只会命中第一个）: {dupes}"

    def test_close_button_wired(self, graph_modal: str):
        assert 'onclick="v2CloseGraph()"' in graph_modal


# ══════════════════════════════════════════════════════════════════════
# 2. 入口按钮
# ══════════════════════════════════════════════════════════════════════

class TestGraphEntryButton:
    def test_std_card_has_graph_button(self, js: str):
        assert "v2OpenGraph('" in js, "标准卡片缺少图谱入口"

    def test_open_functions_defined(self, graph_js: str):
        for fn in ("function v2OpenGraph(", "function v2CloseGraph(",
                   "function v2ToggleGraphNode(", "function v2RenderGraph("):
            assert fn in graph_js, f"缺少函数: {fn}"


# ══════════════════════════════════════════════════════════════════════
# 3. 前后端端点契约
# ══════════════════════════════════════════════════════════════════════

class TestEndpointContract:
    def test_frontend_calls_summary_endpoint(self, graph_js: str):
        assert "/graph'" in graph_js or "'/graph'" in graph_js or \
            "+ '/graph'" in graph_js or "$'{stdId}/graph'" in graph_js, \
            "前端未调用摘要端点 GET /{id}/graph"

    def test_frontend_calls_detail_endpoint(self, graph_js: str):
        assert "/graph/nodes/" in graph_js, "前端未调用明细端点（懒加载缺失）"

    def test_backend_routes_match(self):
        src = _GRAPH_API.read_text(encoding="utf-8")
        assert '"/data-standards-v2/{standard_id}/graph"' in src
        assert '"/data-standards-v2/{standard_id}/graph/nodes/{node_id}"' in src

    def test_router_registered(self):
        pkg = (_ROOT / "app/api/v1/data_standard_v2/__init__.py").read_text(encoding="utf-8")
        assert "from .graph import router as graph_router" in pkg
        assert "router.include_router(graph_router" in pkg


# ══════════════════════════════════════════════════════════════════════
# 4. 折叠契约（真实载荷约束）
# ══════════════════════════════════════════════════════════════════════

class TestCollapseContract:
    """SHGT_SURVEY 有 3288 字段 + 2031 条目 —— 摘要带明细即 ~1.5MB。"""

    def test_summary_renders_badges_not_details(self, graph_js: str):
        assert "field_count" in graph_js, "表节点必须用 field_count 徽章"
        assert "item_count" in graph_js, "字典节点必须用 item_count 徽章"

    def test_details_cached_per_node(self, graph_js: str):
        assert "V2G.details[nodeId]" in graph_js, "明细须缓存（重复展开不重拉）"
        assert "V2G.expanded[nodeId]" in graph_js, "展开状态须可切换（收起）"

    def test_backend_summary_model_has_no_detail_arrays(self):
        """响应模型层面同样不得有 fields/items —— 双保险。

        只查字段声明（`fields:` / `items:`），不查子串 —— 模型 docstring
        里"节点不携带明细数组（fields / items）"这类说明文字是合法的。
        """
        schemas = (_ROOT / "app/schemas/data_standard_v2.py").read_text(encoding="utf-8")
        node_cls = schemas[schemas.index("class GraphNode(BaseModel):"):
                           schemas.index("class GraphEdge(BaseModel):")]
        assert not re.search(r"^\s+fields\s*:", node_cls, re.M), \
            "GraphNode 不得声明 fields 明细字段"
        assert not re.search(r"^\s+items\s*:", node_cls, re.M), \
            "GraphNode 不得声明 items 明细字段"


# ══════════════════════════════════════════════════════════════════════
# 5. 零新依赖 + 深色主题
# ══════════════════════════════════════════════════════════════════════

class TestNoNewDependencies:
    def test_no_graph_libraries(self, js: str):
        for lib in ("cytoscape", "echarts", "d3.select", "vis-network", "@antv/g6"):
            assert lib.lower() not in js.lower(), f"不得引入图可视化库: {lib}"

    def test_no_new_script_tags(self, html: str):
        """项目规矩：不依赖 CDN —— index.html 只允许本地 app.js 一个 script。"""
        scripts = re.findall(r'<script[^>]*src="([^"]+)"', html)
        assert scripts == ["/app/vendor/leaflet/leaflet.css".replace("/app/vendor/leaflet/leaflet.css", "app.js")] or \
            all(not s.startswith(("http://", "https://", "//")) for s in scripts), \
            f"发现外部脚本依赖: {scripts}"

    def test_svg_rendering_inline(self, graph_js: str):
        assert "<svg" in graph_js, "图谱必须手写 SVG 内联渲染"
        assert "createElementNS" not in graph_js or "innerHTML" in graph_js

    def test_uses_css_vars_dark_theme(self, graph_js: str):
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", graph_js))
        assert used, "SVG 必须用 CSS 变量跟随深色主题"
        assert "--accent" in used, "绑定边须用主题 accent 色"


# ══════════════════════════════════════════════════════════════════════
# 6. 孤儿 id / CSS 变量守卫
# ══════════════════════════════════════════════════════════════════════

class TestNoOrphanReferences:
    def test_every_graph_id_exists(self, graph_js: str, html: str):
        referenced = set(re.findall(r"getElementById\('([^']+)'\)", graph_js))
        assert referenced, "未解析到 DOM 引用，守卫本身失效"
        missing = sorted(i for i in referenced if f'id="{i}"' not in html)
        assert not missing, f"JS 引用了不存在的 DOM id: {missing}"

    def test_no_undefined_css_var(self, graph_js: str, graph_modal: str):
        head = _HTML.read_text(encoding="utf-8")[:6000]
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", head))
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", graph_js + graph_modal))
        unknown = sorted(used - declared)
        assert not unknown, f"引用了未定义的 CSS 变量（静默失效）: {unknown}"

    def test_node_syntax_ok(self):
        import subprocess
        p = subprocess.run(["node", "--check", str(_JS)],
                           capture_output=True, text=True)
        assert p.returncode == 0, f"app.js 语法错误:\n{p.stderr}"
