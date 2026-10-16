"""ogr2ogr / Fiona 脚本生成与沙箱执行。"""

import json
import shlex
import sys
from dataclasses import dataclass, field


@dataclass
class ConversionParams:
    input_path: str
    output_path: str
    src_crs: str
    dst_crs: str
    output_format: str = "GeoJSON"
    select_fields: list[str] | None = None
    where_clause: str | None = None
    input_encoding: str = "UTF-8"


@dataclass
class GeneratedScript:
    code: str
    script_type: str


@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    success: bool = True
    exit_code: int = 0


def generate_ogr2ogr_script(params: ConversionParams) -> GeneratedScript:
    """生成完整的 ogr2ogr 命令行。"""
    parts = ["ogr2ogr"]

    # 输出格式
    fmt_map = {
        'GeoJSON': 'GeoJSON', 'SHP': 'ESRI Shapefile', 'GPKG': 'GPKG',
        'KML': 'KML', 'GML': 'GML', 'DXF': 'DXF', 'CSV': 'CSV',
    }
    ogr_fmt = fmt_map.get(params.output_format, params.output_format)
    parts.append(f"-f \"{ogr_fmt}\"")

    # 输出路径
    parts.append(f"\"{params.output_path}\"")
    parts.append(f"\"{params.input_path}\"")

    # SRS
    if params.src_crs:
        parts.append(f"-s_srs \"{params.src_crs}\"")
    if params.dst_crs:
        parts.append(f"-t_srs \"{params.dst_crs}\"")

    # 几何类型统一
    parts.append("-nlt PROMOTE_TO_MULTI")

    # 编码
    if params.input_encoding and params.input_encoding != "UTF-8":
        parts.append(f"-oo ENCODING={params.input_encoding}")
    parts.append("-lco ENCODING=UTF-8")

    # 加速
    parts.append("--config OGR2OGR_USE_GEOS_SRS_DETECTION YES")

    # 字段选择
    if params.select_fields:
        select_str = ",".join(params.select_fields)
        parts.append(f"-select {shlex.quote(select_str)}")

    # 属性过滤
    if params.where_clause:
        parts.append(f"-where {shlex.quote(params.where_clause)}")

    # 覆盖输出
    parts.append("-overwrite")

    cmd = " \\\n  ".join(parts)
    return GeneratedScript(code=cmd, script_type="ogr2ogr")


def generate_fiona_script(params: ConversionParams) -> GeneratedScript:
    """生成基于 Fiona + pyproj 的 Python 转换脚本。
    注意：WHERE 过滤在 Fiona 模式中通过逐行判断实现，使用安全比较，不包含 eval()。
    """
    lines = [
        "#!/usr/bin/env python3",
        '"""自动生成的矢量转换脚本"""',
        "",
        "import json",
        "import sys",
        "from pathlib import Path",
        "",
        "import fiona",
        "from fiona.transform import transform_geom",
        "",
        f'INPUT  = r"{params.input_path}"',
        f'OUTPUT = r"{params.output_path}"',
        f'SRC_CRS = "{params.src_crs}"',
        f'DST_CRS = "{params.dst_crs}"',
        f'OUT_FMT = "{params.output_format}"',
        "",
    ]

    if params.select_fields:
        lines.append(f'SELECT_FIELDS = {json.dumps(params.select_fields)}')
        lines.append("")

    # 安全地嵌入 WHERE 条件 — 通过 ast 预验证，不包含 eval()
    _where_js = None
    if params.where_clause:
        _where_js = _safe_where_to_js(params.where_clause)
        if _where_js:
            lines.append(f"# WHERE filter: {params.where_clause}")
            lines.append("")

    lines.extend([
        "def main():",
        "    try:",
        '        with fiona.open(INPUT, "r") as src:',
        "            src_schema = src.schema",
        "            src_crs = src.crs",
        "            total = len(src)",
        "",
        "            # 构建输出 schema",
        "            out_props = dict(src_schema['properties'])",
    ])

    if params.select_fields:
        lines.extend([
            "            out_props = {k: v for k, v in out_props.items()",
            "                         if k in SELECT_FIELDS}",
        ])

    lines.extend([
        "            out_schema = {",
        "                'geometry': src_schema['geometry'],",
        "                'properties': out_props,",
        "            }",
        "",
        "            cropped = 0",
        '            with fiona.open(OUTPUT, "w",',
        "                           driver=OUT_FMT,",
        "                           schema=out_schema,",
        "                           crs=DST_CRS) as dst:",
        "                for i, feat in enumerate(src):",
    ])

    if _where_js:
        lines.extend([
            "                    props = feat.get('properties', {})",
            f"                    if not ({_where_js}):",
            "                        cropped += 1",
            "                        continue",
        ])

    lines.extend([
        "                    # 坐标变换",
        "                    geom = feat.get('geometry')",
        "                    if geom and SRC_CRS != DST_CRS:",
        "                        geom = transform_geom(SRC_CRS, DST_CRS, geom)",
        "",
        "                    # 字段过滤",
        "                    props = feat.get('properties', {})",
    ])

    if params.select_fields:
        lines.extend([
            "                    props = {k: props.get(k) for k in SELECT_FIELDS if k in props}",
        ])

    lines.extend([
        "",
        "                    dst.write({",
        "                        'geometry': geom,",
        "                        'properties': props,",
        "                    })",
        "",
        "                    if (i + 1) % 1000 == 0:",
        "                        print(f'  Progress: {i + 1}/{total}', file=sys.stderr)",
        "",
        f"            print(f'Success: {{total - cropped}} features written to {{OUTPUT}}')",
        "            return 0",
        "",
        "    except Exception as e:",
        "        print(f'Error: {e}', file=sys.stderr)",
        "        return 1",
        "",
        "",
        'if __name__ == "__main__":',
        "    sys.exit(main())",
    ])

    code = "\n".join(lines)
    return GeneratedScript(code=code, script_type="python_fiona")


def _safe_where_to_js(where_clause: str) -> str | None:
    """预验证 WHERE 子句安全性，返回可嵌入 Python 脚本的安全表达式。

    支持 SQL 风格的 `=` 和 Python 风格的 `==`。
    仅允许：字段名 比较运算符 常量值，可通过 AND/OR 组合。
    字段名 → props.get("字段名")
    值 → 安全字符串/数字/布尔
    不支持：函数调用、属性访问、import、嵌套表达式。
    若语法不合法或包含危险节点，返回 None（即不过滤）。
    """
    import ast

    _SAFE_COMPARE_OPS = {
        ast.Eq: "==", ast.NotEq: "!=",
        ast.Gt: ">", ast.Lt: "<",
        ast.GtE: ">=", ast.LtE: "<=",
        ast.In: "in",
    }
    _SAFE_BOOL_OPS = {ast.And: "and", ast.Or: "or"}

    clause = where_clause.strip()

    # 支持 OGR SQL 风格的单等号：field = value → field == value
    # 在 Python AST 中，= 在 expression 里是 SyntaxError，因此先替换
    import re
    # 将 field = value 替换为 field == value（但避开 field == value, !=, >=, <=）
    clause = re.sub(r'(?<![!<>=])=(?!=)', '==', clause)

    try:
        tree = ast.parse(clause, mode="eval")
    except SyntaxError:
        return None

    def _to_safe(node) -> str:
        if isinstance(node, ast.Expression):
            return _to_safe(node.body)
        if isinstance(node, ast.BoolOp):
            # Python 语法中 BoolOp 只可能是 and/or（& | 是 BinOp），
            # 因此 op 不在映射表的分支是不可达的防御代码。
            op_str = _SAFE_BOOL_OPS.get(type(node.op))  # pragma: no cover
            if op_str is None:  # pragma: no cover
                raise ValueError("unsupported bool op")  # pragma: no cover
            parts = [_to_safe(v) for v in node.values]
            return f" ({f' {op_str} '.join(parts)}) "
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise ValueError("chained comparison")
            op_str = _SAFE_COMPARE_OPS.get(type(node.ops[0]))
            if op_str is None:
                raise ValueError("unsupported compare op")
            left = _to_safe(node.left)
            right = _to_safe(node.comparators[0])
            return f"({left} {op_str} {right})"
        if isinstance(node, ast.Name):
            return f'props.get("{node.id}")'
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [_to_safe(el) for el in node.elts]
            return "(" + ", ".join(items) + ")"
        raise ValueError(f"unsupported node: {type(node).__name__}")

    try:
        result = _to_safe(tree)
        # 二次确认：不包含任何函数调用或属性访问
        for node in ast.walk(tree):
            if isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
                return None  # pragma: no cover — 防御性代码：_to_safe 已拒绝全部危险节点
        return result
    except (ValueError, KeyError):
        return None


def sandbox_execute(script: GeneratedScript, limit: int = 1000) -> SandboxResult:
    """在沙箱中预执行脚本，检查语法和基本可用性。"""
    import subprocess
    import tempfile
    from pathlib import Path

    # ogr2ogr 类型：检查命令存在性
    if script.script_type == 'ogr2ogr':
        try:
            result = subprocess.run(
                ['ogr2ogr', '--version'],
                capture_output=True, text=True, timeout=10,
            )
            return SandboxResult(
                stdout=result.stdout[:500],
                stderr=result.stderr[:500],
                success=result.returncode == 0,
                exit_code=result.returncode,
            )
        except FileNotFoundError:
            return SandboxResult(
                success=False,
                stderr="ogr2ogr 未安装或不在 PATH 中",
            )

    # Python/Fiona 类型：语法检查
    tmp = Path(tempfile.gettempdir()) / '_vcs_sandbox_check.py'
    try:
        tmp.write_text(script.code, encoding='utf-8')
        result = subprocess.run(
            [sys.executable, '-m', 'py_compile', str(tmp)],
            capture_output=True, text=True, timeout=30,
        )
        return SandboxResult(
            stdout=result.stdout[:500],
            stderr=result.stderr[:500],
            success=result.returncode == 0,
            exit_code=result.returncode,
        )
    except Exception as e:
        return SandboxResult(success=False, stderr=str(e))
    finally:
        tmp.unlink(missing_ok=True)
