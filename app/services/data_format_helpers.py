"""数据格式检测纯函数域 — data.py 拆分 Lane① (v2.22.0)。

零路由装饰器、零 DB Session 依赖，纯函数 + 常量表，可独立单测 / 契约守卫。
触发源：BASELINE ADR-0001（路由级覆盖安全网达标后拆分 data.py）；data.py
顶部区块（VECTOR_EXTENSIONS / MIME_MAP / _guess_mime / _is_vector_file）迁移。
"""

from datetime import date, datetime, timezone
from pathlib import Path


VECTOR_EXTENSIONS = frozenset({
    '.geojson', '.json', '.shp', '.shx', '.kml', '.kmz', '.gml',
    '.dxf', '.dgn', '.dwg', '.gpkg', '.tab', '.mif',
})
# 注意: .csv/.xlsx 不在 VECTOR_EXTENSIONS 中 — 分片上传时按 table 处理，不尝试空间展开

MIME_MAP = {
    '.geojson': 'application/geo+json',
    '.json': 'application/json',
    '.xml': 'application/xml',
    '.html': 'text/html',
    '.csv': 'text/csv',
    '.shp': 'application/x-esri-shape',
    '.dxf': 'application/dxf',
    '.kml': 'application/vnd.google-earth.kml+xml',
    '.gpkg': 'application/geopackage+sqlite3',
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.pdf': 'application/pdf',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.obj': 'model/obj',
    '.mdb': 'application/x-msaccess',
}


def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return MIME_MAP.get(ext, 'application/octet-stream')


def _is_vector_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in VECTOR_EXTENSIONS


def _parse_date_bound(value: str, *, day_start: bool) -> datetime:
    """解析 'YYYY-MM-DD' 为 UTC aware datetime 边界。

    day_start=True → 当日 00:00:00（from 侧）
    day_start=False → 当日 23:59:59.999999（to 侧钳至日界 23:59:59）
    """
    d = date.fromisoformat(value)
    if day_start:
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _empty_feature_stats(stored_file_id: str) -> dict:
    """子任务返回的统一统计结构（纯函数）。"""
    return {
        "stored_file_id": stored_file_id,
        "written": 0,
        "skipped_empty": 0,
        "skipped_no_geometry": 0,
        "geometry_type": None,
        "error": None,
    }


def _collect_sub_task_results(results: list) -> tuple[dict, list]:
    """将 asyncio.gather 结果转为 (stats_by_file, errors)（纯函数）。

    - dict 结果：按 stored_file_id 收拢统计，error 字段并入 errors
    - Exception 结果：并入 errors（理论上不会发生，子任务内部已兜底）
    """
    stats_by_file: dict = {}
    errors: list = []
    for r in results:
        if isinstance(r, dict):
            stats_by_file[r.get("stored_file_id")] = r
            if r.get("error"):
                errors.append(r["error"])
        elif isinstance(r, Exception):
            errors.append(str(r))
    return stats_by_file, errors
