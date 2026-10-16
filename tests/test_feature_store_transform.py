# -*- coding: utf-8 -*-
"""feature_store.transform_features_by_file 统一引擎重构的契约测试。

v2.13.0 将写回式变换收敛到统一引擎（resolve_transform_crs + transform_geometry）。
本文件验证重构的**契约**（不需要真实 DB）：
    - 旧私有函数 `_transform_coords` / `_transform_xy` 已移除（防止死代码复现）。
    - 统一引擎解析器可被 feature_store 引用（写回路径共用同一套 CRS 解析）。
    - 统一引擎的域失败语义（inf/NaN → None/failed）对写回路径同样生效。

注：`transform_features_by_file` 本身需要真实 DB 会话（跨三表读写），此处不 mock
DB，而是验证其依赖的无 DB 部分——统一引擎——的契约行为。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.services.feature_store as fs
from app.services.transform_engine import (
    ResolvedCRS,
    transform_geometry,
    transform_feature_list,
)


WGS = ResolvedCRS(srid=4326, pyproj_crs="EPSG:4326", epsg=4326,
                  is_custom=False, source="epsg", display="EPSG:4326")
WEBM = ResolvedCRS(srid=3857, pyproj_crs="EPSG:3857", epsg=3857,
                   is_custom=False, source="epsg", display="EPSG:3857")


def test_feature_store_helpers_removed():
    """旧私有变换辅助应已移除（防止死代码复现）。"""
    assert not hasattr(fs, "_transform_coords")
    assert not hasattr(fs, "_transform_xy")


def test_feature_store_uses_unified_resolver():
    """feature_store 写回路径引用统一引擎的解析器与变换核心。"""
    # 通过遍历模块 AST/源码确认 import，保证写回路径确实复用统一引擎
    src = Path(fs.__file__).read_text(encoding="utf-8")
    assert "resolve_transform_crs" in src
    assert "transform_geometry" in src


def test_write_back_rejects_inf_geometry():
    """写回路径复用的 transform_geometry 对域失败返回 None（→ failed，不写回损坏几何）。"""
    # 同一被 feature_store 使用的 transform_geometry：米制大值误作地理源 → None
    out = transform_geometry(
        {"type": "Point", "coordinates": [34000000, 5000000]}, WGS, WEBM
    )
    assert out is None


def test_write_back_batch_counts_failed():
    """批量变换（写回也走此语义）中域失败计入 failed。"""
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.0, 31.0]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [34000000, 5000000]}, "properties": {}},
    ]
    out, stats = transform_feature_list(feats, WGS, WEBM)
    assert stats.transformed == 1
    assert stats.failed == 1
    assert len(out) == 1
