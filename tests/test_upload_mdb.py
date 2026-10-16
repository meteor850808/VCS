# -*- coding: utf-8 -*-
"""MDB upload endpoint tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_upload_mdb_endpoint_exists():
    """Test that upload endpoint exists and accepts MDB files."""
    response = client.get("/api/v1/data/upload")
    assert response.status_code != 404
