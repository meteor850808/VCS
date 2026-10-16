# -*- coding: utf-8 -*-
"""tasks.py 单元测试 — run_conversion 转换任务编排 + 辅助函数（DB/Celery 全 mock）。"""
import json
import types
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from app.models.conversion_job import JobStatus
from app.workers import tasks as T


def _ns(**kw):
    return types.SimpleNamespace(**kw)


class _FakeSession:
    def __init__(self, job):
        self._job = job
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, model, job_id):
        return self._job

    def commit(self):
        self.commits += 1


def _make_job(**overrides):
    job = _ns(
        id="job-12345678",
        input_path="C:/data/in.geojson",
        output_path="C:/data/out.shp",
        output_format="SHP",
        src_crs=None,
        dst_crs="EPSG:3857",
        select_fields=None,
        where_clause=None,
        target_encoding="utf-8",
        repair_strategy="auto_fix",
        script_type="python_fiona",
        batch_job_id="batch-1",
        status=None, started_at=None, finished_at=None,
        celery_task_id=None,
        detected_format=None, detected_encoding=None,
        detected_crs=None, detection_confidence=None,
        progress=0.0, current_step=None,
        feature_count_input=None, feature_count_output=None,
        feature_fixed=None, feature_dropped=None,
        generated_script=None, generated_script_type=None,
        sandbox_stdout=None, sandbox_stderr=None,
        warnings=None, error_message=None,
    )
    for k, v in overrides.items():
        setattr(job, k, v)
    return job


def _make_fmt(format_name="GeoJSON", confidence=0.9, **kw):
    return _ns(format_name=format_name, encoding="utf-8",
               crs_info="EPSG:4326", confidence=confidence, **kw)


def _make_context(job, *, fmt=None, crs_src=None, crs_dst=None, attr=None,
                  repair=None, check=None, script=None, sandbox=None,
                  fail_step=None, attr_file=False):
    """装配全部 patch，返回 (stack, probes)。"""
    stack = ExitStack()
    probes = {
        "publish": [],        # (args...)
        "run_full": [],       # [(script, job)]
        "process_attrs": [],
        "repair": [],
        "sandbox": [],
        "load_geojson": [],
    }
    tests_module = "app.workers.tasks"

    stack.enter_context(patch(f"{tests_module}.SyncSession",
                              side_effect=lambda: _FakeSession(job)))
    stack.enter_context(patch(f"{tests_module}.publish_progress",
                              side_effect=lambda *a, **k: probes["publish"].append(a)))

    def _detect(*a, **k):
        if fail_step == "detect":
            raise RuntimeError("detect boom")
        return fmt or _make_fmt()

    stack.enter_context(patch(f"{tests_module}.detect_format", side_effect=_detect))

    def _resolve(crs_str):
        if fail_step == "resolve":
            raise ValueError("crs boom")
        if crs_str is None or not crs_str:
            return _ns(epsg=None, wkt="+proj=longlat")
        if "3857" in str(crs_str):
            return crs_dst or _ns(epsg=3857, wkt=None)
        return crs_src or _ns(epsg=4326, wkt=None)

    stack.enter_context(patch(f"{tests_module}.resolve_crs", side_effect=_resolve))

    def _proc_attr(**kw):
        probes["process_attrs"].append(kw)
        if fail_step == "attr":
            raise RuntimeError("attr boom")
        return attr or _ns(warnings=["attr-warn"], total_input=10, total_output=9)

    stack.enter_context(patch(f"{tests_module}.process_attributes", side_effect=_proc_attr))

    stack.enter_context(patch(f"{tests_module}._find_attr_file",
                              side_effect=lambda p, f: "C:/data/in.dbf" if (attr_file or f == "SHP") else None))

    def _repair(features, strategy=None):
        probes["repair"].append(len(features))
        if fail_step == "repair":
            raise RuntimeError("repair boom")
        return repair or _ns(total_input=5, total_output=4, total_fixed=1,
                             total_dropped=1, total_valid=4)

    stack.enter_context(patch(f"{tests_module}.repair_features", side_effect=_repair))
    stack.enter_context(patch(f"{tests_module}.compare_feature_counts",
                              side_effect=lambda a, b: {"passed": True, "message": "ok"}))

    def _load_geojson(path):
        probes["load_geojson"].append(path)
        return [{"type": "Feature", "properties": {}}] * 5

    stack.enter_context(patch(f"{tests_module}._load_geojson_features", side_effect=_load_geojson))

    def _gen_ogr2ogr(*a, **kw):
        if fail_step == "script":
            raise RuntimeError("script boom")
        return script or _ns(code="ogr2ogr ...", script_type="ogr2ogr")

    def _gen_fiona(*a, **kw):
        if fail_step == "script":
            raise RuntimeError("script boom")
        return script or _ns(code="import fiona", script_type="python_fiona")

    stack.enter_context(patch(f"{tests_module}.generate_ogr2ogr_script", side_effect=_gen_ogr2ogr))
    stack.enter_context(patch(f"{tests_module}.generate_fiona_script", side_effect=_gen_fiona))

    def _sandbox(scr, limit=None):
        probes["sandbox"].append((scr.script_type, limit))
        return sandbox or _ns(stdout="out", stderr="", success=True)

    stack.enter_context(patch(f"{tests_module}.sandbox_execute", side_effect=_sandbox))

    def _full(script, job):
        probes["run_full"].append((script.script_type, job))
        if fail_step == "full":
            raise RuntimeError("full boom")

    stack.enter_context(patch(f"{tests_module}._run_full_conversion", side_effect=_full))
    return stack, probes


def _call(job_id="job-1", request=None):
    """在 push_request 上下文中执行 run_conversion.run(job_id)。"""
    T.run_conversion.push_request(**request) if request else T.run_conversion.push_request()
    try:
        return T.run_conversion.run(job_id)
    finally:
        T.run_conversion.pop_request()


class TestRunConversion:
    def test_success_full_path(self):
        job = _make_job()
        stack, probes = _make_context(job)
        with stack:
            ret = _call("job-1", {"id": "t-1", "retries": 0})

        assert ret == {"status": "success", "job_id": "job-1"}
        assert job.status is JobStatus.SUCCESS
        assert job.progress == 1.0
        assert job.celery_task_id == "t-1"
        assert job.feature_count_input == 5   # GeoJSON 几何修复
        assert job.feature_count_output == 4
        assert job.detected_format == "GeoJSON"
        assert job.generated_script == "import fiona"
        assert job.generated_script_type == "python_fiona"
        assert job.sandbox_stdout == "out"
        assert job.warnings == []
        # GeoJSON 无 dbf → 属性步骤跳过 → 5 步 publish
        assert len(probes["publish"]) == 5
        steps = [c[1] for c in probes["publish"]]
        assert steps == ["格式识别", "坐标系解析", "几何修复", "沙箱预检查", "执行转换"]
        assert probes["publish"][-1][4] == "success"  # status 在第 4 位
        # 完整执行调用一次，传 Fiona 脚本
        assert probes["run_full"] == [("python_fiona", job)]

    def test_low_confidence_adds_warning(self):
        job = _make_job()
        stack, probes = _make_context(job, fmt=_make_fmt(confidence=0.3))
        with stack:
            _call()
        assert any("格式置信度低" in w for w in job.warnings)

    def test_attribute_found_warnings_extended(self):
        job = _make_job()
        stack, probes = _make_context(job, attr=_ns(warnings=["a1"], total_input=7, total_output=7),
                                      attr_file=True)
        with stack:
            _call()
        assert "a1" in job.warnings
        assert len(probes["process_attrs"]) == 1
        # 属性步骤 publish 出现（第 3 步）
        assert [c[1] for c in probes["publish"]][2] == "属性处理"

    def test_attribute_exception_skips_step(self):
        job = _make_job()
        stack, probes = _make_context(job, fail_step="attr", attr_file=True)
        with stack:
            _call()
        assert any("属性处理跳过" in w for w in job.warnings)
        assert len(probes["process_attrs"]) == 1  # 尝试过
        assert [c[1] for c in probes["publish"]][2] != "属性处理"

    def test_non_geojson_skips_geometry_repair(self):
        job = _make_job()
        stack, probes = _make_context(job, fmt=_make_fmt(format_name="SHP"))
        with stack:
            _call()
        assert probes["repair"] == []     # 跳过几何修复
        assert probes["load_geojson"] == []
        assert job.feature_count_input is None

    def test_repair_exception_skips_geo_step(self):
        job = _make_job()
        stack, probes = _make_context(job, fail_step="repair")
        with stack:
            _call()
        assert any("几何修复跳过" in w for w in job.warnings)

    def test_feature_count_mismatch_warns(self):
        job = _make_job()
        stack, probes = _make_context(
            job, check={"passed": False, "message": "count mismatch: 5 vs 4"})
        with stack:
            T.run_conversion.push_request()
            try:
                with patch("app.workers.tasks.compare_feature_counts",
                           side_effect=lambda a, b: {"passed": False,
                                                     "message": "count mismatch: 5 vs 4"}):
                    T.run_conversion.run("job-1")
            finally:
                T.run_conversion.pop_request()
        assert any("count mismatch" in w for w in job.warnings)

    def test_ogr2ogr_script_type(self):
        job = _make_job(script_type="ogr2ogr")
        stack, probes = _make_context(job, script=_ns(code="ogr2ogr ...", script_type="ogr2ogr"))
        with stack:
            _call()
        assert job.generated_script_type == "ogr2ogr"
        assert probes["run_full"] == [("ogr2ogr", job)]

    def test_sandbox_failure_adds_warning(self):
        job = _make_job()
        stack, probes = _make_context(
            job, sandbox=_ns(stdout="", stderr="boom detail", success=False))
        with stack:
            _call()
        assert any("沙箱预执行失败: boom detail" in w for w in job.warnings)
        assert job.sandbox_stderr == "boom detail"

    def test_job_not_found_raises_valueerror(self):
        # _get_job 在 try 之外 → ValueError 直接抛出，不写 FAILED
        stack, probes = _make_context(None)
        with stack:
            with pytest.raises(ValueError, match="不存在"):
                _call("missing")

    def test_mid_pipeline_error_marks_failed_and_retries(self):
        job = _make_job()

        def _raise_retry(*a, **k):
            raise RuntimeError("RETRY_SCHEDULED")

        with patch.object(T.run_conversion, "retry", side_effect=_raise_retry):
            stack, probes = _make_context(job, fail_step="full")
            with stack:
                with pytest.raises(RuntimeError, match="RETRY_SCHEDULED"):
                    _call("job-1", {"id": "t-2", "retries": 0})
        assert job.status is JobStatus.FAILED
        assert "full boom" in job.error_message
        assert job.finished_at is not None

    def test_error_with_max_retries_re_raises_exc(self):
        # retries >= max_retries → raise exc（不重试）
        job = _make_job()
        stack, probes = _make_context(job, fail_step="detect")
        with stack:
            with pytest.raises(RuntimeError, match="detect boom"):
                _call("job-1", {"id": "t-3", "retries": 2})
        assert job.status is JobStatus.FAILED


class TestRunFullConversion:
    def test_ogr2ogr_success(self):
        with patch("subprocess.run") as m_run:
            m_run.return_value = _ns(returncode=0, stdout="", stderr="")
            T._run_full_conversion(_ns(script_type="ogr2ogr", code="-overwrite x"), None)
        args = m_run.call_args.args[0] if m_run.call_args.args else m_run.call_args[0][0]
        assert args == ["-overwrite", "x"]

    def test_ogr2ogr_failure_raises(self):
        with patch("subprocess.run") as m_run:
            m_run.return_value = _ns(returncode=1, stdout="", stderr="MAGIC_ERROR_500")
            with pytest.raises(RuntimeError, match="ogr2ogr 失败"):
                T._run_full_conversion(_ns(script_type="ogr2ogr", code="-f x"), None)

    def test_fiona_script_success_and_cleanup(self, tmp_path):
        import os
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            with patch("subprocess.run") as m_run:
                m_run.return_value = _ns(returncode=0, stdout="", stderr="")
                T._run_full_conversion(_ns(script_type="python_fiona", code="x=1"),
                                       _ns(id="job-1234abcd"))
        tmp_file = tmp_path / "vcs_full_job-1234.py"
        assert not tmp_file.exists()  # finally 已清理

    def test_fiona_failure_raises_and_cleans(self, tmp_path):
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            with patch("subprocess.run") as m_run:
                m_run.return_value = _ns(returncode=2, stdout="", stderr="FIONA_ERR")
                with pytest.raises(RuntimeError, match="Fiona 脚本失败"):
                    T._run_full_conversion(_ns(script_type="python_fiona", code="x=1"),
                                           _ns(id="job-1234abcd"))
        assert not (tmp_path / "vcs_full_job-1234.py").exists()


class TestFindAttrFile:
    def test_shp_with_dbf(self, tmp_path):
        dbf = tmp_path / "a.dbf"
        dbf.write_bytes(b"\x00")
        assert T._find_attr_file(str(tmp_path / "a.shp"), "SHP") == str(dbf)

    def test_shp_without_dbf(self, tmp_path):
        assert T._find_attr_file(str(tmp_path / "a.shp"), "SHP") is None

    def test_tabular_input_returned(self):
        for ext in (".csv", ".dbf", ".xlsx", ".xls"):
            assert T._find_attr_file("C:/data/in" + ext, "GeoJSON") == "C:/data/in" + ext

    def test_other_formats_none(self):
        assert T._find_attr_file("C:/data/in.geojson", "GeoJSON") is None


class TestLoadGeojsonFeatures:
    def test_feature_collection(self, tmp_path):
        p = tmp_path / "f.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": [{"a": 1}]}),
                     encoding="utf-8")
        assert T._load_geojson_features(str(p)) == [{"a": 1}]

    def test_single_feature(self, tmp_path):
        p = tmp_path / "f.geojson"
        p.write_text(json.dumps({"type": "Feature", "id": 1}), encoding="utf-8")
        assert T._load_geojson_features(str(p)) == [{"type": "Feature", "id": 1}]

    def test_other_type_returns_empty(self, tmp_path):
        p = tmp_path / "f.geojson"
        p.write_text(json.dumps({"type": "Polygon"}), encoding="utf-8")
        assert T._load_geojson_features(str(p)) == []

    def test_broken_json_raises(self, tmp_path):
        p = tmp_path / "f.geojson"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            T._load_geojson_features(str(p))


class TestGetJob:
    def test_found(self):
        job = _ns(status=None)
        assert T._get_job(_FakeSession(job), "j1") is job

    def test_not_found_raises_valueerror(self):
        with pytest.raises(ValueError, match="不存在"):
            T._get_job(_FakeSession(None), "j1")


class TestSyncEngine:
    def test_engine_and_session_configured(self):
        # 模块级 _sync_engine：postgres 方言 + QueuePool（keepalive/pool_pre_ping 配置）
        assert T._sync_engine.dialect.name == "postgresql"
        pool = T._sync_engine.pool
        assert type(pool).__name__ == "QueuePool"
        assert pool._max_overflow == 10
        assert pool._pool.maxsize >= 5
        assert T.SyncSession is not None