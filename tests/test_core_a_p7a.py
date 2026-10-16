"""P7a-1: core 工具层补全 → 100%。

- temp_cleanup: 清理主体/缺失目录/rmtree 异常吞/信号处理器/注册异常吞
- db_utils: load_standard_fields 真实 DB 全流程
- config: admin_init_key 弱口令拒绝
- logging_setup / file_utils / rate_limit / security_middleware: 全分支
- auth_context: super admin / 委托 / scope / owns / resolve / create_super_admin
- deps.authenticated: super admin / cache hit / cache 读写异常
"""

import importlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings, settings
from app.core import auth_context as auth_mod
from app.core import deps as deps_mod
from app.core import file_utils
from app.core import logging_setup
from app.core import rate_limit
from app.core import temp_cleanup
from app.core.auth_context import AuthContext
from app.core.db_utils import load_standard_fields
from app.core.security import hash_api_key
from app.core.security_middleware import SecurityHeadersMiddleware
from app.db.session import Base
from logging.handlers import RotatingFileHandler
from app.models.api_key import ApiKey


# ── temp_cleanup ──────────────────────────────────────────────────────────────

class TestTempCleanup:

    def test_cleanup_removes_existing(self, tmp_path):
        (tmp_path / "d" / "sub").mkdir(parents=True)
        temp_cleanup.TEMP_DIRS.add(tmp_path)
        temp_cleanup._cleanup_temp_dirs()
        assert not tmp_path.exists()
        assert temp_cleanup.TEMP_DIRS == set()

    def test_cleanup_skips_missing(self, tmp_path):
        missing = tmp_path / "gone"
        temp_cleanup.TEMP_DIRS.add(missing)
        temp_cleanup._cleanup_temp_dirs()  # exists() False → 跳过，不抛
        assert temp_cleanup.TEMP_DIRS == set()

    def test_cleanup_swallows_rmtree_error(self, tmp_path, monkeypatch):
        (tmp_path / "d").mkdir(parents=True)
        temp_cleanup.TEMP_DIRS.add(tmp_path)

        def boom(d, **k):
            raise PermissionError("EACCES")

        monkeypatch.setattr(temp_cleanup.shutil, "rmtree", boom)
        temp_cleanup._cleanup_temp_dirs()
        assert temp_cleanup.TEMP_DIRS == set()

    def test_signal_handler_cleans_and_exits(self, tmp_path):
        (tmp_path / "d").mkdir(parents=True)
        temp_cleanup.TEMP_DIRS.add(tmp_path / "d")
        with pytest.raises(SystemExit) as ei:
            temp_cleanup._handle_signal(15, None)
        assert ei.value.code == 128 + 15
        # SystemExit 前已清理
        assert not (tmp_path / "d").exists()
        assert temp_cleanup.TEMP_DIRS == set()

    def test_signal_registration_error_swallowed(self, monkeypatch):
        """39-40：非主线程/平台信号注册失败被吞。"""
        import signal as sig

        def boom(*a, **k):
            raise OSError("cannot register in worker thread")

        monkeypatch.setattr(sig, "signal", boom)
        mod = importlib.reload(temp_cleanup)
        assert hasattr(mod, "TEMP_DIRS")  # reload 未抛异常


# ── db_utils ──────────────────────────────────────────────────────────────────

class TestLoadStandardFields:

    async def test_happy(self, db_session):
        key = ApiKey(key_hash="h-9", name="k")
        db_session.add(key)
        await db_session.flush()
        std = _mk_std(key.id)
        db_session.add(std)
        await db_session.flush()
        sheet = _mk_sheet(std.id)
        db_session.add(sheet)
        await db_session.flush()
        db_session.add(_mk_field(sheet.id))
        await db_session.flush()

        fields = await load_standard_fields(db_session, std.id, key.id)
        assert fields == {
            "name": {"field_type": "string", "length": 10,
                     "nullable": False, "allowed_values": ["a", "b"]},
        }

    async def test_not_found_returns_none(self, db_session):
        assert await load_standard_fields(
            db_session, "no-such-id", "k") is None


def _mk_std(key_id: str):
    from app.models.data_standard import DataStandard
    return DataStandard(api_key_id=key_id, name="P7a标准", category="national")


def _mk_sheet(std_id: str):
    from app.models.data_standard import DataStandardSheet
    return DataStandardSheet(standard_id=std_id, sheet_name="S1", ordering=0)


def _mk_field(sheet_id: str):
    from app.models.data_standard import DataStandardField
    return DataStandardField(
        sheet_id=sheet_id, field_name="name", field_type="string",
        length=10, nullable=False, allowed_values=["a", "b"], ordering=0)


# ── config ────────────────────────────────────────────────────────────────────

class TestConfig:

    def test_weak_admin_key_rejected(self):
        with pytest.raises(ValidationError):
            Settings(admin_init_key="")
        with pytest.raises(ValidationError):
            Settings(admin_init_key="admin-init-change-me")
        with pytest.raises(ValidationError):
            Settings(admin_init_key="  test  ")

    def test_strong_admin_key_accepted(self):
        s = Settings(admin_init_key="s-strong-1234567890abcdef-xyz")
        assert s.admin_init_key.startswith("s-strong")


# ── logging_setup ─────────────────────────────────────────────────────────────

class TestLoggingSetup:

    def test_get_logger_default_and_named(self):
        from structlog._config import BoundLoggerLazyProxy
        assert isinstance(logging_setup.get_logger(), BoundLoggerLazyProxy)
        assert isinstance(logging_setup.get_logger("vcs.x"), BoundLoggerLazyProxy)

    def test_logger_is_bound(self):
        assert logging_setup.logger is not None

    def test_file_handler_installed_and_idempotent(self):
        """v2.20 轮转写盘：根 logger 恰好一个 RotatingFileHandler，重复装配不叠加。"""
        root = logging.getLogger()
        before = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        # 移除旧实例，测干净装配一次
        for h in before:
            root.removeHandler(h)
            h.close()

        first = logging_setup._ensure_file_handler()
        assert first is not None, "默认 log_file 非空，应能装配"
        again = logging_setup._ensure_file_handler()
        assert again is first, "重复调用应返回同一实例（幂等）"
        count = sum(
            1 for h in root.handlers if isinstance(h, RotatingFileHandler)
        )
        assert count == 1

    def test_log_record_written_to_file(self):
        """写一条 stdlib INFO 记录，应真实落盘到 settings.log_file。

        根 logger 默认级别 WARNING 会拦截 INFO，测试内临时放行后恢复。
        """
        fh = logging_setup._ensure_file_handler()
        root = logging.getLogger()
        saved_level = root.level
        log_path = Path(fh.baseFilename)
        msg = "vcs-rotation-write-check"
        try:
            root.setLevel(logging.INFO)  # 实际应用由 uvicorn 设 INFO
            logging.getLogger("vcs.test").info(msg)
            fh.flush()
            assert msg in log_path.read_text(encoding="utf-8")
        finally:
            root.setLevel(saved_level)

    def test_no_file_handler_when_log_file_empty(self):
        """log_file 为空串（禁用写盘）应短路返回 None，不落任何处理器。"""
        root = logging.getLogger()
        saved_log_file = logging_setup.settings.log_file
        handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        try:
            for h in handlers:  # 清场：幂等短路会优先返回已装 handler，需干净环境
                root.removeHandler(h)
                h.close()
            logging_setup.settings.log_file = ""
            result = logging_setup._ensure_file_handler()
            assert result is None
            count = sum(
                1 for h in root.handlers if isinstance(h, RotatingFileHandler)
            )
            assert count == 0
        finally:
            logging_setup.settings.log_file = saved_log_file
            for h in root.handlers:
                if isinstance(h, RotatingFileHandler):
                    root.removeHandler(h)
                    h.close()
            logging_setup._ensure_file_handler()  # 恢复默认装配

    def test_handler_graceful_degrade_on_oserror(self):
        """写盘失败（OSError，如权限拒绝）优雅降级为仅控制台，返回 None 不抛。"""
        from unittest.mock import patch
        root = logging.getLogger()
        saved_log_file = logging_setup.settings.log_file
        handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        try:
            for h in handlers:  # 清场同上
                root.removeHandler(h)
                h.close()
            # Path.parent.mkdir 抛 OSError（磁盘写满/权限拒绝场景），落入 _ensure_file_handler 的 except OSError
            with patch.object(
                logging_setup.Path, "mkdir", side_effect=OSError("disk full")
            ):
                result = logging_setup._ensure_file_handler()
            assert result is None
        finally:
            logging_setup.settings.log_file = saved_log_file
            for h in root.handlers:
                if isinstance(h, RotatingFileHandler):
                    root.removeHandler(h)
                    h.close()
            logging_setup._ensure_file_handler()  # 恢复默认装配


# ── file_utils ────────────────────────────────────────────────────────────────

class TestValidateFilename:

    def test_empty_name_400(self):
        with pytest.raises(HTTPException) as ei:
            file_utils.validate_filename("   ")
        assert ei.value.status_code == 400
        assert "不能为空" in ei.value.detail

    def test_nul_byte_400(self):
        with pytest.raises(HTTPException) as ei:
            file_utils.validate_filename("a\x00b.shp")
        assert ei.value.status_code == 400

    def test_path_traversal_400(self):
        with pytest.raises(HTTPException) as ei:
            file_utils.validate_filename("../etc/passwd")
        assert ei.value.status_code == 400

    def test_blocked_ext_400(self):
        with pytest.raises(HTTPException) as ei:
            file_utils.validate_filename("evil.exe")
        assert "不允许上传" in ei.value.detail

    def test_archive_allowed_ok(self):
        assert file_utils.validate_filename("data.zip", allow_archives=True) == "data.zip"

    def test_plain_ok(self):
        assert file_utils.validate_filename("road.shp") == "road.shp"


# ── rate_limit ────────────────────────────────────────────────────────────────

class TestGetClientIp:

    @staticmethod
    def _req(headers=None, client=("10.0.0.1", 1234)):
        # ASGI scope 中 header 名为小写（starlette Headers 大小写敏感）
        scope = {
            "type": "http", "method": "GET", "path": "/",
            "headers": [(k.lower().encode(), v.encode())
                        for k, v in (headers or {}).items()],
            "client": client,
        }
        return Request(scope)

    def test_forwarded_takes_first(self):
        ip = rate_limit._get_client_ip(
            self._req({"x-forwarded-for": "1.2.3.4, 5.6.7.8"}))
        assert ip == "1.2.3.4"

    def test_real_ip_stripped(self):
        ip = rate_limit._get_client_ip(self._req({"x-real-ip": " 6.6.6.6 "}))
        assert ip == "6.6.6.6"

    def test_client_host_fallback(self):
        assert rate_limit._get_client_ip(self._req()) == "10.0.0.1"

    def test_no_client_loopback(self):
        assert rate_limit._get_client_ip(self._req(client=None)) == "127.0.0.1"


# ── security_middleware ───────────────────────────────────────────────────────

class TestSecurityHeadersMiddleware:

    @staticmethod
    def _mw():
        # dispatch 不依赖 self.app，__new__ 绕过 BaseHTTPMiddleware.__init__(app)
        return SecurityHeadersMiddleware.__new__(SecurityHeadersMiddleware)

    async def test_oversize_413(self):
        request = Request(scope={
            "type": "http", "method": "POST", "path": "/",
            "headers": [(b"content-length", b"9999999999")],
            "client": ("1.1.1.1", 1),
        })
        with pytest.raises(HTTPException) as ei:
            await self._mw().dispatch(request, AsyncMock(return_value=Response()))
        assert ei.value.status_code == 413

    async def test_prod_headers_hsts(self, monkeypatch):
        monkeypatch.setattr(settings, "debug", False)
        response = Response()
        request = Request(scope={
            "type": "http", "method": "GET", "path": "/",
            "headers": [], "client": ("1.1.1.1", 1),
        })
        out = await self._mw().dispatch(request, AsyncMock(return_value=response))
        assert out.headers["X-Content-Type-Options"] == "nosniff"
        assert out.headers["X-Frame-Options"] == "DENY"
        assert out.headers["Strict-Transport-Security"].startswith("max-age=31536000")

    async def test_debug_no_hsts(self, monkeypatch):
        monkeypatch.setattr(settings, "debug", True)
        out = await self._mw().dispatch(
            Request(scope={"type": "http", "method": "GET", "path": "/",
                           "headers": [], "client": None}),
            AsyncMock(return_value=Response()))
        assert "Strict-Transport-Security" not in out.headers


# ── auth_context ──────────────────────────────────────────────────────────────

class TestAuthContext:

    @staticmethod
    def _key(**kw):
        fields = dict(id="k1", key_hash="h", name="n", is_active=True, group_id=None)
        fields.update(kw)
        return ApiKey(**fields)

    def _ctx(self, super_admin=False, keys=("k1",)):
        return AuthContext(api_key=self._key(),
                           accessible_key_ids=list(keys),
                           is_super_admin=super_admin)

    def test_accessible_key_ids(self):
        assert self._ctx(keys=("a", "b")).accessible_key_ids == ["a", "b"]

    def test_owner_id_super_admin_none(self):
        assert self._ctx(super_admin=True).owner_id is None
        assert self._ctx(super_admin=False).owner_id == "k1"

    def test_group_id_delegated(self):
        g = "g1"
        assert self._ctx().group_id is None

    def test_getattr_delegation(self):
        assert self._ctx().name == "n"
        assert self._ctx().is_active is True

    def test_scope_clause(self):
        import sqlalchemy
        from sqlalchemy.sql.elements import BinaryExpression

        class M:
            api_key_id = sqlalchemy.Column("api_key_id", sqlalchemy.String)

        clause = self._ctx().scope_clause(M)
        assert isinstance(clause, BinaryExpression)
        assert "IN" in str(clause).upper()
        assert str(self._ctx(super_admin=True).scope_clause(M)) == "true"

    def test_owns(self):
        record = MagicMock()
        record.api_key_id = "k1"
        assert self._ctx().owns(record) is True
        record.api_key_id = "other"
        assert self._ctx().owns(record) is False
        assert self._ctx().owns(None) is False
        # owns(None) 恒 False（None 检查在先，super admin 也不例外）
        assert self._ctx(super_admin=True).owns(None) is False
        assert self._ctx(super_admin=True).owns(record) is True

    async def test_resolve_group(self):
        db = AsyncMock()

        class R:
            def all(self):
                return [("k1",), ("k2",)]

        db.execute.return_value = R()
        ctx = await AuthContext.resolve(db, self._key(group_id="g1", id="k1"))
        assert ctx.accessible_key_ids == ["k1", "k2"]
        db.execute.assert_awaited_once()

    async def test_resolve_no_group(self):
        db = AsyncMock()
        ctx = await AuthContext.resolve(db, self._key())
        assert ctx.accessible_key_ids == ["k1"]
        db.execute.assert_not_awaited()

    async def test_create_super_admin(self):
        db = AsyncMock()

        class R:
            def all(self):
                return [("k1",), ("k2",)]

        db.execute.return_value = R()
        ctx = await AuthContext.create_super_admin(db)
        assert ctx.is_super_admin is True
        assert ctx.accessible_key_ids == ["k1", "k2"]
        assert ctx.owner_id is None
        assert ctx.id == "00000000-0000-0000-0000-000000000001"
        assert ctx.name == "__admin__"

    async def test_resolve_virtual(self):
        """resolve 用于 super admin（无真实行）时 group_id 为 None 路径。"""
        db = AsyncMock()
        ctx = await AuthContext.resolve(db, self._key(group_id=None))
        assert not ctx.is_super_admin


# ── deps.authenticated 补分支 ────────────────────────────────────────────────

class TestAuthenticatedBranches:

    @staticmethod
    def _db_with_key(key):
        db = AsyncMock()

        class R:
            def __init__(self, k):
                self._k = k

            def scalar_one_or_none(self):
                return self._k

        db.execute.return_value = R(key)
        return db

    async def test_super_admin_key(self, monkeypatch):
        fake_ctx = MagicMock()
        create = AsyncMock(return_value=fake_ctx)
        monkeypatch.setattr(AuthContext, "create_super_admin", create)
        ctx = await deps_mod.authenticated(db=AsyncMock(),
                                           api_key=settings.admin_init_key)
        assert ctx is fake_ctx
        create.assert_awaited_once()

    async def test_missing_header_401(self):
        with pytest.raises(HTTPException) as ei:
            await deps_mod.authenticated(db=AsyncMock(), api_key=None)
        assert ei.value.status_code == 401

    async def test_cache_hit(self, monkeypatch):
        from app.core import cache as cache_mod
        key = ApiKey(key_hash="x", name="k", id="kid-1", is_active=True)
        r = AsyncMock()
        r.get.return_value = b"kid-1"
        monkeypatch.setattr(cache_mod, "get_redis", AsyncMock(return_value=r))
        resolve = AsyncMock(return_value="RESOLVED")
        monkeypatch.setattr(AuthContext, "resolve", resolve)
        ctx = await deps_mod.authenticated(
            db=self._db_with_key(key), api_key="anything")
        assert ctx == "RESOLVED"
        resolve.assert_awaited_once()

    async def test_cache_read_failure_falls_back(self, monkeypatch):
        from app.core import cache as cache_mod
        key = ApiKey(key_hash="x", name="k", is_active=True)
        monkeypatch.setattr(cache_mod, "get_redis",
                            AsyncMock(side_effect=RuntimeError("redis down")))
        resolve = AsyncMock(return_value="DB-OK")
        monkeypatch.setattr(AuthContext, "resolve", resolve)
        ctx = await deps_mod.authenticated(
            db=self._db_with_key(key), api_key="anything")
        assert ctx == "DB-OK"

    async def test_cache_write_failure_logs(self, monkeypatch, caplog):
        from app.core import cache as cache_mod
        key = ApiKey(key_hash=hash_api_key("raw-key"), name="k",
                     id="apikey-1", is_active=True)
        cache_r = AsyncMock()
        cache_r.get.return_value = None
        cache_r.setex = AsyncMock(side_effect=RuntimeError("setex boom"))
        monkeypatch.setattr(cache_mod, "get_redis", AsyncMock(return_value=cache_r))
        monkeypatch.setattr(AuthContext, "resolve", AsyncMock(return_value="OK"))
        db = self._db_with_key(key)
        with caplog.at_level(logging.DEBUG, logger="app.core.deps"):
            ctx = await deps_mod.authenticated(db=db, api_key="raw-key")
        assert ctx == "OK"
        assert "apikey cache write failed" in caplog.text
        cache_r.setex.assert_awaited()

    async def test_cache_miss_sets_cache(self, monkeypatch):
        from app.core import cache as cache_mod
        key = ApiKey(key_hash=hash_api_key("raw-key"), name="k",
                     id="apikey-1", is_active=True)
        cache_r = AsyncMock()
        cache_r.get.return_value = None
        monkeypatch.setattr(cache_mod, "get_redis", AsyncMock(return_value=cache_r))
        monkeypatch.setattr(AuthContext, "resolve", AsyncMock(return_value="OK"))
        ctx = await deps_mod.authenticated(
            db=self._db_with_key(key), api_key="raw-key")
        assert ctx == "OK"
        cache_r.setex.assert_awaited_once()

    async def test_invalid_key_401(self, monkeypatch):
        from app.core import cache as cache_mod
        monkeypatch.setattr(cache_mod, "get_redis",
                            AsyncMock(return_value=None))  # 跳过真实 Redis
        with pytest.raises(HTTPException) as ei:
            await deps_mod.authenticated(db=self._db_with_key(None),
                                         api_key="nope")
        assert ei.value.status_code == 401
        assert "无效" in ei.value.detail