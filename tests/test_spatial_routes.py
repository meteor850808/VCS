# -*- coding: utf-8 -*-
"""app/api/v1/spatial.py 路由级回归测试（补路由级覆盖：29% → 90%+ 目标）。

覆盖策略：以 conftest 的异步 client + db_session 夹具在 vcs_test 库造
StoredFile / TransformResult，对 transform_engine / export_service / feature_store
的异步桩函数打桩（函数内 import → patch 源模块属性，from-import 时生效）：
- `POST /spatial/{file_id}/transform` 走完整落库链路，同时剪裁 `_inject_crs_member`
  的 EPSG/VCS-CUSTOM/保持/坏 JSON 分支；
- `GET /spatial/crs/catalog` 用真实 PostGIS `spatial_ref_sys` 查询 + `_categorize`
  参数化单元覆盖全部分组分支；
- download 三格式（geojson 直出 / shp / gpkg 走 `_run_sync` executor）。
"""

import pytest
import pytest_asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import func, select

from app.config import settings  # noqa: F401  (确保配置加载)
from app.models.api_key import ApiKey
from app.models.stored_file import StoredFile
from app.models.transform_result import TransformResult
from app.services.transform_engine import ResolvedCRS, TransformStats


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    yield


async def _create_key(db_session, name, key_id=None):
    from app.core.security import generate_api_key
    raw, hashed = generate_api_key()
    key = ApiKey(id=key_id, key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.commit()
    return raw, key


def _res(srid, display=None, source="epsg"):
    """构造 ResolvedCRS 桩对象。"""
    return ResolvedCRS(
        srid=srid,
        pyproj_crs=f"EPSG:{srid}",
        epsg=srid if srid < 910_000 else None,
        is_custom=srid >= 910_000,
        source=source,
        display=display or f"EPSG:{srid}",
    )


def _make_file(db, key, name="test.shp", detected_crs="EPSG:4326", **kw):
    sf = StoredFile(
        api_key_id=key.id,
        original_name=name,
        content_type="application/octet-stream",
        file_size=10,
        file_data=b"fake",
        detected_crs=detected_crs,
        **kw,
    )
    db.add(sf)
    return sf


def _make_result(db, key, **kw):
    tr = TransformResult(
        api_key_id=kw.get("api_key_id", key.id),
        source_file_id=kw.get("source_file_id"),
        source_file_name=kw.get("source_file_name", "a.shp"),
        source_crs=kw.get("source_crs", "EPSG:4326"),
        target_crs=kw.get("target_crs", "EPSG:4490"),
        source_srid=kw.get("source_srid", 4326),
        target_srid=kw.get("target_srid", 4490),
        feature_count=kw.get("feature_count", 2),
        output_size=kw.get("output_size", len(kw.get("output_data") or b"")),
        output_data=kw.get("output_data"),
    )
    db.add(tr)
    return tr


# ── GET /spatial/crs/catalog ────────────────────────────────────────────────

class TestCrsCatalog:
    async def test_catalog_groups(self, client):
        """真实 spatial_ref_sys：分组结构 + 常用项 + 自定义置顶。"""
        resp = await client.get("/api/v1/spatial/crs/catalog")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"catalog", "custom"}
        groups = [g["group"] for g in body["catalog"]]
        # 自定义置顶 + 常用预设
        assert groups[0] == "自定义坐标系"
        assert "常用" in groups
        # PostGIS 全量 spatial_ref_sys 至少有地理坐标系组且含 4326/4490
        geo = next(g for g in body["catalog"] if g["group"] == "地理坐标系")
        srids = {i["srid"] for i in geo["items"]}
        assert 4326 in srids and 4490 in srids

    async def test_catalog_custom_resolve_exception_fallback(self, client):
        """自定义 CRS 解析异常 → 回退静态 srid（不阻断目录）。"""
        with patch("app.services.transform_engine.resolve_transform_crs",
                   side_effect=RuntimeError("no db")):
            resp = await client.get("/api/v1/spatial/crs/catalog")
        assert resp.status_code == 200
        body = resp.json()
        custom_items = body["catalog"][0]["items"]
        assert len(custom_items) == len(body["custom"])

    async def test_catalog_other_fallback(self, client, db_session):
        """兜底\u201c其他\u201d分类可达性：临时把一条 WGS UTM srtext 改脏触发兜底，finally 恢复原值。"""
        from sqlalchemy import text
        orig = (await db_session.execute(
            text("SELECT srtext FROM spatial_ref_sys WHERE srid = 32653"))).scalar()
        assert orig, "空间参考系统 32653 应存在"
        try:
            await db_session.execute(text(
                "UPDATE spatial_ref_sys SET srtext = "
                "'PROJCS[\"WGS 84 / odd\",GEOGCS[\"WGS 84\"]]' WHERE srid = 32653"))
            await db_session.commit()

            resp = await client.get("/api/v1/spatial/crs/catalog")
            assert resp.status_code == 200
            groups = {g["group"]: g["items"] for g in resp.json()["catalog"]}
            others = {i["srid"] for i in groups.get("其他", [])}
            assert 32653 in others
            assert all("EPSG:" in i["name"] for i in groups["其他"])
        finally:
            await db_session.execute(text(
                "UPDATE spatial_ref_sys SET srtext = :o WHERE srid = 32653"),
                {"o": orig})
            await db_session.commit()

    async def test_catalog_group_members(self, client):
        """真实 spatial_ref_sys 驱动 _categorize 全部分支：各投影带分组与 zone 命名。"""
        resp = await client.get("/api/v1/spatial/crs/catalog")
        assert resp.status_code == 200
        groups = {g["group"]: g["items"] for g in resp.json()["catalog"]}

        # PostGIS 标准 spatial_ref_sys 包含全部常用中国 SRID → 所有分组应出现
        cgcs3 = {i["srid"]: i["name"] for i in groups.get("CGCS2000 3°投影带", [])}
        assert 4513 in cgcs3 and "Zone 25" in cgcs3[4513]
        cgcs6 = {i["srid"]: i["name"] for i in groups.get("CGCS2000 6°投影带", [])}
        assert 4491 in cgcs6 and "Zone 13" in cgcs6[4491]
        bj54 = {i["srid"]: i["name"] for i in groups.get("北京1954 3°投影带", [])}
        assert 2401 in bj54 and "Zone 25" in bj54[2401]
        xa3 = {i["srid"]: i["name"] for i in groups.get("西安1980 3°投影带", [])}
        assert 2349 in xa3 and "Zone 25" in xa3[2349]
        xa6 = {i["srid"]: i["name"] for i in groups.get("西安1980 6°投影带", [])}
        assert 2327 in xa6 and "Zone 13" in xa6[2327]
        utm = {i["srid"]: i["name"] for i in groups.get("WGS 84 UTM", [])}
        assert 32651 in utm and "Zone 51N" in utm[32651]


# ── POST /spatial/{file_id}/transform ───────────────────────────────────────

class TestTransformSpatialFeatures:
    _MOCK_TGT = {}

    @contextmanager
    def _patch_engine(self, features, stats, src_res, dst_res):
        """统一打桩 transform_engine 的 async 解析/变换（函数内 import → patch 源模块）。"""
        with patch("app.services.transform_engine.resolve_transform_crs",
                   AsyncMock(side_effect=[src_res, dst_res])), \
                patch("app.services.transform_engine.transform_db_features",
                      AsyncMock(return_value=(features, stats))) as m_trf:
            yield m_trf

    async def test_transform_success(self, client, db_session):
        """标准 EPSG 目标 → 200 + TransformResult 落库（crs 成员注入）。"""
        raw, key = await _create_key(db_session, "spatial_transform_ok")
        sf = _make_file(db_session, key)
        await db_session.commit()

        fc = [{"type": "Feature", "properties": {"n": 1},
               "geometry": {"type": "Point", "coordinates": [121, 31]}}]
        stats = TransformStats(total=1, transformed=1)
        with self._patch_engine(fc, stats,
                                _res(4326, "EPSG:4326"),
                                _res(4490, "EPSG:4490")) as m_trf:
            resp = await client.post(
                f"/api/v1/spatial/{sf.id}/transform",
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["transformed"] == 1 and body["total"] == 1
        assert body["source_crs"] == "EPSG:4326"
        m_trf.assert_awaited_once()

        tr = await db_session.get(TransformResult, body["result_id"])
        assert tr is not None
        assert tr.target_srid == 4490
        assert tr.api_key_id == key.id
        assert b"urn:ogc:def:crs:EPSG::4490" in tr.output_data

    async def test_transform_custom_srid_uses_vcs_namespace(self, client, db_session):
        """自定义 SRID(≥910000) → VCS-CUSTOM 命名空间声明。"""
        raw, key = await _create_key(db_session, "spatial_transform_custom")
        sf = _make_file(db_session, key)
        await db_session.commit()

        fc = [{"type": "Feature", "properties": {},
               "geometry": {"type": "Point", "coordinates": [121, 31]}}]
        with self._patch_engine(fc, TransformStats(total=1, transformed=1),
                                _res(4326, "EPSG:4326"),
                                _res(918984, "Custom:shanghaicity")) as _:
            resp = await client.post(
                f"/api/v1/spatial/{sf.id}/transform",
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 200
        tr = await db_session.get(TransformResult, resp.json()["result_id"])
        assert b"urn:ogc:def:crs:VCS-CUSTOM::918984" in tr.output_data
        assert b"urn:ogc:def:crs:EPSG::918984" not in tr.output_data

    async def test_transform_source_fallback_warning(self, client, db_session):
        """显式 source_crs 无法解析(fallback-unparseable) → warnings 列表。"""
        raw, key = await _create_key(db_session, "spatial_transform_warn")
        sf = _make_file(db_session, key)
        await db_session.commit()

        fc = [{"type": "Feature", "properties": {},
               "geometry": {"type": "Point", "coordinates": [121, 31]}}]
        stats = TransformStats(total=1, transformed=1, warnings=["w1"])
        with self._patch_engine(fc, stats,
                                _res(4326, "EPSG:4326", source="fallback-unparseable"),
                                _res(4490, "EPSG:4490")) as _:
            resp = await client.post(
                f"/api/v1/spatial/{sf.id}/transform?source_crs=BAD:CRS",
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 200
        warnings = resp.json()["warnings"]
        assert any("无法解析" in w for w in warnings)
        assert "w1" in warnings  # stats.warnings 追加

    async def test_transform_no_features_400(self, client, db_session):
        """全部要素失败 → 400 可诊断信息。"""
        raw, key = await _create_key(db_session, "spatial_transform_400")
        sf = _make_file(db_session, key)
        await db_session.commit()

        stats = TransformStats(total=5, transformed=0, failed=3)
        with self._patch_engine([], stats,
                                _res(4326, "EPSG:4326"),
                                _res(4490, "EPSG:4490")) as _:
            resp = await client.post(
                f"/api/v1/spatial/{sf.id}/transform",
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 400
        assert "该文件无可转换的几何要素" in resp.json()["detail"]
        assert "3 个要素变换失败" in resp.json()["detail"]

    async def test_transform_no_features_with_warning_400(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_transform_400w")
        sf = _make_file(db_session, key)
        await db_session.commit()

        stats = TransformStats(total=5, transformed=0, failed=0,
                               warnings=["域外坐标"])
        with self._patch_engine([], stats,
                                _res(4326, "EPSG:4326"),
                                _res(4490, "EPSG:4490")) as _:
            resp = await client.post(
                f"/api/v1/spatial/{sf.id}/transform",
                headers={"X-API-Key": raw},
            )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "；".join(["域外坐标"]) in detail

    async def test_transform_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_transform_404")
        resp = await client.post("/api/v1/spatial/no-such/transform",
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_transform_other_key_forbidden(self, client, db_session):
        """scope 隔离：他人文件 → 404（不泄露存在性）。"""
        raw, owner = await _create_key(db_session, "spatial_tf_owner")
        other_raw, _ = await _create_key(db_session, "spatial_tf_other")
        sf = _make_file(db_session, owner)
        await db_session.commit()

        resp = await client.post(f"/api/v1/spatial/{sf.id}/transform",
                                 headers={"X-API-Key": other_raw})
        assert resp.status_code == 404

    async def test_transform_virtual_admin_key_nulled(self, client, db_session):
        """api_key_id 以 00000000 开头的虚拟管理员 → 落库置 NULL 避免 FK 违规。"""
        raw, key = await _create_key(db_session, "spatial_admin",
                                     key_id="00000000-0000-0000-0000-000000000001")
        sf = _make_file(db_session, key)
        await db_session.commit()

        fc = [{"type": "Feature", "properties": {},
               "geometry": {"type": "Point", "coordinates": [121, 31]}}]
        with self._patch_engine(fc, TransformStats(total=1, transformed=1),
                                _res(4326, "EPSG:4326"),
                                _res(4490, "EPSG:4490")) as _:
            resp = await client.post(
                f"/api/v1/spatial/{sf.id}/transform",
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 200
        tr = await db_session.get(TransformResult, resp.json()["result_id"])
        assert tr.api_key_id is None


# ── _inject_crs_member 单元分支 ─────────────────────────────────────────────

class TestInjectCrsMember:
    def test_empty_payload(self):
        from app.api.v1.spatial import _inject_crs_member
        assert _inject_crs_member(b"", 4490) == b""
        assert _inject_crs_member(b"{}", None) == b"{}"

    def test_already_has_standard_crs(self):
        from app.api.v1.spatial import _inject_crs_member
        payload = b'{"type": "FeatureCollection", "features": [], "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4490"}}}'
        out = _inject_crs_member(payload, 4490)
        assert out == payload  # 已正确 → 原样返回

    def test_custom_bad_urn_corrected(self):
        from app.api.v1.spatial import _inject_crs_member
        # 早期版本误写的无效 EPSG URN → 纠正为 VCS-CUSTOM
        payload = (b'{"type": "FeatureCollection", "features": [],'
                   b'"crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::918984"}}}')
        out = _inject_crs_member(payload, 918984)
        assert b"urn:ogc:def:crs:VCS-CUSTOM::918984" in out
        assert b"EPSG::918984" not in out

    def test_bad_json_returns_original(self):
        from app.api.v1.spatial import _inject_crs_member
        payload = b"not-json"
        assert _inject_crs_member(payload, 4490) == payload


# ── GET /spatial/transform-results ──────────────────────────────────────────

class TestListTransformResults:
    async def test_list_with_total(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_list")
        tr1 = _make_result(db_session, key, output_data=b'{"a":1}')
        tr2 = _make_result(db_session, key, output_data=b'{"b":2}')
        await db_session.commit()

        resp = await client.get("/api/v1/spatial/transform-results",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {i["id"] for i in body["items"]} == {tr1.id, tr2.id}
        assert "output_data" not in body["items"][0]

    async def test_list_pagination_and_scope(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_list_pg")
        other_raw, _ = await _create_key(db_session, "spatial_list_other")
        tr1 = _make_result(db_session, key, output_data=b"1")
        tr2 = _make_result(db_session, key, output_data=b"2")
        tr3 = _make_result(db_session, key, output_data=b"3")
        await db_session.commit()

        resp = await client.get("/api/v1/spatial/transform-results",
                                params={"page": 2, "page_size": 2},
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert resp.json()["total"] == 3
        assert len(resp.json()["items"]) == 1

        # 他人 Key 看不到
        resp2 = await client.get("/api/v1/spatial/transform-results",
                                 headers={"X-API-Key": other_raw})
        assert resp2.json()["total"] == 0


# ── GET /spatial/transform-results/{id}/download ────────────────────────────

class TestDownloadTransformResult:
    async def test_download_geojson(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_dl_geojson")
        tr = _make_result(db_session, key, output_data=b'{"type": "FeatureCollection", "features": []}',
                          source_file_name="road.shp")
        await db_session.commit()

        resp = await client.get(f"/api/v1/spatial/transform-results/{tr.id}/download",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/geo+json")
        assert "road_EPSG:4326_to_EPSG:4490.geojson" in resp.headers["content-disposition"]

    async def test_download_shp(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_dl_shp")
        tr = _make_result(db_session, key, output_data=b"\x50\x4b",
                          source_file_name="road.shp")
        await db_session.commit()

        with patch("app.services.export_service.export_shapefile_zip",
                   new=MagicMock(return_value=b"zipdata")) as m_exp:
            resp = await client.get(
                f"/api/v1/spatial/transform-results/{tr.id}/download?format=shp",
                headers={"X-API-Key": raw})

        assert resp.status_code == 200
        assert resp.content == b"zipdata"
        assert resp.headers["content-type"].startswith("application/zip")
        assert m_exp.call_count == 1
        # target_wkt 从 spatial_ref_sys(4490 srtext) 取到 → 非 None
        call_args = m_exp.call_args.args
        assert call_args[1] is not None  # target_wkt

    async def test_download_gpkg(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_dl_gpkg")
        tr = _make_result(db_session, key, output_data=b"\x47\x50",
                          source_file_name="road.shp")
        await db_session.commit()

        with patch("app.services.export_service.export_gpkg",
                   new=MagicMock(return_value=b"gpkgdata")) as m_exp:
            resp = await client.get(
                f"/api/v1/spatial/transform-results/{tr.id}/download?format=gpkg",
                headers={"X-API-Key": raw})

        assert resp.status_code == 200
        assert resp.content == b"gpkgdata"
        assert resp.headers["content-type"].startswith("application/geopackage")

    async def test_download_custom_srid_no_wkt(self, client, db_session):
        """自定义 SRID(≥BASE) 且库中无行 → 跳过 pyproj 兜底（wkt=None 仍可导出）。"""
        raw, key = await _create_key(db_session, "spatial_dl_custom")
        # 动态挑一个 >= CUSTOM_SRID_BASE 且确认不在 spatial_ref_sys 的 srid
        # （catalog 测试的真实调用会把内置自定义注册进库，不能硬编码 918984）
        from sqlalchemy import text as _text
        rows = (await db_session.execute(_text(
            "SELECT srid FROM spatial_ref_sys WHERE srid >= 910000"))).scalars().all()
        fake_srid = 910_000 + 1_000_000
        while fake_srid in rows:
            fake_srid += 1

        tr = _make_result(db_session, key, output_data=b"\x50\x4b",
                          target_srid=fake_srid, source_file_name="a.shp")
        await db_session.commit()

        with patch("app.services.export_service.export_shapefile_zip",
                   new=MagicMock(return_value=b"z")) as m_exp:
            resp = await client.get(
                f"/api/v1/spatial/transform-results/{tr.id}/download?format=shp",
                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert m_exp.call_args.args[1] is None  # target_wkt None（无行且跳过 pyproj）

    async def test_download_unknown_srid_pyproj_exception(self, client, db_session):
        """srid<BASE 且库中无行 → pyproj 兜底抛异常 → wkt=None 仍可导出。"""
        raw, key = await _create_key(db_session, "spatial_dl_unknown")
        tr = _make_result(db_session, key, output_data=b"\x50\x4b",
                          target_srid=99999, source_file_name="a.shp")
        await db_session.commit()

        with patch("app.services.export_service.export_shapefile_zip",
                   new=MagicMock(return_value=b"z")) as m_exp:
            resp = await client.get(
                f"/api/v1/spatial/transform-results/{tr.id}/download?format=shp",
                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert m_exp.call_args.args[1] is None

    async def test_download_bad_format(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_dl_bad")
        tr = _make_result(db_session, key, output_data=b"{}")
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/spatial/transform-results/{tr.id}/download?format=exe",
            headers={"X-API-Key": raw})
        assert resp.status_code == 400

    async def test_download_not_found_and_gone(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_dl_404")
        resp = await client.get("/api/v1/spatial/transform-results/nope/download",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 404

        tr = _make_result(db_session, key, output_data=None)
        await db_session.commit()
        resp2 = await client.get(
            f"/api/v1/spatial/transform-results/{tr.id}/download",
            headers={"X-API-Key": raw})
        assert resp2.status_code == 410


# ── DELETE /spatial/transform-results/{id} ──────────────────────────────────

class TestDeleteTransformResult:
    async def test_delete_ok(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_del")
        tr = _make_result(db_session, key, output_data=b"x")
        await db_session.commit()

        resp = await client.delete(f"/api/v1/spatial/transform-results/{tr.id}",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 204
        # 避免 identity map 缓存已删对象：用 count 查询从库直读
        cnt = (await db_session.execute(
            select(func.count()).select_from(TransformResult).where(
                TransformResult.id == tr.id)
        )).scalar()
        assert cnt == 0

    async def test_delete_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_del_404")
        resp = await client.delete("/api/v1/spatial/transform-results/nope",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_delete_other_key_forbidden(self, client, db_session):
        raw, owner = await _create_key(db_session, "spatial_del_owner")
        other_raw, _ = await _create_key(db_session, "spatial_del_other")
        tr = _make_result(db_session, owner, output_data=b"x")
        await db_session.commit()

        resp = await client.delete(f"/api/v1/spatial/transform-results/{tr.id}",
                                   headers={"X-API-Key": other_raw})
        assert resp.status_code == 404


# ── POST /spatial/{file_id}/repair ──────────────────────────────────────────

class TestRepairSpatialFeatures:
    async def test_repair_ok(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_repair")
        sf = _make_file(db_session, key)
        await db_session.commit()

        with patch("app.api.v1.spatial.repair_features_by_file",
                   AsyncMock(return_value={"repaired": 4, "failed": 0,
                                          "message": "ok"})) as m_repair:
            resp = await client.post(f"/api/v1/spatial/{sf.id}/repair",
                                     headers={"X-API-Key": raw})

        assert resp.status_code == 200
        body = resp.json()
        assert body["file_id"] == sf.id
        assert body["repaired"] == 4
        m_repair.assert_awaited_once()

    async def test_repair_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "spatial_repair_404")
        resp = await client.post("/api/v1/spatial/no-such/repair",
                                 headers={"X-API-Key": raw})
        assert resp.status_code == 404