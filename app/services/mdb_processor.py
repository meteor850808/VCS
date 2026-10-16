"""MDB (ESRI Personal Geodatabase) 处理器。

读取 .mdb 文件中的空间表和属性表，提取为 GeoJSON features。

设计说明：
- MDB 是 ESRI Personal Geodatabase 格式（.mdb），基于 Microsoft Access。
- 使用 pyodbc + Microsoft Access Driver (ODBC) 连接读取。
- 空间数据存储在 'Shape' / 'Geom' / 'Geometry' 列中，编码为
  ESRI shapebin BLOB（即 shapefile 二进制片段），而非 OGC WKB。
- 每个空间表提取为一组 GeoJSON features，用于后续入库 PostGIS。
"""

import json
import logging
from typing import Any

from app.services._odbc import access_conn_str
from app.services.esri_shapebin import decode_esri_shapebin

logger = logging.getLogger(__name__)

# ESRI 系统表前缀/后缀（不包含用户空间数据）
_ESRI_SYSTEM_PREFIXES = ('GDB_', 'MSys', 'SDE_')
_ESRI_SYSTEM_SUFFIXES = ('_SHAPE_Index', '_Shape_Index', '_SHAPE_INDEX')


def _is_esri_system_table(table_name: str) -> bool:
    """判断是否为 ESRI 内建系统表（不包含用户空间数据）。"""
    return (table_name.startswith(_ESRI_SYSTEM_PREFIXES)
            or table_name.endswith(_ESRI_SYSTEM_SUFFIXES))


def extract_mdb_features(mdb_path: str) -> list[dict[str, Any]]:
    """从 MDB 文件中提取所有空间表的要素。

    Args:
        mdb_path: MDB 文件路径

    Returns:
        [{"table_name": str, "geometry_type": str, "features": [...], "fields": [...]}]
    """
    import pyodbc

    results = []

    try:
        conn_str = access_conn_str(mdb_path)
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        crs_map = _load_crs_map(cursor)

        tables = [row.table_name for row in cursor.tables(tableType='TABLE')]
        logger.info(f"Found {len(tables)} tables in MDB: {tables}")

        for table_name in tables:
            if _is_esri_system_table(table_name):
                continue

            try:
                cursor.execute(f"SELECT * FROM [{table_name}]")
                columns = [desc[0] for desc in cursor.description]

                has_shape = any(_is_geometry_column(col) for col in columns)
                if not has_shape:
                    logger.debug(f"Table {table_name} has no geometry column, skipping")
                    continue

                rows = cursor.fetchall()
                features = []

                for row in rows:
                    row_dict = dict(zip(columns, row))

                    shape_data = None
                    shape_col = None
                    for col in columns:
                        if _is_geometry_column(col):
                            shape_data = row_dict[col]
                            shape_col = col
                            break

                    if shape_data is None:
                        continue

                    geometry = _convert_shape_to_geojson(shape_data)
                    if geometry is None:
                        continue

                    properties = {k: v for k, v in row_dict.items()
                                 if not _is_geometry_column(k)}

                    # 修正 pyodbc 返回的 bytes 类型，确保 JSON 可序列化
                    properties = _serializable_properties(properties)

                    features.append({
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": properties
                    })

                if features:
                    geometry_type = _detect_geometry_type(features)

                    crs_srid, crs_wkt = crs_map.get(table_name, (None, None))

                    results.append({
                        "table_name": table_name,
                        "geometry_type": geometry_type,
                        "features": features,
                        "fields": columns,
                        "crs_srid": crs_srid,
                        "crs_wkt": crs_wkt,
                    })
                    logger.info(f"Extracted {len(features)} features from table {table_name}")

            except Exception as e:
                logger.warning(f"Failed to extract table {table_name}: {e}")
                continue

        conn.close()

    except pyodbc.Error as e:
        logger.error(f"Failed to connect to MDB: {e}")
        raise

    return results


def _load_crs_map(cursor) -> dict[str, tuple[int | None, str | None]]:
    """从 GDB_GeomColumns × GDB_SpatialRefs 读取各空间表的坐标系。

    返回 {table_name: (srid, srtext)}。GDB_GeomColumns 存放
    TableName → SRID 映射；GDB_SpatialRefs 存放 SRID → SRTEXT (ESRI WKT)。

    PGeo 坐标以投影米为单位直接存储（不经 FalseX/XYUnits 换算），
    但坐标系定义（SRTEXT）必须随数据一起入库，否则 PostGIS 会误判为
    WGS84 (4326) 导致下游变换错误。
    """
    result: dict[str, tuple[int | None, str | None]] = {}

    # SRID → SRTEXT 字典
    srid_to_srtext: dict[int, str] = {}
    try:
        cursor.execute("SELECT SRID, SRTEXT FROM GDB_SpatialRefs")
        for srid, srtext in cursor.fetchall():
            if srid is not None:
                srid_to_srtext[int(srid)] = str(srtext or "")
    except Exception as e:
        logger.warning(f"Failed to read GDB_SpatialRefs: {e}")

    # TableName → SRID 映射（只关心用户空间表）
    try:
        cursor.execute("SELECT TableName, SRID FROM GDB_GeomColumns")
        for table_name, srid in cursor.fetchall():
            if table_name is None:
                continue
            tname = str(table_name)
            srid_int = int(srid) if srid is not None else None
            result[tname] = (srid_int, srid_to_srtext.get(srid_int))
    except Exception as e:
        logger.warning(f"Failed to read GDB_GeomColumns: {e}")

    return result


def extract_mdb_to_stored_files(
    content: bytes,
    filename: str,
    api_key_id: str,
    max_bytes: int
    ) -> list[dict[str, Any]]:
    """将 MDB 转换为 StoredFile 字典列表（每个空间表一个）。

    Args:
        content: MDB 文件二进制内容
        filename: 原始文件名
        api_key_id: API Key ID
        max_bytes: 最大文件大小限制

    Returns:
        [{"stored_file": dict, "features": list}]
        stored_file 字典包含 StoredFile 构造所需的全部字段
    """
    import hashlib
    import tempfile
    import uuid
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix='.mdb', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        tables = extract_mdb_features(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    results = []
    for table_data in tables:
        table_name = table_data["table_name"]
        features = table_data["features"]
        geometry_type = table_data.get("geometry_type", "Unknown")
        fields = table_data.get("fields", [])
        crs_wkt = table_data.get("crs_wkt")

        geojson_dict = {
            "type": "FeatureCollection",
            "features": features
        }
        geojson_bytes = json.dumps(geojson_dict, ensure_ascii=False).encode('utf-8')

        if len(geojson_bytes) > max_bytes:
            logger.warning(f"Table {table_name} GeoJSON ({len(geojson_bytes)} bytes) exceeds limit ({max_bytes})")
            continue

        # 计算 file_hash 用于去重
        file_hash = hashlib.sha256(geojson_bytes).hexdigest()

        # 返回字典而非 StoredFile 对象，让调用方构造
        stored_file_dict = {
            "id": str(uuid.uuid4()),
            "api_key_id": api_key_id,
            "original_name": f"{filename}/{table_name}.geojson",
            "content_type": "application/geo+json",
            "file_size": len(geojson_bytes),
            "file_data": geojson_bytes,
            "file_hash": file_hash,
            "source_archive": filename,
            "is_archive": False,
            "data_type": "spatial",
            "geometry_type": geometry_type,
            "file_format": "GeoJSON",
            "field_schema": {"fields": [{"name": f, "type": "string"} for f in fields]},
        }
        # MDB 内部的坐标系定义（ESRI WKT，来自 GDB_SpatialRefs.SRTEXT）。
        # 交给 resolve_ingest_srid 映射 EPSG 或注册自定义 SRID，
        # 避免 GK 米制坐标被当作 WGS84 入库。
        if crs_wkt:
            stored_file_dict["detected_crs"] = crs_wkt

        results.append({"stored_file": stored_file_dict, "features": features})

    return results


def _is_geometry_column(col: str) -> bool:
    """判断是否为几何列。"""
    return col.lower() in ('shape', 'geom', 'geometry')


def _serializable_properties(properties: dict) -> dict:
    """将属性值转换为 JSON 可序列化类型。

    pyodbc 对某些字段（如 OLE Object 列）会返回 bytes，
    直接 json.dumps 会失败，这里统一转换。
    """
    result = {}
    for k, v in properties.items():
        if isinstance(v, bytes):
            result[k] = v.hex()
        elif isinstance(v, (int, float, str, bool)) or v is None:
            result[k] = v
        else:
            result[k] = str(v)
    return result


def _convert_shape_to_geojson(shape_data: Any) -> dict | None:
    """将 PGeo SHAPE 列数据（ESRI shapebin BLOB）转换为 GeoJSON geometry。

    Args:
        shape_data: SHAPE 列数据（shapebin bytes / WKB bytes / WKT str）

    Returns:
        GeoJSON geometry dict or None
    """
    try:
        if isinstance(shape_data, (bytes, bytearray, memoryview)):
            data = bytes(shape_data)
            # 优先按 PGeo shapebin 解码；若类型字节为 0x01 开头且后续
            # 是标准 WKB 结构（可能来自 GDB_Items 等），回退到 WKB 尝试
            geom = decode_esri_shapebin(data)
            if geom is not None:
                return geom

            # 回退：standard WKB（误差容错）
            from shapely import wkb
            geom = wkb.loads(data)
            return json.loads(geom.to_geojson() if hasattr(geom, 'to_geojson')
                              else __import__('shapely').to_geojson(geom))
        elif isinstance(shape_data, str):
            from shapely import wkt
            import shapely
            geom = wkt.loads(shape_data)
            return json.loads(shapely.to_geojson(geom))
        else:
            return None
    except Exception as e:
        logger.debug(f"Failed to convert shape data: {e}")
        return None


def _detect_geometry_type(features: list[dict]) -> str:
    """检测几何类型。

    Args:
        features: GeoJSON Feature 列表

    Returns:
        几何类型字符串
    """
    from collections import Counter

    type_counter = Counter()
    for feature in features:
        geom = feature.get("geometry", {})
        geom_type = geom.get("type", "Unknown")
        type_counter[geom_type] += 1

    if not type_counter:
        return "Unknown"

    return type_counter.most_common(1)[0][0]
