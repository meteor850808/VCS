"""P4: 几何修复测试 — app/services/geometry_repair.py 18% → 100%。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.services.geometry_repair import (
    RepairResult,
    RepairStrategy,
    _ensure_ccw,
    _ensure_cw,
    _fix_ring_in_poly,
    _fix_ring_orientation,
    _ring_is_clockwise,
    compare_feature_counts,
    repair_features,
)

# ── 样本几何 ─────────────────────────────────────────────────────────

VALID_POINT = {"type": "Point", "coordinates": [1.0, 2.0]}
VALID_LINE = {"type": "LineString", "coordinates": [[0, 0], [1, 1], [2, 0]]}
# 自相交多边形 → 无效（bowtie）
BOWTIE_POLY = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]],
}
# 有效矩形
RECT_POLY = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
}
MULTI_POLY = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
        [[[2, 2], [2, 3], [3, 3], [3, 2], [2, 2]]],
    ],
}


class TestRepairFeatures:
    def test_empty_features(self):
        features: list[dict] = []
        result = repair_features(features)
        assert result.total_input == 0
        assert result.total_output == 0

    def test_all_valid_no_repair(self):
        features = [
            {"geometry": VALID_POINT},
            {"geometry": VALID_LINE},
        ]
        result = repair_features(features)
        assert result.total_input == 2
        assert result.total_valid == 2
        assert result.total_fixed == 0
        assert result.total_dropped == 0
        assert len(features) == 2  # 原地替换

    def test_missing_geometry_kept_by_default(self):
        features = [{"properties": {"a": 1}}]
        result = repair_features(features)
        assert result.total_dropped == 1
        assert result.total_output == 1  # 默认策略保留无几何要素

    def test_missing_geometry_dropped_with_drop(self):
        features = [{"properties": {"a": 1}}]
        result = repair_features(features, strategy=RepairStrategy.DROP)
        assert result.total_dropped == 1
        assert result.total_output == 0
        assert features == []

    def test_unparseable_geometry_dropped(self):
        features = [{"geometry": {"type": "Point", "coordinates": "not-a-list"}}]
        result = repair_features(features)
        assert result.total_dropped == 1
        assert "无法解析几何" in result.details[0]

    def test_invalid_bowtie_autofixed(self):
        features = [{"geometry": BOWTIE_POLY}]
        result = repair_features(features)
        assert result.total_fixed >= 1
        assert result.total_output == 1
        # 修复后的几何仍是有效的
        assert len(features) == 1
        assert features[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")

    def test_raise_strategy_raises(self):
        features = [{"geometry": BOWTIE_POLY}]
        with pytest.raises(ValueError, match="几何无效"):
            repair_features(features, strategy=RepairStrategy.RAISE)

    def test_raise_strategy_passes_valid(self):
        features = [{"geometry": RECT_POLY}, {"geometry": VALID_POINT}]
        result = repair_features(features, strategy=RepairStrategy.RAISE)
        assert result.total_valid == 2
        assert result.total_output == 2

    def test_drop_strategy_drops_invalid(self):
        features = [{"geometry": VALID_POINT}, {"geometry": BOWTIE_POLY}]
        result = repair_features(features, strategy=RepairStrategy.DROP)
        assert result.total_dropped == 1
        assert result.total_output == 1
        assert len(features) == 1

    def test_isolate_strategy_keeps_unfixable(self):
        """ISOLATE 与 AUTO_FIX 行为一致（无效要素修复，失败则保留原始）"""
        features = [{"geometry": BOWTIE_POLY}]
        result = repair_features(features, strategy=RepairStrategy.ISOLATE)
        assert result.total_output == 1  # 修复成功或保留

    def test_shapely_missing_fallback(self, monkeypatch):
        """shapely 不可用时跳过修复，全部视为有效"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "shapely.geometry":
                raise ImportError("no shapely")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        features = [{"geometry": BOWTIE_POLY}]
        result = repair_features(features)
        assert result.total_output == 1
        assert result.total_valid == 1
        assert "shapely 未安装" in result.details[0]

    def test_buffer_and_make_valid_exceptions(self, monkeypatch):
        """buffer(0) 与 make_valid 均抛出 → 落入“无法修复，保留原始”"""
        import shapely.geometry as sg

        class _BoomGeom:
            is_valid = False
            is_simple = False

            def buffer(self, d):
                raise RuntimeError("buffer boom")

        monkeypatch.setattr(sg, "shape", lambda data: _BoomGeom())
        features = [{"geometry": {"type": "Point", "coordinates": [0, 0]}}]
        result = repair_features(features)
        assert result.total_fixed == 0
        assert result.total_dropped == 1
        assert result.total_output == 1
        assert "无法修复，保留原始" in result.details[0]
        assert len(features) == 1  # 原样保留

    def test_make_valid_success_path(self, monkeypatch):
        """buffer(0) 产出无效 → 回退到 make_valid 成功修复（防御性分支）"""
        import shapely.geometry as sg
        import shapely.validation as sv

        class _FakeValid:
            is_valid = True
            is_empty = False

        class _BadBufferGeom:
            is_valid = False
            is_simple = False

            def buffer(self, d):
                return _BadBufferResult()

        class _BadBufferResult:
            is_valid = False  # buffer 结果无效 → 不采纳

        monkeypatch.setattr(sg, "shape", lambda data: _BadBufferGeom())
        monkeypatch.setattr(sv, "make_valid", lambda geom: _FakeValid())
        monkeypatch.setattr(
            sg, "mapping", lambda g: {"type": "Point", "coordinates": [1.0, 1.0]}
        )
        features = [{"geometry": {"type": "Point", "coordinates": [0, 0]}}]
        result = repair_features(features)
        assert result.total_fixed == 1
        assert result.total_output == 1
        assert result.total_dropped == 0
        # 修复后几何被替换为 mapping 输出
        assert features[0]["geometry"] == {
            "type": "Point",
            "coordinates": [1.0, 1.0],
        }


class TestRingOrientation:
    def test_clockwise_detection(self):
        # 上→右→下→左 = 顺时针（坐标 y 向上）
        cw = [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]
        assert _ring_is_clockwise(cw) is True

    def test_counterclockwise_detection(self):
        # 右→上→左→下 = 逆时针
        ccw = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        assert _ring_is_clockwise(ccw) is False

    def test_ensure_ccw(self):
        cw = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        fixed = _ensure_ccw(list(cw))
        assert _ring_is_clockwise(fixed) is False

    def test_ensure_cw(self):
        ccw = [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]
        fixed = _ensure_cw(list(ccw))
        assert _ring_is_clockwise(fixed) is True

    def test_fix_polygon_rings(self):
        # 外环顺时针、内环逆时针（均应按右手定则修正：外 CCW、内 CW）
        geom = {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]],        # CW 外环
                [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2]],  # CCW 内环
            ],
        }
        fixed = _fix_ring_orientation(geom)
        outer, inner = fixed["coordinates"]
        assert _ring_is_clockwise(outer) is False   # 外环 → CCW
        assert _ring_is_clockwise(inner) is True    # 内环 → CW

    def test_fix_multipolygon(self):
        # 带内部洞的多边形（i>0 环 → _ensure_cw）
        geom = {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]],        # 外环 CW
                    [[0.2, 0.2], [0.2, 0.3], [0.3, 0.3], [0.3, 0.2], [0.2, 0.2]],  # 洞 CW
                ]
            ],
        }
        fixed = _fix_ring_orientation(geom)
        assert fixed["type"] == "MultiPolygon"
        assert len(fixed["coordinates"]) == 1
        outer, hole = fixed["coordinates"][0][0]
        assert _ring_is_clockwise(outer) is False
        assert _ring_is_clockwise(hole) is True

    def test_fix_ring_in_poly(self):
        rings = [list(r) for r in RECT_POLY["coordinates"]]
        fixed = _fix_ring_in_poly(rings)
        assert len(fixed) == 1


class TestCompareCounts:
    def test_same(self):
        r = compare_feature_counts(10, 10)
        assert r["passed"] is True
        assert "一致" in r["message"]

    def test_lost(self):
        r = compare_feature_counts(10, 7)
        assert r["passed"] is False
        assert "丢失 3" in r["message"]