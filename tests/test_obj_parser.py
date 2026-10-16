"""P4: OBJ 解析器测试 — app/services/obj_parser.py 0% → 100%。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.services.obj_parser import (
    ObjFace,
    ObjObject,
    ObjParseResult,
    ObjVertex,
    obj_to_geojson_features,
    parse_obj_bytes,
    parse_obj_file,
)


# ── 样本数据 ─────────────────────────────────────────────────────────

SIMPLE_OBJ = """\
# 简单立方体
mtllib cube.mtl
o cube
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
vn 0 0 1
vt 0 0
vt 1 1
usemtl red
f 1 2 3 4
f 1//1 2//1 3//1
f 1/1 2/1 3/1
"""

OBJ_NO_OBJECT = """\
v 0 0 0
v 1 0 0
f 1 2
"""

OBJ_MALFORMED = """\
v 0 0
v notanumber 1 0
vn no numbers here
vt bad too
f
o
usemtl
"""


class TestParseObjFile:
    @pytest.fixture(autouse=True)
    def tmp_obj(self, tmp_path):
        self.tmp_path = tmp_path

    def _write(self, content):
        p = self.tmp_path / "test.obj"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_parse_simple_obj(self):
        result = parse_obj_file(self._write(SIMPLE_OBJ))
        assert result.total_vertices == 4
        assert result.total_faces == 3
        assert len(result.objects) == 1
        obj = result.objects[0]
        assert obj.name == "cube"
        assert obj.material == "red"
        assert result.materials == ["red"]
        assert result.has_normals is True
        assert result.has_texture_coords is True
        assert [v.x for v in obj.vertices] == [0, 1, 1, 0]

    def test_face_index_parsing_formats(self):
        """f 格式: v  / v/vt / v//vn / v/vt/vn"""
        result = parse_obj_file(self._write(SIMPLE_OBJ))
        faces = [f.vertex_indices for f in result.objects[0].faces]
        assert faces[0] == [1, 2, 3, 4]          # 纯顶点
        assert faces[1] == [1, 2, 3]             # v//vn
        assert faces[2] == [1, 2, 3]             # v/vt

    def test_default_object_when_no_o(self):
        result = parse_obj_file(self._write(OBJ_NO_OBJECT))
        assert len(result.objects) == 1
        obj = result.objects[0]
        assert obj.name == "default"
        assert len(obj.vertices) == 2
        assert len(obj.faces) == 1

    def test_malformed_lines_skipped(self):
        result = parse_obj_file(self._write(OBJ_MALFORMED))
        # v 0 0 缺 z → 跳过；v notanumber → 跳过
        assert result.total_vertices == 0
        # 空 f / 空 usemtl 均不崩溃；空 o 仍会创建默认命名对象
        assert result.total_faces == 0
        assert len(result.objects) == 1  # 空 o → object_0
        assert result.objects[0].name == "object_0"
        assert result.materials == []

    def test_empty_and_comment_lines(self):
        result = parse_obj_file(self._write("# 只有注释\n\n"))
        assert result.total_vertices == 0
        assert result.total_faces == 0
        assert result.objects == []

    def test_bytes_and_ctrl(self):
        """'#' 注释、空行、无对象 → 不崩溃"""
        result = parse_obj_file(self._write("g group1\nv 0.5 0.5 0.5\n"))
        assert len(result.objects) == 1
        assert result.objects[0].name == "group1"
        assert result.total_vertices == 1

    def test_file_not_found_returns_empty(self):
        result = parse_obj_file(str(self.tmp_path / "missing.obj"))
        assert result.total_vertices == 0
        assert result.objects == []


class TestObjToGeoJson:
    def test_point_features_from_objects(self):
        obj = ObjObject(
            name="box",
            vertices=[ObjVertex(0, 0, 0), ObjVertex(2, 2, 2)],
            faces=[ObjFace([1, 2])],
            material="steel",
        )
        result = ObjParseResult(objects=[obj])
        features = obj_to_geojson_features(result)
        assert len(features) == 1
        f = features[0]
        assert f["geometry"]["type"] == "Point"
        assert f["geometry"]["coordinates"] == [1.0, 1.0, 1.0]  # 质心
        props = f["properties"]
        assert props["object_name"] == "box"
        assert props["vertex_count"] == 2
        assert props["face_count"] == 1
        assert props["material"] == "steel"
        assert props["min_x"] == 0 and props["max_x"] == 2

    def test_object_without_vertices_skipped(self):
        result = ObjParseResult(objects=[ObjObject(name="empty")])
        assert obj_to_geojson_features(result) == []

    def test_empty_result(self):
        assert obj_to_geojson_features(ObjParseResult()) == []


class TestParseObjBytes:
    def test_roundtrip(self):
        result = parse_obj_bytes(SIMPLE_OBJ.encode("utf-8"))
        assert result.total_vertices == 4
        assert result.total_faces == 3

    def test_tempfile_cleaned(self):
        import glob
        import os
        import tempfile

        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "*.obj")))
        parse_obj_bytes(b"v 0 0 0\n")
        after = set(glob.glob(os.path.join(tempfile.gettempdir(), "*.obj")))
        assert after == before  # 临时文件已被删除