# -*- coding: utf-8 -*-
"""app/api/v1/quality.py 路由级回归测试（补路由级覆盖：27% → 90%+ 目标）。

覆盖策略：以 tests/conftest.py 的异步 client + api_key_header + db_session 夹具，
在 vcs_test 库直接造 StoredFile / QualityReport 记录，并对
app.api.v1.quality.run_quality_check 打桩（返回确定的 check_result dict），
以驱动 /quality/* 各端点的分支（权限校验 / 无要素拒检 / 报告详情派生 /
HTML 即时生成 / JSON 下载 / GeoJSON+Excel 导出 / delete / batch-delete / delete-all）。

说明：
- run_quality_check 是真实全流程（属性+拓扑+评分，依赖真实要素），既有覆盖见
  tests/test_topology_checker.py（topology_checker 100%）；此处打桩隔离，
  仅验证路由层的编排、鉴权与响应构造。
- 分表"实际有要素但 feature_count=0 放行"分支（S4）单独覆盖。
- check/upload 的新 orchestrator（指定 data_standard_id → run_quality_check）
  与旧 check_quality 回退链路分别覆盖；回退依赖 GDAL 的真实同步接口，
  复用与 test_api.py TestQuality 一致的小文件方式。
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.models.stored_file import StoredFile
from app.models.quality_report import QualityReport
from app.models.api_key import ApiKey


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    """与其它路由测试相同的测试库 autouse 包装。"""
    yield


async def _create_key(db_session, name):
    """快速建测试 Key，返回 (raw, key_orm)。"""
    from app.core.security import generate_api_key
    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.commit()
    return raw, key


_SAMPLE_RESULT = {
    "attr_results": [],
    "attr_summary": {
        "total": 0, "passed": 0, "failed": 0,
        "by_severity": {"error": 0, "warning": 0},
    },
    "topo_results": [],
    "topo_summary": {
        "total": 0, "passed": 0, "failed": 0,
        "by_severity": {"error": 0, "warning": 0},
    },
    "scores": {
        "completeness": 1.0, "accuracy": 1.0, "consistency": 1.0,
        "uniqueness": 1.0, "topology": 1.0, "overall": 1.0,
    },
    "total_violations": 0,
    "violation_samples": [],
}


# ── POST /quality/check（对已存储文件质检） ─────────────────────────────────

class TestQualityCheckStored:
    async def test_check_stored_success(self, client, db_session):
        """正常路径：有要素文件 + run_quality_check 打桩 → 200 与响应字段。"""
        raw, key = await _create_key(db_session, "qc_stored_ok")
        sf = StoredFile(
            original_name="路网.geojson", api_key_id=key.id,
            layer_name="路网", content_type="application/geo+json",
            file_size=10, file_data=b"{}", feature_count=5,
        )
        db_session.add(sf)
        await db_session.commit()

        with patch(
            "app.api.v1.quality.run_quality_check",
            new=AsyncMock(return_value=_SAMPLE_RESULT),
        ):
            resp = await client.post(
                "/api/v1/quality/check",
                params={"file_id": sf.id},
                json={
                    "geometry_type": "LineString",
                    # 覆盖拓扑配置的自定义 tolerances 解析分支
                    "topology_config": {
                        "enabled_rules": ["must_not_self_intersect"],
                        "tolerances": {"snap": 0.001},
                    },
                },
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["stored_file_id"] == sf.id
        assert body["file_name"] == "路网.geojson"
        assert body["total_violations"] == 0
        assert body["scores"]["completeness"] == 1.0

        # 校验写入了 QualityReport
        row = await db_session.get(QualityReport, body["report_id"])
        assert row is not None
        assert row.stored_file_id == sf.id
        assert row.html_report_path is not None

    async def test_check_stored_not_found(self, client, db_session):
        """文件不存在或无权限 → 404。"""
        raw, key = await _create_key(db_session, "qc_404")
        resp = await client.post(
            "/api/v1/quality/check",
            params={"file_id": "no-such-file"},
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]

    async def test_check_stored_no_features_reject(self, client, db_session):
        """feature_count=0 且分表无实际要素 → 400 拒检（不生成空报告满分）。"""
        raw, key = await _create_key(db_session, "qc_nofeat")
        sf = StoredFile(
            original_name="空.geojson", api_key_id=key.id,
            layer_name="空", content_type="application/geo+json",
            file_size=10, file_data=b"{}", feature_count=0,
        )
        db_session.add(sf)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/quality/check",
            params={"file_id": sf.id},
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 400
        assert "无图形要素" in resp.json()["detail"]

    async def test_check_stored_orphan_file_404(self, client, db_session):
        """文件存在但 api_key 不同 → 404 无权限。"""
        raw, key = await _create_key(db_session, "qc_owner")
        other_raw, other_key = await _create_key(db_session, "qc_other")
        sf = StoredFile(
            original_name="他人文件.geojson", api_key_id=key.id,
            layer_name="他人文件", content_type="application/geo+json",
            file_size=10, file_data=b"{}", feature_count=5,
        )
        db_session.add(sf)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/quality/check",
            params={"file_id": sf.id},
            headers={"X-API-Key": other_raw},
        )
        assert resp.status_code == 404


# ── POST /quality/check/upload（上传 + 质检，兼容旧接口） ───────────────────

class TestQualityCheckUpload:
    async def test_upload_new_orchestrator(self, client, db_session, tmp_path):
        """指定 data_standard_id → 走 run_quality_check 新 orchestrator → 200。"""
        from app.models.data_standard import DataStandard
        raw, key = await _create_key(db_session, "qc_upload_new")
        std = DataStandard(api_key_id=key.id, name="测试标准", code="STD-T", category="custom")
        db_session.add(std)
        await db_session.commit()

        with patch(
            "app.api.v1.quality.run_quality_check",
            new=AsyncMock(return_value=_SAMPLE_RESULT),
        ):
            resp = await client.post(
                "/api/v1/quality/check/upload",
                data={
                    "data_standard_id": std.id,
                    # 新 orchestrator 分支的 topology_config 解析（279-280）
                    "topology_config": '{"enabled_rules": ["must_not_overlap"], "tolerances": {"gap": 1e-4}}',
                },
                files={"file": ("道路.shp", b"fake-shp", "application/octet-stream")},
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["file_name"] == "道路.shp"
        assert body["total_features"] == 0
        assert body["score_completeness"] == 1.0

    async def test_upload_fallback_legacy(self, client, db_session):
        """未指定标准 → 回退旧 check_quality 同步接口 → 200。

        用无标准的最小真实文件，走 load_standard_fields(None) → check_quality。
        """
        raw, key = await _create_key(db_session, "qc_upload_legacy")
        import io
        # 构造最小 GeoJSON（属性结构检查所需字段齐全）
        geojson = (
            '{"type":"FeatureCollection","features":['
            '{"type":"Feature","properties":{"name":"A"},"geometry":'
            '{"type":"Point","coordinates":[1.0,1.0]}}]}'
        ).encode("utf-8")
        resp = await client.post(
            "/api/v1/quality/check/upload",
            files={"file": ("点.geojson", io.BytesIO(geojson), "application/geo+json")},
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["detected_format"] is not None

    async def test_upload_invalid_topology_json(self, client, db_session):
        """回退链路里 topology_config 非 JSON → 400。"""
        raw, key = await _create_key(db_session, "qc_upload_badjson")
        import io
        geojson = (
            '{"type":"FeatureCollection","features":['
            '{"type":"Feature","properties":{"name":"A"},"geometry":'
            '{"type":"Point","coordinates":[1.0,1.0]}}]}'
        ).encode("utf-8")
        resp = await client.post(
            "/api/v1/quality/check/upload",
            data={"topology_config": "{not-json"},
            files={"file": ("点.geojson", io.BytesIO(geojson), "application/geo+json")},
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 400
        assert "topology_config" in resp.json()["detail"]

    async def test_upload_orchestrator_failure_fallback_404(self, client, db_session, tmp_path):
        """新 orchestrator 抛异常 → 回退旧接口；但标准不可达 → 404。

        覆盖 except Exception 回退分支 + load_standard_fields None → 404 分支。
        """
        from app.models.data_standard import DataStandard
        from app.core.security import generate_api_key
        from app.models.api_key import ApiKey
        raw, key = await _create_key(db_session, "qc_upload_fb404")
        # 该标准属于另一个 key → load_standard_fields 权限校验失败返回 None
        other_key = ApiKey(key_hash=generate_api_key()[1], name="qc_fb_other", is_active=True)
        db_session.add(other_key)
        await db_session.commit()
        std = DataStandard(api_key_id=other_key.id, name="他人标准", code="OTHER", category="custom")
        db_session.add(std)
        await db_session.commit()

        import io
        geojson = (
            '{"type":"FeatureCollection","features":['
            '{"type":"Feature","properties":{"name":"A"},"geometry":'
            '{"type":"Point","coordinates":[1.0,1.0]}}]}'
        ).encode("utf-8")

        with patch(
            "app.api.v1.quality.run_quality_check",
            new=AsyncMock(side_effect=RuntimeError("orchestrator boom")),
        ):
            resp = await client.post(
                "/api/v1/quality/check/upload",
                data={"data_standard_id": std.id},
                files={"file": ("点.geojson", io.BytesIO(geojson), "application/geo+json")},
                headers={"X-API-Key": raw},
            )
        assert resp.status_code == 404
        assert "数据标准不存在" in resp.json()["detail"]


# ── GET /quality 列表 / 详情 / html / json / export ─────────────────────────

async def _seed_report(db_session, key, file_name="路网.geojson", with_violations=True):
    """造一条 QualityReport，返回其 id。"""
    report = QualityReport(
        api_key_id=key.id,
        file_name=file_name,
        detected_format="stored_file",
        total_features=5,
        total_fields=3,
        completeness_score=0.9,
        score_completeness=0.9,
        score_accuracy=0.85,
        score_consistency=0.8,
        score_uniqueness=0.75,
        score_topology=0.7,
        constraint_violations=(
            [
                {
                    "rule_code": "attr_1", "rule_name": "字段非空",
                    "status": "fail", "severity": "error",
                    "failed_count": 2, "total_checked": 5, "message": "m",
                    "field": "name", "layer": "路网",
                    "samples": [0, "缺失"],
                }
            ]
            if with_violations else []
        ),
        topology_violations=(
            [
                {
                    "rule_code": "32xx", "rule_name": "自相交",
                    "status": "fail", "severity": "warning",
                    "failed_count": 1, "total_checked": 5, "message": "t",
                    "feature_indices": [3],
                }
            ]
            if with_violations else []
        ),
        attribute_summary={"total": 1, "passed": 0, "failed": 1},
        topology_summary={"total": 1, "passed": 0, "failed": 1},
    )
    db_session.add(report)
    await db_session.flush()
    return report.id


class TestQualityDetail:
    async def test_get_report_detail(self, client, db_session):
        """详情端点派生违规样本 / 严重度分布 / 违规总数。"""
        raw, key = await _create_key(db_session, "qc_detail")
        rid = await _seed_report(db_session, key)
        await db_session.commit()

        resp = await client.get(f"/api/v1/quality/{rid}", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == rid
        assert body["total_violations"] == 3
        assert body["severity_dist"] == {"error": 1, "warning": 1}
        # v2.20 权重契约：score_overall 由后端按 WEIGHTS 计算（0.9×.25+.85×.25+.8×.20+.75×.15+.7×.15）
        assert body["score_overall"] == pytest.approx(0.815)
        samples = body["violation_samples"]
        # 属性样本：int → feature_index，其它 → sample
        attr_smpl = [s for s in samples if s["rule_code"] == "attr_1"]
        assert any(s["feature_index"] == 0 and s["sample"] is None for s in attr_smpl)
        assert any(s["sample"] == "缺失" for s in attr_smpl)
        topo_smpl = [s for s in samples if s["rule_code"].startswith("32")]
        assert any(s["feature_index"] == 3 for s in topo_smpl)

    async def test_get_report_detail_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_detail_404")
        resp = await client.get("/api/v1/quality/nope", headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_get_report_detail_no_violations(self, client, db_session):
        """无违规数据 → 空派生结果。"""
        raw, key = await _create_key(db_session, "qc_detail_empty")
        rid = await _seed_report(db_session, key, with_violations=False)
        await db_session.commit()
        resp = await client.get(f"/api/v1/quality/{rid}", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_violations"] == 0
        assert body["violation_samples"] == []

    async def test_get_report_detail_mixed_status(self, client, db_session):
        """非 fail 状态行被跳过（446/461 continue 分支），severity 兜底计数。"""
        raw, key = await _create_key(db_session, "qc_detail_mixed")
        report = QualityReport(
            api_key_id=key.id,
            file_name="混合.geojson",
            detected_format="stored_file",
            total_features=3,
            constraint_violations=[
                {"rule_code": "p1", "rule_name": "通过项", "status": "pass",
                 "severity": "info", "total_checked": 1, "failed_count": 0,
                 "message": "ok", "samples": ["x"]},
                {"rule_code": "f1", "rule_name": "失败项", "status": "fail",
                 "severity": "info", "total_checked": 3, "failed_count": 2,
                 "message": "bad", "samples": [7]},
            ],
            topology_violations=[
                {"rule_code": "t1", "rule_name": "拓扑通过", "status": "pass",
                 "severity": "info", "total_checked": 1, "failed_count": 0,
                 "message": "ok"},
                {"rule_code": "t2", "rule_name": "拓扑跳过", "status": "skip",
                 "severity": "info", "total_checked": 1, "failed_count": 0,
                 "message": "skipped"},
            ],
        )
        db_session.add(report)
        await db_session.commit()

        resp = await client.get(f"/api/v1/quality/{report.id}", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        # 仅 fail 状态计入违规
        assert body["total_violations"] == 2
        # 未知严重度被兜底计入（sev=info / None→'warning 缺失'键不存在则记为 0，实际按 get 取值）
        sev = body["severity_dist"]
        f1_sev = [s["severity"] for s in body["constraint_violations"]
                  if s.get("rule_code") == "f1"]
        assert f1_sev == ["info"]


class TestQualityHtml:
    async def test_html_report(self, client, db_session):
        """HTML 报告即时生成，包含五维仪表与严重度表。"""
        raw, key = await _create_key(db_session, "qc_html")
        rid = await _seed_report(db_session, key)
        await db_session.commit()
        resp = await client.get(f"/api/v1/quality/{rid}/html", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        assert "质量检查报告" in html
        assert "错误" in html
        assert "综合评分" in html

    async def test_html_report_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_html_404")
        resp = await client.get("/api/v1/quality/nope/html", headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_html_report_mixed_status_rows(self, client, db_session):
        """HTML 样本循环跳过非 fail 状态行（602 continue）。"""
        raw, key = await _create_key(db_session, "qc_html_skip")
        report = QualityReport(
            api_key_id=key.id,
            file_name="混合2.geojson",
            detected_format="stored_file",
            total_features=2,
            score_completeness=0.5,
            score_accuracy=0.5,
            score_consistency=0.5,
            score_uniqueness=0.5,
            score_topology=0.5,
            constraint_violations=[
                {"rule_code": "p1", "status": "pass", "total_checked": 1,
                 "failed_count": 0, "message": "ok", "samples": ["x"]},
            ],
            topology_violations=[],
        )
        db_session.add(report)
        await db_session.commit()

        resp = await client.get(f"/api/v1/quality/{report.id}/html", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        # pass 行显示为「通过」，样本保持空
        assert "通过" in resp.text
        assert "无违规样本" in resp.text

    async def test_html_report_all_pass(self, client, db_session):
        """全零分 + 无违规 → 红色阈值分支、sev-ok、样本表为空。"""
        raw, key = await _create_key(db_session, "qc_html_pass")
        report = QualityReport(
            api_key_id=key.id,
            file_name="通过.geojson",
            detected_format="stored_file",
            total_features=1,
            score_completeness=0.0,
            score_accuracy=0.0,
            score_consistency=0.0,
            score_uniqueness=0.0,
            score_topology=0.0,
            constraint_violations=[],
            topology_violations=[],
        )
        db_session.add(report)
        await db_session.commit()

        resp = await client.get(f"/api/v1/quality/{report.id}/html", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        html = resp.text
        assert "全部检查通过" in html
        assert "无违规样本" in html


class TestQualityJson:
    async def test_json_report(self, client, db_session):
        """JSON 下载 → 200 + 附件头。"""
        raw, key = await _create_key(db_session, "qc_json")
        rid = await _seed_report(db_session, key)
        report = await db_session.get(QualityReport, rid)
        report.json_report_path = str(__file__)  # 指向真实存在的文件
        await db_session.commit()
        resp = await client.get(f"/api/v1/quality/{rid}/json", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    async def test_json_report_missing_path(self, client, db_session):
        """报告无 json_report_path → 404。"""
        raw, key = await _create_key(db_session, "qc_json_nopath")
        rid = await _seed_report(db_session, key)  # json_report_path 为 None
        await db_session.commit()
        resp = await client.get(f"/api/v1/quality/{rid}/json", headers={"X-API-Key": raw})
        assert resp.status_code == 404


class TestQualityList:
    async def test_list_reports(self, client, db_session):
        """列表按时间倒序返回，带五维评分与违规计数。"""
        raw, key = await _create_key(db_session, "qc_list")
        await _seed_report(db_session, key, file_name="A.geojson")
        await _seed_report(db_session, key, file_name="B.geojson")
        await db_session.commit()

        resp = await client.get("/api/v1/quality", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = [r["file_name"] for r in data]
        assert set(names) == {"A.geojson", "B.geojson"}
        first = data[0]
        assert first["score_completeness"] == 0.9
        assert first["issues_count"] == 2  # attr 1 + topo 1

    async def test_list_reports_pagination(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_list_pg")
        for i in range(3):
            await _seed_report(db_session, key, file_name=f"F{i}.geojson")
        await db_session.commit()
        resp = await client.get(
            "/api/v1/quality", params={"page": 2, "page_size": 2},
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestQualityExport:
    async def test_export_geojson(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_geo")
        rid = await _seed_report(db_session, key)
        await db_session.commit()
        with patch(
            "app.api.v1.quality.export_violations_geojson",
            new=AsyncMock(return_value=b'{"type":"FeatureCollection"}'),
        ):
            resp = await client.get(
                f"/api/v1/quality/{rid}/export", params={"format": "geojson"},
                headers={"X-API-Key": raw},
            )
        assert resp.status_code == 200
        assert "geo+json" in resp.headers["content-type"]
        assert resp.json()["type"] == "FeatureCollection"

    async def test_export_excel(self, client, db_session):
        """Excel 导出分支。"""
        raw, key = await _create_key(db_session, "qc_xlsx")
        rid = await _seed_report(db_session, key)
        await db_session.commit()

        def _fake_excel(report, path):
            import openpyxl
            wb = openpyxl.Workbook()
            wb.save(path)

        with patch(
            "app.api.v1.quality.export_violations_excel",
            new=_fake_excel,
        ):
            resp = await client.get(
                f"/api/v1/quality/{rid}/export", params={"format": "excel"},
                headers={"X-API-Key": raw},
            )
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["content-type"] or "octet-stream" in resp.headers["content-type"]

    async def test_export_report_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_exp_404")
        resp = await client.get(
            "/api/v1/quality/nope/export", params={"format": "geojson"},
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 404


# ── 删除端点 ───────────────────────────────────────────────────────────────

class TestQualityDelete:
    async def test_delete_single(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_del")
        rid = await _seed_report(db_session, key)
        await db_session.commit()
        resp = await client.delete(f"/api/v1/quality/{rid}", headers={"X-API-Key": raw})
        assert resp.status_code == 204
        row = await db_session.get(QualityReport, rid)
        assert row is None

    async def test_delete_single_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_del_404")
        resp = await client.delete("/api/v1/quality/nope", headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_batch_delete(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_delb")
        r1 = await _seed_report(db_session, key, file_name="A.geojson")
        r2 = await _seed_report(db_session, key, file_name="B.geojson")
        await db_session.commit()
        resp = await client.post(
            "/api/v1/quality/batch-delete",
            data={"report_ids": [r1, r2]},
            headers={"X-API-Key": raw},
        )
        assert resp.status_code == 204
        assert await db_session.get(QualityReport, r1) is None
        assert await db_session.get(QualityReport, r2) is None

    async def test_delete_all(self, client, db_session):
        raw, key = await _create_key(db_session, "qc_dela")
        await _seed_report(db_session, key, file_name="A.geojson")
        await _seed_report(db_session, key, file_name="B.geojson")
        await db_session.commit()
        resp = await client.post("/api/v1/quality/delete-all", headers={"X-API-Key": raw})
        assert resp.status_code == 204
        remaining = (await db_session.execute(
            __import__("sqlalchemy").select(QualityReport)
        )).scalars().all()
        # 其余测试文件也可能造报告，仅断言本 key 的报告被清空
        mine = [r for r in remaining if r.api_key_id == key.id]
        assert mine == []
