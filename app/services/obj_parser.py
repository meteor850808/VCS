"""OBJ 3D 模型解析器 — 提取顶点、面片、材质信息。

Phase 4: 支持 .obj 文件的几何解析，将 3D 模型转换为 PostGIS 可存储的要素。
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ObjVertex:
    x: float
    y: float
    z: float


@dataclass
class ObjFace:
    vertex_indices: list[int]      # 顶点索引（1-based，OBJ 规范）
    material: Optional[str] = None


@dataclass
class ObjObject:
    name: str
    vertices: list[ObjVertex] = field(default_factory=list)
    faces: list[ObjFace] = field(default_factory=list)
    material: Optional[str] = None


@dataclass
class ObjParseResult:
    objects: list[ObjObject] = field(default_factory=list)
    total_vertices: int = 0
    total_faces: int = 0
    materials: list[str] = field(default_factory=list)
    has_normals: bool = False
    has_texture_coords: bool = False


def parse_obj_file(file_path: str) -> ObjParseResult:
    """解析 OBJ 文件，返回结构化结果。"""
    result = ObjParseResult()
    current_object = None
    global_vertices: list[ObjVertex] = []
    global_normals: list[tuple[float, float, float]] = []
    global_texcoords: list[tuple[float, float]] = []

    try:
        text = Path(file_path).read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        logger.error(f"读取 OBJ 文件失败: {e}")
        return result

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        token = parts[0]

        if token in ('o', 'g'):
            # 新对象/组（o 和 g 语义相同：标记顶点/面片的归属）
            name = parts[1] if len(parts) > 1 else f"object_{len(result.objects)}"
            current_object = ObjObject(name=name)
            result.objects.append(current_object)

        elif token == 'v':
            # 顶点
            try:
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                v = ObjVertex(x=x, y=y, z=z)
                global_vertices.append(v)
                if current_object:
                    current_object.vertices.append(v)
                result.total_vertices += 1
            except (IndexError, ValueError):
                pass

        elif token == 'vn':
            # 法线
            result.has_normals = True
            try:
                nx, ny, nz = float(parts[1]), float(parts[2]), float(parts[3])
                global_normals.append((nx, ny, nz))
            except (IndexError, ValueError):
                pass

        elif token == 'vt':
            # 纹理坐标
            result.has_texture_coords = True
            try:
                u, v = float(parts[1]), float(parts[2])
                global_texcoords.append((u, v))
            except (IndexError, ValueError):
                pass

        elif token == 'f':
            # 面
            face_vertices = []
            for p in parts[1:]:
                # OBJ 面格式: v/vt/vn 或 v//vn 或 v/vt 或 v
                indices = p.split('/')
                v_idx = int(indices[0]) if indices[0] else 0
                face_vertices.append(v_idx)

            if face_vertices:
                if not current_object:
                    # 没有对象定义时创建默认对象，继承已解析的全局顶点
                    current_object = ObjObject(name="default", vertices=list(global_vertices))
                    result.objects.append(current_object)
                face = ObjFace(vertex_indices=face_vertices)
                current_object.faces.append(face)
                result.total_faces += 1

        elif token == 'usemtl':
            # 使用材质
            mat_name = parts[1] if len(parts) > 1 else None
            if mat_name and mat_name not in result.materials:
                result.materials.append(mat_name)
            if current_object:
                current_object.material = mat_name

        elif token == 'mtllib':
            # 材质库文件
            pass

    return result


def obj_to_geojson_features(result: ObjParseResult) -> list[dict]:
    """将 OBJ 解析结果转换为 GeoJSON Feature 列表。

    每个对象转换为一个 Point Feature（质心）+ 属性信息。
    对于有面片的对象，额外生成面要素（如果面片数足够）。
    """
    features = []

    for obj in result.objects:
        if not obj.vertices:
            continue

        # 计算质心
        cx = sum(v.x for v in obj.vertices) / len(obj.vertices)
        cy = sum(v.y for v in obj.vertices) / len(obj.vertices)
        cz = sum(v.z for v in obj.vertices) / len(obj.vertices)

        # 点要素（质心）
        point_feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [cx, cy, cz],
            },
            "properties": {
                "object_name": obj.name,
                "vertex_count": len(obj.vertices),
                "face_count": len(obj.faces),
                "material": obj.material,
                "min_x": min(v.x for v in obj.vertices),
                "max_x": max(v.x for v in obj.vertices),
                "min_y": min(v.y for v in obj.vertices),
                "max_y": max(v.y for v in obj.vertices),
                "min_z": min(v.z for v in obj.vertices),
                "max_z": max(v.z for v in obj.vertices),
            },
        }
        features.append(point_feature)

    return features


def parse_obj_bytes(content: bytes) -> ObjParseResult:
    """从字节内容解析 OBJ。"""
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(delete=False, suffix='.obj', mode='wb') as f:
        f.write(content)
        tmp_path = f.name

    try:
        return parse_obj_file(tmp_path)
    finally:
        os.unlink(tmp_path)
