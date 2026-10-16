"""直接构造 UploadFile 列表，绕过 multipart 编码，验证 CSV 导入的文件名分支。

multipart 里 filename 为空时 httpx 会退化成普通表单字段，FastAPI 在进路由前就
返回 422，所以「无文件名上传」这个分支无法通过 HTTP 层触发，只能直接打函数。
"""
import pytest

from app.api.v1.data_standard_v2 import import_export as ie


class _FakeUpload:
    """最小 UploadFile 替身：只有 filename 和 read()。"""

    def __init__(self, filename, payload: bytes):
        self.filename = filename
        self._payload = payload

    async def read(self):
        return self._payload


class _Recorder:
    """只记录 add() 调用的假 session，用来跑 legacy 导入分支。"""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objs):
        self.added.extend(objs)


@pytest.mark.asyncio
async def test_legacy_import_warns_for_unnamed_file():
    rec = _Recorder()
    tables, fields, warnings = await ie._create_csv_children_legacy(
        rec, "std-legacy", {"普通表.csv": "﻿a,b\n1,2\n".encode("utf-8")}, unnamed=1)
    assert (tables, fields) == (1, 2), (tables, fields)
    assert any("无文件名" in w for w in warnings), warnings


@pytest.mark.asyncio
async def test_read_upload_blobs_counts_and_drops_unnamed():
    """_read_upload_blobs：无文件名的 part 计数丢弃、其余按文件名收进 blobs。

    multipart 里 filename 为空会被 httpx 退化成普通表单字段、FastAPI 先判 422，
    所以这条直打辅助函数。
    """
    blobs, unnamed = await ie._read_upload_blobs([
        _FakeUpload("", b"x"),
        _FakeUpload("a.csv", b"\xef\xbb\xbfa\n"),
        _FakeUpload("b.csv", b"\xef\xbb\xbfb\n"),
        _FakeUpload("", b"y"),
    ])
    assert unnamed == 2
    assert sorted(blobs) == ["a.csv", "b.csv"]
    assert blobs["a.csv"] == b"\xef\xbb\xbfa\n"
