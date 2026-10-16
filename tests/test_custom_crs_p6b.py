"""P6b: custom_crs.py 服务级全覆盖（56% → 100%）。

覆盖目标：
  106/109-110           _fix_swapped_false_origin 无参/坏数 passthrough
  115-128               GK 带号例外 + 上海互换纠正
  136-154               _fix_swapped_false_origin_wkt（WKT1 纠正 / 异常兜底）
  169-170               _parse_with_pyproj 异常兜底
  193/197-200           parse_crs_definition pyproj-epsg / 畸形 WKT 分支
  213-236/	           resolve_ingest_srid_sync（缓存 / epsg / 注册 / fallback 全分支）
  250-253/263-300       _apply_false_origin_fix 纠正分支 + _ensure_registered_sync
                        （INSERT / match / 自愈 UPDATE / 换盐 / 10 次碰撞 RuntimeError）
  319-324/338/346-355/363-371  async 版对应分支
  387/391               coord_domain_warning 非地理 / 超域告警
  411-413               get_srid_is_geographic_sync
  426/430-437           _classify_srid_row 全判定分支
  471-481/487           validate_loaded_extent_sync（空数据 / 真实 extent 超域）
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.services import custom_crs as qc

SYNC_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/vcs_test"

# 无 EPSG、pyproj 可解析的投影字符串（自测专用内容 → srid 稳定唯一）
CUSTOM_PROJ4 = (
    "+proj=tmerc +lat_0=0 +lon_0=117 +k=1 +x_0=500000 +y_0=0 "
    "+ellps=GRS80 +units=m +no_defs"
)
# 上海地方坐标系互换缺陷实案形式（x_0 为千米级负值、y_0=0）
SH_PROJ4 = (
    "+proj=tmerc +lat_0=0 +lon_0=121.474 +k=1 +x_0=-3457147.81 +y_0=0 "
    "+ellps=GRS80 +units=m +no_defs"
)
GARBAGE = "definitely-not-a-crs-!!!"


def _expected_proj4():
    """parse 输出即注册实际 proj4（pyproj 规范化，可能带 +type=crs）。"""
    _, p4, _ = qc.parse_crs_definition(CUSTOM_PROJ4)
    return qc._normalize_crs_text(p4)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sync_session():
    eng = create_engine(SYNC_URL)
    sess = Session(eng)
    yield sess
    sess.close()
    eng.dispose()


@pytest.fixture
def reset_cache(monkeypatch):
    """每个测试独立进程内缓存。"""
    monkeypatch.setattr(qc, "_resolution_cache", {})


@pytest.fixture
def track_srids(sync_session):
    """记录测试产生的 custom 行 srid，teardown 清理（幂等，防二次运行污染）。"""
    created = []

    def _track(custom_ids, other_ids=None):
        created.append((list(custom_ids), list(other_ids or [])))

    yield _track
    # 先提交 sync 侧挂起写入（释放行锁），再清理本测试产生的行
    sync_session.commit()
    for custom, other in created:
        ids = custom + other
        if ids:
            sync_session.execute(
                text("DELETE FROM spatial_ref_sys WHERE srid = ANY(:ids)"),
                {"ids": ids},
            )
            sync_session.commit()


# ── 纯函数：假东/假北互换 ───────────────────────────────────────────────────

class TestFixFalseOrigin:

    def test_missing_params(self):
        """106：无 x_0/y_0 → 原样。"""
        s = "+proj=tmerc +lat_0=0"
        assert qc._fix_swapped_false_origin(s) == (s, None)

    def test_bad_number(self):
        """109-110：数值解析失败 → 原样。"""
        s = "+proj=tmerc +x_0=abc +y_0=0"
        assert qc._fix_swapped_false_origin(s) == (s, None)

    def test_number_value_error(self):
        """109-110：正则可匹配但 float 失败（1.2.3）→ 原样。"""
        s = "+proj=tmerc +x_0=1.2.3 +y_0=0"
        assert qc._fix_swapped_false_origin(s) == (s, None)

    def test_not_suspect(self):
        """111-112：|x_0|≤999km 或 y_0≠0 → 原样。"""
        s = "+proj=tmerc +x_0=500000 +y_0=3457147.81"
        assert qc._fix_swapped_false_origin(s) == (s, None)

    def test_gk_band_exception(self):
        """115-116：带号制 GK 假东 n×1e6+500k → 不判互换。"""
        s = "+proj=tmerc +x_0=41500000 +y_0=0"
        assert qc._fix_swapped_false_origin(s) == (s, None)

    def test_swap_detected(self):
        """118-128：上海案 → 纠正 x_0=0/y_0=-3457147.81 + 告警。"""
        fixed, warn = qc._fix_swapped_false_origin(SH_PROJ4)
        assert "+x_0=0" in fixed and "+y_0=-3457147.81" in fixed
        assert "假东/假北疑似互换" in warn
        # 原样文本保留（溯源用）→ 纠正不丢其余参数
        assert "+lon_0=121.474" in fixed


class TestFixWkt:

    def test_none(self):
        """136-137。"""
        assert qc._fix_swapped_false_origin_wkt(None, -3457147.81) is None

    def test_wkt1_both_params(self):
        """142-152：WKT1 False_Easting/False_Northing 同步纠正。"""
        wkt = ('PROJCS["X",GEOGCS["GCS"],PROJECTION["Transverse_Mercator"],'
               'PARAMETER["False_Easting",-3457147.81],'
               'PARAMETER["False_Northing",0],UNIT["Meter",1]]')
        out = qc._fix_swapped_false_origin_wkt(wkt, -3457147.81)
        assert 'PARAMETER["False_Easting",0]' in out
        assert 'PARAMETER["False_Northing",-3457147.81]' in out

    def test_bad_type(self):
        """153-154：非字符串 → 异常兜底原样。"""
        assert qc._fix_swapped_false_origin_wkt(123, 1.0) == 123


# ── 纯函数：pyproj 解析 / 域校验 / 分类 ─────────────────────────────────────

class TestParseWithPyproj:

    def test_ok(self):
        """164-168：EPSG:4549 → (4549, proj4, wkt)。"""
        epsg, p4, wkt = qc._parse_with_pyproj("EPSG:4549")
        assert epsg == 4549 and p4 and wkt

    def test_unparseable(self):
        """169-170：不可解析 → None。"""
        assert qc._parse_with_pyproj(GARBAGE) is None


class TestCoordDomainWarning:

    def test_projected(self):
        """387：投影坐标系 → None。"""
        assert qc.coord_domain_warning(False, (110.0, 20.0, 130.0, 40.0)) is None

    def test_in_domain(self):
        assert qc.coord_domain_warning(True, (110.0, 20.0, 130.0, 40.0)) is None

    def test_out_of_domain(self):
        """391：地理坐标系下坐标超 ±180/±90 → 告警。"""
        w = qc.coord_domain_warning(True, (200.0, 5.0, 210.0, 30.0))
        assert w and "坐标域校验失败" in w


class TestClassifySridRow:

    def test_none_row(self):
        """426。"""
        assert qc._classify_srid_row(None) is None

    def test_proj4_longlat(self):
        """428-429。"""
        assert qc._classify_srid_row(("+proj=longlat +datum=WGS84", "")) is True

    def test_proj4_projected(self):
        """430-431。"""
        assert qc._classify_srid_row(("+proj=tmerc +lat_0=0", "")) is False

    def test_wkt_proj(self):
        """432-433。"""
        r = ("", 'PROJCRS["X",PROJECTION["Transverse_Mercator"]]')
        assert qc._classify_srid_row(r) is False

    def test_wkt_geod(self):
        """434-435。"""
        assert qc._classify_srid_row(("", 'GEODCRS["WGS 84",GEOGCS["GCS"]]')) is True

    def test_unresolvable(self):
        """437：无法判定 → None。"""
        assert qc._classify_srid_row(("", "whatever")) is None


# ── 纯函数：parse_crs_definition 分支 ────────────────────────────────────────

class TestParseCrsDefinition:

    def test_empty(self):
        """183-184：空 → (4326, None, None)。"""
        assert qc.parse_crs_definition("") == (4326, None, None)
        assert qc.parse_crs_definition(None) == (4326, None, None)

    def test_epsg_direct(self):
        """187-188：EPSG:4326 → epsg（resolve_crs 短路，proj4/wkt 为 None）。"""
        assert qc.parse_crs_definition("EPSG:4326") == (4326, None, None)
        assert qc.parse_crs_definition("EPSG:4549")[0] == 4549

    def test_pyproj_epsg_fallback(self, monkeypatch):
        """193：resolve_crs 无 epsg 但 pyproj 补出 epsg。"""
        monkeypatch.setattr(
            "app.services.crs_transform.resolve_crs",
            lambda s: SimpleNamespace(epsg=None, proj4=None, wkt=None),
        )
        epsg, p4, wkt = qc.parse_crs_definition("EPSG:4549")
        assert epsg == 4549 and p4

    def test_malformed_wkt(self, monkeypatch):
        """197-200：resolve 认为 WKT 但 pyproj 解析失败 → (None,None,None)。"""
        monkeypatch.setattr(
            "app.services.crs_transform.resolve_crs",
            lambda s: SimpleNamespace(epsg=None, proj4=None,
                                      wkt='GEOGCS["畸形",DATUM["X"]]'),
        )
        assert qc.parse_crs_definition(GARBAGE) == (None, None, None)


# ── 同步版：resolve_ingest_srid_sync / _ensure_registered_sync ───────────────

class TestResolveIngestSridSync:

    @pytest.mark.usefixtures("reset_cache", "track_srids")
    def test_cache_hit(self, sync_session):
        """213-216：二次解析命中缓存（同一 CRS 串）。"""
        qc.resolve_ingest_srid_sync(sync_session, "EPSG:4326")
        assert len(qc._resolution_cache) == 1
        r2 = qc.resolve_ingest_srid_sync(sync_session, "EPSG:4326")
        assert r2.srid == 4326 and len(qc._resolution_cache) == 1

    def test_epsg_direct(self, sync_session):
        """220-221。"""
        r = qc.resolve_ingest_srid_sync(sync_session, "EPSG:4326")
        assert r.srid == 4326 and not r.is_custom and r.source == "epsg"

    def test_register_new(self, sync_session, reset_cache, track_srids):
        """263-283：无 EPSG → INSERT 注册 VCS-CUSTOM。"""
        r = qc.resolve_ingest_srid_sync(sync_session, CUSTOM_PROJ4)
        assert r.is_custom and r.epsg is None and r.source == "wkt-custom"
        assert 910_000 <= r.srid <= 998_999
        row = sync_session.execute(
            text("SELECT auth_name, proj4text FROM spatial_ref_sys WHERE srid=:s"),
            {"s": r.srid},
        ).first()
        assert row[0] == "VCS-CUSTOM"
        assert qc._normalize_crs_text(row[1]) == _expected_proj4()
        track_srids([r.srid])

    def test_existing_match(self, sync_session, reset_cache, track_srids):
        """287-289：已注册且一致 → 直接命中（无新增行）。"""
        r1 = qc.resolve_ingest_srid_sync(sync_session, CUSTOM_PROJ4)
        qc._resolution_cache.clear()
        count_before = sync_session.execute(
            text("SELECT count(*) FROM spatial_ref_sys WHERE srid=:s"), {"s": r1.srid}
        ).scalar()
        r2 = qc.resolve_ingest_srid_sync(sync_session, CUSTOM_PROJ4)
        count_after = sync_session.execute(
            text("SELECT count(*) FROM spatial_ref_sys WHERE srid=:s"), {"s": r1.srid}
        ).scalar()
        assert r2.srid == r1.srid and count_after == count_before == 1
        track_srids([r1.srid])

    def test_self_heal(self, sync_session, reset_cache, track_srids):
        """291-297：注册行定义落后 → 原地自愈 UPDATE。"""
        r1 = qc.resolve_ingest_srid_sync(sync_session, CUSTOM_PROJ4)
        # 人为改旧定义
        sync_session.execute(
            text("UPDATE spatial_ref_sys SET proj4text='+proj=tmerc 旧版本' "
                 "WHERE srid=:s AND auth_name=:a"),
            {"s": r1.srid, "a": qc.AUTH_NAME},
        )
        sync_session.commit()
        qc._resolution_cache.clear()
        r2 = qc.resolve_ingest_srid_sync(sync_session, CUSTOM_PROJ4)
        row = sync_session.execute(
            text("SELECT proj4text FROM spatial_ref_sys WHERE srid=:s"), {"s": r1.srid}
        ).first()
        assert r2.srid == r1.srid
        assert qc._normalize_crs_text(row[0]) == _expected_proj4()
        track_srids([r1.srid])

    def test_fallback_unparseable(self, sync_session, reset_cache, monkeypatch):
        """225-233：resolve 无 epsg 且 pyproj 失败 → 回退 4326（unparseable）。

        resolve_crs 对任意串在 78 行兜底返回 epsg=4326，故真实输入不可达；
        需 patch 元素据为空以进入 unparseable 分支。
        """
        monkeypatch.setattr(
            "app.services.crs_transform.resolve_crs",
            lambda s: SimpleNamespace(epsg=None, proj4=None, wkt=None),
        )
        r = qc.resolve_ingest_srid_sync(sync_session, GARBAGE)
        assert r.srid == 4326 and r.source == "fallback-unparseable"

    def test_empty_is_epsg(self, sync_session, reset_cache):
        """空串 → parse 直返 4326 → epsg 分支（非 fallback）。"""
        r = qc.resolve_ingest_srid_sync(sync_session, "")
        assert r.srid == 4326 and r.source == "epsg"

    def test_collision_retry(self, sync_session, reset_cache, track_srids):
        """298-299：salt0 被外部占用 → 换盐注册成功。"""
        norm = _expected_proj4()  # pyproj 规范化版（resolve 内部实际 hash 输入）
        s0 = qc._deterministic_custom_srid(norm, 0)
        sync_session.execute(
            text("INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text) "
                 "VALUES (:s, 'OTHER', :s, '', '')"),
            {"s": s0},
        )
        sync_session.commit()
        r = qc.resolve_ingest_srid_sync(sync_session, CUSTOM_PROJ4)
        assert r.is_custom and r.srid != s0
        track_srids([r.srid], [s0])

    def test_collision_exhaust(self, sync_session, reset_cache, track_srids):
        """300：连续 10 盐全占用 → RuntimeError。"""
        norm = _expected_proj4()  # 与 resolve 内部 hash 输入一致
        ids = [qc._deterministic_custom_srid(norm, s) for s in range(10)]
        for s in ids:
            sync_session.execute(
                text("INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text) "
                     "VALUES (:s, 'OTHER', :s, '', '')"),
                {"s": s},
            )
        sync_session.commit()
        try:
            with pytest.raises(RuntimeError, match="连续 10 次哈希碰撞"):
                qc.resolve_ingest_srid_sync(sync_session, CUSTOM_PROJ4)
        finally:
            track_srids([], ids)

    def test_swap_quirk_warns(self, sync_session, reset_cache, track_srids):
        """264-266/250-253：SH 案 → 纠正 + 注册（quirk 告警）。"""
        r = qc.resolve_ingest_srid_sync(sync_session, SH_PROJ4)
        assert r.is_custom
        row = sync_session.execute(
            text("SELECT proj4text FROM spatial_ref_sys WHERE srid=:s"), {"s": r.srid}
        ).first()
        assert "+x_0=0" in row[0] and "+y_0=-3457147.81" in row[0]
        track_srids([r.srid])


# ── 异步版：resolve_ingest_srid_async / _ensure_registered_async ─────────────

@pytest.mark.usefixtures("reset_cache")
class TestResolveIngestSridAsync:

    async def test_epsg_direct(self, db_session):
        r = await qc.resolve_ingest_srid_async(db_session, "EPSG:4326")
        assert r.srid == 4326 and not r.is_custom and r.source == "epsg"

    async def test_cache_hit(self, db_session):
        """307-310：async 缓存命中。"""
        r1 = await qc.resolve_ingest_srid_async(db_session, "EPSG:4326")
        r2 = await qc.resolve_ingest_srid_async(db_session, "EPSG:4326")
        assert r1 is r2

    async def test_existing_match(self, db_session, track_srids):
        """361：async 已存在正确注册 → 直接匹配（无新增行）。"""
        r1 = await qc.resolve_ingest_srid_async(db_session, CUSTOM_PROJ4)
        await db_session.commit()
        qc._resolution_cache.clear()
        count_before = (await db_session.execute(
            text("SELECT count(*) FROM spatial_ref_sys WHERE srid=:s"), {"s": r1.srid}
        )).scalar()
        r2 = await qc.resolve_ingest_srid_async(db_session, CUSTOM_PROJ4)
        count_after = (await db_session.execute(
            text("SELECT count(*) FROM spatial_ref_sys WHERE srid=:s"), {"s": r1.srid}
        )).scalar()
        assert r2.srid == r1.srid and count_after == count_before == 1
        await db_session.commit()  # 释放读事务
        track_srids([r1.srid])

    async def test_register_new(self, db_session, track_srids):
        """345-355：async INSERT。"""
        r = await qc.resolve_ingest_srid_async(db_session, CUSTOM_PROJ4)
        assert r.is_custom
        await db_session.commit()
        row = (await db_session.execute(
            text("SELECT auth_name FROM spatial_ref_sys WHERE srid=:s"), {"s": r.srid}
        )).first()
        assert row[0] == qc.AUTH_NAME
        track_srids([r.srid])

    async def test_self_heal(self, db_session, track_srids):
        """363-369：async 自愈 UPDATE。"""
        r1 = await qc.resolve_ingest_srid_async(db_session, CUSTOM_PROJ4)
        await db_session.execute(
            text("UPDATE spatial_ref_sys SET proj4text='+proj=tmerc 旧版本' "
                 "WHERE srid=:s AND auth_name=:a"),
            {"s": r1.srid, "a": qc.AUTH_NAME},
        )
        await db_session.commit()
        qc._resolution_cache.clear()
        r2 = await qc.resolve_ingest_srid_async(db_session, CUSTOM_PROJ4)
        row = (await db_session.execute(
            text("SELECT proj4text FROM spatial_ref_sys WHERE srid=:s"), {"s": r1.srid}
        )).first()
        assert r2.srid == r1.srid
        assert qc._normalize_crs_text(row[0]) == _expected_proj4()
        await db_session.commit()  # 释放行锁，否则 track_srids 的 sync DELETE 无限等待
        track_srids([r1.srid])

    async def test_fallback_unparseable(self, db_session, monkeypatch):
        """319-324：async fallback 告警（同 sync，patch 元素据）。"""
        monkeypatch.setattr(
            "app.services.crs_transform.resolve_crs",
            lambda s: SimpleNamespace(epsg=None, proj4=None, wkt=None),
        )
        r = await qc.resolve_ingest_srid_async(db_session, GARBAGE)
        assert r.source == "fallback-unparseable"

    async def test_collision_retry(self, db_session, track_srids):
        """370：async 换盐。"""
        norm = _expected_proj4()
        s0 = qc._deterministic_custom_srid(norm, 0)
        await db_session.execute(
            text("INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text) "
                 "VALUES (:s, 'OTHER', :s, '', '')"),
            {"s": s0},
        )
        await db_session.commit()
        r = await qc.resolve_ingest_srid_async(db_session, CUSTOM_PROJ4)
        assert r.is_custom and r.srid != s0
        await db_session.commit()  # 提交 resolve 的 INSERT，释放行锁
        track_srids([r.srid], [s0])

    async def test_collision_exhaust(self, db_session, track_srids):
        """371：async RuntimeError。"""
        norm = _expected_proj4()
        ids = [qc._deterministic_custom_srid(norm, s) for s in range(10)]
        for s in ids:
            await db_session.execute(
                text("INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text) "
                     "VALUES (:s, 'OTHER', :s, '', '')"),
                {"s": s},
            )
        await db_session.commit()
        try:
            with pytest.raises(RuntimeError, match="连续 10 次哈希碰撞"):
                await qc.resolve_ingest_srid_async(db_session, CUSTOM_PROJ4)
        finally:
            track_srids([], ids)

    async def test_swap_quirk(self, db_session, track_srids):
        """338/346-355：async SH 纠正注册。"""
        r = await qc.resolve_ingest_srid_async(db_session, SH_PROJ4)
        assert r.is_custom
        await db_session.commit()
        row = (await db_session.execute(
            text("SELECT proj4text FROM spatial_ref_sys WHERE srid=:s"), {"s": r.srid}
        )).first()
        assert "+x_0=0" in row[0]
        track_srids([r.srid])


# ── sync 地理判定 + extent 校验 ──────────────────────────────────────────────

class TestGeographicSync:

    def test_get_is_geographic(self, sync_session):
        """411-413：4326 → True。"""
        assert qc.get_srid_is_geographic_sync(sync_session, 4326) is True

    def test_unknown_srid_none(self, sync_session):
        assert qc.get_srid_is_geographic_sync(sync_session, 2_000_000) is None

    def test_extent_warning_empty(self):
        """486-487：is_geo None 或无 extent → None。"""
        assert qc._extent_warning(None, [("1", "2", "3", "4")]) is None
        assert qc._extent_warning(True, []) is None

    def test_extent_warning_aggregate(self):
        """488-492：多表 extent 聚合 → 超域告警。"""
        w = qc._extent_warning(True, [(200.0, 5.0, 210.0, 30.0), (220.0, 10.0, 230.0, 40.0)])
        assert w and "坐标域校验失败" in w

    def test_validate_loaded_extent_sync_no_data(self, sync_session):
        """471-481：有地理判定但无要素 → None。"""
        assert qc.validate_loaded_extent_sync(sync_session, "no-such-fid", 4326) is None


class TestValidateLoadedExtentSyncReal(TestGeographicSync):
    """真实空间数据：point_features 分区 + ST_Extent 超域 → 告警。"""

    FID = "p6b-ext-fid"

    async def test_validate_loaded_extent_sync_out_of_domain(self, db_session, sync_session):
        # 建 LIST 子分区并插入 4326 域外点（200, 5）
        await db_session.execute(text(
            "CREATE TABLE IF NOT EXISTS point_features_p6b PARTITION OF point_features "
            "FOR VALUES IN ('" + self.FID + "')"
        ))
        await db_session.execute(text(
            "INSERT INTO point_features (id, stored_file_id, layer_name, source_name, "
            "feature_index, geometry_type, geometry, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :fid, 'L', 'src', 0, 'Point', "
            "ST_GeomFromText('POINT(200 5)', 4326), now(), now())"
        ), {"fid": self.FID})
        await db_session.commit()
        try:
            w = qc.validate_loaded_extent_sync(sync_session, self.FID, 4326)
            assert w and "坐标域校验失败" in w
        finally:
            # 结束 validate 的读事务，否则其 ACCESS SHARE 锁阻塞 DROP 的
            # AccessExclusiveLock（父表 point_features），导致无限等待
            sync_session.rollback()
            await db_session.execute(text("DROP TABLE IF EXISTS point_features_p6b"))
            await db_session.commit()

    async def test_validate_loaded_extent_async_out_of_domain(self, db_session):
        """450-466：async validate_loaded_extent 真实分区超域 → 告警。"""
        await db_session.execute(text(
            "CREATE TABLE IF NOT EXISTS point_features_p6b PARTITION OF point_features "
            "FOR VALUES IN ('" + self.FID + "')"
        ))
        await db_session.execute(text(
            "INSERT INTO point_features (id, stored_file_id, layer_name, source_name, "
            "feature_index, geometry_type, geometry, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :fid, 'L', 'src', 0, 'Point', "
            "ST_GeomFromText('POINT(200 5)', 4326), now(), now())"
        ), {"fid": self.FID})
        await db_session.commit()
        try:
            w = await qc.validate_loaded_extent(db_session, self.FID, 4326)
            assert w and "坐标域校验失败" in w
        finally:
            await db_session.commit()  # 结束读事务，释放 ACCESS SHARE 锁
            await db_session.execute(text("DROP TABLE IF EXISTS point_features_p6b"))
            await db_session.commit()