# -*- coding: utf-8 -*-
"""export_service MDB 写入的平台守卫。"""
import sys

import pytest

from app.services import export_service


def test_guard_raises_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="仅支持 Windows"):
        export_service._ensure_mdb_export_supported()


def test_guard_passes_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    export_service._ensure_mdb_export_supported()  # 不抛
