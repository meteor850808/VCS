"""v2.22.2 前端契约：数据标准「导入 / 导出 / 模板」合并为单框三段 + 唯一格式选择器。

背景（见 docs/revision_v2_22_2.md）：原弹窗是 3 主 Tab（导入|导出|模板）+ 导入下
3 子 Tab（JSON|Excel|CSV）= 6 个切换按钮、2 个 dropzone，纵向空间浪费；且
`mode=create/update/upsert` 后端早已实现并有测试覆盖，前端却从不下发（永远 create），
导致 update 路径不可达。

守卫六类事实：
  1. 旧双层 Tab 脚手架彻底移除（切换按钮 6 → 3）；
  2. 导入/导出/模板三段同框并列，不再互斥 display 切换；
  3. 唯一格式选择器 v2IeFmt 驱动 accept / multiple / 文案 / 导入端点；
  4. 导入 mode 选择器暴露并真实下发（JSON 走 Query，其余走 Form）；
  5. 两个 dropzone 合并为一个 file input，且无重复 DOM id（getElementById 语义）；
  6. 导出 CSV 的历史 400 缺陷已由后端 v2.22.3 补齐，UI 解封并明示 ZIP/无字典取舍。
"""

from pathlib import Path
import re
import shutil

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_HTML = _ROOT / "app" / "frontend" / "index.html"
_JS = _ROOT / "app" / "frontend" / "app.js"
_IMPEXP = _ROOT / "app" / "api" / "v1" / "data_standard_v2" / "import_export.py"


@pytest.fixture(scope="module")
def html() -> str:
    return _HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return _JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def modal(html: str) -> str:
    """截取导入/导出弹窗的 HTML 片段（id=v2ImportModal 到下一个区块注释）。"""
    start = html.index('<div id="v2ImportModal"')
    nxt = html.find("<!-- ═══", start + 10)
    return html[start: nxt if nxt != -1 else len(html)]


@pytest.fixture(scope="module")
def ie_js(js: str) -> str:
    """截取 app.js 中导入/导出相关代码段（v2ShowImpExp 到 Bootstrap）。"""
    start = js.index("function v2ShowImpExp()")
    end = js.index("// ── Bootstrap", start)
    return js[start:end]


# ══════════════════════════════════════════════════════════════════════
# 1. 旧 Tab 脚手架清除
# ══════════════════════════════════════════════════════════════════════

class TestOldTabScaffoldingRemoved:
    """3 主 Tab + 3 子 Tab 的双层切换已下线。"""

    def test_tab_functions_gone(self, js: str):
        for fn in ("function v2IETab(", "function v2ImpTab("):
            assert fn not in js, f"旧 Tab 切换函数未移除: {fn}"
        for call in ("v2IETab('", "v2ImpTab('"):
            assert call not in js, f"旧 Tab 调用残留: {call}"

    def test_old_element_ids_gone(self, html: str):
        for eid in (
            "v2IEImport", "v2IEExport", "v2IETemplate",
            "v2IEImport", "v2IEExport", "v2IETemplate",
            "v2IETabImport", "v2IETabExport", "v2IETabTemplate",
            "v2ImpTabJson", "v2ImpTabExcel", "v2ImpTabCsv",
            "v2ImpJsonData", "v2ImpExcelName", "v2ImpCsvName",
            "v2ExcelZone", "v2ExcelInput", "v2CsvZone", "v2CsvInput",
        ):
            assert f'id="{eid}"' not in html, f"旧 DOM id 残留: {eid}"

    def test_old_tab_css_class_gone(self, html: str, js: str):
        assert "v2-ie-tab" not in html, "旧 v2-ie-tab 样式类残留"
        assert "v2-ie-tab" not in js, "旧 v2-ie-tab 样式类残留（JS）"


# ══════════════════════════════════════════════════════════════════════
# 2. 单框三段并列
# ══════════════════════════════════════════════════════════════════════

class TestThreeSectionsCoexist:
    """导入 / 导出 / 模板 三段同框，不再互斥显示。"""

    def test_section_anchors_exist(self, modal: str):
        for sid in ("v2IeSecImport", "v2IeSecExport", "v2IeSecTemplate"):
            assert f'id="{sid}"' in modal, f"缺少分段锚点: {sid}"

    def test_sections_never_hidden(self, modal: str):
        """三段容器不得内联 display:none —— 同框并列是这个设计的核心。"""
        for sid in ("v2IeSecImport", "v2IeSecExport", "v2IeSecTemplate"):
            tag = re.search(rf'<div id="{sid}"[^>]*>', modal)
            assert tag, f"未找到分段容器: {sid}"
            assert "display:none" not in tag.group(0), f"分段被隐藏（应同框并列）: {sid}"

    def test_js_does_not_toggle_three_sections(self, ie_js: str):
        """v2IeFmt 只切格式相关元素，不得触碰三个分段容器。"""
        for sid in ("v2IeSecImport", "v2IeSecExport", "v2IeSecTemplate"):
            assert sid not in ie_js, f"格式切换不应操作分段容器: {sid}"


# ══════════════════════════════════════════════════════════════════════
# 3. 唯一格式选择器
# ══════════════════════════════════════════════════════════════════════

class TestSingleFormatSelector:
    """6 个切换按钮 → 1 组格式选择器（3 个）。"""

    def test_selector_bar_exists(self, modal: str):
        assert 'id="v2IeFmtBar"' in modal

    def test_exactly_three_format_buttons(self, modal: str):
        bar = modal[modal.index('id="v2IeFmtBar"'):]
        bar = bar[: bar.index("</div>") + 6]
        btns = re.findall(r'<button[^>]*data-fmt="(\w+)"', bar)
        assert btns == ["json", "excel", "csv"], f"格式按钮应为 json/excel/csv，实得 {btns}"

    def test_fmt_config_table(self, js: str):
        assert "const V2_IE_FMT" in js, "缺少格式配置表 V2_IE_FMT"
        assert "function v2IeFmt(fmt)" in js

    def test_selector_drives_file_input(self, ie_js: str):
        assert "inp.accept = cfg.accept" in ie_js, "格式选择器须驱动 accept"
        assert "inp.multiple = cfg.multiple" in ie_js, "格式选择器须驱动 multiple"

    def test_selector_drives_labels(self, ie_js: str):
        assert "v2IeExportBtn" in ie_js, "导出按钮文案须跟随格式"
        assert "v2IeTplBtn" in ie_js, "模板按钮文案须跟随格式"

    def test_csv_accept_includes_zip(self, js: str):
        """v2.22.3 §六·补七：CSV 导出产出 ZIP 归档，导入必须原样收得回来。

        此前 accept='.csv'（v2.22.2 散装语义）—— UI 导出的归档自己都导不回去。
        """
        assert "'.csv,.zip'" in js, "CSV 格式须同时接受散装 .csv 与归档 .zip"

    def test_csv_drop_hint_mentions_zip_archive(self, js: str):
        """拖拽提示要如实告知可直接拖入导出的 ZIP，别让用户以为只能散装多选。"""
        assert "可多选散装 CSV，或直接拖入导出的 ZIP 归档" in js


# ══════════════════════════════════════════════════════════════════════
# 4. 导入 mode 选择器暴露并下发
# ══════════════════════════════════════════════════════════════════════

class TestImportModeWired:
    """mode=create/update/upsert 后端早有实现（import_export.py VALID_IMPORT_MODES），
    合并前前端从不下发。此处钉死「暴露 + 真实下发」，防回退。"""

    def test_mode_select_exists(self, modal: str):
        assert 'id="v2IeMode"' in modal
        for val in ('value="create"', 'value="upsert"', 'value="update"'):
            assert val in modal, f"mode 选项缺失: {val}"

    def test_mode_read_and_sent(self, ie_js: str):
        assert "getElementById('v2IeMode').value" in ie_js
        assert "fd.append('mode', mode)" in ie_js, "Excel/CSV 导入须以 Form 下发 mode"
        assert "import/json?mode=" in ie_js, "JSON 导入须以 Query 下发 mode"

    def test_backend_accepts_those_modes(self):
        src = _IMPEXP.read_text(encoding="utf-8")
        assert 'VALID_IMPORT_MODES = ("create", "update", "upsert")' in src


# ══════════════════════════════════════════════════════════════════════
# 5. 单一 dropzone + 无重复 DOM id
# ══════════════════════════════════════════════════════════════════════

class TestSingleDropzone:
    def test_exactly_one_file_input(self, modal: str):
        assert modal.count('<input type="file"') == 1, "弹窗内应只剩一个 file input"
        assert 'id="v2IeFile"' in modal
        assert 'id="v2IeDropZone"' in modal

    def test_no_duplicate_dom_ids(self, modal: str):
        ids = re.findall(r'\bid="([^"]+)"', modal)
        dupes = {i for i in ids if ids.count(i) > 1}
        assert not dupes, f"弹窗内重复 DOM id（getElementById 只取首个）: {sorted(dupes)}"

    def test_drag_drop_bound_once(self, ie_js: str):
        assert ie_js.count("ondragover") == 1, "拖拽绑定应只在一个 dropzone 上"
        assert ie_js.count("ondrop") == 1, "拖拽绑定应只在一个 dropzone 上"


# ══════════════════════════════════════════════════════════════════════
# 6. 端点契约 + CSV 导出（v2.22.3 由拦截改为启用）
# ══════════════════════════════════════════════════════════════════════

class TestEndpointWiring:
    """前后端端点契约：合并 UI 不得改端点。"""

    def test_template_paths_literal(self, js: str):
        """模板端点不受格式切换影响，仍是三张字面量 URL。"""
        for path in (
            "/api/v1/data-standards-v2/template/json",
            "/api/v1/data-standards-v2/template/excel",
            "/api/v1/data-standards-v2/template/csv",
        ):
            assert path in js, f"前端丢失模板端点引用: {path}"

    def test_import_json_path(self, js: str):
        """JSON 导入带 mode 查询串（其余走 Form，故 JSON 是唯一字面量路径）。"""
        assert "/api/v1/data-standards-v2/import/json?mode=" in js

    def test_import_file_path_dispatched_by_selector(self, js: str):
        """Excel/CSV 导入端点由格式选择器动态拼装，三种格式必须都在配置表里。"""
        assert "'/api/v1/data-standards-v2/import/' + fmt" in js, "文件导入端点须由格式选择器驱动"
        block = js[js.index("const V2_IE_FMT"): js.index("};", js.index("const V2_IE_FMT"))]
        for fmt in ("json", "excel", "csv"):
            assert f"\n  {fmt}:" in block, f"格式配置表缺少: {fmt}"

    def test_export_path(self, ie_js: str):
        assert "'/api/v1/data-standards-v2/' + V2.stdId + '/export?format=' + fmt" in ie_js

    def test_csv_export_enabled_with_zip_extension(self, ie_js: str):
        """v2.22.3 起后端已实现 csv 导出（ZIP 归档），前端必须解封。

        CSV 归档是 manifest + 6 个角色 CSV，含字典与字段-字典绑定，可无损往返 ——
        tooltip 要如实描述归档结构，别让用户以为导出的还是旧的一表一文件形态。
        """
        assert "expBtn.disabled = false" in ie_js, "CSV 导出后端已支持，按钮不得再禁用"
        assert "不支持 CSV" not in ie_js, "拦截文案已作废（v2.22.2 遗留）"
        # 下载扩展名：csv 产出 ZIP，不能沿用 fmt
        assert "fmt === 'csv' ? 'zip' : fmt" in ie_js, "CSV 导出为 ZIP，扩展名须映射为 zip"
        assert "不含字典" not in ie_js, "v2.22.3 起 CSV 归档已含字典与绑定，旧文案须作废"
        assert "_manifest.json" in ie_js, "tooltip 须说明归档含 _manifest.json"

    def test_backend_export_formats(self):
        """钉死后端真实能力：v2.22.3 起 json/excel/csv 三分支齐备。"""
        src = _IMPEXP.read_text(encoding="utf-8")
        assert 'if format == "json":' in src
        assert 'elif format == "excel":' in src
        assert 'elif format == "csv":' in src
        assert 'media_type="application/zip"' in src
        assert '不支持的导出格式' in src   # 真正未实现的格式仍 400


# ══════════════════════════════════════════════════════════════════════
# 7. 孤儿 DOM id 守卫
# ══════════════════════════════════════════════════════════════════════

class TestNoOrphanElementIds:
    """合并两个 dropzone 为一个后，getElementById 拼写错误会静默取到 null。"""

    def test_every_referenced_id_exists(self, ie_js: str, html: str):
        referenced = set(re.findall(r"getElementById\('([^']+)'\)", ie_js))
        assert referenced, "未解析到任何 DOM 引用，守卫本身失效"
        missing = sorted(i for i in referenced if f'id="{i}"' not in html)
        assert not missing, f"JS 引用了不存在的 DOM id（getElementById 静默返回 null）: {missing}"

    def test_no_legacy_id_reference(self, ie_js: str):
        for eid in ("v2IEImport", "v2IEExport", "v2IETemplate", "v2ExpStdInfo",
                    "v2ExcelZone", "v2ExcelInput", "v2CsvZone", "v2CsvInput",
                    "v2ImpJsonData", "v2ImpJsonName", "v2ImpExcelName", "v2ImpCsvName"):
            assert f"'{eid}'" not in ie_js, f"新代码仍引用旧 DOM id: {eid}"

    def test_opener_still_wired(self, html: str):
        """工具栏入口按钮仍指向 v2ShowImpExp（改名会断入口）。"""
        assert 'onclick="v2ShowImpExp()"' in html

    def test_no_undefined_css_var(self, modal: str):
        """弹窗内引用的 CSS 变量必须在 index.html 的 :root 里真实存在。

        文本色阶是 t0(主)→t1(次)→t2(弱)，没有 t3 —— 写错不会报错，只会静默
        回落成继承色，肉眼在深色主题下极难发现。
        """
        root = _HTML.read_text(encoding="utf-8")[:6000]
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", root))
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", modal))
        unknown = sorted(used - declared)
        assert not unknown, f"弹窗引用了未定义的 CSS 变量（静默失效）: {unknown}"

    def test_section_first_child_no_border(self, html: str):
        """首个分段无上边框 —— 用显式 id 选择器，:first-of-type 会被 fmt bar 的 div 抢先匹配。"""
        assert "#v2IeSecImport{margin-top:0;padding-top:0;border-top:none;}" in html
        assert ".v2-ie-sec:first-of-type" not in html, "首个分段请用 #v2IeSecImport 显式选择器"


# ══════════════════════════════════════════════════════════════════════
# 8. JS 语法守卫
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用，跳过 JS 语法检查")
class TestJsSyntax:
    """内容契约测试看不见语法错误（重构残留的裸 `}` 也能让全部字符串断言通过），
    补一道真解析器关卡。"""

    def test_app_js_parses(self, tmp_path):
        import subprocess
        r = subprocess.run(
            ["node", "--check", str(_JS)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert r.returncode == 0, f"app.js 语法错误:\n{r.stderr}"

    def test_no_dead_placeholder_symbols(self, js: str):
        for sym in ("OLD_removed", "TODO_REMOVE", "DELETE_ME"):
            assert sym not in js, f"残留占位符号: {sym}"


