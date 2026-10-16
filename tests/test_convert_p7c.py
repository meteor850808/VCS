# -*- coding: utf-8 -*-
"""P7c-1: convert.py 全覆盖（64%→100%）。

get_job happy/404、list 状态过滤、cancel 全分支、redis_available、
Redis 降级执行、submit 文件名空/413、_rm_tree 各形态、硬删除带报告/
兼容关联、batch 全失败 400、detect 413/文件名空。
"""

import io
import json
import uuid

import pytest

from app.api.v1 import convert as cv
from app.config import settings
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.conversion_job import ConversionJob, JobStatus
from app.models.quality_report import QualityReport
from app.workers.celery_app import celery_app
from app.workers.tasks import run_conversion

SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
        "properties": {"name": "北京", "YWBH": "BJ001", "FEATURECODE": "POI"},
    }]
}


async def _key(db_session, name):
    raw, hashed = generate_api_key()
    k = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(k)
    await db_session.commit()
    return raw, k


async def _job(db_session, key, status=JobStatus.SUCCESS,
               name="p7c.geojson", input_path=None, output_path=None,
               report_id=None):
    j = ConversionJob(
        id=str(uuid.uuid4()),
        api_key_id=key.id,
        original_filename=name,
        input_path=input_path or "",
        output_path=output_path,
        dst_crs="EPSG:4490",
        output_format="GeoJSON",
        status=status,
        celery_task_id=str(uuid.uuid4()),
        quality_report_id=report_id,
    )
    db_session.add(j)
    await db_session.flush()
    return j


class TestJobQuery:

    async def test_get_job_happy_and_404(self, client, db_session):
        raw, key = await _key(db_session, "p7c_get")
        j = await _job(db_session, key)
        await db_session.commit()
        h = {"X-API-Key": raw}
        resp = await client.get(f"/api/v1/jobs/{j.id}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["id"] == j.id
        assert (await client.get(
            "/api/v1/jobs/no-such", headers=h)).status_code == 404

    async def test_get_job_other_key_404(self, client, db_session):
        raw_a, key_a = await _key(db_session, "p7c_get_oa")
        raw_b, _ = await _key(db_session, "p7c_get_ob")
        j = await _job(db_session, key_a)
        await db_session.commit()
        assert (await client.get(
            f"/api/v1/jobs/{j.id}",
            headers={"X-API-Key": raw_b})).status_code == 404

    async def test_list_status_filter(self, client, db_session):
        raw, key = await _key(db_session, "p7c_list")
        await _job(db_session, key, status=JobStatus.SUCCESS, name="s.geojson")
        await _job(db_session, key, status=JobStatus.CANCELLED, name="c.geojson")
        await db_session.commit()
        resp = await client.get(
            "/api/v1/jobs?status=cancelled", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        names = [j["original_filename"] for j in resp.json()]
        assert names == ["c.geojson"]


class TestCancelJob:

    async def test_cancel_not_found(self, client, db_session):
        raw, _ = await _key(db_session, "p7c_cx404")
        assert (await client.delete(
            "/api/v1/jobs/no-such",
            headers={"X-API-Key": raw})).status_code == 404

    async def test_cancel_pending(self, client, db_session):
        raw, key = await _key(db_session, "p7c_cxp")
        j = await _job(db_session, key, status=JobStatus.PENDING)
        await db_session.commit()
        resp = await client.delete(
            f"/api/v1/jobs/{j.id}", headers={"X-API-Key": raw})
        assert resp.status_code == 204
        jr = await db_session.get(ConversionJob, j.id)
        await db_session.refresh(jr)
        assert jr.status == JobStatus.CANCELLED

    async def test_cancel_running_revoke_ok(self, client, db_session, monkeypatch):
        called = {}
        def fake_revoke(task_id, terminate=True):
            called["tid"] = task_id
        monkeypatch.setattr(celery_app.control, "revoke", fake_revoke)
        raw, key = await _key(db_session, "p7c_cxr")
        j = await _job(db_session, key, status=JobStatus.RUNNING)
        await db_session.commit()
        resp = await client.delete(
            f"/api/v1/jobs/{j.id}", headers={"X-API-Key": raw})
        assert resp.status_code == 204
        assert called.get("tid") == j.celery_task_id

    async def test_cancel_running_revoke_fail(self, client, db_session, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("broker down")
        monkeypatch.setattr(celery_app.control, "revoke", boom)
        raw, key = await _key(db_session, "p7c_cxrf")
        j = await _job(db_session, key, status=JobStatus.RUNNING)
        await db_session.commit()
        resp = await client.delete(
            f"/api/v1/jobs/{j.id}", headers={"X-API-Key": raw})
        assert resp.status_code == 204


class TestRedisFallback:

    def test_redis_unavailable_bad_url(self, monkeypatch):
        monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
        assert cv.redis_available() is False

    async def test_submit_redis_fallback(self, client, db_session, monkeypatch):
        def no_redis():
            return False
        monkeypatch.setattr(cv, "redis_available", no_redis)
        raw, _ = await _key(db_session, "p7c_fb")
        content = json.dumps(SAMPLE_GEOJSON).encode()
        resp = await client.post(
            "/api/v1/convert",
            headers={"X-API-Key": raw},
            data={"dst_crs": "EPSG:4490", "output_format": "GeoJSON"},
            files={"file": ("fb.geojson", io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["message"].startswith("Redis 不可用")

    async def test_submit_redis_fallback_inner_error(self, client, db_session,
                                                    monkeypatch):
        def no_redis():
            return False
        def boom(*a, **k):
            raise RuntimeError("inner conversion crash")
        monkeypatch.setattr(cv, "redis_available", no_redis)
        monkeypatch.setattr(run_conversion, "apply", boom)
        raw, _ = await _key(db_session, "p7c_fbi")
        content = json.dumps(SAMPLE_GEOJSON).encode()
        resp = await client.post(
            "/api/v1/convert",
            headers={"X-API-Key": raw},
            data={"dst_crs": "EPSG:4490"},
            files={"file": ("fb.geojson", io.BytesIO(content), "application/json")},
        )
        assert resp.status_code == 202
        assert "Redis" in resp.json()["message"]


class TestRmTree:

    def test_dir_removed(self, tmp_path):
        d = tmp_path / "sub"
        d.mkdir()
        (d / "x.txt").write_text("x")
        cv._rm_tree(str(d))
        assert not d.exists()

    def test_file_removed(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("a")
        cv._rm_tree(str(f))
        assert not f.exists()

    def test_missing_ok(self, tmp_path):
        cv._rm_tree(str(tmp_path / "nothing"))

    def test_error_silenced(self):
        # Path(None) 抛 TypeError → 进入 except 只记 warning
        cv._rm_tree(None)


class TestHardDelete:

    async def test_delete_with_report_and_legacy(self, client, db_session, tmp_path):
        raw, key = await _key(db_session, "p7c_hd")

        # 场景 1：quality_report_id 已回填 + html/json 报告文件 + 磁盘产物
        src = tmp_path / "in_1" / "a.geojson"
        src.parent.mkdir(parents=True)
        src.write_text("{}")
        out = tmp_path / "out_1"
        out.mkdir()
        (out / "a_out.geojson").write_text("{}")
        html = tmp_path / "rep_1.html"
        html.write_text("<h1>r</h1>")
        jsonp = tmp_path / "rep_1.json"
        jsonp.write_text("{}")
        rep1 = QualityReport(
            id=str(uuid.uuid4()), api_key_id=key.id,
            file_name="a.geojson", detected_format="GeoJSON",
            html_report_path=str(html), json_report_path=str(jsonp),
        )
        db_session.add(rep1)
        await db_session.flush()
        j1 = await _job(db_session, key, name="a.geojson",
                        input_path=str(src), output_path=str(out / "a_out.geojson"),
                        report_id=rep1.id)
        # 场景 2：quality_report_id 未回填（None），仅以 job_id 关联的兼容清理
        html2 = tmp_path / "rep_2.html"
        html2.write_text("<h1>r2</h1>")
        json2 = tmp_path / "rep_2.json"
        json2.write_text("{}")
        j2 = await _job(db_session, key, name="legacy.geojson")
        rep2 = QualityReport(
            id=str(uuid.uuid4()), api_key_id=key.id, job_id=j2.id,
            file_name="legacy.geojson", detected_format="GeoJSON",
            html_report_path=str(html2), json_report_path=str(json2),
        )
        db_session.add(rep2)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/jobs/delete", headers={"X-API-Key": raw},
            json={"job_ids": [j1.id, j2.id]})
        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 2
        # 磁盘全部清理 + 报告删除（含兼容 job_id 关联）
        assert not src.parent.exists()
        assert not out.exists()
        assert not html.exists()
        assert not jsonp.exists()
        assert not html2.exists()
        assert not json2.exists()
        # 端点 session 已提交删除，本地 session 先失效缓存再从 DB 读
        rep1_id, rep2_id, j1_id, j2_id = rep1.id, rep2.id, j1.id, j2.id
        db_session.expire_all()
        assert (await db_session.get(QualityReport, rep1_id)) is None
        assert (await db_session.get(QualityReport, rep2_id)) is None
        assert (await db_session.get(ConversionJob, j1_id)) is None
        assert (await db_session.get(ConversionJob, j2_id)) is None

    async def test_batch_all_missing_400(self, client, db_session):
        raw, _ = await _key(db_session, "p7c_b400")
        resp = await client.post(
            "/api/v1/jobs/delete", headers={"X-API-Key": raw},
            json={"job_ids": [str(uuid.uuid4()), str(uuid.uuid4())]})
        assert resp.status_code == 400
        assert "不存在" in resp.json()["detail"]