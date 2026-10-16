"""坐标转换结果导出服务 — 支持 GeoJSON / Shapefile(zip 打包) / GeoPackage / MDB / CSV / File Geodatabase。

用 fiona(GDAL/OGR) 将 `transform_results.output_data`(GeoJSON FeatureCollection)
实时导出为不同 GIS 格式。核心动机：GeoJSON(RFC 7946) 的 `crs` 成员**无法原生表达
自定义投影坐标系**（如 shanghaicity=SRID 918984），导致"转换/转回自定系"的结果在
QGIS/ArcGIS 里无法按正确投影打开。而 Shapefile 自带 `.prj`(WKT)，GeoPackage 自带
CRS 定义，两者都能完整保留自定义投影。

设计约束（fiona 同步库）：
- 本模块为**同步**函数，由 FastAPI 同步线程池执行，不阻塞事件循环。
- Shapefile 不支持混合几何类型，schema 几何类型取第一个非空要素。
- SHAPE 属性字段(.dbf)只支持标量(str/int/float/bool)；非标量属性丢弃并备注。
- SHP 是多文件格式，统一打包为 `.zip` 交付，内含水 .shp/.shx/.dbf/.prj/.cpg。

MDB / CSV（v2.19.0 数据导出，source 为文件特征的 FeatureCollection）：
- MDB：ADOX(ACE OLEDB 12.0) 建空库 + pyodbc 写入，按 layer_name 拆表，
  几何以 GeoJSON 文本 + WKT 双列存 MEMO；非 Esri PGDB 专有结构。
- CSV：UTF-8 BOM，几何输出 WKT。
"""

from __future__ import annotations

import io
import json
import logging
import math
import zipfile
from pathlib import Path
from typing import Any, Iterable

from app.services._odbc import access_conn_str, is_windows

logger = logging.getLogger(__name__)

# Shapefile geometry 类型 → fiona 允许值
_ALLOWED_GEOM = {
    "Point", "LineString", "Polygon",
    "MultiPoint", "MultiLineString", "MultiPolygon",
}


def _iter_features(output_data: bytes):
    """解析 output_data 为 GeoJSON FeatureCollection，产出一个 Feature dict。"""
    try:
        fc = json.loads(output_data)
    except Exception:
        fc = None
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise ValueError("output_data 不是有效的 GeoJSON FeatureCollection")
    feats = fc.get("features") or []
    if not isinstance(feats, list) or not feats:
        raise ValueError("output_data 无可导出要素")
    return feats


def _scalar_props(props: dict) -> tuple[dict, list[str]]:
    """保留标量属性用于 SHP/GPKG；返回 (精简属性, 被丢弃的非标量键)。"""
    keep: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in (props or {}).items():
        if v is None or isinstance(v, (str, int, float, bool)):
            keep[k] = v
        else:
            dropped.append(k)
    return keep, dropped


def _build_schema(feats: list[dict]) -> tuple[dict, int]:
    """推导 fiona schema。返回 (schema, geometry 类型 index)。"""
    geom_type = None
    props_schema: dict[str, str] = {}
    for feat in feats:
        g = (feat.get("geometry") or {}).get("type")
        if g in _ALLOWED_GEOM:
            geom_type = geom_type or g
        for k, v in _scalar_props(feat.get("properties") or {})[0].items():
            if k in props_schema:
                continue
            if isinstance(v, bool):
                props_schema[k] = "str"  # dbf bool 不稳定，统一按 str
            elif isinstance(v, int):
                props_schema[k] = "int"
            elif isinstance(v, float):
                props_schema[k] = "float"
            else:
                props_schema[k] = "str"
    if geom_type is None:
        raise ValueError("无法从转换结果推导几何类型")
    schema = {
        "geometry": geom_type,
        "properties": props_schema or {"fid": "int"},
    }
    return schema, len(props_schema)


def _build_gdb_schema(feats: list[dict]) -> tuple[dict, int]:
    """推导 OpenFileGDB 专用 schema——int 溢出(>32-bit)列映射 int64。

    v2.19.5：ArcGIS 个人地理数据库(.mdb) 被 Pro 废止，改产 File Geodatabase。
    OpenFileGDB 驱动原生支持 Integer64（esriFieldTypeInteger64），大整数
    （如地籍编号 1100230546425258）不会像 mdb 那样需要转文本保整；
    但 fiona 的 "int" 映射 Integer32，必须显式升级为 "int64" 防溢出。
    各层独立构建 schema（FGD 每 Feature Class 单几何类型），返回值与
    _build_schema 相同。
    """
    geom_type = None
    props_schema: dict[str, str] = {}
    for feat in feats:
        g = (feat.get("geometry") or {}).get("type")
        if g in _ALLOWED_GEOM:
            geom_type = geom_type or g
        for k, v in _scalar_props(feat.get("properties") or {})[0].items():
            if k in props_schema:
                # 跨行合并：任一值超 32-bit → 升级 int64（与 _merge_access_type 同思路）
                if props_schema[k] == "int" and isinstance(v, int) \
                        and not -(2**31) <= v < 2**31:
                    props_schema[k] = "int64"
                continue
            if isinstance(v, bool):
                props_schema[k] = "str"
            elif isinstance(v, int):
                props_schema[k] = "int64" if not -(2**31) <= v < 2**31 else "int"
            elif isinstance(v, float):
                props_schema[k] = "float"
            else:
                props_schema[k] = "str"
    if geom_type is None:
        raise ValueError("无法从转换结果推导几何类型")
    schema = {
        "geometry": geom_type,
        "properties": props_schema or {"fid": "int"},
    }
    return schema, len(props_schema)


def _shapefile_field_map(orig_names: list[str]) -> dict[str, str]:
    """原始属性名 → DBF 合法 ASCII 列名（≤10 字符，去重）。

    v2.19.3：Shapefile 的 .dbf 字段名仅允许 ASCII（且 ≤10 字符），GDB/DWG
    提取特征常带中文列名（如「地块名称(项目名称)」），原样写入时 GDAL
    报错且错误消息含 GBK 字节导致 fiona 解码崩溃（UnicodeDecodeError）。
    中文/长列名映射为稳定 ASCII 名（保留 ASCII 片段，冲突加序号）；
    csv/mdb/gpkg 不受此限，保留原始列名。
    """
    used: set[str] = set()
    out: dict[str, str] = {}
    for i, name in enumerate(orig_names):
        base = "".join(c for c in name if c.isascii() and (c.isalnum() or c == "_"))
        base = base.strip("_")[:8] or f"F{i + 1}"
        cand, k = base, 1
        while cand.lower() in used:
            cand = f"{base[:7]}{k}"
            k += 1
        used.add(cand.lower())
        out[name] = cand
    return out


def _build_shapefile_schema(feats: list[dict]) -> tuple[dict, dict[str, str]]:
    """shapefile 专用：建 schema + 返回 原名→ASCII 列名 映射。"""
    schema, _ = _build_schema(feats)
    names = list(schema["properties"])
    pmap = _shapefile_field_map(names)
    schema["properties"] = {pmap[k]: v for k, v in schema["properties"].items()}
    return schema, pmap


def _fiona_records(feats: list[dict], schema_props: dict,
                   name_map: dict[str, str] | None = None) -> list[dict]:
    """把 GeoJSON Feature 列表转成 fiona write 记录（含简化属性 + fid）。

    name_map: 原始属性名 → ASCII 列名映射（shapefile 用；其余格式传 None）。
    """
    records = []
    for i, feat in enumerate(feats):
        props, _ = _scalar_props(feat.get("properties") or {})
        if name_map:
            row = {name_map.get(k, k): v for k, v in props.items()}
        else:
            row = dict(props)
        if not schema_props:
            row = {"fid": i}
        else:
            # 补齐缺失属性键，避免 fiona 报缺字段
            for k in schema_props:
                row.setdefault(k, None)
        records.append({
            "type": "Feature",
            "id": str(i),
            "geometry": feat.get("geometry"),
            "properties": row,
        })
    return records


def export_geojson(output_data: bytes, *_args, **_kw) -> bytes:
    """GeoJSON：直接返回（调用方已负责 CRS 声明/纠正）。"""
    return output_data


def _group_by_geometry(feats: list[dict]) -> dict[str, list[dict]]:
    """按几何类型分组（v2.19.1：Shapefile/GPKG 单层只允许单一几何类型；
    真实 DWG/GDB 混合 Point+LineString+Polygon 数据需分文件/分层导出）。"""
    groups: dict[str, list[dict]] = {}
    for f in feats:
        gtype = (f.get("geometry") or {}).get("type") or "None"
        groups.setdefault(gtype, []).append(f)
    return groups


def export_shapefile_zip(
    output_data: bytes, wkt: str | None, stem: str,
) -> bytes:
    """用 fiona 写 Shapefile(内存 .shp/.shx/.dbf/.prj/.cpg) 并打包为 zip。

    wkt: 目标坐标系 WKT；为空时 fiona 不写 .prj（默认 WGS84 语义）。
    stem: 主文件名前缀（会做文件名清洗）。

    v2.19.1：混合几何类型按类型分组，每组一个 .shp（文件名 base_<Type>.shp），
    兼容单一类型数据（文件名保持 base.shp 不变）。
    """
    import fiona

    feats = _iter_features(output_data)
    base = _safe_stem(stem)
    grouped = _group_by_geometry(feats)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        import tempfile, os
        # ignore_cleanup_errors: Windows 下 GDAL/fiona 对 .dbf 的句柄偶发
        # 延迟释放（WinError 32 文件占用），清理失败不应让整个导出 500。
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            for gtype, sub in grouped.items():
                suffix = "" if len(grouped) == 1 else "_" + gtype
                shp_path = os.path.join(td, base + suffix + ".shp")
                schema, name_map = _build_shapefile_schema(sub)
                records = _fiona_records(sub, schema.get("properties") or {}, name_map)
                kwargs = {
                    "driver": "ESRI Shapefile",
                    "schema": schema,
                    # 用 UTF-8 编码写 .dbf/.cpg，避免中文属性在 iso-8859-1 下变 '?'
                    "encoding": "UTF-8",
                }
                if wkt:
                    kwargs["crs_wkt"] = wkt
                with fiona.open(shp_path, "w", **kwargs) as dst:
                    dst.writerecords(records)
                for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                    p = os.path.join(td, base + suffix + ext)
                    if os.path.exists(p):
                        zf.writestr(base + suffix + ext, open(p, "rb").read())
    return buf.getvalue()


def export_gpkg(output_data: bytes, wkt: str | None, stem: str) -> bytes:
    """用 fiona 写 GeoPackage(.gpkg)。GPKG 内嵌 CRS 定义，兼容自定义投影。

    v2.19.1：混合几何类型下，fiona(本机 GDAL) 无法对同一文件追加第二层
    （append 模式 NULL pointer），故每类型各写一个独立 .gpkg 并打包为 zip
    （文件名 base_<Type>.gpkg）；单类型保持单文件返回（向后兼容）。
    """
    import fiona
    import tempfile, os

    feats = _iter_features(output_data)
    base = _safe_stem(stem)
    grouped = _group_by_geometry(feats)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        if len(grouped) == 1:
            gpkg_path = os.path.join(td, base + ".gpkg")
            gtype, sub = next(iter(grouped.items()))
            schema, _ = _build_schema(sub)
            records = _fiona_records(sub, schema.get("properties") or {})
            kwargs = {"driver": "GPKG", "schema": schema}
            if wkt:
                kwargs["crs_wkt"] = wkt
            with fiona.open(gpkg_path, "w", layer=base, **kwargs) as dst:
                dst.writerecords(records)
            with open(gpkg_path, "rb") as f:
                return f.read()

        # 混合几何：每类型独立文件 → zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for gtype, sub in grouped.items():
                gpkg_path = os.path.join(td, f"{base}_{gtype}.gpkg")
                schema, _ = _build_schema(sub)
                records = _fiona_records(sub, schema.get("properties") or {})
                kwargs = {"driver": "GPKG", "schema": schema}
                if wkt:
                    kwargs["crs_wkt"] = wkt
                with fiona.open(gpkg_path, "w", layer=f"{base}_{gtype}", **kwargs) as dst:
                    dst.writerecords(records)
                zf.write(gpkg_path, arcname=f"{base}_{gtype}.gpkg")
        return buf.getvalue()


def export_gdb(output_data: bytes, wkt: str | None, stem: str) -> bytes:
    """用 fiona 写 File Geodatabase（`.gdb` 目录）并打包为 zip。

    v2.19.5：ArcGIS Pro **不支持**个人地理数据库(.mdb)，官方只提供
    Convert Personal Geodatabase 转换工具；本机 GDAL 3.6+ 的 OpenFileGDB
    驱动原生支持**创建/写入** File Geodatabase（无 arcpy 依赖）。
    File Geodatabase 是 ArcGIS Pro 的直接原生格式：解压 zip 后
    在 Pro 中「添加数据 → 文件夹 → *.gdb」即可显示图形。

    结构语义：
    - 按 `layer_name` 分组 → 每个图层一个 Feature Class（名 `lyr_<layer>`，
      与 MDB 表名同规则）；同层混合几何类型 → 追加 `_<Type>` 后缀拆分。
    - CRS：经 `crs_wkt` 传入（自定义投影 PROJCS 完整保留，与 shp/gpkg 一致）。
    - 属性：全部标量保留原始中文列名；大整数列升级 int64 保整。
    - 返回 zip：根目录 `{base}.gdb/`，内含 gdbtable/系统表文件。
    """
    import fiona
    import tempfile, os

    feats = _iter_features(output_data)
    base = _safe_stem(stem)

    # 分组：(layer, gtype) → 要素列表；layer_name 是表维度列，不单列（同 mdb）
    groups: dict[tuple[str, str], list[dict]] = {}
    for f in feats:
        props, _ = _scalar_props(f.get("properties") or {})
        layer = props.pop("layer_name", None) or ""
        f = {**f, "properties": props}
        gtype = (f.get("geometry") or {}).get("type") or "None"
        groups.setdefault((str(layer), gtype), []).append(f)

    # 每 layer 内几何类型数：同层多类型需加后缀
    per_layer_geom: dict[str, set[str]] = {}
    for (layer, gtype) in groups:
        per_layer_geom.setdefault(layer, set()).add(gtype)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        gdb_path = os.path.join(td, base + ".gdb")
        layer_names: list[str] = []
        for (layer, gtype), sub in groups.items():
            fc_name = _safe_table_name(layer)
            if len(per_layer_geom.get(layer, set())) > 1:
                fc_name = f"{fc_name}_{gtype}"
            schema, _ = _build_gdb_schema(sub)
            records = _fiona_records(sub, schema.get("properties") or {})
            x_origin, y_origin = _gdb_origin(sub)
            kwargs = {
                "driver": "OpenFileGDB", "schema": schema, "layer": fc_name,
                # Integer64 需 Pro 3.2+ 目标版本，否则大整数降级 Float64 丢精度
                "TARGET_ARCGIS_VERSION": "ARCGIS_PRO_3_2_OR_LATER",
                # 坐标网格原点取该组要素最小值（见 _gdb_origin 注释）
                "XORIGIN": str(x_origin), "YORIGIN": str(y_origin),
            }
            if wkt:
                kwargs["crs_wkt"] = wkt
            # 实测：OpenFileGDB 对已存在数据集以 "w" 打开是**追加**语义
            # （各 layer 共存）；"a" 模式反而报 NULL pointer error（同 v2.19.1
            # GPKG 追加问题）。首层创建数据集，后续层追加到同一 .gdb。
            # CHECK_WITH_INVERT_PROJ=False：OpenFileGDB 写入多边形/线时会对
            # 每个点做逆向投影域校验（EPSG 投影坐标如 CGCS2000 3°带常被误判
            # "Point outside of projection domain"），关闭收敛校验（同 gdalwarp
            # #4934 处理）；ESA 域检查不影响 ArcGIS 打开。
            with fiona.Env(CHECK_WITH_INVERT_PROJ=False):
                with fiona.open(gdb_path, "w", **kwargs) as dst:
                    dst.writerecords(records)
            layer_names.append(fc_name)

        # 打包 zip：根目录 base.gdb/
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(gdb_path):
                for fn in files:
                    fp = os.path.join(root, fn)
                    arc = os.path.join(base + ".gdb", os.path.relpath(fp, gdb_path))
                    zf.write(fp, arcname=arc.replace("\\", "/"))
    return buf.getvalue()


def _gdb_origin(features: list[dict]) -> tuple[int, int]:
    """为 OpenFileGDB 坐标网格计算安全原点（XORIGIN/YORIGIN）。

    OpenFileGDB/GDB 把坐标编码为相对网格原点的正整数偏移：
    `int((coord - origin) * scale)`，要求 `coord >= origin`，否则
    （Project-CRS 或地理 CRS 默认 origin≈-400）出现负坐标（如 AutoCAD
    图纸坐标拼接后 x/y 可为负）直接报
    `GDAL Error: Cannot encode Y value.`（写层中断、导出失败）。

    ArcGIS 建库时自动把原点设到数据范围外；OpenFileGDB 创建选项
    XORIGIN/YORIGIN 即对应此网格原点。这里取该组要素坐标最小值外扩
    一个整数（向下取整 -1），保证全部坐标落在网格正半区。

    2D 坐标即够；3D 的 Z/M 有独立 ZORIGIN/MORIGIN（默认 -100000，
    高程/横距在该范围内无需调整；若异常仍由 fiona 原生报错暴露）。
    """
    min_x = min_y = None
    for f in features:
        g = f.get("geometry") or {}
        c = g.get("coordinates")
        if not c:
            continue
        for pt in _walk_coords(c):
            x, y, *_ = pt
            if min_x is None or x < min_x:
                min_x = x
            if min_y is None or y < min_y:
                min_y = y
    if min_x is None:
        return 0, 0
    return math.floor(min_x) - 1, math.floor(min_y) - 1


def _walk_coords(obj) -> Iterable[tuple]:
    """递归遍历 GeoJSON coordinates，产出最底层坐标元组（x, y[, z]）。"""
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            yield tuple(obj)
        else:
            for child in obj:
                yield from _walk_coords(child)


def _safe_stem(name: str) -> str:
    """清洗为合法文件名前缀（去扩展名 + 非法字符 + 控制字符，限定长度）。"""
    stem = Path(name).stem
    bad = ['<', '>', ':', '"', '/', '\\', '|', '?', '*', '\0', '\n', '\r']
    for b in bad:
        stem = stem.replace(b, "_")
    stem = stem.strip().strip(".")
    # 仅保留可打印 ASCII + 常见 CJK/BOM 安全字符，避免 dbf/文件名编码问题
    out = []
    for ch in stem:
        if 32 <= ord(ch) < 127 or 0x4E00 <= ord(ch) <= 0x9FFF:
            out.append(ch)
    stem = "".join(out) or "transform_result"
    return stem[:96]


# ── MDB（Access 数据库）导出 ────────────────────────────────────────────────
#
# 依赖：pyodbc + Microsoft Access Driver（写数据）；win32com + ACE OLEDB 12.0
# （ADOX 创建空库 —— ODBC 驱动无法直接创建新 .mdb 文件）。部署需安装
# Microsoft Access Database Engine（本机已装）。
# 几何以 GeoJSON 文本 + WKT 双列存 MEMO，任何可读 Access 的工具（含 ArcGIS 表
# 视图、QGIS + ODBC）均可打开；非 Esri Personal GDB 专有结构。

_ACCESS_INT = "INTEGER"
_ACCESS_DOUBLE = "DOUBLE"
_ACCESS_TEXT = "TEXT(16)"
_ACCESS_MEMO = "MEMO"


def _safe_table_name(name: str) -> str:
    """清洗为 Access 合法表名：前缀 `lyr_` 规避英文保留字，<64 字符。"""
    out = []
    for ch in (name or "")[:60]:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    stem = "".join(out).strip("_") or "features"
    return stem if stem == "features" else "lyr_" + stem


def _safe_column_name(name: str) -> str:
    """清洗为 Access 合法列名：前缀 `f_` 规避保留字（name/desc 等）。"""
    out = []
    for ch in (name or "")[:60]:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    col = "".join(out).strip("_") or "f"
    return "f_" + col


def _scalar_sql_type(v) -> str:
    if isinstance(v, bool):
        return _ACCESS_TEXT
    if isinstance(v, int):
        # Access INTEGER 为 32-bit 有符号；超范围（地籍编号等大整数）
        # 写入会 22018「数据类型不匹配」→ 转文本列保整。
        if -(2**31) <= v <= 2**31 - 1:
            return _ACCESS_INT
        return _ACCESS_MEMO
    if isinstance(v, float):
        return _ACCESS_DOUBLE
    return _ACCESS_MEMO  # 长字符串（TEXT 上限 255 不可靠）


_NUMERIC_TYPES = {_ACCESS_INT, _ACCESS_DOUBLE}


def _merge_access_type(cur: str, v) -> str:
    """合并列类型：首个值定基，后续值若与既有族冲突则升格。

    - 数值族（INTEGER/DOUBLE）互通 → 统一 DOUBLE（保留小数）；
    - 出现 str（含 None→MEMO 兜底）→ 整列 MEMO（Access 数值列写文本
      报 22018「标准表达式数据类型不匹配」）。
    """
    want = _scalar_sql_type(v)
    if cur == want:
        return cur
    if cur in _NUMERIC_TYPES and want in _NUMERIC_TYPES:
        return _ACCESS_DOUBLE
    return _ACCESS_MEMO


def _access_literal(v, force_quote: bool = False) -> str:
    """Access SQL 内联字面量（转义单引号）。

    长文本（几何 JSON/WKT、MEMO 属性）**不能**走参数绑定：
    Microsoft Access ODBC 驱动的 SQLBindParameter 对 >255 字节参数
    报 HYC00「所选函数未实现」(106)。故凡可能超长的值一律拼为
    SQL 字面量（'' 转义），短标量继续类型化字面量。

    force_quote: MEMO/TEXT 列一律引号包裹——大整数（如地籍编号）
    以数字字面量写入 MEMO 列仍可能触发 22018 类型错误，转文本保整。
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)) and not force_quote:
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


_JET4_TEMPLATE = b"Standard Jet DB"  # Jet4 / Access 2000 格式签名
_JET4_TEMPLATE_VER = b"\x01"  # 版本字节（Access 2000）


def _jet4_template_path() -> str | None:
    """定位打包的 Jet4.0 空 .mdb 模板（`app/resources/empty_jet4.mdb`）。

    模板来源：由 tests/fixtures 的 ArcGIS 个人地理数据库（Jet4 格式）清空
    用户表后固化。运行时直接复制模板 + pyodbc 写入，**不经过 ADOX 建库**。
    """
    import os
    import __main__ as _main
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "resources", "empty_jet4.mdb"),
        os.path.join(os.path.dirname(here), "resources", "empty_jet4.mdb"),
        os.path.join(os.path.dirname(here), "tests", "fixtures", "mdb",
                     "sample_migration.mdb"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _is_jet4_mdb(path: str) -> bool:
    """快速校验：文件头 `Standard Jet DB` + 版本字节 0x01（Access 2000）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return False
    return (head[4:20].rstrip(b"\x00") == _JET4_TEMPLATE
            and len(head) > 20 and head[20:21] == _JET4_TEMPLATE_VER)


def _ensure_mdb_export_supported() -> None:
    """MDB 写入依赖 ADOX/ACE OLEDB + Access ODBC 驱动，仅 Windows 可用。"""
    if not is_windows():
        raise RuntimeError(
            "MDB 导出仅支持 Windows（ADOX/ACE OLEDB + Access ODBC 写入）；"
            "当前平台不支持，请改用 GeoJSON/Shapefile/GeoPackage/CSV。"
        )


def _create_empty_mdb(path: str) -> None:
    """创建空 .mdb（**Jet4 / Access 2000 格式**）。

    v2.19.4：优先复制打包的 Jet4 模板 `app/resources/empty_jet4.mdb`
    —— 直接 ADOX(ACE OLEDB 12.0) 建库在 Office ClickToRun 环境会被重定向到
    ACE 16.0，生成的实为 **.accdb 内部结构**（头 `Standard ACE DB` + 版本 02），
    但扩展名仍是 .mdb，Microsoft Access / ArcGIS 打开报「无法识别/不是有效数据库」。
    模板复制免除建库，且 ACE 驱动原地读写 Jet4 库**不会升级格式**（实测头不变）。

    回退链：包内模板 → tests/fixtures 夹具（若模板未打包）→ ADOX 建库（旧行为）。
    """
    import os
    import shutil
    src = _jet4_template_path()
    if src and _is_jet4_mdb(src):
        if src.endswith("sample_migration.mdb"):
            # 夹具自带 Esri GDB 系统表，复制后用备份为空库
            shutil.copy(src, path)
            _drop_all_user_tables(path)
        else:
            shutil.copy(src, path)
        if os.path.isfile(path):
            return

    try:
        import win32com.client
    except ImportError as e:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "导出 MDB 需要 pywin32（ADOX 建库），请先执行: pip install pywin32"
        ) from e

    cat = win32com.client.Dispatch("ADOX.Catalog")
    cat.Create("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=" + path)
    cat = None  # 释放 COM


def _drop_all_user_tables(path: str) -> None:
    """用 pyodbc 删除模板中残留的用户表（保 MSys* 系统表），逐表容错。"""
    import pyodbc
    conn = pyodbc.connect(
        access_conn_str(path),
        autocommit=True,
    )
    try:
        cur = conn.cursor()
        for table in (
            "GDB_ColumnInfo", "GDB_GeomColumns", "GDB_ItemRelationships",
            "GDB_ItemRelationshipTypes", "GDB_Items", "GDB_Items_Shape_Index",
            "GDB_ItemTypes", "GDB_ReplicaLog", "GDB_SpatialRefs",
            "LANDSURVEY_DEMARCATE_RANGE", "LANDSURVEY_DEMARCATE_RANGE_SHAPE_Index",
            "LANDSURVEY_DETAIL_PARCEL", "LANDSURVEY_DETAIL_PARCEL_SHAPE_Index",
            "LANDSURVEY_PLANNING_RANGE", "LANDSURVEY_PLANNING_RANGE_SHAPE_Index",
            "SelectedObjects", "Selections",
        ):
            try:
                cur.execute(f"DROP TABLE [{table}]")
            except Exception:
                pass  # 已删除或不存在
    finally:
        conn.close()


def export_mdb(output_data: bytes, stem: str = "export") -> bytes:
    """导出 .mdb：按 `layer_name` 拆分多表，几何 JSON 存 MEMO 列（另付 WKT 列）。"""
    _ensure_mdb_export_supported()
    import os
    import tempfile
    import pyodbc

    feats = _iter_features(output_data)
    groups: dict[str, list[dict]] = {}
    for f in feats:
        props, _ = _scalar_props(f.get("properties") or {})
        layer = props.get("layer_name") or ""
        groups.setdefault(_safe_table_name(str(layer)), []).append(f)

    mdb_path = os.path.join(
        tempfile.gettempdir(), f"vcs_export_{os.urandom(8).hex()}.mdb"
    )
    try:
        _create_empty_mdb(mdb_path)
        conn = pyodbc.connect(
            access_conn_str(mdb_path),
            autocommit=True,
        )
        try:
            cur = conn.cursor()
            for tname, flist in groups.items():
                # 属性列 schema（layer_name 是表维度，不单列）
                schema_props: dict[str, str] = {}
                for f in flist:
                    props, _ = _scalar_props(f.get("properties") or {})
                    for k, v in props.items():
                        if k == "layer_name":
                            continue
                        if k not in schema_props:
                            schema_props[k] = _scalar_sql_type(v)
                        else:
                            # 全列扫描合并类型，避免跨行混型导致 22018
                            schema_props[k] = _merge_access_type(schema_props[k], v)
                cols = ["fid INTEGER", "geometry_type TEXT(24)",
                        "geometry MEMO", "geometry_wkt MEMO"]
                for k, t in schema_props.items():
                    cols.append(f"[{_safe_column_name(k)}] {t}")
                cur.execute(f"CREATE TABLE [{tname}] ({', '.join(cols)})")

                for i, f in enumerate(flist):
                    g = f.get("geometry") or {}
                    gtype = g.get("type") or None
                    geomtxt = json.dumps(g, ensure_ascii=False) if gtype else None
                    wkt_txt = None
                    if gtype:
                        try:
                            from shapely.geometry import shape
                            wkt_txt = shape(g).wkt
                        except Exception:
                            wkt_txt = None
                    props, _ = _scalar_props(f.get("properties") or {})
                    # 各列类型序列：基础四列 + 属性列（决定字面量引号策略）
                    col_kinds: list[str] = [_ACCESS_INT, _ACCESS_TEXT, _ACCESS_MEMO, _ACCESS_MEMO]
                    for k in schema_props:
                        col_kinds.append(schema_props[k])
                    vals: list = [i, gtype, geomtxt, wkt_txt]
                    for k in schema_props:
                        vals.append(props.get(k))
                    # 内联字面量（param 绑定对 Access 长文本 HYC00/106）；
                    # MEMO/TEXT 列强制引号包裹（大整数数字字面量写文本列仍 22018）
                    q = ", ".join(
                        _access_literal(
                            v,
                            force_quote=kind in (_ACCESS_MEMO, _ACCESS_TEXT),
                        )
                        for v, kind in zip(vals, col_kinds)
                    )
                    cur.execute(f"INSERT INTO [{tname}] VALUES ({q})")
        finally:
            conn.close()
        with open(mdb_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(mdb_path):
            os.remove(mdb_path)


def export_csv(output_data: bytes, stem: str = "export") -> bytes:
    """导出 CSV：fid/layer_name/geometry_type/geometry(WKT) + 标量属性并集。

    输出 utf-8-sig（带 BOM），Excel 直接双击打开中文不乱码。
    """
    import csv
    import io as _io

    feats = _iter_features(output_data)
    from shapely.geometry import shape

    prop_order: list[str] = []
    for f in feats:
        props, _ = _scalar_props(f.get("properties") or {})
        for k in props:
            if k not in prop_order:
                prop_order.append(k)
    base_cols = ["fid", "layer_name", "geometry_type", "geometry"]
    extra_cols = [c for c in prop_order if c != "layer_name"]

    buf = _io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(base_cols + extra_cols)

    for i, f in enumerate(feats):
        props, _ = _scalar_props(f.get("properties") or {})
        g = f.get("geometry") or {}
        gtype = g.get("type") or ""
        wkt_txt = ""
        if gtype:
            try:
                wkt_txt = shape(g).wkt
            except Exception:
                wkt_txt = json.dumps(g, ensure_ascii=False)
        row = [i, props.get("layer_name") or "", gtype, wkt_txt]
        for c in extra_cols:
            row.append(props.get(c))
        w.writerow(row)

    return buf.getvalue().encode("utf-8-sig")