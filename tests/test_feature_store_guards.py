# -*- coding: utf-8 -*-
"""load_file_features_geojson 守卫分支测试（P7 覆盖回跌收口，全库最后 2 miss）。

目标: app/services/feature_store.py L512-516
- wkt_str 为空 → continue（feature 被跳过）
- geometry 为 empty → continue（feature 被跳过）
"""
import pytest


class _Feat:
    def __init__(self, fid, geom, props=None, layer_name="L"):
        self.id = fid
        self.geometry = geom
        self.properties = props or {}
        self.layer_name = layer_name
        # 模拟 SQLAlchemy 表述对象
        self.tbl_id_attr = fid


class _ScalarResult:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v


class _ScalarsAll:
    def __init__(self, feats):
        self._f = feats

    def scalars(self):
        return self

    def all(self):
        return self._f


class _FakeDb:
    def __init__(self, feats, wkt_values):
        self.feats = feats
        self.wkt_values = wkt_values
        self.i = 0

    async def execute(self, stmt):
        s = str(stmt)
        if "ST_AsText" in s:
            val = self.wkt_values[self.i] if self.i < len(self.wkt_values) else None
            self.i += 1
            return _ScalarResult(val)
        return _ScalarsAll(self.feats)


class TestLoadFeaturesGuards:
    @pytest.mark.asyncio
    async def test_skip_null_wkt(self):
        """ST_AsText 返回 None → feature 跳过（L512-513）。"""
        from app.services.feature_store import load_file_features_geojson

        feat = _Feat("f1", "POINT(1 1)")
        db = _FakeDb([feat], [None])  # 第一个 feature 的 wkt 为空
        result = await load_file_features_geojson(db, "file-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_empty_geometry(self):
        """有效的空几何 WKT → feature 跳过（L515-516）。"""
        from app.services.feature_store import load_file_features_geojson

        feat = _Feat("f2", "POINT(1 1)")
        db = _FakeDb([feat], ["POLYGON EMPTY"])
        result = await load_file_features_geojson(db, "file-2")
        assert result == []

    @pytest.mark.asyncio
    async def test_happy_path_feature_included(self):
        """正常 feature → 组装 GeoJSON（含 layer_name 补全）。"""
        from app.services.feature_store import load_file_features_geojson

        feat = _Feat("f3", "POINT(1 1)", props={"name": "A"})
        db = _FakeDb([feat], ["POINT (1 1)"])
        result = await load_file_features_geojson(db, "file-3")
        assert len(result) == 1
        f = result[0]
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        assert f["properties"]["name"] == "A"
        assert f["properties"]["layer_name"] == "L"