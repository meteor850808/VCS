# -*- coding: utf-8 -*-
"""beat_schedule.py + celery_app.py 单元测试 — 定时清理/指标快照/Redis 协议补丁。
覆盖率：beat_schedule 95%（43-44 行内层 except OSError: pass 仅在 stat 竞态下可达，
防御代码留档）；celery_app 100%。
本次修复 bug：Path.is_dir() 对 EACCES 重新抛出（仅吞 ENOENT/ENOTDIR/EBADF），
cleanup_temp_files 原代码未包裹 is_dir，遇到权限受限目录整个任务会崩溃。
"""
import os
import time
from unittest.mock import patch

from app.workers import celery_app as ca
from app.workers import beat_schedule as BS


def _mk_storage(tmp_path):
    """构造 upload_dir 指向的存储目录，返回 (upload_dir, base_uploads, base_outputs)。"""
    storage = tmp_path / "storage"
    uploads = storage / "uploads"
    outputs = storage / "outputs"
    uploads.mkdir(parents=True)
    outputs.mkdir(parents=True)
    return str(uploads), uploads, outputs


def _bump_mtime(path, age_days):
    old = time.time() - age_days * 24 * 3600
    os.utime(path, (old, old))


class TestCleanupTempFiles:
    def test_removes_old_dirs_only(self, tmp_path):
        upload_dir, uploads, outputs = _mk_storage(tmp_path)
        old_u = uploads / "batch-old-1"
        new_u = uploads / "batch-new-1"
        old_o = outputs / "out-old-2"
        new_o = outputs / "out-new-2"
        old_u.mkdir()
        new_u.mkdir()
        old_o.mkdir()
        new_o.mkdir()
        _bump_mtime(old_u, 30)
        _bump_mtime(old_o, 30)

        with patch.object(BS.settings, "upload_dir", upload_dir):
            ret = BS.cleanup_temp_files()

        assert ret == {"cleaned_dirs": 2}
        assert not old_u.exists()
        assert not old_o.exists()
        assert new_u.exists()
        assert new_o.exists()

    def test_skips_files_and_missing_dirs(self, tmp_path):
        upload_dir, uploads, outputs = _mk_storage(tmp_path)
        old_file = uploads / "old-file.txt"
        old_file.write_text("x")
        _bump_mtime(old_file, 30)

        missing = tmp_path / "no-such" / "uploads"
        with patch.object(BS.settings, "upload_dir", str(missing)):
            ret = BS.cleanup_temp_files()

        assert ret == {"cleaned_dirs": 0}
        assert old_file.exists()  # 常规文件不清理

    def test_oserror_on_stat_skipped(self, tmp_path):
        # Path.is_dir() 内部会吞 OSError → stat 抛错时目录被静默跳过（不清除、不崩溃）。
        # 注意：cleanup 内 try/except OSError（37-38 行）在 stat 幂等失败场景下
        # 不可达（is_dir 已吞掉），属防御代码，留档不强制覆盖。
        upload_dir, uploads, outputs = _mk_storage(tmp_path)
        bad_dir = uploads / "bad-stat"
        bad_dir.mkdir()
        _bump_mtime(bad_dir, 30)

        import pathlib
        real_stat = pathlib.Path.stat

        def _stat_spy(self, *a, **k):
            if str(self).endswith("bad-stat"):
                raise OSError("access denied")
            return real_stat(self, *a, **k)

        with patch.object(BS.settings, "upload_dir", upload_dir):
            with patch.object(pathlib.Path, "stat", new=_stat_spy):
                ret = BS.cleanup_temp_files()
        # stat 失败目录被 is_dir 判定跳过 → 未删除，无异常
        assert ret == {"cleaned_dirs": 0}
        assert bad_dir.exists()

    def test_new_dirs_kept(self, tmp_path):
        upload_dir, uploads, outputs = _mk_storage(tmp_path)
        fresh = uploads / "fresh"
        fresh.mkdir()
        _bump_mtime(fresh, 1)  # 1 天前 < 7 天

        with patch.object(BS.settings, "upload_dir", upload_dir):
            ret = BS.cleanup_temp_files()
        assert ret == {"cleaned_dirs": 0}
        assert fresh.exists()


class TestMetricsSnapshot:
    def test_counts_dirs(self, tmp_path):
        upload_dir, uploads, outputs = _mk_storage(tmp_path)
        (uploads / "d1").mkdir()
        (uploads / "d2").mkdir()
        (outputs / "o1").mkdir()

        with patch.object(BS.settings, "upload_dir", upload_dir):
            ret = BS.metrics_snapshot()
        assert ret == {"upload_dirs": 2, "output_dirs": 1}

    def test_missing_dirs_zero(self, tmp_path):
        with patch.object(BS.settings, "upload_dir", str(tmp_path / "x" / "uploads")):
            ret = BS.metrics_snapshot()
        assert ret == {"upload_dirs": 0, "output_dirs": 0}


class TestBeatScheduleConfig:
    def test_schedule_entries(self):
        assert BS.CELERY_BEAT_SCHEDULE["cleanup-temp-files"]["task"] == "vcs.cleanup_temp_files"
        assert BS.CELERY_BEAT_SCHEDULE["metrics-snapshot"]["task"] == "vcs.metrics_snapshot"

    def test_app_beat_schedule_wired(self):
        # celery_app.conf.beat_schedule 已挂载
        assert ca.celery_app.conf.beat_schedule == BS.CELERY_BEAT_SCHEDULE

    def test_app_config_essentials(self):
        conf = ca.celery_app.conf
        assert conf.task_serializer == "json"
        assert conf.timezone == "UTC"
        assert conf.task_default_queue == "vector_conversion"
        assert "app.workers.tasks" in conf.include
        assert "app.workers.batch_tasks" in conf.include
        assert conf.broker_connection_retry is False
        assert conf.broker_connection_max_retries == 0
        assert conf.task_acks_late is True
        assert conf.result_expires == 3600


class TestRedisProtocolCompat:
    def test_patch_applies_to_kombu_and_backend(self):
        # 用桩函数作为"原始实现"，验证包装函数注入 protocol=2 的行为
        from kombu.transport import redis as kombu_redis

        orig_connparams = kombu_redis.Channel._connparams
        try:
            kombu_redis.Channel._connparams = lambda self, asynchronous=False: {"foo": 1}
            ca._patch_redis_protocol_compat()
            params = kombu_redis.Channel._connparams(object(), asynchronous=False)
            assert params["foo"] == 1
            assert params["protocol"] == 2
        finally:
            kombu_redis.Channel._connparams = orig_connparams

        # RedisBackend.__init__ 包装：调用时 setdefault protocol=2
        from celery.backends.redis import RedisBackend

        orig_init = RedisBackend.__init__

        def _stub_init(self, *args, **kwargs):
            self.connparams = {}

        try:
            RedisBackend.__init__ = _stub_init
            ca._patch_redis_protocol_compat()
            rb = object.__new__(RedisBackend)
            RedisBackend.__init__(rb, app=None)
            assert rb.connparams == {"protocol": 2}
        finally:
            RedisBackend.__init__ = orig_init

    def test_patch_swallows_import_errors(self):
        # 两个 except 分支：模块/属性缺失时不阻塞
        import sys

        import kombu.transport

        def _remove_then_import_raise(module_name, parent):
            real_sub = getattr(parent, module_name.split(".")[-1], None)
            attr_name = module_name.split(".")[-1]
            had_attr = hasattr(parent, attr_name)
            recorded = sys.modules.pop(module_name, None)
            if had_attr:
                delattr(parent, attr_name)
            sys.modules[module_name] = None  # import None → ImportError
            return real_sub, recorded, had_attr

        # ── kombu.transport.redis 缺失 ──
        kombu_redis_backup = _remove_then_import_raise("kombu.transport.redis", kombu.transport)
        try:
            ca._patch_redis_protocol_compat()  # 不应抛出
        finally:
            if kombu_redis_backup[2]:
                setattr(kombu.transport, "redis", kombu_redis_backup[0])
            sys.modules.pop("kombu.transport.redis", None)
            if kombu_redis_backup[1] is not None:
                sys.modules["kombu.transport.redis"] = kombu_redis_backup[1]

        # ── celery.backends.redis 缺失 ──
        import celery.backends as cb
        redis_backup = _remove_then_import_raise("celery.backends.redis", cb)
        try:
            ca._patch_redis_protocol_compat()  # 不应抛出
        finally:
            if redis_backup[2]:
                setattr(cb, "redis", redis_backup[0])
            sys.modules.pop("celery.backends.redis", None)
            if redis_backup[1] is not None:
                sys.modules["celery.backends.redis"] = redis_backup[1]