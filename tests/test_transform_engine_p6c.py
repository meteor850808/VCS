# -*- coding: utf-8 -*-
"""transform_engine（统一坐标变换引擎）P6c 服务级补全测试。

覆盖目标（transform_engine.py → 100%）：
    - resolve_transform_crs：空串 / Custom:code 已知·未知 / 内置 code /
      generic EPSG·自定义·fallback·odd-source else
    - _pyproj_crs_for：非 custom 短路 / 库查询异常兜底
    - TransformStats.ok / _extent_of 空枝 / _coords_finite 空枝 /
      _transform_coord_tree 空坐标 / _srid_is_geographic 49xx
    - transform_geometry：变换异常捕获 / 目标地理越界告警
    - transform_feature_dict：无几何 / 变换失败
    - transform_db_features：真实分区表全流程（含 empty 几何 skipped、异常计数）
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import transform_engine as te
from app.services.custom_crs import SridResolution
from app.services.transform_engine import ResolvedCRS, TransformStats

SYNC_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/vcs_test"

WGS = ResolvedCRS(srid=4326, pyproj_crs="EPSG:4326", epsg=4326,
                  is_custom=False, source="epsg", display="EPSG:4326")
MERC = ResolvedCRS(srid=3857, pyproj_crs="EPSG:3857", epsg=3857,
                   is_custom=False, source="epsg", display="EPSG:3857")


@pytest.fixture
def sync_session():
    eng = create_engine(SYNC_URL)
    sess = Session(eng)
    yield sess
    sess.close()
    eng.dispose()


@pytest.fixture
def track_srids(sync_session):
    """记录测试产生的 custom 行 srid，teardown 清理（幂等）。"""
    created = []

    def _track(custom_ids, other_ids=None):
        created.append((list(custom_ids), list(other_ids or [])))

    yield _track
    sync_session.commit()
    for custom, other in created:
        ids = custom + other
        if ids:
            sync_session.execute(
                text("DELETE FROM spatial_ref_sys WHERE srid = ANY(:ids)"),
                {"ids": ids},
            )
            sync_session.commit()


# ── resolve_transform_crs ───────────────────────────────────────────────────

class TestResolveTransformCrs:

    async def test_empty_fallback(self, db_session):
        """72-75：空串 → fallback-empty。"""
        r = await te.resolve_transform_crs(db_session, "")
        assert r.srid == 4326 and r.source == "fallback-empty"
        assert r.pyproj_crs == "EPSG:4326" and r.display == "EPSG:4326"

    async def test_custom_explicit(self, db_session, track_srids):
        """78-82：Custom:shanghaicity → 内置展开注册。"""
        r = await te.resolve_transform_crs(db_session, "Custom:shanghaicity")
        assert r.is_custom and r.display == "Custom:shanghaicity"
        assert r.srid >= 910000
        assert "tmerc" in r.pyproj_crs  # 纠正后库注册 proj4text
        track_srids([r.srid])

    async def test_custom_unknown(self, db_session):
        """83-84：未知 Custom:code → 通用解析（回退 4326）。"""
        r = await te.resolve_transform_crs(db_session, "Custom:no-such-code-xyz")
        assert r.srid == 4326 and r.display == "EPSG:4326"

    async def test_custom_builtin_code(self, db_session, track_srids):
        """87-89：内置 code 直接映射。"""
        r = await te.resolve_transform_crs(db_session, "shanghaicity")
        assert r.is_custom and r.display == "shanghaicity" and r.srid >= 910000
        track_srids([r.srid])

    async def test_generic_epsg(self, db_session):
        """91/141-143：EPSG:4549 → pyproj_crs=EPSG:4549。"""
        r = await te.resolve_transform_crs(db_session, "EPSG:4549")
        assert r.epsg == 4549 and r.pyproj_crs == "EPSG:4549"
        assert r.display == "EPSG:4549"

    async def test_generic_custom_proj4(self, db_session, track_srids):
        """144-146：自定义 proj4 → is_custom，回填库注册 proj4text。"""
        proj4 = ("+proj=tmerc +lat_0=0 +lon_0=121.275 +k=1 +x_0=0 "
                 "+y_0=-3457147.81 +ellps=krass +units=m +no_defs")
        r = await te.resolve_transform_crs(db_session, proj4)
        assert r.is_custom and "tmerc" in r.pyproj_crs
        track_srids([r.srid])

    async def test_generic_fallback_unparseable(self, db_session, monkeypatch):
        """147-148：source=fallback-* → pyproj_crs=EPSG:4326。"""
        # 真实不可解析串经 resolve_crs 兜底返回 epsg=4326 → source="epsg"；
        # 147 分支仅当 source 为 fallback-* 时触发，故 patch 解析结果。
        async def fake_resolve(db, s):
            return SridResolution(srid=4326, is_custom=False, epsg=None,
                                  source="fallback-unparseable")
        monkeypatch.setattr(te, "resolve_ingest_srid_async", fake_resolve)
        r = await te.resolve_transform_crs(db_session, "definitely-not-a-crs-!!!")
        assert r.source == "fallback-unparseable" and r.pyproj_crs == "EPSG:4326"

    async def test_generic_odd_source_else(self, db_session, monkeypatch):
        """149-150/151：非 epsg/非 custom/非 fallback → pyproj_crs=源文本。"""
        async def fake_resolve(db, s):
            return SridResolution(srid=4326, is_custom=False, epsg=None,
                                  source="mystery-source")
        monkeypatch.setattr(te, "resolve_ingest_srid_async", fake_resolve)
        r = await te.resolve_transform_crs(db_session, "WEIRD-INPUT")
        assert r.pyproj_crs == "WEIRD-INPUT"
        assert r.display == "EPSG:4326"  # 151：res.srid=4326


# ── _pyproj_crs_for ─────────────────────────────────────────────────────────

class TestPyprojCrsFor:

    async def test_not_custom_returns_fallback(self, db_session):
        """121-122：非自定义 → 直接 fallback。"""
        res = SridResolution(srid=4326, is_custom=False, epsg=4326, source="epsg")
        assert await te._pyproj_crs_for(db_session, res, "FALLBACK") == "FALLBACK"

    async def test_lookup_exception_fallback(self, db_session, monkeypatch):
        """130-132：库查询异常 → 兜底 fallback。"""
        async def boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(db_session, "execute", boom)
        res = SridResolution(srid=910123, is_custom=True, epsg=None, source="wkt-custom")
        assert await te._pyproj_crs_for(db_session, res, "FALLBACK-WKT") == "FALLBACK-WKT"


# ── 纯函数边角分支 ──────────────────────────────────────────────────────────

class TestTransformStats:

    def test_ok_property(self):
        """228-230：failed==0 → ok。"""
        assert TransformStats().ok
        assert not TransformStats(failed=1).ok


class TestExtentOf:

    def test_empty_coords(self):
        """248：coordinates 空 → None。"""
        assert te._extent_of({"type": "Point", "coordinates": []}) is None

    def test_no_coords_key(self):
        """257：无 coordinates 键 → None。"""
        assert te._extent_of({}) is None

    def test_no_numeric_leaves(self):
        """261：无数值叶子 → None。"""
        assert te._extent_of({"type": "X", "coordinates": [[], []]}) is None

    def test_ok(self):
        assert te._extent_of({"type": "Point", "coordinates": [121, 31]}) == (121, 31, 121, 31)


class TestCoordsFinite:

    def test_empty_coords(self):
        """271：空坐标 → 有限。"""
        assert te._coords_finite({"type": "LineString", "coordinates": []}) is True

    def test_none_coords(self):
        """281-282：无 coordinates → 有限。"""
        assert te._coords_finite({"type": "Point"}) is True

    def test_inf_rejected(self):
        assert te._coords_finite({"type": "Point", "coordinates": [float("inf"), 1]}) is False


class TestTransformCoordTree:

    def test_empty_coords_noop(self):
        """303-305：空坐标直接返回。"""
        g = {"type": "LineString"}
        te._transform_coord_tree(g, None)  # transformer 不会被调用
        assert g["type"] == "LineString"


class _BoomTransformer:
    """transform 必抛（用于覆盖 transform 异常捕获分支）。"""

    def transform(self, x, y):
        raise RuntimeError("boom")


class TestTransformGeometry:

    def test_transform_exception_caught(self, monkeypatch):
        """368-373：变换过程异常 → 捕获并返回 None + 告警。"""
        import pyproj
        monkeypatch.setattr(pyproj.Transformer, "from_crs",
                            lambda *a, **k: _BoomTransformer())
        w = []
        out = te.transform_geometry({"type": "Point", "coordinates": [121.0, 31.0]},
                                    WGS, WGS, warnings=w)
        assert out is None
        assert any("坐标变换失败" in x and "boom" in x for x in w)

    def test_dst_out_of_domain_warn(self):
        """390-398：目标为地理但结果越界 → 告警（仍返回几何）。"""
        w = []
        out = te.transform_geometry({"type": "Point", "coordinates": [200.0, 5.0]},
                                    WGS, WGS, warnings=w)
        assert out is not None  # 恒等变换成功
        assert any("结果疑似异常" in x for x in w)

    def test_z_kept(self):
        """288-292：Z 维保留。"""
        out = te.transform_geometry({"type": "Point", "coordinates": [121.0, 31.0, 100.0]},
                                    WGS, MERC)
        assert out["coordinates"][2] == 100.0 and len(out["coordinates"]) == 3


class TestSridIsGeographic:

    def test_49xx_range(self):
        """416-417：49xx 区段 → 地理。"""
        assert te._srid_is_geographic(4908) is True
        assert te._srid_is_geographic(4978) is True


class TestTransformFeatureDict:

    def test_no_geometry(self):
        """426-428：无 geometry → None。"""
        assert te.transform_feature_dict({"type": "Feature", "properties": {}},
                                         WGS, WGS) is None

    def test_transform_failed(self, monkeypatch):
        """429-431：几何变换失败 → None。"""
        import pyproj
        monkeypatch.setattr(pyproj.Transformer, "from_crs",
                            lambda *a, **k: _BoomTransformer())
        bad = {"type": "Feature",
               "geometry": {"type": "Point", "coordinates": [121.0, 31.0]},
               "properties": {}}
        assert te.transform_feature_dict(bad, WGS, WGS) is None


# ── transform_db_features（真实分区表全流程）────────────────────────────────

class TestTransformDbFeatures:

    FID = "p6c-fid"

    async def _create_partition(self, db):
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS point_features_p6c PARTITION OF point_features "
            "FOR VALUES IN ('" + self.FID + "')"
        ))

    async def _drop_partition(self, db):
        await db.execute(text("DROP TABLE IF EXISTS point_features_p6c"))
        await db.commit()

    async def test_real_transform(self, db_session):
        """487-536：点+线变换成功，含 _feature_table。"""
        await self._create_partition(db_session)
        await db_session.execute(text(
            "INSERT INTO point_features (id, stored_file_id, layer_name, source_name, "
            "feature_index, geometry_type, geometry, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :fid, 'L', 'src', 1, 'Point', "
            "ST_GeomFromText('POINT(121.5 31.2)', 4326), now(), now()), "
            "(gen_random_uuid(), :fid, 'L', 'src', 2, 'LineString', "
            "ST_GeomFromText('LINESTRING(121.5 31.2, 121.6 31.3)', 4326), now(), now())"
        ), {"fid": self.FID})
        await db_session.commit()
        try:
            src = await te.resolve_transform_crs(db_session, "EPSG:4549")
            dst = await te.resolve_transform_crs(db_session, "EPSG:4326")
            feats, stats = await te.transform_db_features(
                db_session, self.FID, src, dst, include_table_name=True,
            )
            assert stats.total == 2 and stats.transformed == 2
            assert stats.failed == 0 and stats.skipped == 0
            assert len(feats) == 2
            assert feats[0]["type"] == "Feature"
            assert feats[0]["properties"]["_feature_table"] == "point_features"
        finally:
            await db_session.commit()  # 结束 transform_db_features 的读事务
            await self._drop_partition(db_session)

    async def test_empty_geometry_skipped(self, db_session):
        """507-513：POINT EMPTY → skipped。"""
        await self._create_partition(db_session)
        await db_session.execute(text(
            "INSERT INTO point_features (id, stored_file_id, layer_name, source_name, "
            "feature_index, geometry_type, geometry, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :fid, 'L', 'src', 3, 'Point', "
            "ST_GeomFromText('POINT EMPTY', 4326), now(), now())"
        ), {"fid": self.FID})
        await db_session.commit()
        try:
            feats, stats = await te.transform_db_features(
                db_session, self.FID, WGS, WGS,
            )
            assert stats.total == 1 and stats.skipped == 1
            assert feats == []
        finally:
            await db_session.commit()
            await self._drop_partition(db_session)

    async def test_no_rows(self, db_session):
        """空 fid（三表均无分区无行）→ total=0。"""
        feats, stats = await te.transform_db_features(db_session, "no-such-fid", WGS, WGS)
        assert stats.total == 0 and feats == []

    async def test_null_geometry_skipped(self, db_session):
        """507-509：geometry 为 NULL → wkt 空 → skipped。"""
        await self._create_partition(db_session)
        await db_session.execute(text(
            "INSERT INTO point_features (id, stored_file_id, layer_name, source_name, "
            "feature_index, geometry_type, geometry, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :fid, 'L', 'src', 5, 'Point', NULL, now(), now())"
        ), {"fid": self.FID})
        await db_session.commit()
        try:
            feats, stats = await te.transform_db_features(db_session, self.FID, WGS, WGS)
            assert stats.total == 1 and stats.skipped == 1 and feats == []
        finally:
            await db_session.commit()
            await self._drop_partition(db_session)

    async def test_domain_failure_counts_failed(self, db_session):
        """514-521：变换结果 inf（米制误标地理）→ failed。"""
        await self._create_partition(db_session)
        # Web Mercator 米制坐标被当作地理经纬度 → 变换产出 inf → transform_geometry None
        await db_session.execute(text(
            "INSERT INTO point_features (id, stored_file_id, layer_name, source_name, "
            "feature_index, geometry_type, geometry, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :fid, 'L', 'src', 6, 'Point', "
            "ST_GeomFromText('POINT(34000000 5000000)', 4326), now(), now())"
        ), {"fid": self.FID})
        await db_session.commit()
        try:
            feats, stats = await te.transform_db_features(
                db_session, self.FID, WGS, MERC,
            )
            assert stats.total == 1 and stats.failed == 1 and feats == []
        finally:
            await db_session.commit()
            await self._drop_partition(db_session)

    async def test_exception_counts_failed(self, db_session, monkeypatch):
        """530-533：transform_geometry 抛异常 → failed 计数。"""
        await self._create_partition(db_session)
        await db_session.execute(text(
            "INSERT INTO point_features (id, stored_file_id, layer_name, source_name, "
            "feature_index, geometry_type, geometry, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :fid, 'L', 'src', 4, 'Point', "
            "ST_GeomFromText('POINT(121.5 31.2)', 4326), now(), now())"
        ), {"fid": self.FID})
        await db_session.commit()

        def boom(*a, **k):
            raise RuntimeError("transform boom")
        monkeypatch.setattr(te, "transform_geometry", boom)
        try:
            feats, stats = await te.transform_db_features(
                db_session, self.FID, WGS, WGS,
            )
            assert stats.total == 1 and stats.failed == 1
            assert feats == []
        finally:
            await db_session.commit()
            await self._drop_partition(db_session)