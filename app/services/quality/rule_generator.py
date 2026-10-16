"""质检规则生成器 — 从数据标准字段定义自动生成质检规则。

v2.10.1: 从旧 v1 模型 (DataStandardField) 迁移到 v2 模型 (StandardField)。

流程:
  StandardField  →  RuleType 判定  →  QualityRule 对象

规则类型映射:
  - nullable=False / is_required=True  →  NOT_NULL
  - data_type                          →  TYPE_MATCH
  - length                             →  LENGTH_LIMIT
  - is_primary_key                     →  UNIQUE
  - dict_links + dictionary items      →  VALUE_IN_SET
"""

import uuid
from datetime import datetime, timezone

from app.models.standard_field import StandardField
from app.models.quality_rule import QualityRule


def generate_rules_from_field(
    field: StandardField,
    api_key_id: str,
    mark_auto: bool = True,
) -> list[QualityRule]:
    """从单个 StandardField (v2) 生成一组 QualityRule。

    Args:
        field: 标准字段定义 (v2)。
        api_key_id: 所属用户 ID。
        mark_auto: 标记为自动生成。

    Returns:
        生成的 QualityRule 列表（可能 0~5 条）。
    """
    rules: list[QualityRule] = []
    label = field.field_alias or field.field_name

    # ── NOT_NULL: 不可空字段 ─────────────────────────────────
    if not field.nullable or field.is_required:
        rules.append(QualityRule(
            id=str(uuid.uuid4()),
            api_key_id=api_key_id,
            standard_field_id=field.id,
            rule_type="NOT_NULL",
            rule_name=f"字段 {label} 不可为空",
            field_pattern=field.field_name,
            severity="critical",
            params=None,
            enabled=True,
            is_auto=mark_auto,
        ))

    # ── TYPE_MATCH: 字段类型匹配 ─────────────────────────────
    if field.data_type:
        rules.append(QualityRule(
            id=str(uuid.uuid4()),
            api_key_id=api_key_id,
            standard_field_id=field.id,
            rule_type="TYPE_MATCH",
            rule_name=f"字段 {label} 应为 {field.data_type} 类型",
            field_pattern=field.field_name,
            severity="warning",
            params={"expected_type": field.data_type},
            enabled=True,
            is_auto=mark_auto,
        ))

    # ── LENGTH_LIMIT: 最大长度 ───────────────────────────────
    if field.length and field.length > 0:
        rules.append(QualityRule(
            id=str(uuid.uuid4()),
            api_key_id=api_key_id,
            standard_field_id=field.id,
            rule_type="LENGTH_LIMIT",
            rule_name=f"字段 {label} 长度不超过 {field.length}",
            field_pattern=field.field_name,
            severity="warning",
            params={"max_length": field.length},
            enabled=True,
            is_auto=mark_auto,
        ))

    # ── UNIQUE: 主键字段 ────────────────────────────────────
    if field.is_primary_key:
        rules.append(QualityRule(
            id=str(uuid.uuid4()),
            api_key_id=api_key_id,
            standard_field_id=field.id,
            rule_type="UNIQUE",
            rule_name=f"字段 {label} 应唯一（主键）",
            field_pattern=field.field_name,
            severity="error",
            params=None,
            enabled=True,
            is_auto=mark_auto,
        ))

    # ── VALUE_IN_SET: 字典关联的值域控制 ──────────────────────
    # 通过 dict_links 关联的字典条目作为允许值列表
    try:
        for link in (field.dict_links or []):
            if link.dictionary and link.dictionary.items:
                allowed = [item.item_code for item in link.dictionary.items if item.item_code]
                if allowed:
                    rules.append(QualityRule(
                        id=str(uuid.uuid4()),
                        api_key_id=api_key_id,
                        standard_field_id=field.id,
                        rule_type="VALUE_IN_SET",
                        rule_name=f"字段 {label} 值必须在字典 {link.dictionary.dict_name} 中",
                        field_pattern=field.field_name,
                        severity="warning",
                        params={"allowed_values": allowed},
                        enabled=True,
                        is_auto=mark_auto,
                    ))
    except Exception:
        # 字典加载失败不影响其他规则生成
        pass

    return rules


async def generate_rules_for_standard(
    standard_id: str,
    api_key_id: str,
    db_session,
) -> list[QualityRule]:
    """为整个数据标准生成质检规则（覆盖所有表的所有字段）。

    Args:
        standard_id: DataStandard.id。
        api_key_id: 所属用户 ID。
        db_session: SQLAlchemy async session（用于查询字段）。

    Returns:
        新生成的 QualityRule 列表。

    v2.10.1: 使用 v2 模型 StandardField + StandardTable。
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    stmt = (
        select(StandardField)
        .where(StandardField.standard_id == standard_id)
        .options(selectinload(StandardField.dict_links))
    )
    result = await db_session.execute(stmt)
    fields = result.scalars().all()

    all_rules: list[QualityRule] = []
    for field in fields:
        all_rules.extend(generate_rules_from_field(field, api_key_id))

    return all_rules
