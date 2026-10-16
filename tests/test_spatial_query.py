"""P0: spatial_query 服务单测 — 用 fake async session 驱动，不连 PostGIS。

覆盖 app/services/spatial_query.py：
- bbox_query / knn_query / spatial_relation / spatial_stats 的 SQL 生成与结果映射
- 无效空间关系 → ValueError
- layer 限定单表 vs 跨三表逻辑
- fake session 捕获 SQL 文本与绑定参数，返回伪造行验证解析
"""

import pytest

from app.services.spatial_query import SpatialQueryResult, SpatialQueryService


class _Row:
    """简易行对象，支持属性访问。"""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeResult:
    """模拟 AsyncResult：可迭代 + fetchone + scalar。"""
    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar_value = scalar_value
        self._fetched = False

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self):
        if self._fetched:
            return None
        self._fetched = True
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar_value


def _result(rows=None, scalar=None):
    return _FakeResult(rows=rows or [], scalar_value=scalar)


class _FakeSession:
    """记录 execute 调用并按序返回预设 result（逐序列举请求值）。"""
    def __init__(self, results=None):
        # results: 每个 execute 调用应返回的 _FakeResult，按序消费
        self._queue = list(results or [])
        self._default = _result([])
        self.calls = []  # (sql_text, params)

    async def execute(self, query, params=None):
        self.calls.append((str(query), params))
        if self._queue:
            return self._queue.pop(0)
        return self._default


def _svc(session=None):
    return SpatialQueryService(session or _FakeSession())


# ── bbox_query ───────────────────────────────────────────────

class TestBBox:
    async def test_returns_mapped_results(self):
        sess = _FakeSession([
            _FakeResult([
                _Row(feature_id="f1", properties={"name": "a"}, geom_wkt="POINT(1 2)"),
                _Row(feature_id="f2", properties={}, geom_wkt="POINT(3 4)"),
            ])
        ])
        svc = SpatialQueryService(sess)
        results = await svc.bbox_query(0, 0, 10, 10)
        assert isinstance(results[0], SpatialQueryResult)
        assert [r.feature_id for r in results] == ["f1", "f2"]
        assert results[0].properties == {"name": "a"}
        assert results[0].geom_wkt == "POINT(1 2)"

    async def test_sql_contains_union_makeenvelope_and_limit(self):
        sess = _FakeSession([_FakeResult([])])
        svc = SpatialQueryService(sess)
        await svc.bbox_query(0, 0, 10, 10, layer="all", limit=50)
        sql, params = sess.calls[0]
        assert params["xmin"] == 0 and params["xmax"] == 10
        assert params["limit"] == 50
        assert "UNION ALL" in sql
        assert "ST_MakeEnvelope" in sql
        assert "LIMIT 50" in sql
        # 默认跨三表
        assert sql.count("FROM point_features") == 1
        assert sql.count("FROM line_features") == 1
        assert sql.count("FROM polygon_features") == 1

    async def test_single_layer_restricts_table(self):
        sess = _FakeSession([_FakeResult([])])
        svc = SpatialQueryService(sess)
        await svc.bbox_query(0, 0, 1, 1, layer="polygon_features")
        sql, _ = sess.calls[0]
        assert sql.count("FROM polygon_features") == 1
        assert "FROM line_features" not in sql
        assert "FROM point_features" not in sql

    async def test_empty_result(self):
        sess = _FakeSession([_FakeResult([])])
        svc = SpatialQueryService(sess)
        assert await svc.bbox_query(0, 0, 1, 1) == []


# ── knn_query ────────────────────────────────────────────────

class TestKnn:
    async def test_distance_parsed_to_float(self):
        sess = _FakeSession([
            _FakeResult([
                _Row(feature_id="n1", properties={}, geom_wkt="POINT(1 1)", distance=12.5),
                _Row(feature_id="n2", properties={}, geom_wkt="POINT(2 2)", distance=None),
            ])
        ])
        svc = SpatialQueryService(sess)
        results = await svc.knn_query(116.0, 39.0, k=2)
        assert results[0].distance == 12.5
        assert results[1].distance is None

    async def test_sql_orders_by_distance_and_limits(self):
        sess = _FakeSession([_FakeResult([])])
        svc = SpatialQueryService(sess)
        await svc.knn_query(116, 39, k=5)
        sql, params = sess.calls[0]
        assert params["lon"] == 116 and params["lat"] == 39
        assert params["k"] == 5
        assert "ST_MakePoint" in sql
        assert "ORDER BY distance" in sql
        assert "LIMIT :k" in sql

    async def test_specific_layer(self):
        sess = _FakeSession([_FakeResult([])])
        svc = SpatialQueryService(sess)
        await svc.knn_query(116, 39, layer="point_features")
        sql, _ = sess.calls[0]
        assert sql.count("FROM point_features") == 1


# ── spatial_relation ─────────────────────────────────────────

class TestSpatialRelation:
    async def test_invalid_relation_raises(self):
        svc = _svc()
        with pytest.raises(ValueError, match="不支持的空间关系"):
            await svc.spatial_relation("src", relation="kisses")

    async def test_valid_relations_accepted(self):
        for rel in ("intersects", "contains", "within", "touches", "crosses", "overlaps"):
            sess = _FakeSession([_FakeResult([])])
            svc = SpatialQueryService(sess)
            await svc.spatial_relation("f-1", relation=rel, layer="all")
            sql, params = sess.calls[0]
            assert params["source_id"] == "f-1"
            assert f"ST_{rel.capitalize()}" in sql

    async def test_returns_mapped(self):
        sess = _FakeSession([
            _FakeResult([_Row(feature_id="f2", properties={"kind": "x"}, geom_wkt="POINT(1 1)")])
        ])
        svc = SpatialQueryService(sess)
        results = await svc.spatial_relation("f1", layer="point_features")
        assert results[0].feature_id == "f2"
        assert results[0].properties == {"kind": "x"}

    async def test_cross_table_union(self):
        sess = _FakeSession([_FakeResult([])])
        svc = SpatialQueryService(sess)
        await svc.spatial_relation("f1", layer="all")
        sql, _ = sess.calls[0]
        assert " UNION " in sql
        assert sql.count("t.id = :source_id") >= 1


# ── spatial_stats ────────────────────────────────────────────

class TestSpatialStats:
    async def test_aggregates_all_tables(self):
        # 3 次 count/vertices + 1 次 polygon area = 4 次 execute
        sess = _FakeSession([
            _result(rows=[_Row(feature_count=5, total_vertices=10)]),   # point count
            _result(rows=[_Row(feature_count=3, total_vertices=8)]),    # line count
            _result(rows=[_Row(feature_count=7, total_vertices=20)]),   # polygon count
            _result(scalar=100.0),                                      # polygon area
        ])
        svc = SpatialQueryService(sess)
        stats = await svc.spatial_stats()
        assert stats == {"feature_count": 15, "total_area": 100.0, "total_vertices": 38}
        assert len(sess.calls) == 4

    async def test_single_layer_no_area_query(self):
        sess = _FakeSession([
            _result(rows=[_Row(feature_count=2, total_vertices=4)]),
        ])
        svc = SpatialQueryService(sess)
        stats = await svc.spatial_stats(layer="point_features")
        assert stats["feature_count"] == 2
        assert stats["total_vertices"] == 4
        assert len(sess.calls) == 1  # 无 polygon → 不执行 area 查询

    async def test_polygon_area_only_for_polygon(self):
        sess = _FakeSession([
            _result(rows=[_Row(feature_count=1, total_vertices=5)]),   # polygon count
            _result(scalar=250.5),                                     # polygon area
        ])
        svc = SpatialQueryService(sess)
        stats = await svc.spatial_stats(layer="polygon_features")
        assert stats["total_area"] == 250.5
        assert stats["total_vertices"] == 5