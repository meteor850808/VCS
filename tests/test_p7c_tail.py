# -*- coding: utf-8 -*-
"""P7c 收官补测：data.py / quality.py 最后 4 个真实分支（其余 miss 已 pragma 置灰）。

覆盖点：
- data.py _populate_table_schema（274-277）：坏 .xlsx 字节 → 最外层 except 兜底
- data.py _load_shp_group_features_async（2129）：伴生文件含 .shp → continue
- quality.py _quality_check_stored（108-109）：feature_count=0 但分表有要素 → 放行（S4）
"""

import io
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.api.v1 import data as data_module
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.quality_report import QualityReport
from app.models.spatial_feature import PointFeature
from app.models.stored_file import StoredFile
from tests.conftest import _TestSession


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _autouse_test_db(test_db):
    """与其它路由测试相同的测试库 autouse 包装。"""
    yield


async def _create_key(db_session, name):
    """快速建测试 Key，返回 (raw, key_orm)。"""
    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name=name, is_active=True)
    db_session.add(key)
    await db_session.commit()
    return raw, key


# ══════════════════════════════════════════════════════════════════════════════
# data.py _populate_table_schema（274-277）except 兜底
# ══════════════════════════════════════════════════════════════════════════════

class TestPopulateTableSchemaExcept:
    async def test_poison_xlsx(self, monkeypatch):
        """_parse_xlsx_schema 抛错（坏文件/解析器异常）→ 最外层 except 兜底。"""
        monkeypatch.setattr(
            "app.services.format_detection._parse_xlsx_schema",
            lambda p: (_ for _ in ()).throw(RuntimeError("bad xlsx")),
        )
        sf = StoredFile(original_name="poison.xlsx", api_key_id="k")
        data_module._populate_table_schema(sf, b"x", "poison.xlsx")
        assert sf.file_format == "xlsx"
        assert sf.data_type == "table"
        assert sf.field_schema is None

    async def test_poison_csv(self, monkeypatch):
        """_parse_csv_schema 抛错 → 最外层 except 兜底（表级防御）。"""
        monkeypatch.setattr(
            "app.services.format_detection._parse_csv_schema",
            lambda p: (_ for _ in ()).throw(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")),
        )
        sf = StoredFile(original_name="poison.csv", api_key_id="k")
        data_module._populate_table_schema(sf, b"\xff\xfe\x00", "poison.csv")
        assert sf.file_format == "csv"
        assert sf.data_type == "table"


# ══════════════════════════════════════════════════════════════════════════════
# data.py _load_shp_group_features_async（2129）伴生 .shp continue
# ══════════════════════════════════════════════════════════════════════════════

class TestShpGroupShpCompanion:
    _FEAT = [{"geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}}]

    async def test_companion_contains_shp(self, monkeypatch):
        """伴生文件 dict 含 .shp 自身 → 循环 2129 continue，不重复写同名文件。"""
        monkeypatch.setattr(data_module, "AsyncSessionLocal", _TestSession)
        monkeypatch.setattr(data_module, "extract_features_from_file",
                            lambda path, drv: self._FEAT)
        monkeypatch.setattr(data_module, "write_features_batch",
                            AsyncMock(return_value={"written": 2, "skipped_empty": 0, "skipped_no_geometry": 0}))
        monkeypatch.setattr(data_module, "validate_loaded_extent", AsyncMock(return_value=None))
        stats = await data_module._load_shp_group_features_async(
            "sf-1", "roads.shp", b"shp-bytes",
            {".shp": b"dup", ".dbf": b"dbf", ".shx": b"shx"}, "owner-1", srid=4326)
        assert stats["written"] == 2
        assert stats["geometry_type"] == "Point"


# ══════════════════════════════════════════════════════════════════════════════
# quality.py S4：feature_count=0 但分表实际有要素 → 放行（108-109）
# ══════════════════════════════════════════════════════════════════════════════

_SAMPLE_RESULT = {
    "attr_results": [],
    "attr_summary": {
        "total": 0, "passed": 0, "failed": 0,
        "by_severity": {"error": 0, "warning": 0},
    },
    "topo_results": [],
    "topo_summary": {
        "total": 0, "passed": 0, "failed": 0,
        "by_severity": {"error": 0, "warning": 0},
    },
    "scores": {
        "completeness": 1.0, "accuracy": 1.0, "consistency": 1.0,
        "uniqueness": 1.0, "topology": 1.0, "overall": 1.0,
    },
    "total_violations": 0,
    "violation_samples": [],
}


class TestQualityS4Release:
    async def test_count_zero_but_features_exist(self, client, db_session):
        """feature_count=0 但 point 分表实际有要素 → 放行并以实际要素质检（200）。"""
        raw, key = await _create_key(db_session, "p7c_s4")
        sf = StoredFile(
            original_name="s4.geojson", api_key_id=key.id,
            layer_name="s4", content_type="application/geo+json",
            file_size=10, file_data=b"{}", feature_count=0,
        )
        db_session.add(sf)
        await db_session.flush()

        # 创建 point 分区分表并插入一条实际要素（geometry=NULL 满足 ST_IsValid 检查）
        sid = sf.id
        safe = sid.replace("-", "_")
        await db_session.execute(text(
            f'CREATE TABLE IF NOT EXISTS point_features_p_{safe} '
            f"PARTITION OF point_features FOR VALUES IN ('{sid}')"
        ))
        await db_session.execute(text(
            f'CREATE INDEX IF NOT EXISTS ix_point_features_p_{safe}_idx '
            f'ON point_features_p_{safe} (stored_file_id)'
        ))
        feat = PointFeature(
            id=str(uuid.uuid4()), stored_file_id=sid, api_key_id=key.id,
            layer_name="s4", source_name="s4.geojson",
            feature_index=0, geometry_type="Point",
        )
        db_session.add(feat)
        await db_session.commit()

        from unittest.mock import patch
        with patch("app.api.v1.quality.run_quality_check",
                   new=AsyncMock(return_value=_SAMPLE_RESULT)):
            resp = await client.post(
                "/api/v1/quality/check",
                params={"file_id": sid},
                headers={"X-API-Key": raw},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stored_file_id"] == sid
        assert body["total_violations"] == 0
        # 校验写入了 QualityReport
        row = await db_session.get(QualityReport, body["report_id"])
        assert row is not None
        assert row.stored_file_id == sid