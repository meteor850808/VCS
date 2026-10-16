"""格式检测服务 — Magic Bytes + 扩展名 + chardet 编码检测 + schema 解析。

v2.4 变更：
- 新增 OBJ 3D 模型格式检测
- 新增 XLSX/CSV 表格格式检测
- 新增 MDB (ESRI Personal GDB) 格式检测
- 新增 detect_data_type() 分类：geometry / spatial / table / archive / unknown
- MDB 从 TABLE_FORMATS 移至 GEOMETRY_FORMATS，返回 "spatial"
- 扩展 EXTENSION_MAP 覆盖更多格式
"""

import json
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import chardet


@dataclass
class FieldSchema:
    name: str
    field_type: str       # "string" | "integer" | "float" | "date" | "geometry"
    nullable: bool = True
    sample: object = None  # 采样值


@dataclass
class FormatResult:
    format_name: str
    encoding: str
    crs_info: Optional[str] = None
    confidence: float = 1.0
    magic_bytes: Optional[str] = None
    attribute_schema: list[FieldSchema] = field(default_factory=list)
    feature_count: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    data_type: str = "unknown"         # geometry/spatial/table/archive/unknown
    geometry_type: Optional[str] = None  # Point/LineString/Polygon/MultiPolygon/3d/table
    layer_count: int = 0
    field_schema_json: Optional[list[dict]] = None  # 供 StoredFile.field_schema 存储


# ── Magic Bytes / Header Signatures ──────────────────────────────────────────

MAGIC_PATTERNS: dict[str, tuple[Optional[bytes], Optional[str]]] = {
    # format: (header_bytes_or_offset_encoding, text_pattern_utf8)
    "SHP":     (b"\x00\x00\x27\x0a", None),       # SHP starts with file code 9994 big-endian
    "TIFF_LE": (b"II\x2a\x00", None),              # Little-endian TIFF
    "TIFF_BE": (b"MM\x00\x2a", None),              # Big-endian TIFF
    "DWG":     (None, None),                        # DWG: check version bytes AC10xx
    "DXF":     (None, "  0\nSECTION"),              # DXF text header
    "GeoJSON": (None, '{"type"'),                   # GeoJSON starts with {"type"
    "KML":     (None, '<?xml'),                     # KML is XML-based
    "GML":     (None, '<gml:'),                     # GML namespace
    "GPKG":    (b"SQLite format 3\x00", None),      # GeoPackage is SQLite
    "OBJ":     (None, None),                        # OBJ: text-based, check "v " lines
    "MDB":     (None, None),                        # MDB: Jet DB header
}

EXTENSION_MAP: dict[str, str] = {
    '.shp': 'SHP', '.shx': 'SHP', '.dbf': 'SHP',
    '.geojson': 'GeoJSON', '.json': 'GeoJSON',
    '.kml': 'KML', '.kmz': 'KMZ',
    '.gml': 'GML',
    '.dxf': 'DXF', '.dwg': 'DWG', '.dgn': 'DGN',
    '.tab': 'TAB', '.mif': 'TAB',
    '.gpkg': 'GPKG',
    '.tif': 'TIFF', '.tiff': 'TIFF', '.geotiff': 'TIFF',
    '.csv': 'CSV', '.xlsx': 'XLSX', '.xls': 'XLSX',
    '.obj': 'OBJ', '.OBJ': 'OBJ',
    '.mdb': 'MDB', '.MDB': 'MDB',
    '.zip': 'ZIP', '.tar': 'TAR', '.gz': 'TAR', '.tgz': 'TAR',
}

# ── 数据类型分类 ──────────────────────────────────────────────────────────────

# 有几何信息的格式
# 注意: DWG 要素解析依赖 LibreDWG dwg2dxf（tools/libredwg/，GDAL 本身无 DWG 驱动），
# 工具缺失时由 dwg_converter 优雅降级为空（识别归类不受影响）。
# DGN: GDAL 内置 V7 驱动可完整解析要素；V8 需 ODA 驱动（缺失），失败时优雅降级为空。
GEOMETRY_FORMATS = {"SHP", "GeoJSON", "GPKG", "KML", "KMZ", "GML", "DXF", "TAB", "OBJ", "DWG", "DGN", "MDB"}
# 纯属性/表格格式
TABLE_FORMATS = {"CSV", "XLSX"}
# 压缩包格式
ARCHIVE_FORMATS = {"ZIP", "TAR"}


# ── 几何类型归一化 ──────────────────────────────────────────────────────────────
# 前端展示用统一标签：Multi* 归入父类型
_GEOM_NORMALIZE: dict[str, str] = {
    "Point": "Point", "MultiPoint": "Point",
    "LineString": "LineString", "MultiLineString": "LineString",
    "Polygon": "Polygon", "MultiPolygon": "Polygon",
    "GeometryCollection": "Polygon",
    "3d": "3d", "TIN": "3d", "PolyhedralSurface": "3d", "MultiPatch": "3d",
}


def normalize_geometry_type(raw: str | None) -> str | None:
    """将原始几何类型归一化为前端展示用的五大类: Point/LineString/Polygon/3d/table。"""
    if not raw:
        return None
    return _GEOM_NORMALIZE.get(raw, raw)


# fiona schema 的维度声明前缀/后缀（GDB 等驱动会报告 "3D MultiPolygon"/"Polygon ZM"）
_DIM_PREFIX_RE = re.compile(r"^(3D|2D|3DM)\s+", re.IGNORECASE)
_DIM_SUFFIX_RE = re.compile(r"\s+(Z|M|ZM|XYZM|3D|2D)\s*$", re.IGNORECASE)


def normalize_stored_geometry_type(raw: str | None) -> str | None:
    """将任意来源的原始几何类型统一为存储/展示用的五类: Point/LineString/Polygon/3d/table。

    处理两类脏值：
    1. fiona schema 的维度声明前缀/后缀 —— "3D MultiPolygon" → "MultiPolygon"、
       "Polygon ZM" → "Polygon"（维度是元数据声明，实际要素可能全 2D，不应误导展示）
    2. 未归一化的 Multi* / 3D 复合类型 —— "MultiPolygon" → "Polygon"、
       "MultiPatch" → "3d"（复用 _GEOM_NORMALIZE）

    Returns:
        五类归一化值；raw 为空返回 None。
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # 剥离维度声明前缀（如 "3D MultiPolygon"）
    s = _DIM_PREFIX_RE.sub("", s, count=1).strip()
    # 剥离维度声明后缀（如 "Polygon ZM" / "LineString Z"）
    s = _DIM_SUFFIX_RE.sub("", s).strip()
    # 纯维度声明无形状信息："3D" → 3d（3D 复合类型展示类）；"2D" → None（无法判断形状）
    if s.lower() == "3d":
        return "3d"
    if s.lower() == "2d":
        return None
    # 五类归一化（Multi* → 父类；MultiPatch/TIN/PolyhedralSurface → 3d）
    return normalize_geometry_type(s)


def detect_data_type(fmt: str, content: bytes = b"") -> str:
    """根据格式名或文件名判断数据类型。

    支持传入格式名（如 "MDB"）或文件名（如 "test.mdb"）。
    可选 content 参数用于 magic bytes 兜底检测。
    """
    if '.' in fmt:
        ext = Path(fmt).suffix.lower()
        fmt_upper = EXTENSION_MAP.get(ext, fmt.split('.')[-1].upper())
    else:
        fmt_upper = fmt.upper()

    if fmt_upper == 'MDB':
        return "spatial"

    # magic bytes 兜底: 文件内容以 MDB 签名开头
    if content and content[:16] == b'Standard Jet DB':
        return "spatial"

    if fmt_upper in GEOMETRY_FORMATS:
        return "geometry"
    if fmt_upper in TABLE_FORMATS:
        return "table"
    if fmt_upper in ARCHIVE_FORMATS:
        return "archive"
    return "unknown"


def detect_format(file_path: str) -> FormatResult:
    """检测矢量文件格式，返回完整的 FormatResult。"""
    if not os.path.exists(file_path):
        return FormatResult(format_name="Unknown", encoding="UTF-8", confidence=0.0)

    ext = Path(file_path).suffix.lower()
    ext_format = EXTENSION_MAP.get(ext, "Unknown")

    header = _read_header_bytes(file_path, 4096)
    magic_format = _detect_by_magic(header, ext)

    encoding = _detect_encoding(file_path)
    crs_info = _extract_crs(file_path, magic_format or ext_format)
    schema, feature_count, layer_count = _parse_schema(file_path, magic_format or ext_format)

    # 置信度计算
    confidence = 1.0
    if magic_format and ext_format and magic_format != ext_format:
        confidence = 0.85  # 扩展名与 magic bytes 不一致
    elif magic_format:
        confidence = 0.95
    elif ext_format != "Unknown":
        confidence = 0.6
    else:
        confidence = 0.1

    fmt = magic_format or ext_format
    data_type = detect_data_type(fmt)
    magic_hex = header[:4].hex() if header else None

    # 构建 field_schema_json
    field_schema_json = [
        {"name": s.name, "type": s.field_type, "nullable": s.nullable, "sample": s.sample}
        for s in schema
    ]

    return FormatResult(
        format_name=fmt,
        encoding=encoding,
        crs_info=crs_info,
        confidence=confidence,
        magic_bytes=magic_hex,
        attribute_schema=schema,
        feature_count=feature_count,
        data_type=data_type,
        geometry_type=detect_geometry_type(file_path, fmt),
        layer_count=layer_count or (1 if feature_count else 0),
        field_schema_json=field_schema_json if field_schema_json else None,
    )


def detect_geometry_type(file_path: str, fmt: str) -> str | None:
    """检测文件的主导几何类型，返回归一化后的类型 (Point/LineString/Polygon/3d/table)。"""
    try:
        if fmt == "GeoJSON":
            return _detect_geom_geojson(file_path)
        elif fmt == "SHP":
            return _detect_geom_shp(file_path)
        elif fmt in ("KML", "KMZ"):
            return _detect_geom_kml(file_path)
        elif fmt == "GPKG":
            return _detect_geom_gpkg(file_path)
        elif fmt == "OBJ":
            return "3d"
        elif fmt in ("CSV", "XLSX"):
            return "table"
    except Exception:
        pass
    return None


def _detect_geom_geojson(file_path: str) -> str | None:
    """从 GeoJSON 采样前 100 个要素提取几何类型。"""
    try:
        with open(file_path, encoding="utf-8-sig") as f:
            data = json.load(f)
        features = data.get("features", [])
        types: dict[str, int] = {}
        for feat in features[:100]:
            gt = (feat.get("geometry") or {}).get("type")
            if gt:
                types[gt] = types.get(gt, 0) + 1
        if not types:
            return None
        dominant = max(types, key=types.get)
        return normalize_geometry_type(dominant)
    except Exception:
        return None


def _detect_geom_shp(file_path: str) -> str | None:
    """从 Shapefile 主文件 (.shp) 头部读取几何类型。"""
    try:
        p = Path(file_path)
        shp_path = p if p.suffix.lower() == ".shp" else p.with_suffix(".shp")
        if not shp_path.exists():
            return None
        with open(shp_path, "rb") as f:
            f.seek(32)  # 跳过文件头 100 bytes 中的前 32 bytes
            shape_type = struct.unpack("<I", f.read(4))[0]
        SHAPE_TYPES = {1: "Point", 3: "LineString", 5: "Polygon",
                       8: "Point", 11: "Point", 13: "Point",
                       21: "LineString", 23: "LineString",
                       25: "Polygon", 31: "3d"}
        raw = SHAPE_TYPES.get(shape_type)
        return normalize_geometry_type(raw)
    except Exception:
        return None


def _detect_geom_kml(file_path: str) -> str | None:
    """从 KML 文件采样提取几何类型。"""
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(file_path)
        ns = {"kml": "http://www.opengis.net/kml/2.2"}
        types: dict[str, int] = {}
        for elem in tree.iter():
            tag = elem.tag.replace(f"{{{ns['kml']}}}", "").lower()
            if tag in ("point", "linearring", "linearring", "linestring", "polygon", "multigeometry"):
                mapping = {"point": "Point", "linestring": "LineString",
                           "linearring": "LineString", "linearring": "LineString",
                           "polygon": "Polygon", "multigeometry": "Polygon"}
                gt = mapping.get(tag, tag)
                types[gt] = types.get(gt, 0) + 1
        if not types:
            return None
        dominant = max(types, key=types.get)
        return normalize_geometry_type(dominant)
    except Exception:
        return None


def _detect_geom_gpkg(file_path: str) -> str | None:
    """从 GeoPackage 的 gpkg_geometry_columns 读取几何类型。"""
    try:
        import sqlite3
        conn = sqlite3.connect(file_path)
        cur = conn.execute(
            "SELECT geometry_type_name FROM gpkg_geometry_columns LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            raw = row[0].split()[0]  # "GEOMETRY" -> None, "POINT Z" -> "Point"
            if raw.upper() == "GEOMETRY":
                return None
            return normalize_geometry_type(raw.capitalize())
    except Exception:
        return None


def _read_header_bytes(file_path: str, size: int = 4096) -> bytes:
    try:
        with open(file_path, 'rb') as f:
            return f.read(size)
    except Exception:
        return b""


def _detect_by_magic(header: bytes, ext: str = "") -> Optional[str]:
    if len(header) < 4:
        return None

    # SHP: file code 9994 (0x0000270A) at byte 0-3 (big-endian)
    code = struct.unpack('>i', header[0:4])[0]
    if code in (9994,):
        return "SHP"
    if code == 9993:
        return "SHP"  # .shx index file

    # DWG: "AC" + version at byte 0-5
    dwg_magic = header[0:6]
    if dwg_magic[:2] == b"AC" and dwg_magic[2:].isdigit():
        return "DWG"

    # TIFF
    if header[:4] in (b"II\x2a\x00", b"MM\x00\x2a"):
        return "TIFF"

    # GeoPackage (SQLite)
    if header[:16] == b"SQLite format 3\x00":
        return "GPKG"

    # MDB: Jet Database header "Standard Jet DB"
    if header[:22] == b"Standard Jet DB\x00":
        return "MDB"

    # Text-based detection (strip BOM + whitespace)
    raw_text = header[:200].decode('utf-8', errors='replace')
    text_start = raw_text.lstrip('\ufeff \t\n\r')

    if text_start.startswith('{"type"'):
        return "GeoJSON"

    if '<?xml' in text_start[:100]:
        lower = text_start.lower()
        if '<kml' in lower:
            return "KML"
        if '<gml:' in lower:
            return "GML"

    if '  0\nSECTION' in text_start:
        return "DXF"

    # OBJ: text-based, starts with "v " (vertex) or "mtllib" or "o "
    stripped = text_start.strip()
    if ext.lower() == '.obj' or ext.lower() == '.OBJ':
        if stripped.startswith('v ') or stripped.startswith('# ') or stripped.startswith('o '):
            return "OBJ"

    return None


def _detect_encoding(file_path: str) -> str:
    """使用 chardet 检测文件编码。"""
    try:
        with open(file_path, 'rb') as f:
            raw = f.read(65536)
        result = chardet.detect(raw)
        return result['encoding'] or 'UTF-8'
    except Exception:
        return 'UTF-8'


def _extract_crs(file_path: str, fmt: str) -> Optional[str]:
    """根据格式提取 CRS 信息。"""
    p = Path(file_path)

    if fmt == 'SHP':
        prj_path = p.with_suffix('.prj')
        if prj_path.exists():
            try:
                return prj_path.read_text(encoding='utf-8').strip()
            except Exception:
                pass

    elif fmt == 'GeoJSON':
        try:
            with open(file_path, encoding='utf-8-sig') as f:
                data = json.load(f)
            crs = data.get('crs', {})
            props = crs.get('properties', {})
            return props.get('name') or str(crs) if crs else None
        except Exception:
            pass

    elif fmt == 'KML':
        try:
            text = Path(file_path).read_text(encoding='utf-8', errors='replace')
            import re
            m = re.search(r'<kml[^>]*>', text[:500])
            return "WGS84 (KML default)" if m else None
        except Exception:
            pass

    elif fmt == 'OBJ':
        # OBJ 通常无 CRS 信息
        return None

    return None


def _parse_schema(file_path: str, fmt: str) -> tuple[list[FieldSchema], Optional[int], int]:
    """解析属性字段 schema、要素数量、图层数量。返回 (schema, feature_count, layer_count)。"""
    try:
        if fmt == 'GeoJSON':
            return _parse_geojson_schema(file_path)
        elif fmt == 'SHP':
            return _parse_shapefile_schema(file_path)
        elif fmt == 'KML':
            return _parse_kml_schema(file_path)
        elif fmt == 'OBJ':
            return _parse_obj_schema(file_path)
        elif fmt == 'CSV':
            return _parse_csv_schema(file_path)
        elif fmt == 'XLSX':
            return _parse_xlsx_schema(file_path)
    except Exception:
        pass
    return [], None, 0


def _parse_geojson_schema(file_path: str) -> tuple[list[FieldSchema], Optional[int], int]:
    # utf-8-sig 自动跳过 BOM（浏览器导出的 GeoJSON 常带 BOM）
    with open(file_path, encoding='utf-8-sig') as f:
        data = json.load(f)

    features = data.get('features', [])
    feature_count = len(features)
    schema = []
    layer_count = 1

    # FeatureCollection 可能包含多种 geometry type
    geom_types = set()

    if features and 'properties' in features[0]:
        props = features[0]['properties'] or {}
        for key, val in props.items():
            ft = _infer_type(val)
            schema.append(FieldSchema(name=key, field_type=ft, sample=val))

    for feat in features[:100]:  # 采样前 100 个
        gt = (feat.get('geometry') or {}).get('type')
        if gt:
            geom_types.add(gt)

    return schema, feature_count, layer_count


def _parse_shapefile_schema(file_path: str) -> tuple[list[FieldSchema], Optional[int], int]:
    """读取 .dbf 文件头获取字段定义。"""
    p = Path(file_path)
    dbf_path = p.with_suffix('.dbf')
    if not dbf_path.exists():
        return [], None, 0

    try:
        with open(dbf_path, 'rb') as f:
            header = f.read(32)
            if len(header) < 32:
                return [], None, 0

            header_len = struct.unpack('<H', header[8:10])[0]
            num_bytes = struct.unpack('<H', header[10:12])[0]
            record_count = struct.unpack('<I', header[4:8])[0]

            f.seek(32)
            schema = []
            while f.tell() < header_len - 1:
                field_desc = f.read(32)
                if field_desc[0] == 0x0D or field_desc[0] == 0:
                    break
                name = field_desc[:11].split(b'\x00')[0].decode('ascii', errors='replace').strip()
                field_type_char = chr(field_desc[11])
                if name:
                    schema.append(FieldSchema(name=name, field_type=_dbf_type(field_type_char)))
            return schema, record_count, 1
    except Exception:
        return [], None, 0


def _parse_kml_schema(file_path: str) -> tuple[list[FieldSchema], Optional[int], int]:
    """从 KML 的 ExtendedData/Schema 提取字段结构。"""
    try:
        text = Path(file_path).read_text(encoding='utf-8', errors='replace')
        import re
        # 从 Schema 定义中提取字段名
        simple_fields = re.findall(r'name="(\w+)"\s+type="(\w+)"', text[:10000])
        # 从第一个 Placemark 的 SimpleData 中提取
        simple_data = re.findall(r'<SimpleData name="(\w+)">', text[:20000])
        seen = set()
        schema = []
        for name in simple_data:
            if name not in seen:
                seen.add(name)
                schema.append(FieldSchema(name=name, field_type="string"))
        if not schema:
            for name, ftype in simple_fields:
                if name not in seen:
                    seen.add(name)
                    schema.append(FieldSchema(name=name, field_type=_kml_type(ftype)))
        # 粗略统计 Placemark 数量
        pm_count = len(re.findall(r'<Placemark>', text))
        return schema, pm_count if pm_count > 0 else None, 1
    except Exception:
        return [], None, 0


def _parse_obj_schema(file_path: str) -> tuple[list[FieldSchema], Optional[int], int]:
    """解析 OBJ 3D 模型文件：统计顶点/面片/组信息。"""
    try:
        text = Path(file_path).read_text(encoding='utf-8', errors='replace')
        vertex_count = 0
        face_count = 0
        object_names = []
        current_object = None

        for line in text.splitlines():
            line = line.strip()
            if line.startswith('v '):
                vertex_count += 1
            elif line.startswith('f '):
                face_count += 1
            elif line.startswith('o '):
                name = line[2:].strip()
                if name:
                    object_names.append(name)
                    current_object = name

        # OBJ 的"字段"是顶点坐标
        schema = [
            FieldSchema(name="vertex_x", field_type="float", nullable=False),
            FieldSchema(name="vertex_y", field_type="float", nullable=False),
            FieldSchema(name="vertex_z", field_type="float", nullable=False),
        ]

        return schema, face_count, len(object_names) or 1
    except Exception:
        return [], None, 0


def _parse_csv_schema(file_path: str) -> tuple[list[FieldSchema], Optional[int], int]:
    """解析 CSV 表头和采样行。"""
    try:
        import csv
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.reader(f)
            headers = next(reader, None)
            if not headers:
                return [], None, 0

            # 采样前 10 行推断类型
            samples: list[list] = []
            row_count = 0
            for row in reader:
                row_count += 1
                if len(samples) < 10:
                    samples.append(row)

            schema = []
            for i, h in enumerate(headers):
                h = h.strip()
                if not h:
                    h = f"col_{i+1}"
                # 从采样行推断类型
                vals = [row[i] if i < len(row) else "" for row in samples]
                ft = _infer_csv_type(vals)
                sample_val = next((v for v in vals if v.strip()), None)
                schema.append(FieldSchema(name=h, field_type=ft, sample=sample_val))

            return schema, row_count, 1
    except Exception:
        return [], None, 0


def _parse_xlsx_schema(file_path: str) -> tuple[list[FieldSchema], Optional[int], int]:
    """解析 XLSX 第一个 sheet 的表头和采样行。"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            return [], None, 0

        rows = list(ws.iter_rows(max_row=12, values_only=True))
        wb.close()

        if not rows:
            return [], None, 0

        headers = [str(c) if c is not None else f"col_{i+1}" for i, c in enumerate(rows[0])]
        data_rows = rows[1:]  # 去掉表头
        row_count = ws.max_row - 1 if ws.max_row else 0

        schema = []
        for i, h in enumerate(headers):
            h = h.strip()
            if not h:
                h = f"col_{i+1}"
            vals = [str(row[i]) if i < len(row) and row[i] is not None else "" for row in data_rows]
            ft = _infer_csv_type(vals)
            sample_val = next((v for v in vals if v.strip()), None)
            schema.append(FieldSchema(name=h, field_type=ft, sample=sample_val))

        return schema, row_count, 1
    except ImportError:
        # openpyxl 未安装
        return [], None, 0
    except Exception:
        return [], None, 0


def _infer_type(val) -> str:
    """从 GeoJSON 采样值推断字段类型（不解析字符串 numeric）。

    注意：`quality/rule_engine._infer_type` 有一份支持字符串 numeric 解析的
    增强副本，二者独立内聚、无相互依赖；改动本函数须一并核对规则引擎副本。
    """
    if isinstance(val, bool):
        return "string"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "float"
    return "string"


def _infer_csv_type(vals: list[str]) -> str:
    """从字符串采样值推断字段类型。"""
    non_empty = [v.strip() for v in vals if v.strip()]
    if not non_empty:
        return "string"

    # 尝试 integer
    int_count = sum(1 for v in non_empty if _is_integer(v))
    if int_count / len(non_empty) > 0.8:
        return "integer"

    # 尝试 float
    float_count = sum(1 for v in non_empty if _is_float(v))
    if float_count / len(non_empty) > 0.8:
        return "float"

    return "string"


def _is_integer(s: str) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        return False


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _dbf_type(c: str) -> str:
    return {
        'C': 'string', 'N': 'float', 'F': 'float',
        'I': 'integer', 'L': 'string', 'D': 'date',
        'T': 'date', 'M': 'string', 'G': 'string',
        'B': 'string',
    }.get(c, 'string')


def _kml_type(t: str) -> str:
    return {
        'string': 'string', 'int': 'integer', 'integer': 'integer',
        'float': 'float', 'double': 'float', 'bool': 'string',
    }.get(t.lower(), 'string')
