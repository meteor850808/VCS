# -*- coding: utf-8 -*-
"""P5j — app/services/dwg_converter.py 服务级 100%。

LibreDWG 封装（dwg2dxf.exe）。工具未部署 → 降级返回 None 是设计语义，
通过 monkeypatch _DWG2DXF / subprocess.run / tempfile.mkdtemp 全分支覆盖，
不依赖真实 exe。
"""
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest import MonkeyPatch

from app.services import dwg_converter as dc


def _install_tool_fake(tmp_path, monkeypatch: MonkeyPatch) -> Path:
    """让 is_dwg_converter_available() 为 True，workdir 指向 tmp_path。"""
    exe = tmp_path / "dwg2dxf.exe"
    exe.write_text("fake")
    monkeypatch.setattr(dc, "_DWG2DXF", exe)
    monkeypatch.setattr(dc, "tempfile", SimpleNamespace(mkdtemp=lambda prefix: str(tmp_path)))
    return exe


def _completed(returncode: int, stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")


class TestIsDwgConverterAvailable:
    def test_true_when_deployed(self, tmp_path, monkeypatch):
        exe = _install_tool_fake(tmp_path, monkeypatch)
        assert dc.is_dwg_converter_available() is True
        assert exe.is_file()

    def test_false_when_missing(self, monkeypatch):
        monkeypatch.setattr(dc, "_DWG2DXF", Path(str(uuid.uuid4())) / "dwg2dxf.exe")
        monkeypatch.setattr(dc.shutil, "which", lambda *a, **k: None)
        assert dc.is_dwg_converter_available() is False


class TestDwgToDxf:
    def test_converter_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dc, "_DWG2DXF", tmp_path / "nope.exe")
        monkeypatch.setattr(dc.shutil, "which", lambda *a, **k: None)
        monkeypatch.setattr(dc, "subprocess", SimpleNamespace(run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用"))))
        assert dc.dwg_to_dxf(str(tmp_path / "a.dwg")) is None

    def test_success_returns_dxf_path(self, tmp_path, monkeypatch, caplog):
        _install_tool_fake(tmp_path, monkeypatch)
        (tmp_path / "out.dxf").write_text("0\nSECTION\n0\nEOF")
        calls = {}
        def _run(cmd, **kw):
            calls["cmd"] = cmd
            calls["cwd"] = kw.get("cwd")
            assert kw["timeout"] == 120
            return _completed(0)
        monkeypatch.setattr(dc, "subprocess", SimpleNamespace(run=_run))
        with caplog.at_level(logging.DEBUG):
            result = dc.dwg_to_dxf(str(tmp_path / "图纸.dwg"))
        assert result == str(tmp_path / "out.dxf")
        assert "图纸.dwg" in str(calls["cmd"][-1])
        assert str(dc._DWG2DXF) in str(calls["cmd"][0])
        assert calls["cwd"] == (str(dc._TOOLS_DIR) if sys.platform == "win32" else None)  # win 需同目录 DLL

    def test_returncode_nonzero_returns_none(self, tmp_path, monkeypatch, caplog):
        _install_tool_fake(tmp_path, monkeypatch)
        call = {"n": 0}
        def _run(cmd, **kw):
            call["n"] += 1
            return _completed(3, "line1\nline2\nUnrecognized entity\n")
        monkeypatch.setattr(dc, "subprocess", SimpleNamespace(run=_run))
        with caplog.at_level(logging.WARNING):
            assert dc.dwg_to_dxf(str(tmp_path / "bad.dwg")) is None
        assert call["n"] == 1
        assert "Unrecognized entity" in caplog.text  # stderr tail 进告警
        assert not (tmp_path / "out.dxf").exists()

    def test_no_output_file_returns_none(self, tmp_path, monkeypatch, caplog):
        _install_tool_fake(tmp_path, monkeypatch)
        monkeypatch.setattr(dc, "subprocess", SimpleNamespace(run=lambda *a, **k: _completed(0, "")))
        with caplog.at_level(logging.WARNING):
            assert dc.dwg_to_dxf(str(tmp_path / "x.dwg")) is None

    def test_success_with_stderr_goes_debug(self, tmp_path, monkeypatch, caplog):
        _install_tool_fake(tmp_path, monkeypatch)
        (tmp_path / "out.dxf").write_text("0\nEOF")
        monkeypatch.setattr(dc, "subprocess", SimpleNamespace(
            run=lambda *a, **k: _completed(0, "WARNING: Unstable Class 121")))
        with caplog.at_level(logging.DEBUG):
            result = dc.dwg_to_dxf(str(tmp_path / "s.dwg"))
        assert result is not None
        assert "Unstable Class" in caplog.text

    def test_timeout_returns_none(self, tmp_path, monkeypatch, caplog):
        _install_tool_fake(tmp_path, monkeypatch)
        def _run(*a, **k):
            raise subprocess.TimeoutExpired("dwg2dxf", 120)
        monkeypatch.setattr(dc.subprocess, "run", _run)  # 保留模块级 subprocess
        with caplog.at_level(logging.WARNING):
            assert dc.dwg_to_dxf(str(tmp_path / "t.dwg")) is None
        assert "超时" in caplog.text

    def test_unexpected_exception_returns_none(self, tmp_path, monkeypatch, caplog):
        _install_tool_fake(tmp_path, monkeypatch)
        def _run(*a, **k):
            raise OSError("DLL 缺失")
        monkeypatch.setattr(dc.subprocess, "run", _run)
        with caplog.at_level(logging.WARNING):
            assert dc.dwg_to_dxf(str(tmp_path / "e.dwg")) is None
        assert "DLL 缺失" in caplog.text


class TestCleanupConvertedDxf:
    def test_none_returns_silently(self, monkeypatch):
        monkeypatch.setattr(dc, "shutil", SimpleNamespace(rmtree=lambda p, **k: (_ for _ in ()).throw(AssertionError("不应调用"))))
        assert dc.cleanup_converted_dxf(None) is None

    def test_removes_workdir(self, tmp_path):
        (tmp_path / "out.dxf").write_text("0")
        dc.cleanup_converted_dxf(str(tmp_path / "out.dxf"))
        assert not tmp_path.exists()

    def test_rmtree_exception_swallowed(self, tmp_path, monkeypatch):
        def _boom(path, ignore_errors=False):
            raise PermissionError("占用中")
        monkeypatch.setattr(dc, "shutil", SimpleNamespace(rmtree=_boom))
        dc.cleanup_converted_dxf(str(tmp_path / "out.dxf"))  # 不抛