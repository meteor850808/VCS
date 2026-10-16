# -*- coding: utf-8 -*-
"""batch_tasks.py 单元测试 — orchestrate_batch 批量任务编排（DB/Celery 全 mock）。"""
from unittest.mock import patch

from app.workers.batch_tasks import orchestrate_batch


class _FakeBatch:
    """可写属性的假 BatchJob。"""

    def __init__(self, batch_job_id):
        self.id = batch_job_id
        self.status = None
        self.started_at = None
        self.finished_at = None
        self.success_count = None
        self.failed_count = None
        self.error_message = None


class _FakeSession:
    """记录 commit 的假会话，get 返回预设对象。"""

    def __init__(self, batch):
        self._batch = batch
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, model, batch_job_id):
        return self._batch

    def commit(self):
        self.commits += 1


class _FakeSessionFactory:
    """sessionmaker 工厂：每次调用返回新的 _FakeSession。"""

    def __init__(self, batch):
        self._batch = batch
        self.instances = []

    def __call__(self):
        s = _FakeSession(self._batch)
        self.instances.append(s)
        return s


class _FakeGroupResult:
    def __init__(self, value):
        self._value = value

    def get(self, propagate=False, timeout=None):
        return self._value


class _FakeGroup:
    """假 celery group：__call__ 消费生成器记录签名，apply_async 返回预设结果。"""

    def __init__(self, result_set, apply_exc=None):
        self._result_set = result_set
        self.apply_exc = apply_exc
        self.signed_ids = []
        self.applied = False

    def __call__(self, task_iterable):
        self.signed_ids = [t for t in task_iterable]
        return self

    def apply_async(self):
        self.applied = True
        if self.apply_exc is not None:
            raise self.apply_exc
        return _FakeGroupResult(self._result_set)


class _FakeRunConversion:
    """假 run_conversion 任务：记录 .s() 签名参数。"""

    def __init__(self, ids):
        self._ids = ids

    def s(self, job_id):
        self._ids.append(job_id)
        return ("sig", job_id)


def _run(batch, result_set, apply_exc=None):
    """执行 orchestrate_batch，返回 (返回值, group, run_conversion_ids, session_factory, publish_calls)。"""
    group_factory = _FakeGroup(result_set, apply_exc)
    session_factory = _FakeSessionFactory(batch)
    publish_calls = []
    rc_ids = []

    with patch("app.workers.batch_tasks.group", side_effect=group_factory):
        with patch("app.workers.batch_tasks.run_conversion", _FakeRunConversion(rc_ids)):
            with patch("sqlalchemy.create_engine"):
                with patch("sqlalchemy.orm.sessionmaker",
                           side_effect=lambda bind: session_factory):
                    with patch("app.services.progress_tracker.publish_batch_update",
                               side_effect=lambda *a, **k: publish_calls.append(a)):
                        ret = orchestrate_batch("b0", ["j1", "j2"])
    return ret, group_factory, rc_ids, session_factory, publish_calls


class TestOrchestrateBatch:
    def test_batch_not_found_returns_error(self):
        ret, _, _, session_factory, publish = _run(None, [])
        assert ret["status"] == "error"
        assert "不存在" in ret["message"]
        assert publish == []

    def test_success_when_all_jobs_succeed(self):
        batch = _FakeBatch("b1")
        ret, group_factory, rc_ids, session_factory, publish = _run(
            batch, [{"status": "success", "id": 1}, {"status": "success", "id": 2}])
        assert ret["status"] == "success"
        assert ret["success"] == 2
        assert ret["failed"] == 0
        assert batch.status.value == "success"
        assert batch.success_count == 2
        assert batch.failed_count == 0
        assert batch.finished_at is not None
        assert rc_ids == ["j1", "j2"]  # run_conversion.s 签名参数
        assert group_factory.signed_ids == [("sig", "j1"), ("sig", "j2")]
        assert group_factory.applied
        # publish 两次：running 与 success
        assert [c[1] for c in publish] == ["running", "success"]
        # 两个 session 都已 commit（第一个 + 汇总）
        assert len(session_factory.instances) >= 2
        assert all(s.commits >= 1 for s in session_factory.instances)

    def test_partial_when_some_fail(self):
        batch = _FakeBatch("b2")
        ret, _, _, _, publish = _run(
            batch, [{"status": "success"}, {"status": "error", "message": "x"}])
        assert ret["success"] == 1
        assert ret["failed"] == 1
        assert batch.status.value == "partial"
        assert batch.success_count == 1
        assert batch.failed_count == 1
        assert publish[-1][1:] == ("partial", 2, 1, 1)

    def test_all_failed_status(self):
        batch = _FakeBatch("b3")
        ret, _, _, _, publish = _run(
            batch, [{"status": "error"}, {"status": "error"}])
        assert ret["success"] == 0
        assert ret["failed"] == 2
        assert batch.status.value == "failed"
        assert batch.error_message == "所有子任务均失败"
        assert publish[-1][1] == "failed"

    def test_non_dict_result_counts_as_failed(self):
        batch = _FakeBatch("b4")
        ret, _, _, _, _ = _run(
            batch, [{"status": "success"}, "not-a-dict"])
        assert ret["success"] == 1
        assert ret["failed"] == 1  # failed = len(job_ids) - success
        assert batch.status.value == "partial"

    def test_exception_in_group_marks_failed(self):
        batch = _FakeBatch("b5")
        ret, _, _, session_factory, publish = _run(
            batch, [], apply_exc=RuntimeError("celery down"))
        assert ret == {"status": "error", "message": "celery down"}
        assert batch.status.value == "failed"
        assert batch.error_message == "celery down"
        assert batch.finished_at is not None
        # 异常路径不再发布最终状态更新（只发布 running）
        assert [c[1] for c in publish] == ["running"]
        assert len(session_factory.instances) == 2
        assert all(s.commits >= 1 for s in session_factory.instances)

    def test_get_timeout_exception_failed_path(self):
        # apply_async 成功后 get() 抛异常 → 也走失败汇总
        class _BoomGet(_FakeGroupResult):
            def get(self, propagate=False, timeout=None):
                raise TimeoutError("timeout after 7200s")

        batch = _FakeBatch("b6")
        group_factory = _FakeGroup([])
        group_factory.apply_async = lambda: _BoomGet(None)
        session_factory = _FakeSessionFactory(batch)
        publish_calls = []
        with patch("app.workers.batch_tasks.group", side_effect=group_factory):
            with patch("app.workers.batch_tasks.run_conversion", _FakeRunConversion([])):
                with patch("sqlalchemy.create_engine"):
                    with patch("sqlalchemy.orm.sessionmaker",
                               side_effect=lambda bind: session_factory):
                        with patch("app.services.progress_tracker.publish_batch_update",
                                   side_effect=lambda *a, **k: publish_calls.append(a)):
                            ret = orchestrate_batch("b6", ["j1"])
        assert ret["status"] == "error"
        assert "timeout" in ret["message"]
        assert batch.status.value == "failed"
        assert len(session_factory.instances) == 2
        assert all(s.commits >= 1 for s in session_factory.instances)