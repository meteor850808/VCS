"""全局常量定义。"""

# ── 格式与转换 ──────────────────────────────────────────────────────────────────

# 输出格式 → 文件扩展名映射
OUTPUT_SUFFIX_MAP: dict[str, str] = {
    "GeoJSON": ".geojson", "SHP": ".shp", "GPKG": ".gpkg",
    "KML": ".kml", "GML": ".gml", "DXF": ".dxf",
}

# 支持的输出格式列表
SUPPORTED_OUTPUT_FORMATS: list[str] = list(OUTPUT_SUFFIX_MAP.keys())

# ── 安全检查 ────────────────────────────────────────────────────────────────────

# 禁止上传的可执行/危险扩展名（完整版，含压缩包）
# 压缩包单独归档，data.py 中用 DANGEROUS_EXTENSIONS - ARCHIVE_EXTENSIONS
DANGEROUS_EXTENSIONS = frozenset({
    '.exe', '.bat', '.cmd', '.com', '.msi', '.scr', '.ps1',
    '.sh', '.bash', '.vbs', '.js', '.jar', '.py', '.php', '.asp', '.aspx',
    '.jsp', '.war', '.zip', '.rar', '.7z', '.tar', '.gz', '.tgz',
})

# 压缩包扩展名（数据存储中允许上传但会被解压处理）
ARCHIVE_EXTENSIONS = frozenset({'.zip', '.rar', '.7z', '.tar', '.gz', '.tgz'})

# 关键字段列表（大小写不敏感匹配）
CRITICAL_FIELDS = [
    "YWBH", "FEATURECODE", "ywbh", "featurecode", "FEATURE_CODE",
    "业务编号", "特征码",
]
