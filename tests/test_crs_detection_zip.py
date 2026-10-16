"""回归测试：ZIP 解压 SHP 的 CRS 识别（v2.13.x 修复）。

背景：从压缩包解压的 Shapefile，其配套 .prj（投影坐标系 WKT）在格式检测时未被
一并写入临时目录，导致 detected_crs=None → SRID 回退 4326，前端源 CRS 显示错误。
"""
import os
import tempfile
from unittest.mock import MagicMock

SHANGHAI_WKT = (
    'PROJCS["ShanghaiCity",GEOGCS["GCS_Beijing_1954",'
    'DATUM["D_Beijing_1954",SPHEROID["Krasovsky_1940",6378245,298.3]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",0],PARAMETER["False_Northing",-3457147.81],'
    'PARAMETER["Central_Meridian",121.275],PARAMETER["Scale_Factor",1],'
    'PARAMETER["Latitude_Of_Origin",0],UNIT["meter",1]]'
)


def test_extract_crs_reads_prj_for_shp():
    from app.services.format_detection import _extract_crs

    with tempfile.TemporaryDirectory() as td:
        shp = os.path.join(td, "layer.shp")
        with open(shp, "wb") as f:
            f.write(b"dummy")
        with open(os.path.join(td, "layer.prj"), "w", encoding="utf-8") as f:
            f.write(SHANGHAI_WKT)
        crs = _extract_crs(shp, "SHP")
        assert crs and "PROJCS" in crs


def test_extract_crs_returns_none_without_prj():
    from app.services.format_detection import _extract_crs

    with tempfile.TemporaryDirectory() as td:
        shp = os.path.join(td, "layer.shp")
        with open(shp, "wb") as f:
            f.write(b"dummy")
        # 无 .prj
        assert _extract_crs(shp, "SHP") is None


def test_detect_and_populate_writes_companion_prj(monkeypatch):
    """companion .prj 应被写入临时目录，使 detect_format/_extract_crs 能读到。"""
    from app.api.v1.data import _detect_and_populate

    captured_tmp = {}

    def fake_detect_format(tmp_path):
        # 模拟 detect_format：断言临时目录里存在 .prj
        prj = os.path.join(os.path.dirname(tmp_path), "layer.prj")
        captured_tmp["prj_exists"] = os.path.exists(prj)
        if os.path.exists(prj):
            captured_tmp["prj_text"] = open(prj, encoding="utf-8").read()
        from app.services.format_detection import FormatResult
        res = FormatResult(format_name="SHP", encoding="UTF-8", confidence=1.0)
        res.crs_info = open(prj, encoding="utf-8").read() if os.path.exists(prj) else None
        return res

    monkeypatch.setattr("app.api.v1.data.detect_format", fake_detect_format)
    sf = MagicMock()
    _detect_and_populate(
        sf, b"dummy-shp", "layer.shp",
        {".prj": SHANGHAI_WKT.encode("utf-8"), ".dbf": b"dummy-dbf"},
    )
    assert captured_tmp.get("prj_exists") is True
    assert captured_tmp.get("prj_text") == SHANGHAI_WKT
    assert sf.detected_crs is not None and "PROJCS" in sf.detected_crs


def test_detect_and_populate_without_companion_no_prj(monkeypatch):
    """直接上传（无 companion）时，detect_format 临时目录无 .prj —— 保持原行为。"""
    from app.api.v1.data import _detect_and_populate

    captured_tmp = {}

    def fake_detect_format(tmp_path):
        prj = os.path.join(os.path.dirname(tmp_path), "layer.prj")
        captured_tmp["prj_exists"] = os.path.exists(prj)
        from app.services.format_detection import FormatResult
        res = FormatResult(format_name="SHP", encoding="UTF-8", confidence=1.0)
        res.crs_info = None
        return res

    monkeypatch.setattr("app.api.v1.data.detect_format", fake_detect_format)
    sf = MagicMock()
    _detect_and_populate(sf, b"dummy-shp", "layer.shp")
    assert captured_tmp.get("prj_exists") is False
    assert sf.detected_crs is None
