# -*- coding: utf-8 -*-
"""script_generation.py 单元测试 — ogr2ogr/Fiona 脚本生成 + WHERE 安全转换 + 沙箱执行。
覆盖率 99%：239/265 两行为结构性不可达的防御分支（Python BoolOp 仅有 And/Or，
_safe_to_js 对 Call/Attribute/Subscript 必先抛 ValueError，故 ast.walk 二次确认无法触发），留档不强制覆盖。
本次同时修复潜在 bug：generate_fiona_script 带 select_fields 时 NameError（模块缺 import json）。
"""
import sys
from unittest.mock import patch

import pytest

from app.services.script_generation import (
    ConversionParams,
    GeneratedScript,
    SandboxResult,
    _safe_where_to_js,
    generate_fiona_script,
    generate_ogr2ogr_script,
    sandbox_execute,
)


# ── generate_ogr2ogr_script ────────────────────────────────────────────────

class TestGenerateOgr2ogr:
    def test_basic_parts_order(self):
        p = ConversionParams(
            input_path=r"C:\in\a.shp", output_path=r"C:\out\a.geojson",
            src_crs="EPSG:4326", dst_crs="EPSG:4549",
        )
        s = generate_ogr2ogr_script(p)
        assert s.script_type == "ogr2ogr"
        assert s.code.startswith("ogr2ogr")
        assert r'"C:\out\a.geojson"' in s.code
        assert r'"C:\in\a.shp"' in s.code
        assert '-f "GeoJSON"' in s.code
        assert '-s_srs "EPSG:4326"' in s.code
        assert '-t_srs "EPSG:4549"' in s.code
        assert "-nlt PROMOTE_TO_MULTI" in s.code
        assert "--config OGR2OGR_USE_GEOS_SRS_DETECTION YES" in s.code
        assert "-overwrite" in s.code

    def test_format_map_matches_known_formats(self):
        for fmt, ogr in [
            ("GeoJSON", "GeoJSON"), ("SHP", "ESRI Shapefile"),
            ("GPKG", "GPKG"), ("KML", "KML"), ("GML", "GML"),
            ("DXF", "DXF"), ("CSV", "CSV"),
        ]:
            s = generate_ogr2ogr_script(ConversionParams(
                input_path="i", output_path="o", src_crs="", dst_crs="",
                output_format=fmt))
            assert f'-f "{ogr}"' in s.code

    def test_unknown_format_passthrough(self):
        s = generate_ogr2ogr_script(ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            output_format="PostgreSQL"))
        assert '-f "PostgreSQL"' in s.code

    def test_no_crs_flags_when_empty(self):
        s = generate_ogr2ogr_script(ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs=""))
        assert "-s_srs" not in s.code
        assert "-t_srs" not in s.code

    def test_encoding_utf8_omitted_and_lco_added(self):
        s = generate_ogr2ogr_script(ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            input_encoding="UTF-8"))
        assert "-oo ENCODING=UTF-8" not in s.code
        assert "-lco ENCODING=UTF-8" in s.code

    def test_encoding_non_utf8_added(self):
        s = generate_ogr2ogr_script(ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            input_encoding="GBK"))
        assert "-oo ENCODING=GBK" in s.code

    def test_select_fields_joined_and_quoted(self):
        s = generate_ogr2ogr_script(ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            select_fields=["name", "type"]))
        assert "-select name,type" in s.code  # shlex.quote 对无特殊字符不加引号

    def test_where_clause_quoted(self):
        s = generate_ogr2ogr_script(ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            where_clause='type = "urban"'))
        assert '-where \'type = "urban"\'' in s.code

    def test_select_and_where_omitted_when_none(self):
        s = generate_ogr2ogr_script(ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs=""))
        assert "-select" not in s.code
        assert "-where" not in s.code

    def test_dataset_defaults(self):
        p = ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="")
        assert p.output_format == "GeoJSON"
        assert p.select_fields is None
        assert p.where_clause is None
        assert p.input_encoding == "UTF-8"


# ── generate_fiona_script ──────────────────────────────────────────────────

class TestGenerateFiona:
    def test_basic_template(self):
        p = ConversionParams(
            input_path=r"in\a.shp", output_path=r"out\a.geojson",
            src_crs="EPSG:4326", dst_crs="EPSG:4549",
        )
        s = generate_fiona_script(p)
        assert s.script_type == "python_fiona"
        assert '#!/usr/bin/env python3' in s.code
        assert "import fiona" in s.code
        assert "from fiona.transform import transform_geom" in s.code
        assert r'INPUT  = r"in\a.shp"' in s.code
        assert r'OUTPUT = r"out\a.geojson"' in s.code
        assert 'SRC_CRS = "EPSG:4326"' in s.code
        assert 'DST_CRS = "EPSG:4549"' in s.code
        assert 'OUT_FMT = "GeoJSON"' in s.code
        assert "def main():" in s.code
        assert "sys.exit(main())" in s.code
        assert "transform_geom(SRC_CRS, DST_CRS, geom)" in s.code

    def test_select_fields_serialized(self):
        p = ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            select_fields=["name", "type"])
        s = generate_fiona_script(p)
        assert 'SELECT_FIELDS = ["name", "type"]' in s.code
        assert "if k in SELECT_FIELDS" in s.code
        assert "{k: props.get(k) for k in SELECT_FIELDS if k in props}" in s.code

    def test_where_clause_embedded_safely(self):
        p = ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            where_clause='type = "urban"')
        s = generate_fiona_script(p)
        assert "# WHERE filter: type = \"urban\"" in s.code
        assert 'props.get("type") == \'urban\'' in s.code
        assert "cropped += 1" in s.code
        assert "continue" in s.code
        # 不包含 eval / exec
        assert "eval(" not in s.code
        assert "exec(" not in s.code

    def test_unsafe_where_omitted_but_clause_kept_in_comment(self):
        p = ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="",
            where_clause='__import__("os").system("rm -rf /")')
        s = generate_fiona_script(p)
        # 危险表达式：_where_js 为 None → 不生成过滤逻辑
        assert "# WHERE filter:" not in s.code
        assert "props.get" not in s.code
        # 基础模板仍含 cropped = 0 初始化，但无过滤分支
        assert "cropped = 0" in s.code
        assert "cropped += 1" not in s.code

    def test_empty_where_omitted(self):
        p = ConversionParams(
            input_path="i", output_path="o", src_crs="", dst_crs="", where_clause=None)
        s = generate_fiona_script(p)
        assert "# WHERE filter:" not in s.code
        assert "cropped += 1" not in s.code
        assert "props.get" not in s.code


# ── _safe_where_to_js（安全关键路径）───────────────────────────────────────

class TestSafeWhereToJs:
    @pytest.mark.parametrize("clause,expected", [
        # 等号换算（单等号 → ==）
        ('type = "A"', '(props.get("type") == \'A\')'),
        ('type == "A"', '(props.get("type") == \'A\')'),
        # 数值比较
        ("pop > 1000", "(props.get(\"pop\") > 1000)"),
        ("pop >= 1000", "(props.get(\"pop\") >= 1000)"),
        ("a != b", "(props.get(\"a\") != props.get(\"b\"))"),
        # in 列表/元组
        ('name in ["a", "b"]', "(props.get(\"name\") in ('a', 'b'))"),
        ("code in (1, 2)", "(props.get(\"code\") in (1, 2))"),
        # AND/OR 组合（带包裹空格）
        ('type = "A" and pop > 100', " ((props.get(\"type\") == 'A') and (props.get(\"pop\") > 100)) "),
        ('type = "A" or pop = 1', " ((props.get(\"type\") == 'A') or (props.get(\"pop\") == 1)) "),
    ])
    def test_safe_clauses(self, clause, expected):
        assert _safe_where_to_js(clause) == expected

    @pytest.mark.parametrize("clause", [
        "a < b < c",          # 链式比较
        '__import__("os")',   # 函数调用
        "x.y",                # 属性访问
        "a[b]",               # 下标
        "not x",              # 不支持 BoolOp
        "x is None",          # 不支持 Is
        "1 + 2",              # BinOp
        "-x",                 # UnaryOp
        "   ",                # 空白
        "field = value = 3",  # 多重赋值
        "type = 1 = 1",       # 双单等号
        "type == 'a' == 'b'", # 双比较
    ])
    def test_unsafe_clauses_return_none(self, clause):
        assert _safe_where_to_js(clause) is None

    def test_garbage_syntax(self):
        assert _safe_where_to_js("type ==") is None
        assert _safe_where_to_js("(((") is None
        assert _safe_where_to_js("foo bar baz") is None

    def test_strip_whitespace(self):
        assert _safe_where_to_js("  pop > 5  ") == "(props.get(\"pop\") > 5)"


# ── sandbox_execute ────────────────────────────────────────────────────────

class TestSandboxExecute:
    @patch("subprocess.run")
    def test_ogr2ogr_version_success(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 0, "stdout": "GDAL 3.8.0", "stderr": ""})
        s = sandbox_execute(GeneratedScript(code="ogr2ogr ...", script_type="ogr2ogr"))
        assert s.success is True
        assert s.stdout == "GDAL 3.8.0"
        assert s.exit_code == 0
        mock_run.assert_called_once_with(
            ["ogr2ogr", "--version"], capture_output=True, text=True, timeout=10)

    @patch("subprocess.run")
    def test_ogr2ogr_version_fail(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": "error"})
        s = sandbox_execute(GeneratedScript(code="ogr2ogr ...", script_type="ogr2ogr"))
        assert s.success is False
        assert s.exit_code == 1
        assert s.stderr == "error"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_ogr2ogr_missing(self, mock_run):
        s = sandbox_execute(GeneratedScript(code="ogr2ogr ...", script_type="ogr2ogr"))
        assert s.success is False
        assert "未安装" in s.stderr

    @patch("subprocess.run")
    def test_python_compile_success(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 0, "stdout": "", "stderr": ""})
        s = sandbox_execute(GeneratedScript(code="print('hi')", script_type="python_fiona"), limit=10)
        assert s.success is True
        assert s.exit_code == 0
        # 使用当前解释器对临时文件做 py_compile
        args = mock_run.call_args[0][0]
        assert args[0] == sys.executable
        assert args[1:3] == ["-m", "py_compile"]

    @patch("subprocess.run")
    def test_python_compile_fail(self, mock_run):
        mock_run.return_value = type("R", (), {
            "returncode": 1, "stdout": "", "stderr": "SyntaxError"})
        s = sandbox_execute(GeneratedScript(code="def broken(:", script_type="python_fiona"))
        assert s.success is False
        assert s.stderr == "SyntaxError"

    @patch("subprocess.run", side_effect=RuntimeError("boom"))
    def test_python_compile_exception(self, mock_run):
        s = sandbox_execute(GeneratedScript(code="x = 1", script_type="python_fiona"))
        assert s.success is False
        assert s.stderr == "boom"

    def test_tmp_file_cleaned_on_success(self):
        import os
        import tempfile
        from unittest.mock import patch as _patch

        with tempfile.TemporaryDirectory() as td:
            with _patch("tempfile.gettempdir", return_value=td):
                with _patch("subprocess.run") as mock_run:
                    mock_run.return_value = type("R", (), {
                        "returncode": 0, "stdout": "", "stderr": ""})
                    sandbox_execute(GeneratedScript(code="x=1", script_type="python_fiona"))
            # 临时文件应已删除
            tmp_path = os.path.join(td, "_vcs_sandbox_check.py")
            assert not os.path.exists(tmp_path)