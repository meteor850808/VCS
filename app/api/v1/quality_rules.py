"""质检规则管理 API — 查询/创建/更新/删除质检规则。"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import authenticated
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.quality_rule import QualityRule
from app.services.quality.rule_generator import generate_rules_from_field, generate_rules_for_standard
from app.models.standard_field import StandardField

router = APIRouter()


class RuleCreate(BaseModel):
    standard_field_id: str | None = None
    rule_type: str
    rule_name: str
    field_pattern: str = ""
    severity: str = "warning"
    params: dict | None = None
    enabled: bool = True


class RuleUpdate(BaseModel):
    rule_name: str | None = None
    severity: str | None = None
    params: dict | None = None
    enabled: bool | None = None


class RuleResponse(BaseModel):
    id: str
    standard_field_id: str | None
    rule_type: str
    rule_name: str
    field_pattern: str
    severity: str
    params: dict | None
    enabled: bool
    is_auto: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("/quality-rules", response_model=list[RuleResponse], summary="查询质检规则列表")
async def list_rules(
    rule_type: Optional[str] = Query(None, description="按规则类型过滤"),
    enabled_only: bool = Query(False, description="仅返回已启用的规则"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(QualityRule).where(api_key.scope_clause(QualityRule))
    if rule_type:
        q = q.where(QualityRule.rule_type == rule_type)
    if enabled_only:
        q = q.where(QualityRule.enabled == True)
    q = q.order_by(QualityRule.rule_type, QualityRule.field_pattern)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/quality-rules", response_model=RuleResponse, status_code=201, summary="创建质检规则")
async def create_rule(
    body: RuleCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    rule = QualityRule(
        id=str(uuid.uuid4()),
        api_key_id=api_key.id,
        standard_field_id=body.standard_field_id,
        rule_type=body.rule_type,
        rule_name=body.rule_name,
        field_pattern=body.field_pattern or body.rule_name,
        severity=body.severity,
        params=body.params,
        enabled=body.enabled,
        is_auto=False,
    )
    db.add(rule)
    await db.commit()
    return rule


@router.put("/quality-rules/{rule_id}", response_model=RuleResponse, summary="更新质检规则")
async def update_rule(
    rule_id: str,
    body: RuleUpdate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    rule = await db.get(QualityRule, rule_id)
    if not rule or not api_key.owns(rule):
        raise HTTPException(status_code=404, detail="规则不存在")
    if body.rule_name is not None:
        rule.rule_name = body.rule_name
    if body.severity is not None:
        rule.severity = body.severity
    if body.params is not None:
        rule.params = body.params
    if body.enabled is not None:
        rule.enabled = body.enabled
    rule.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return rule


@router.delete("/quality-rules/{rule_id}", status_code=204, summary="删除质检规则")
async def delete_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    rule = await db.get(QualityRule, rule_id)
    if not rule or not api_key.owns(rule):
        raise HTTPException(status_code=404, detail="规则不存在")
    await db.delete(rule)
    await db.commit()


@router.post("/quality-rules/generate/{standard_id}", response_model=list[RuleResponse],
             status_code=201, summary="从数据标准自动生成质检规则")
async def generate_rules(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """从指定数据标准的字段定义自动生成质检规则（幂等：先删除旧规则再创建）。"""
    # 查询该标准下已有的所有字段 ID (v2: StandardField)
    fq = select(StandardField.id).where(StandardField.standard_id == standard_id)
    field_ids = (await db.execute(fq)).scalars().all()

    if field_ids:
        # 删除该标准下的旧自动规则
        delete_q = select(QualityRule).where(
            QualityRule.standard_field_id.in_(field_ids),
            api_key.scope_clause(QualityRule),
        )
        old = (await db.execute(delete_q)).scalars().all()
        for r in old:
            await db.delete(r)

    # 重新生成
    rules = await generate_rules_for_standard(standard_id, api_key.id, db)
    for r in rules:
        db.add(r)
    await db.commit()

    # 返回新创建的规则
    list_q = select(QualityRule).where(
        QualityRule.standard_field_id.in_(field_ids),
        api_key.scope_clause(QualityRule),
    )
    result = await db.execute(list_q)
    return result.scalars().all()
