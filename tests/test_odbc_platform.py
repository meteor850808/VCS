# -*- coding: utf-8 -*-
"""_odbc：平台感知的 Access/MDB ODBC 助手。"""
import sys

from app.services import _odbc


def test_driver_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _odbc.is_windows() is True
    assert _odbc.access_driver() == "Microsoft Access Driver (*.mdb, *.accdb)"


def test_driver_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _odbc.is_windows() is False
    assert _odbc.access_driver() == "MDBTools"


def test_conn_str_win32_has_trailing_semicolon(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _odbc.access_conn_str(r"C:\a\x.mdb") == (
        "DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:\\a\\x.mdb;")


def test_conn_str_posix_no_trailing_semicolon(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _odbc.access_conn_str("/data/a.mdb") == "DRIVER={MDBTools};DBQ=/data/a.mdb"
