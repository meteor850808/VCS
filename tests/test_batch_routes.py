# -*- coding: utf-8 -*-
"""app/api/v1/batch.py 路由级回归测试（补路由级覆盖：30% → 90%+ 目标）。

覆盖策略：以 tests/conftest.py 的异步 client + db_session 夹具，在 vcs_test 库
直接造 BatchJob / ConversionJob 记录，并对 `redis_available` 与
`run_conversion.apply` / `apply_async` / celery `revoke` 打桩：
- 避免真实连 Redis（本机若有 Redis，`redis_available` 可能为 True 走非确定分支）
- 验证提交编排的两大分支：Redis 可用（apply_async 并行提交）与
  Redis 不可用（降级进程内 `apply` + max_retries 保存/恢复）
- 验证取消路径对 RUNNING 且带 celery_task_id 子任务的 revoke 语义与状态翻转
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from sqlalchemy import select

from app.config import settings
from app.models.api_key import ApiKey
from app.models.batch_job import BatchJob, BatchJobStatus
from app.models.conversion_job import ConversionJob, JobStatus


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


def _make_batch(db, key, name="批量A", status=BatchJobStatus.SUCCESS, **kw):
    batch = BatchJob(
        api_key_id=key.id,
        name=name,
        status=status,
        total_count=kw.get("total_count", 1),
        success_count=kw.get("success_count", 0),
        failed_count=kw.get("failed_count", 0),
        output_format=kw.get("output_format", "GeoJSON"),
        dst_crs=kw.get("dst_crs", "EPSG:4490"),
    )
    if "created_at" in kw:
        batch.created_at = kw["created_at"]
    db.add(batch)
    return batch


def _make_child(db, key, batch_id, filename="a.shp", status=JobStatus.PENDING,
                celery_task_id=None):
    job = ConversionJob(
        id=None,  # 让 ORM 生成默认 uuid
        api_key_id=key.id,
        batch_job_id=batch_id,
        original_filename=filename,
        input_path=f"/tmp/{filename}",
        output_path=None,
        dst_crs="EPSG:4490",
        output_format="GeoJSON",
        status=status,
        celery_task_id=celery_task_id,
    )
    db.add(job)
    return job


# ── redis_available（Redis 可用性探测，打桩 redis 客户端）───────────────────

class TestRedisAvailable:
    async def testredis_available_true(self):
        from app.api.v1.batch import redis_available
        fake = MagicMock()
        fake.ping.return_value = True
        with patch("redis.from_url", return_value=fake) as m_from_url:
            assert redis_available() is True
        m_from_url.assert_called_once()

    async def testredis_available_false_on_ping_error(self):
        from app.api.v1.batch import redis_available
        fake = MagicMock()
        fake.ping.side_effect = Exception("no redis")
        with patch("redis.from_url", return_value=fake):
            assert redis_available() is False

    async def testredis_available_false_on_import_error(self):
        from app.api.v1.batch import redis_available
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            assert redis_available() is False


# ── POST /batch/convert ─────────────────────────────────────────────────────

class TestSubmitBatchConversion:
    async def test_submit_with_redis(self, client, db_session, tmp_path):
        """Redis 可用 → apply_async 并行提交 → 202 + RUNNING + 子任务落库。"""
        raw, key = await _create_key(db_session, "batch_submit_ok")
        with patch.object(settings, "upload_dir", str(tmp_path / "up1")), \
                patch("app.api.v1.batch.redis_available", return_value=True), \
                patch("app.api.v1.batch.run_conversion.apply_async") as m_apply:
            resp = await client.post(
                "/api/v1/batch/convert",
                files=[
                    ("files", ("a.geojson", b"{}", "application/geo+json")),
                    ("files", ("b.geojson", b"{}", "application/geo+json")),
                ],
                data={"dst_crs": "EPSG:4490", "output_format": "GeoJSON"},
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["file_count"] == 2
        assert body["status"] == "running"
        assert m_apply.call_count == 2

        batch = await db_session.get(BatchJob, body["batch_job_id"])
        assert batch is not None
        assert batch.status == "running"
        assert batch.total_count == 2

        rows = (await db_session.execute(
            select(ConversionJob).where(
                ConversionJob.batch_job_id == body["batch_job_id"]
            )
        )).scalars().all()
        assert len(rows) == 2
        # 每个子任务以 task_id=jid 提交
        submitted_ids = {kw["task_id"] for _, kw in m_apply.call_args_list}
        assert submitted_ids == {r.id for r in rows}

    async def test_submit_redis_unavailable_fallback(self, client, db_session, tmp_path):
        """Redis 不可用 → 降级进程内 apply 同步执行，max_retries 保存/恢复。"""
        from app.workers.tasks import run_conversion
        raw, key = await _create_key(db_session, "batch_submit_fb")
        with patch.object(settings, "upload_dir", str(tmp_path / "up2")), \
                patch("app.api.v1.batch.redis_available", return_value=False), \
                patch.object(run_conversion, "max_retries", 3), \
                patch("app.api.v1.batch.run_conversion.apply") as m_apply:
            resp = await client.post(
                "/api/v1/batch/convert",
                files=[("files", ("a.shp", b"xy", "application/octet-stream"))],
                headers={"X-API-Key": raw},
            )
            final_max_retries = run_conversion.max_retries

        assert resp.status_code == 202
        body = resp.json()
        assert "已进程内执行" in body["message"]
        # 降级路径先将 max_retries 置 0 后恢复为原值
        assert m_apply.call_count == 1
        # Task.apply(args=..., task_id=...) —— args 是关键字参数
        call_kw = m_apply.call_args.kwargs
        assert isinstance(call_kw["args"], list) and len(call_kw["args"]) == 1
        job_id = call_kw["args"][0]
        assert call_kw["task_id"] == job_id
        assert final_max_retries == 3

        batch = await db_session.get(BatchJob, body["batch_job_id"])
        assert batch is not None
        assert batch.status == "running"

    async def test_submit_fallback_apply_exception(self, client, db_session, tmp_path):
        """降级执行中 apply 抛异常 → 内部 except 捕获，不中断 202。"""
        raw, key = await _create_key(db_session, "batch_submit_exc")
        with patch.object(settings, "upload_dir", str(tmp_path / "up3")), \
                patch("app.api.v1.batch.redis_available", return_value=False), \
                patch("app.api.v1.batch.run_conversion.apply",
                      side_effect=RuntimeError("boom")):
            resp = await client.post(
                "/api/v1/batch/convert",
                files=[("files", ("a.shp", b"xy", "application/octet-stream"))],
                headers={"X-API-Key": raw},
            )
        assert resp.status_code == 202
        assert "已进程内执行" in resp.json()["message"]

    async def test_submit_exceeds_max_batch(self, client, db_session):
        """超过 max_batch_size → 413。"""
        raw, key = await _create_key(db_session, "batch_submit_413")
        with patch.object(settings, "max_batch_size", 2), \
                patch("app.api.v1.batch.redis_available", return_value=True):
            resp = await client.post(
                "/api/v1/batch/convert",
                files=[
                    ("files", (f"f{i}.geojson", b"{}", "application/geo+json"))
                    for i in range(3)
                ],
                headers={"X-API-Key": raw},
            )
        assert resp.status_code == 413
        assert "最多" in resp.json()["detail"]


# ── GET /batch/{batch_id} ───────────────────────────────────────────────────

class TestGetBatchJob:
    async def test_get_detail(self, client, db_session):
        raw, key = await _create_key(db_session, "batch_get_ok")
        batch = _make_batch(db_session, key, status=BatchJobStatus.RUNNING,
                            total_count=5, success_count=3, failed_count=1)
        await db_session.commit()

        resp = await client.get(f"/api/v1/batch/{batch.id}",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == batch.id
        assert body["status"] == "running"
        assert body["success_count"] == 3
        assert "duration_seconds" in body

    async def test_get_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "batch_get_404")
        resp = await client.get("/api/v1/batch/no-such",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 404

    async def test_get_other_key_forbidden(self, client, db_session):
        """scope 隔离：他人批量 → 404。"""
        raw, key = await _create_key(db_session, "batch_owner")
        other_raw, _ = await _create_key(db_session, "batch_other")
        batch = _make_batch(db_session, key, name="他人批量")
        await db_session.commit()

        resp = await client.get(f"/api/v1/batch/{batch.id}",
                                headers={"X-API-Key": other_raw})
        assert resp.status_code == 404


# ── GET /batch（列表）───────────────────────────────────────────────────────

class TestListBatchJobs:
    async def test_list_desc_order(self, client, db_session):
        raw, key = await _create_key(db_session, "batch_list")
        now = datetime.now(timezone.utc)
        _make_batch(db_session, key, name="A",
                    created_at=now - timedelta(minutes=10))  # 更早
        _make_batch(db_session, key, name="B", created_at=now)  # 更新
        await db_session.commit()

        resp = await client.get("/api/v1/batch", headers={"X-API-Key": raw})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = [b["name"] for b in data]
        assert set(names) == {"A", "B"}
        assert data[0]["name"] == "B"  # created_at 倒序（更新的在前）

    async def test_list_pagination(self, client, db_session):
        raw, key = await _create_key(db_session, "batch_list_pg")
        for i in range(3):
            _make_batch(db_session, key, name=f"B{i}")
        await db_session.commit()

        resp = await client.get("/api/v1/batch", params={"page": 2, "page_size": 2},
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert len(resp.json()) == 1


# ── DELETE /batch/{batch_id} ────────────────────────────────────────────────

class TestCancelBatchJob:
    async def test_cancel_with_running_child(self, client, db_session):
        """取消 RUNNING + celery_task_id 子任务 → revoke + 全量 CANCELLED。"""
        raw, key = await _create_key(db_session, "batch_cancel")
        batch = _make_batch(db_session, key, status=BatchJobStatus.RUNNING)
        await db_session.flush()  # 先生成 batch.id 再挂载子任务
        child = _make_child(db_session, key, batch.id, status=JobStatus.RUNNING,
                            celery_task_id="celery-123")
        await db_session.commit()

        with patch("app.workers.celery_app.celery_app", new=MagicMock()) as m_celery:
            resp = await client.delete(f"/api/v1/batch/{batch.id}",
                                       headers={"X-API-Key": raw})

        assert resp.status_code == 204
        m_celery.control.revoke.assert_called_once_with("celery-123", terminate=True)

        # db_session 在 expire_on_commit=False 下缓存旧状态，须 refresh 从库读回路由写的结果
        await db_session.refresh(batch)
        await db_session.refresh(child)
        assert batch.status == "cancelled"
        assert child.status == "cancelled"

    async def test_cancel_child_not_running_no_revoke(self, client, db_session):
        """子任务非 RUNNING 或无限 → 不调 revoke。"""
        raw, key = await _create_key(db_session, "batch_cancel_nr")
        batch = _make_batch(db_session, key, status=BatchJobStatus.PENDING)
        await db_session.flush()  # 先生成 batch.id
        # 无 celery_task_id 的 RUNNING（revoke 条件不满足）
        _make_child(db_session, key, batch.id, status=JobStatus.RUNNING,
                    celery_task_id=None)
        # 有 celery_task_id 但非 RUNNING（revoke 条件不满足）
        _make_child(db_session, key, batch.id, filename="b.shp",
                    status=JobStatus.SUCCESS, celery_task_id="celery-9")
        await db_session.commit()

        with patch("app.workers.celery_app.celery_app", new=MagicMock()) as m_celery:
            resp = await client.delete(f"/api/v1/batch/{batch.id}",
                                       headers={"X-API-Key": raw})

        assert resp.status_code == 204
        m_celery.control.revoke.assert_not_called()

    async def test_cancel_not_found(self, client, db_session):
        raw, key = await _create_key(db_session, "batch_cancel_404")
        resp = await client.delete("/api/v1/batch/no-such",
                                   headers={"X-API-Key": raw})
        assert resp.status_code == 404


# ── GET /batch/{batch_id}/jobs ──────────────────────────────────────────────

class TestListBatchSubJobs:
    async def test_list_sub_jobs(self, client, db_session):
        raw, key = await _create_key(db_session, "batch_subjobs")
        batch = _make_batch(db_session, key)
        await db_session.flush()  # 先生成 batch.id
        c1 = _make_child(db_session, key, batch.id, filename="a.shp")
        c2 = _make_child(db_session, key, batch.id, filename="b.shp")
        await db_session.commit()

        resp = await client.get(f"/api/v1/batch/{batch.id}/jobs",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert {j["original_filename"] for j in data} == {"a.shp", "b.shp"}
        assert data[0]["id"] in (c1.id, c2.id)

    async def test_list_sub_jobs_empty(self, client, db_session):
        raw, key = await _create_key(db_session, "batch_subjobs_empty")
        batch = _make_batch(db_session, key)
        await db_session.commit()

        resp = await client.get(f"/api/v1/batch/{batch.id}/jobs",
                                headers={"X-API-Key": raw})
        assert resp.status_code == 200
        assert resp.json() == []