# -*- coding: utf-8 -*-
"""平台感知的 MS Access (MDB) ODBC 连接助手。

- Windows: 官方 Microsoft Access Driver（pyodbc 读写）。
- POSIX:   mdbtools 提供的 MDBTools 驱动（**只读**；写见 export_service 守卫）。
"""
from __future__ import annotations

import sys

ACCESS_DRIVER_WIN = "Microsoft Access Driver (*.mdb, *.accdb)"
ACCESS_DRIVER_POSIX = "MDBTools"


def is_windows() -> bool:
    """当前是否为 Windows 平台（运行时判定，便于测试 monkeypatch）。"""
    return sys.platform == "win32"


def access_driver() -> str:
    """返回当前平台可用的 Access ODBC 驱动名。"""
    return ACCESS_DRIVER_WIN if is_windows() else ACCESS_DRIVER_POSIX


def access_conn_str(path: str) -> str:
    """构造 pyodbc 连接串（保持 Windows 旧形态：含尾分号）。

    POSIX 的 MDBTools 驱动会把尾分号拼进文件名，故不加尾分号。
    """
    if is_windows():
        return f"DRIVER={{{ACCESS_DRIVER_WIN}}};DBQ={path};"
    return f"DRIVER={{{ACCESS_DRIVER_POSIX}}};DBQ={path}"
