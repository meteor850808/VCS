"""CRS 坐标变换 — pyproj 集成 + 中文别名解析 + 文件 CRS 提取。"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CRSInfo:
    epsg: int | None = None
    wkt: str | None = None
    proj4: str | None = None


# 中文 / 常用别名
CRS_ALIASES: dict[str, str] = {
    # 中国常用
    "国家2000":  "EPSG:4490",
    "cgcs2000":  "EPSG:4490",
    "北京54":    "EPSG:4214",
    "beijing54": "EPSG:4214",
    "西安80":    "EPSG:4610",
    "xian80":    "EPSG:4610",
    "wgs84":     "EPSG:4326",
    "wgs 84":    "EPSG:4326",
    # Web 墨卡托
    "百度":      "EPSG:3857",
    "web墨卡托": "EPSG:3857",
    "web mercator": "EPSG:3857",
    "3857":      "EPSG:3857",
    "4326":      "EPSG:4326",
    "4490":      "EPSG:4490",
    # 中国2000 其他表达
    "china2000": "EPSG:4490",
    "china 2000": "EPSG:4490",
    "大地2000":  "EPSG:4490",
}


def resolve_crs(crs_str: str) -> CRSInfo:
    """解析 CRS 字符串，返回 CRSInfo。支持 EPSG:xxxx、别名、WKT。"""
    if not crs_str:
        return CRSInfo(epsg=4326)

    s = crs_str.strip()

    # 别名查表
    alias_result = CRS_ALIASES.get(s.lower())
    if alias_result:
        s = alias_result

    # EPSG:xxxx 格式
    if s.upper().startswith("EPSG:"):
        try:
            return CRSInfo(epsg=int(s.split(":")[1]))
        except (IndexError, ValueError):
            pass

    # 纯数字 → 视为 EPSG 编码
    if s.isdigit():
        return CRSInfo(epsg=int(s))

    # 尝试 pyproj 解析
    try:
        from pyproj import CRS
        crs = CRS.from_user_input(s)
        epsg = crs.to_epsg()
        if epsg:
            return CRSInfo(epsg=epsg, proj4=crs.to_proj4())
        return CRSInfo(wkt=crs.to_wkt(), proj4=crs.to_proj4())
    except Exception:
        pass

    # 回退：保留原始字符串作为 WKT
    if s.startswith(("GEOGCS", "PROJCS", "COMPD_CS")):
        return CRSInfo(wkt=s)
    return CRSInfo(epsg=4326)


def extract_crs_from_prj(prj_path: str) -> CRSInfo | None:
    """从 .prj 文件读取 WKT 并解析为 CRSInfo。"""
    try:
        with open(prj_path, encoding='utf-8', errors='replace') as f:
            wkt = f.read().strip()
        if not wkt:
            return None
        return resolve_crs(wkt)
    except Exception:
        return None


def crs_to_srid(crs_str: str | None) -> int:
    """将 CRS 描述解析为 SRID 整型；无法解析时回退 4326。

    支持 EPSG:xxxx、纯数字、中文别名、pyproj 可解析的 WKT/URN
    （如 .prj 文件全文、GeoJSON crs 的 urn:ogc:def:crs:EPSG::4490）。
    """
    if not crs_str:
        return 4326
    info = resolve_crs(crs_str)
    return info.epsg if info.epsg else 4326


def transform_coords(x: float, y: float, src_crs: str, dst_crs: str) -> tuple[float, float]:
    """坐标点转换（使用 pyproj，always_xy=True 统一经纬度顺序）。

    always_xy=True：输入输出恒为 (lon, lat) / (x, y) 顺序，
    避免 EPSG:4326/4490 地理 CRS 默认 (lat, lon) 轴序导致坐标颠倒。
    """
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
        nx, ny = transformer.transform(x, y)
        return float(nx), float(ny)
    except Exception as e:
        logger.warning("CRS 变换失败 %s -> %s: %s", src_crs, dst_crs, e)
        return x, y


def get_supported_crs_list() -> list[str]:
    return [
        "EPSG:4326",    # WGS 84
        "EPSG:4490",    # CGCS 2000
        "EPSG:4214",    # Beijing 1954
        "EPSG:4610",    # Xian 1980
        "EPSG:3857",    # Web Mercator
        "EPSG:4547",    # CGCS2000 / 3-degree Gauss-Kruger CM 114E
        "EPSG:4548",    # CGCS2000 / 3-degree Gauss-Kruger CM 117E
        "EPSG:4549",    # CGCS2000 / 3-degree Gauss-Kruger CM 120E
        "EPSG:4550",    # CGCS2000 / 3-degree Gauss-Kruger CM 123E
    ]
