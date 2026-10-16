"""P5m: feature_store.py 服务级全覆盖（53% → 100%）。

数据管道三服务的最后一块：P5k 全程 monkeypatch `write_features_batch(_sync)` 等
入口，feature_store 本体（分区管理 / 要素写入 / 图层聚合 / 跨表查询 / CRS 变换 /
几何修复）从未被真实走一遍。

策略：
- sync 分区管理/写入：`_FakeSyncSession`（sync execute 直接返回结果）
- async 路径：`_FakeDB`（async execute 支持 await）
- transform / repair：真实 shapely + monkeypatch `transform_engine`
- 1 条真实 PostGIS 冒烟（test_db fixture）

覆盖目标：54-65、104-114、123、163、197、214-232、395-437、448-486。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.feature_store as fs
from app.models.spatial_feature import (
    ALL_FEATURE_TABLES,
    PointFeature,
    LineFeature,
    PolygonFeature,
)


# ── fake 会话体系 ────────────────────────────────────────────────────────────

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows or []

    def all(self):
        return self._rows


class _FakeResult:
    """结果容器：支持 `.scalar()` / `.scalars().all()` / `.one()` / `.all()`。"""

    def __init__(self, scalar_val=None, rows=None, scalars_rows=None):
        self._sv = scalar_val
        self._rows = rows or []
        self._sr = scalars_rows

    def scalar(self):
        return self._sv

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0] if self._rows else (None, None)

    def scalars(self):
        return _FakeScalars(self._sr)


class _FakeSyncSession:
    """同步 ORM Session 替身：`session.execute()` 直接返回结果。

    用于 _ensure_partition_sync / drop_partition_sync / write_features_batch_sync。
    """

    def __init__(self, results=None):
        self._queue = list(results or [])
        self.executed: list[str] = []
        self.added: list = []
        self.flushed = 0

    def _next(self):
        return self._queue.pop(0) if self._queue else _FakeResult()

    def execute(self, stmt, params=None):
        self.executed.append(str(stmt))
        return self._next()

    def flush(self):
        self.flushed += 1

    def add(self, obj):
        self.added.append(obj)


class _FakeDB:
    """AsyncSession 替身：`await db.execute()` 返回结果。

    用于 drop_partition / write_feature / write_features_batch /
    aggregate_layer_stats / count_by_file / query_features_by_file /
    transform / repair。
    """

    def __init__(self, results=None):
        self._queue = list(results or [])
        self.executed: list[str] = []
        self.added: list = []
        self.flushed = 0

    def _next(self):
        return self._queue.pop(0) if self._queue else _FakeResult()

    async def execute(self, stmt, params=None):
        self.executed.append(str(stmt))
        return self._next()

    async def flush(self):
        self.flushed += 1

    def add(self, obj):
        self.added.append(obj)


# ── helpers ──────────────────────────────────────────────────────────────────

def _pt_feat(x=1.0, y=2.0, layer="L1"):
    return {
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": {"n": 1},
        "feature_index": 0,
        "layer_name": layer,
    }


def _scalres(*feats) -> _FakeResult:
    return _FakeResult(scalars_rows=list(feats))


def _wktres(wkt) -> _FakeResult:
    return _FakeResult(scalar_val=wkt)


def _cntrow(layer, c):
    return SimpleNamespace(layer_name=layer, c=c)


def _featrow(fid, fidx, geojson=None, props=None, layer="L"):
    return SimpleNamespace(
        id=fid, feature_index=fidx, layer_name=layer, source_name="f",
        geometry_type="x", geojson=geojson, properties=props or {},
    )


# ── 分区管理（sync）──────────────────────────────────────────────────────────

class TestEnsurePartitionSync:

    def test_creates_when_missing(self):
        db = _FakeSyncSession([_FakeResult(scalar_val=None), _FakeResult()])
        fs._ensure_partition_sync(db, PolygonFeature, "sid-1")
        creates = [s for s in db.executed if "CREATE TABLE IF NOT EXISTS" in s]
        assert creates, db.executed
        assert "polygon_features_p_sid_1" in creates[0]
        assert "FOR VALUES IN ('sid-1')" in creates[0]
        assert db.flushed == 1

    def test_skips_when_exists(self):
        db = _FakeSyncSession([_FakeResult(scalar_val="polygon_features_p_sid_1")])
        fs._ensure_partition_sync(db, PointFeature, "sid-1")
        assert db.executed == ['SELECT to_regclass(:p)']
        assert db.flushed == 0

    def test_sql_quote_escape(self):
        db = _FakeSyncSession([_FakeResult(scalar_val=None), _FakeResult()])
        fs._ensure_partition_sync(db, PointFeature, "s'i'd")
        creates = [s for s in db.executed if "CREATE TABLE" in s]
        assert "('s''i''d')" in creates[0]


# ── 分区管理（async）─────────────────────────────────────────────────────────

class TestEnsurePartitionAsync:

    async def test_creates_when_missing(self):
        db = _FakeDB([_FakeResult(scalar_val=None), _FakeResult()])
        await fs._ensure_partition(db, LineFeature, "sid-2")
        creates = [s for s in db.executed if "CREATE TABLE" in s]
        assert creates and "line_features_p_sid_2" in creates[0]
        assert db.flushed == 1

    async def test_skips_when_exists(self):
        db = _FakeDB([_FakeResult(scalar_val="line_features_p_sid_2")])
        await fs._ensure_partition(db, LineFeature, "sid-2")
        assert len(db.executed) == 1 and "CREATE" not in db.executed[0]


# ── drop_partition ───────────────────────────────────────────────────────────

class TestDropPartition:

    async def test_drops_all_tables(self):
        # 每表：exists=True → DETACH → DROP；3 表 × 3 次
        db = _FakeDB([_FakeResult(scalar_val="p"), _FakeResult(), _FakeResult()] * 3)
        await fs.drop_partition(db, "sid-1")
        detaches = [s for s in db.executed if "DETACH PARTITION" in s]
        drops = [s for s in db.executed if s.startswith("DROP TABLE")]
        assert len(detaches) == 3 and len(drops) == 3

    async def test_none_exist(self):
        db = _FakeDB([_FakeResult(scalar_val=None)] * 3)
        await fs.drop_partition(db, "sid-1")
        assert len(db.executed) == 3
        assert not any("DETACH" in s for s in db.executed)

    def test_sync_drops_all_tables(self):
        db = _FakeSyncSession([_FakeResult(scalar_val="p"), _FakeResult(), _FakeResult()] * 3)
        fs.drop_partition_sync(db, "sid-1")
        assert len([s for s in db.executed if "DETACH" in s]) == 3
        assert len([s for s in db.executed if s.startswith("DROP TABLE")]) == 3

    def test_sync_none_exist(self):
        db = _FakeSyncSession([_FakeResult(scalar_val=None)] * 3)
        fs.drop_partition_sync(db, "sid-1")
        assert len(db.executed) == 3


# ── _write_feature_common ────────────────────────────────────────────────────

class TestWriteFeatureCommon:

    def test_happy_point(self):
        sf, status = fs._write_feature_common(
            PointFeature, "sf", "ak", "f.geojson",
            {"geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
             "properties": {"a": 1}, "feature_index": 3}, 4326)
        assert status == "written"
        assert sf.stored_file_id == "sf" and sf.api_key_id == "ak"
        assert sf.source_name == "f.geojson" and sf.feature_index == 3
        assert sf.geometry_type == "Point"
        assert sf.geometry is not None

    def test_normalizes_3d_composite(self):
        sf, status = fs._write_feature_common(
            PolygonFeature, "sf", "ak", "f",
            {"geometry": {"type": "MultiPatch",
                          "coordinates": [[[[0, 0], [2, 0], [1, 2], [0, 0]]]]}},
            4326)
        assert status == "written"
        assert sf.geometry_type == "MultiPolygon"

    def test_no_geometry_returns_none(self):
        assert fs._write_feature_common(
            PointFeature, "sf", "ak", "f", {"properties": {}}, 4326
        ) == (None, "skipped_no_geometry")
        assert fs._write_feature_common(
            PointFeature, "sf", "ak", "f",
            {"geometry": {"coordinates": []}}, 4326
        ) == (None, "skipped_no_geometry")

    def test_bad_geometry_skipped_empty(self):
        sf, status = fs._write_feature_common(
            PointFeature, "sf", "ak", "f",
            {"geometry": {"type": "Point", "coordinates": ["bad"]}}, 4326)
        assert status == "skipped_empty"
        assert sf.geometry is None and sf.properties is None


# ── write_feature（async）────────────────────────────────────────────────────

class TestWriteFeature:

    async def test_ensure_default(self):
        db = _FakeDB([_FakeResult(scalar_val=None), _FakeResult()])
        status = await fs.write_feature(
            db, "sf", "ak", "f.geojson",
            {"geometry": {"type": "Point", "coordinates": [1.0, 2.0]}})
        assert status == "written"
        assert any("CREATE TABLE" in s for s in db.executed)
        assert len(db.added) == 1

    async def test_ensure_false_skips_ddl(self):
        db = _FakeDB()
        status = await fs.write_feature(
            db, "sf", "ak", "f", {"properties": {}}, ensure=False)
        assert status == "skipped_no_geometry"
        assert db.executed == [] and db.added == []


# ── write_features_batch（async）─────────────────────────────────────────────

class TestWriteFeaturesBatch:

    async def test_flush_every_and_stats(self):
        feats = [_pt_feat(), {"properties": {}}, _pt_feat(3.0, 4.0)]
        db = _FakeDB([_FakeResult(scalar_val="exists")] * 3)
        stats = await fs.write_features_batch(
            db, "sf", "ak", "f.geojson", feats, flush_every=2)
        assert stats == {
            "written": 2, "skipped_no_geometry": 1,
            "skipped_empty": 0, "total": 3,
        }
        assert db.flushed == 1

    async def test_empty_input(self):
        db = _FakeDB([_FakeResult(scalar_val=None)] * 3)
        stats = await fs.write_features_batch(db, "sf", "ak", "f", [])
        assert stats["total"] == 0 and stats["written"] == 0
        assert len([s for s in db.executed if "CREATE" in s]) == 3


# ── write_features_batch_sync ────────────────────────────────────────────────

class TestWriteFeaturesBatchSync:

    def test_generator_ensured_set(self):
        src = [_pt_feat(), {"properties": {}}, _pt_feat(3.0, 4.0)]
        # ensure 查不存在 → 各自建点/面分区；后续同表不再 ensure
        db = _FakeSyncSession([_FakeResult(scalar_val=None), _FakeResult(),
                               _FakeResult(scalar_val=None), _FakeResult()])
        stats = fs.write_features_batch_sync(db, "sf", "ak", "f", iter(src), flush_every=2)
        assert stats == {
            "written": 2, "skipped_no_geometry": 1,
            "skipped_empty": 0, "total": 3,
        }
        assert len([s for s in db.executed if "CREATE" in s]) == 2
        assert db.flushed == 3  # 2 次 ensure 建分区各 flush + flush_every 命中 1 次
    def test_skipped_empty_counted(self):
        db = _FakeSyncSession([_FakeResult(scalar_val="exists")])
        stats = fs.write_features_batch_sync(
            db, "sf", "ak", "f",
            [{"geometry": {"type": "Point", "coordinates": ["x"]}}])
        assert stats == {
            "written": 0, "skipped_empty": 1,
            "skipped_no_geometry": 0, "total": 1,
        }

    def test_geom_none_type(self):
        db = _FakeSyncSession([_FakeResult(scalar_val="exists")])
        stats = fs.write_features_batch_sync(
            db, "sf", "ak", "f", [{"geometry": None}])
        assert stats["skipped_no_geometry"] == 1 and stats["total"] == 1


# ── aggregate_layer_stats ────────────────────────────────────────────────────

class TestAggregateLayerStats:

    async def test_mixed_and_default_layer(self):
        db = _FakeDB([
            _FakeResult(rows=[_cntrow("L1", 2), _cntrow(None, 1)]),
            _FakeResult(rows=[_cntrow("L1", 3)]),
            _FakeResult(rows=[]),
            _FakeResult(),  # delete stored_file_layers
            _FakeResult(),  # update stored_files
        ])
        out = await fs.aggregate_layer_stats(db, "sf", "ak")
        assert out == {"layers": 2, "point": 3, "line": 3, "polygon": 0, "total": 6}
        assert len(db.added) == 2
        l1 = next(o for o in db.added if o.layer_name == "L1")
        assert l1.geometry_type == "mixed"
        assert l1.point_count == 2 and l1.line_count == 3 and l1.feature_count == 5
        dflt = next(o for o in db.added if o.layer_name == "(默认图层)")
        assert dflt.geometry_type == "Point" and dflt.point_count == 1
        assert any("DELETE" in s for s in db.executed)
        assert any("UPDATE" in s for s in db.executed)

    async def test_single_geom_type(self):
        db = _FakeDB([
            _FakeResult(rows=[_cntrow("P", 7)]),
            _FakeResult(rows=[]),
            _FakeResult(rows=[]),
            _FakeResult(), _FakeResult(),
        ])
        out = await fs.aggregate_layer_stats(db, "sf", "")
        assert out == {"layers": 1, "point": 7, "line": 0, "polygon": 0, "total": 7}
        assert db.added[0].geometry_type == "Point"

    async def test_all_empty(self):
        db = _FakeDB([
            _FakeResult(rows=[]), _FakeResult(rows=[]), _FakeResult(rows=[]),
            _FakeResult(), _FakeResult(),
        ])
        out = await fs.aggregate_layer_stats(db, "sf", "")
        assert out == {"layers": 0, "point": 0, "line": 0, "polygon": 0, "total": 0}
        assert db.added == []


# ── count_by_file ────────────────────────────────────────────────────────────

class TestCountByFile:

    async def test_with_api_key_filter(self):
        db = _FakeDB([_FakeResult(scalar_val=2), _FakeResult(scalar_val=3),
                      _FakeResult(scalar_val=1)])
        assert await fs.count_by_file(db, "sf", "ak") == 6

    async def test_super_admin_no_filter(self):
        db = _FakeDB([_FakeResult(scalar_val=0), _FakeResult(scalar_val=0),
                      _FakeResult(scalar_val=None)])
        assert await fs.count_by_file(db, "sf", None) == 0


# ── query_features_by_file ──────────────────────────────────────────────────

class TestQueryFeaturesByFile:

    async def test_empty_file(self):
        db = _FakeDB([_FakeResult(scalar_val=0)] * 3)
        page, total = await fs.query_features_by_file(db, "sf", None)
        assert page == [] and total == 0
        assert len(db.executed) == 3

    async def test_sort_and_geojson_parse(self):
        pt = _featrow("p1", 2, geojson='{"type":"Point","coordinates":[1,2]}')
        ln = _featrow("l1", 1, geojson=None)
        db = _FakeDB([
            _FakeResult(scalar_val=1), _FakeResult(scalar_val=1), _FakeResult(scalar_val=0),
            _FakeResult(rows=[pt]), _FakeResult(rows=[ln]), _FakeResult(rows=[]),
        ])
        page, total = await fs.query_features_by_file(db, "sf", None, limit=10)
        assert total == 2
        assert [r["feature_index"] for r in page] == [1, 2]
        assert page[0]["geometry"] is None
        assert page[1]["geometry"] == {"type": "Point", "coordinates": [1, 2]}

    async def test_offset_limit_paging(self):
        pt_rows = [_featrow(f"p{i}", i) for i in range(3)]
        db = _FakeDB([
            _FakeResult(scalar_val=3), _FakeResult(scalar_val=0), _FakeResult(scalar_val=0),
            _FakeResult(rows=pt_rows), _FakeResult(rows=[]), _FakeResult(rows=[]),
        ])
        page, total = await fs.query_features_by_file(db, "sf", "ak", offset=1, limit=1)
        assert total == 3 and len(page) == 1 and page[0]["feature_index"] == 1


# ── transform_features_by_file ───────────────────────────────────────────────

class TestTransformFeaturesByFile:

    async def test_happy_skip_fail_error(self):
        f1, f2 = SimpleNamespace(id="f1", geometry=None), SimpleNamespace(id="f2", geometry=None)
        f3, f4 = SimpleNamespace(id="f3", geometry=None), SimpleNamespace(id="f4", geometry=None)
        f5, f6 = SimpleNamespace(id="f5", geometry=None), SimpleNamespace(id="f6", geometry=None)

        db = _FakeDB([
            _scalres(f1, f2), _wktres("POINT (1 2)"), _wktres(None),
            _scalres(f3, f4), _wktres("POLYGON EMPTY"), _wktres("POINT (5 6)"),
            _scalres(f5, f6), _wktres("POINT (7 8)"), _wktres("POINT (9 10)"),
        ])
        geojson_out = {"type": "Point", "coordinates": [2.0, 3.0]}
        calls = iter([geojson_out, None, ValueError("boom"), geojson_out])

        def fake_transform(geojson, src, dst, warnings=None):
            r = next(calls)
            if isinstance(r, Exception):
                raise r
            return r

        with patch("app.services.transform_engine.resolve_transform_crs",
                   new=AsyncMock(side_effect=["src-ok", "dst-ok"])), \
             patch("app.services.transform_engine.transform_geometry",
                   side_effect=fake_transform):
            out = await fs.transform_features_by_file(
                db, "sf", "ak", "EPSG:4326", "EPSG:4490", target_srid=4490)

        assert out == {"transformed": 2, "total": 6, "failed": 2}
        from geoalchemy2 import WKTElement
        assert isinstance(f1.geometry, WKTElement) and f1.geometry.srid == 4490
        assert isinstance(f6.geometry, WKTElement)
        assert f4.geometry is None and f5.geometry is None


# ── repair_features_by_file ──────────────────────────────────────────────────

_BOWTIE = "POLYGON ((0 0, 2 2, 0 2, 2 0, 0 0))"


def _oneres(wkt, srid=4326):
    return _FakeResult(rows=[(wkt, srid)])


class TestRepairFeaturesByFile:

    async def test_repair_skips_and_fixes(self):
        p1 = SimpleNamespace(id="p1", geometry=None)
        p2 = SimpleNamespace(id="p2", geometry=None)
        p3 = SimpleNamespace(id="p3", geometry=None)
        p4 = SimpleNamespace(id="p4", geometry=None)
        p5 = SimpleNamespace(id="p5", geometry=None)
        p6 = SimpleNamespace(id="p6", geometry=None)

        db = _FakeDB([
            _scalres(p1, p2), _oneres("POINT (1 2)"), _oneres(_BOWTIE),
            _scalres(p3, p4), _oneres(None), _oneres(_BOWTIE),
            _scalres(p5, p6), _oneres("POINT (7 8)"), _oneres(_BOWTIE, 4490),
        ])
        out = await fs.repair_features_by_file(db, "sf", "ak")
        assert out == {"repaired": 3, "total": 6}
        assert p2.geometry is not None and p4.geometry is not None
        assert p6.geometry is not None and p6.geometry.srid == 4490

    async def test_exception_swallowed(self, monkeypatch):
        """loads 产物 buffer 抛异常 → except 吞掉继续（482-484）。"""
        class _ExplodingGeom:
            is_valid = False

            def buffer(self, distance):
                raise RuntimeError("boom")

        monkeypatch.setattr(fs.shp_wkt, "loads", lambda w: _ExplodingGeom())
        e1 = SimpleNamespace(id="e1", geometry=None)
        e2 = SimpleNamespace(id="e2", geometry=None)
        e3 = SimpleNamespace(id="e3", geometry=None)
        db = _FakeDB([
            _scalres(e1, e2), _oneres("POINT (1 2)"), _oneres("POINT (1 2)"),
            _scalres(e3), _oneres("POINT (1 2)"),
            _scalres(),
        ])
        out = await fs.repair_features_by_file(db, "sf", "ak")
        assert out == {"repaired": 0, "total": 3}
        assert all(f.geometry is None for f in (e1, e2, e3))

    async def test_make_valid_fallback(self, monkeypatch):
        """buffer(0) 产物仍 invalid → 478 make_valid 兜底修复。

        buffer(0) 返回真实 shapely 无效(self-intersect)多边形 → 真实 make_valid
        按 shapely 语义修复为有效多边形，覆盖 478。
        """
        from shapely.geometry import Polygon

        bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0)])  # is_valid=False

        class _Geom:
            is_valid = False

            def buffer(self, distance):
                # 真 shapely 对象：linked 到 bowtie，让 make_valid 能真实处理
                return bowtie

        monkeypatch.setattr(fs.shp_wkt, "loads", lambda w: _Geom())
        fixed = SimpleNamespace(id="fx", geometry=None)
        db = _FakeDB([
            _scalres(fixed), _oneres("POINT (0 0)"),
            _scalres(), _scalres(),
        ])
        out = await fs.repair_features_by_file(db, "sf", "ak")
        assert out == {"repaired": 1, "total": 1}
        from geoalchemy2 import WKTElement
        assert isinstance(fixed.geometry, WKTElement)


# ── 真实 PostGIS 冒烟 ──────────────────────────────────────────────────────

class TestRealSmoke:

    async def test_write_query_aggregate_drop(self, test_db, api_key_header):
        from tests.conftest import _TestSession
        from app.models.api_key import ApiKey
        from app.models.stored_file import StoredFile

        async with _TestSession() as db:
            key = (await db.execute(
                __import__("sqlalchemy").select(ApiKey))).scalars().first()

        sid = str(uuid.uuid4())
        feats = [
            _pt_feat(116.3, 39.9),
            {"geometry": {"type": "LineString",
                          "coordinates": [[0, 0], [1, 1]]},
             "properties": {}, "feature_index": 1, "layer_name": "L1"},
            {"properties": {"txt": "x"}, "feature_index": 2},
        ]
        async with _TestSession() as db:
            # 真实流程调用方先建 StoredFile，aggregate 的 FK 依赖它
            sf = StoredFile(
                id=sid, api_key_id=key.id, original_name="smoke.geojson",
                data_type="GeoJSON", file_format="geojson",
            )
            db.add(sf)
            await db.flush()

            stats = await fs.write_features_batch(db, sid, key.id, "smoke.geojson", feats)
            assert stats == {"written": 2, "skipped_empty": 0,
                             "skipped_no_geometry": 1, "total": 3}

            total = await fs.count_by_file(db, sid, key.id)
            assert total == 2

            page, t2 = await fs.query_features_by_file(db, sid, key.id)
            assert t2 == 2 and len(page) == 2

            agg = await fs.aggregate_layer_stats(db, sid, key.id)
            assert agg["layers"] == 1 and agg["total"] == 2
            assert agg["point"] == 1 and agg["line"] == 1

            rep = await fs.repair_features_by_file(db, sid, key.id)
            assert rep == {"repaired": 0, "total": 2}

            await fs.drop_partition(db, sid)
            assert await fs.count_by_file(db, sid, key.id) == 0
