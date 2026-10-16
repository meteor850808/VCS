"""属性结构检查引擎 — 从 v2.2 标准自动生成规则，向量化执行。

9 项检查:
  A1 必要图层检查       31010001
  A2 要素类型正确性     31010002
  A3 属性结构正确性     31010003
  A4 字段必填项         31010004
  A5 属性值域控制       31010005
  A6 字段唯一值         31010006
  A7 必要图层不为空     31010007
  A8 字段值小数位       31010008
  A9 字段长度限制       31010009
"""

import logging
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from app.models.standard_field import StandardField
from app.models.standard_dictionary import StandardDictionary
from app.models.dictionary_item import DictionaryItem
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.spatial_feature import (
    PointFeature, LineFeature, PolygonFeature,
    GEOMETRY_TABLE_MAP,
)

logger = logging.getLogger(__name__)

# 几何类型映射：标准类型 → 实际 geometry_type 值
GEOMETRY_TYPE_MAP = {
    "Point": {"Point", "MultiPoint"},
    "LineString": {"LineString", "MultiLineString"},
    "Polygon": {"Polygon", "MultiPolygon"},
}

MODEL_MAP = {
    "Point": PointFeature,
    "LineString": LineFeature,
    "Polygon": PolygonFeature,
}


# ── 数据结构 ─────────────────────────────────────────────────────────────────

@dataclass
class StandardTableDef:
    """标准表定义（加载后的内部表示）。"""
    table_name: str
    table_code: str | None
    geometry_type: str | None  # Point/LineString/Polygon/None
    fields: list["StandardFieldDef"] = field(default_factory=list)
    table_id: str | None = None   # 【S4】标准表 ID（standard_table_id 过滤用）


@dataclass
class StandardFieldDef:
    """标准字段定义。"""
    field_name: str
    data_type: str          # string/integer/float/date/datetime/boolean
    length: int | None = None
    precision: int | None = None
    nullable: bool = True
    is_required: bool = False
    is_primary_key: bool = False
    dict_item_codes: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    """单条检查结果。"""
    rule_code: str
    rule_name: str
    status: str             # pass / fail / info
    severity: str           # error / warning / info
    field_name: str = ""
    layer: str = ""
    failed_count: int = 0
    total_checked: int = 0
    expected: str = ""
    actual: str = ""
    message: str = ""
    samples: list = field(default_factory=list)


# ── 标准数据加载 ─────────────────────────────────────────────────────────────

async def load_standard_tables(
    db: AsyncSession,
    standard_id: str,
    standard_table_id: str | None = None,
) -> list[StandardTableDef]:
    """从 v2.2 标准加载完整定义（含字典值域）。

    递归遍历 layer_groups → children → tables → fields → dict_links → dictionary → items

    Args:
        standard_table_id: 【S4】指定时仅加载该标准表（对应文件关联的图层），
                           为空则加载标准全部表（原有行为）。
    """
    q = select(DataStandard).where(DataStandard.id == standard_id).options(
        selectinload(DataStandard.layer_groups)
            .selectinload(StandardLayerGroup.children),
        selectinload(DataStandard.layer_groups)
            .selectinload(StandardLayerGroup.tables)
            .selectinload(StandardTable.fields)
            .selectinload(StandardField.dict_links)
            .selectinload(StandardFieldDictLink.dictionary)
            .selectinload(StandardDictionary.items),
    )
    result = await db.execute(q)
    std = result.scalar_one_or_none()
    if not std:
        return []

    tables: list[StandardTableDef] = []

    def _collect(layer: StandardLayerGroup):
        for t in layer.tables:
            if standard_table_id and str(t.id) != str(standard_table_id):
                continue
            fields = []
            for f in t.fields:
                item_codes = []
                for link in (f.dict_links or []):
                    if link.dictionary:
                        item_codes.extend(i.item_code for i in (link.dictionary.items or []))
                fields.append(StandardFieldDef(
                    field_name=f.field_name,
                    data_type=f.data_type,
                    length=f.length,
                    precision=f.precision,
                    nullable=f.nullable,
                    is_required=f.is_required,
                    is_primary_key=f.is_primary_key,
                    dict_item_codes=item_codes,
                ))
            tables.append(StandardTableDef(
                table_name=t.table_name,
                table_code=t.table_code,
                geometry_type=t.geometry_type,
                fields=fields,
                table_id=str(t.id),
            ))
        for child in (layer.children or []):
            _collect(child)

    for lg in (std.layer_groups or []):
        if lg.parent_id is not None:
            # 非顶层组由顶层组递归 _collect 覆盖，避免重复收集
            continue
        _collect(lg)

    return tables


# ── 特征数据加载 ─────────────────────────────────────────────────────────────

async def load_features_by_file(
    db: AsyncSession,
    stored_file_id: str,
    api_key_id: str,
) -> list[dict]:
    """从 PostGIS 三表加载已入库的特征数据。"""
    features = []
    for model in (PointFeature, LineFeature, PolygonFeature):
        q = select(
            model.feature_index,
            model.layer_name,
            model.source_name,
            model.geometry_type,
            model.properties,
        ).where(
            model.stored_file_id == stored_file_id,
        ).order_by(model.feature_index)
        rows = (await db.execute(q)).all()
        for r in rows:
            features.append({
                "feature_index": r.feature_index,
                "layer_name": r.layer_name,
                "source_name": r.source_name,
                "geometry_type": r.geometry_type,
                "properties": r.properties or {},
            })
    return features


# ── 属性检查引擎 ─────────────────────────────────────────────────────────────

async def run_attribute_checks(
    db: AsyncSession,
    standard_id: str,
    stored_file_id: str,
    api_key_id: str,
    standard_table_id: str | None = None,
) -> tuple[list[CheckResult], dict]:
    """
    Phase 1: 属性结构检查（向量化）。

    Args:
        standard_table_id: 【S4】指定后仅检查该标准表（文件关联图层），
                           为空检查标准全部表（原有行为）。

    Returns:
        (check_results, summary)
        summary = {total, passed, failed, by_severity: {error: N, warning: N}}
    """
    # 加载标准定义
    std_tables = await load_standard_tables(db, standard_id, standard_table_id)
    if not std_tables:
        return [CheckResult(
            rule_code="31010001", rule_name="必要图层检查",
            status="fail", severity="error",
            message=f"标准 {standard_id} 无任何表定义",
        )], {"total": 1, "passed": 0, "failed": 1, "by_severity": {"error": 1}}

    # 加载特征数据
    features = await load_features_by_file(db, stored_file_id, api_key_id)
    if not features:
        return [CheckResult(
            rule_code="31010007", rule_name="必要图层不为空",
            status="fail", severity="error",
            message="已入库文件无任何要素",
        )], {"total": 1, "passed": 0, "failed": 1, "by_severity": {"error": 1}}

    # 按 layer_name 分组
    layer_map: dict[str, list[dict]] = {}
    for f in features:
        layer_map.setdefault(f["layer_name"], []).append(f)

    # 收集所有标准表名（用于 A1）
    expected_table_names = {t.table_name for t in std_tables}
    actual_layer_names = set(layer_map.keys())

    # 【S4】单表模式：文件已关联标准表（standard_table_id），文件本身就是该图层的载体，
    # 源数据 layer_name（临时文件名）与标准表名属于不同命名空间，不再比较图层名；
    # 仅按该表定义做字段级检查。
    single_table_mode = standard_table_id is not None

    results: list[CheckResult] = []

    # ── A1: 必要图层检查 ────────────────────────────────────────────────
    if single_table_mode:
        # 单表模式：跳过缺失图层判断（文件即该表承载），要素为空已由上方守卫拦截
        results.append(CheckResult(
            rule_code="31010001", rule_name="必要图层检查",
            status="pass", severity="error",
            message=f"文件已关联标准表 '{next(iter(expected_table_names), '')}'，图层检查通过",
        ))
    else:
        missing_layers = expected_table_names - actual_layer_names
        if missing_layers:
            results.append(CheckResult(
                rule_code="31010001", rule_name="必要图层检查",
                status="fail", severity="error",
                expected=f"需要: {sorted(expected_table_names)}",
                actual=f"实际: {sorted(actual_layer_names)}",
                message=f"缺失必要图层: {sorted(missing_layers)}",
            ))
        else:
            results.append(CheckResult(
                rule_code="31010001", rule_name="必要图层检查",
                status="pass", severity="error",
                message=f"所有 {len(expected_table_names)} 个必要图层均存在",
            ))

    # 逐标准表检查
    for std_table in std_tables:
        tname = std_table.table_name
        # 【S4】单表模式下使用文件全部要素；多表模式按 layer_name 匹配
        layer_feats = features if single_table_mode else layer_map.get(tname, [])

        # ── A7: 必要图层不为空 ──────────────────────────────────────────
        if not layer_feats:
            results.append(CheckResult(
                rule_code="31010007", rule_name="必要图层不为空",
                status="fail", severity="error", layer=tname,
                message=f"图层 '{tname}' 无要素",
            ))
            continue

        # 构建 DataFrame
        df = pd.DataFrame([f["properties"] for f in layer_feats])

        # ── A2: 要素类型正确性 ──────────────────────────────────────────
        if std_table.geometry_type:
            expected_types = GEOMETRY_TYPE_MAP.get(std_table.geometry_type, {std_table.geometry_type})
            actual_geom_types = {f["geometry_type"] for f in layer_feats if f["geometry_type"]}
            wrong_geom = actual_geom_types - expected_types
            if wrong_geom:
                results.append(CheckResult(
                    rule_code="31010002", rule_name="要素类型正确性",
                    status="fail", severity="error", layer=tname,
                    expected=std_table.geometry_type,
                    actual=str(sorted(wrong_geom)),
                    message=f"图层 '{tname}' 要素类型不匹配: 期望 {std_table.geometry_type}, 实际 {wrong_geom}",
                ))
            else:
                results.append(CheckResult(
                    rule_code="31010002", rule_name="要素类型正确性",
                    status="pass", severity="error", layer=tname,
                    message=f"图层 '{tname}' 要素类型正确",
                ))

        # 收集所有标准字段名
        std_field_names = {f.field_name for f in std_table.fields}
        actual_field_names = set(df.columns)

        # ── A3: 属性结构正确性 ──────────────────────────────────────────
        missing_fields = std_field_names - actual_field_names
        extra_fields = actual_field_names - std_field_names
        type_mismatches = []

        if missing_fields or extra_fields:
            parts = []
            if missing_fields:
                parts.append(f"缺失字段: {sorted(missing_fields)}")
            if extra_fields:
                parts.append(f"多余字段: {sorted(extra_fields)}")
            results.append(CheckResult(
                rule_code="31010003", rule_name="属性结构正确性",
                status="fail", severity="warning", layer=tname,
                expected=f"标准字段: {sorted(std_field_names)}",
                actual=f"实际字段: {sorted(actual_field_names)}",
                message=f"图层 '{tname}' {'; '.join(parts)}",
            ))
        else:
            results.append(CheckResult(
                rule_code="31010003", rule_name="属性结构正确性",
                status="pass", severity="warning", layer=tname,
                message=f"图层 '{tname}' 属性结构完整",
            ))

        # 逐字段检查
        for field_def in std_table.fields:
            fname = field_def.field_name
            if fname not in df.columns:
                continue  # A3 已报告

            col = df[fname]

            # ── A4: 字段必填项 ──────────────────────────────────────
            if field_def.is_required:
                # 【S5 修复】pandas 2.x 字符串列 dtype 为 str（非 object），
                # 不能依赖 dtype==object 判断，统一用 isna + 空串掩码统计缺失
                empty_mask = col.isna() | (col.astype(str).str.strip() == "")
                null_count = int(empty_mask.sum())
                if null_count > 0:
                    # df 行序与 layer_feats 一致，取原始 feature_index 作为样本
                    sample_pos = df[empty_mask].index.tolist()[:5]
                    samples = [layer_feats[i]["feature_index"] for i in sample_pos]
                    results.append(CheckResult(
                        rule_code="31010004", rule_name="字段必填项",
                        status="fail", severity="error", layer=tname, field_name=fname,
                        failed_count=null_count,
                        total_checked=len(df),
                        expected="必填",
                        actual=f"{null_count} 条空值",
                        message=f"图层 '{tname}' 必填字段 '{fname}' 有 {null_count} 条空值",
                        samples=samples,
                    ))
                else:
                    results.append(CheckResult(
                        rule_code="31010004", rule_name="字段必填项",
                        status="pass", severity="error", layer=tname, field_name=fname,
                        total_checked=len(df),
                        message=f"图层 '{tname}' 字段 '{fname}' 必填项检查通过",
                    ))

            # ── A5: 属性值域控制 ────────────────────────────────────
            if field_def.dict_item_codes:
                allowed = set(field_def.dict_item_codes)
                valid_mask = col.notna() & (col.astype(str).str.strip() != "")
                invalid_mask = valid_mask & ~col.astype(str).isin(allowed)
                invalid_count = int(invalid_mask.sum())
                if invalid_count > 0:
                    samples = df[invalid_mask][fname].head(5).tolist()
                    results.append(CheckResult(
                        rule_code="31010005", rule_name="属性值域控制",
                        status="fail", severity="warning", layer=tname, field_name=fname,
                        failed_count=invalid_count,
                        total_checked=int(valid_mask.sum()),
                        expected=f"值域({len(allowed)}项): {sorted(allowed)[:10]}...",
                        actual=f"违规值: {samples}",
                        message=f"图层 '{tname}' 字段 '{fname}' 有 {invalid_count} 条值不在允许值域内",
                        samples=samples,
                    ))
                else:
                    results.append(CheckResult(
                        rule_code="31010005", rule_name="属性值域控制",
                        status="pass", severity="warning", layer=tname, field_name=fname,
                        total_checked=int(valid_mask.sum()),
                        message=f"图层 '{tname}' 字段 '{fname}' 值域检查通过",
                    ))

            # ── A6: 字段唯一值 ─────────────────────────────────────
            if field_def.is_primary_key:
                dup_mask = col.duplicated(keep=False) & col.notna()
                dup_count = int(dup_mask.sum())
                if dup_count > 0:
                    dup_values = df[dup_mask][fname].head(5).tolist()
                    results.append(CheckResult(
                        rule_code="31010006", rule_name="字段唯一值",
                        status="fail", severity="error", layer=tname, field_name=fname,
                        failed_count=dup_count,
                        total_checked=len(df),
                        expected="唯一",
                        actual=f"{dup_count} 条重复",
                        message=f"图层 '{tname}' 主键字段 '{fname}' 有 {dup_count} 条重复值",
                        samples=dup_values,
                    ))
                else:
                    results.append(CheckResult(
                        rule_code="31010006", rule_name="字段唯一值",
                        status="pass", severity="error", layer=tname, field_name=fname,
                        total_checked=len(df),
                        message=f"图层 '{tname}' 字段 '{fname}' 唯一性检查通过",
                    ))

            # ── A8: 字段值小数位 ───────────────────────────────────
            if field_def.precision is not None and col.notna().any():
                str_vals = col.dropna().astype(str)
                has_dot = str_vals.str.contains(".", regex=False)
                if has_dot.any():
                    decimal_places = str_vals[has_dot].str.split(".", expand=True)[1].str.len()
                    over = int((decimal_places > field_def.precision).sum())
                    if over > 0:
                        samples = df[col.notna()].head(5)[fname].tolist()
                        results.append(CheckResult(
                            rule_code="31010008", rule_name="字段值小数位",
                            status="fail", severity="warning", layer=tname, field_name=fname,
                            failed_count=over,
                            total_checked=int(has_dot.sum()),
                            expected=f"最多 {field_def.precision} 位小数",
                            actual=f"{over} 条超过",
                            message=f"图层 '{tname}' 字段 '{fname}' 有 {over} 条超过 {field_def.precision} 位小数",
                            samples=samples,
                        ))
                    else:
                        results.append(CheckResult(
                            rule_code="31010008", rule_name="字段值小数位",
                            status="pass", severity="warning", layer=tname, field_name=fname,
                            total_checked=int(has_dot.sum()),
                            message=f"图层 '{tname}' 字段 '{fname}' 小数位检查通过",
                        ))

            # ── A9: 字段长度限制 ───────────────────────────────────
            if field_def.length is not None and col.notna().any():
                lengths = col.dropna().astype(str).str.len()
                over = int((lengths > field_def.length).sum())
                if over > 0:
                    samples = df[col.notna()].head(5)[fname].tolist()
                    results.append(CheckResult(
                        rule_code="31010009", rule_name="字段长度限制",
                        status="fail", severity="warning", layer=tname, field_name=fname,
                        failed_count=over,
                        total_checked=len(lengths),
                        expected=f"最多 {field_def.length} 字符",
                        actual=f"{over} 条超过",
                        message=f"图层 '{tname}' 字段 '{fname}' 有 {over} 条超过 {field_def.length} 字符",
                        samples=samples,
                    ))
                else:
                    results.append(CheckResult(
                        rule_code="31010009", rule_name="字段长度限制",
                        status="pass", severity="warning", layer=tname, field_name=fname,
                        total_checked=len(lengths),
                        message=f"图层 '{tname}' 字段 '{fname}' 长度检查通过",
                    ))

    # ── 汇总 ──────────────────────────────────────────────────────────
    summary = _summarize(results)
    return results, summary


def _summarize(results: list[CheckResult]) -> dict:
    """汇总检查结果。"""
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    by_severity = {"error": 0, "warning": 0, "info": 0}
    for r in results:
        if r.status == "fail":
            by_severity[r.severity] = by_severity.get(r.severity, 0) + 1
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "by_severity": by_severity,
    }
