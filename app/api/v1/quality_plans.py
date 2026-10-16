"""质检方案 CRUD API。

端点:
  GET    /quality-plans           查询方案列表
  GET    /quality-plans/{plan_id} 查询方案详情
  POST   /quality-plans           创建方案
  PUT    /quality-plans/{plan_id} 更新方案
  DELETE /quality-plans/{plan_id} 删除方案
  POST   /quality-plans/default   初始化默认方案（幂等）
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import authenticated
from app.db.session import get_db
from app.models.quality_plan import QualityPlan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quality-plans", tags=["quality-plans"])


# ── Schema ──
class PlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="方案名称")
    description: str | None = None
    attr_enabled: bool = True
    topo_config: dict | None = None
    standard_id: str | None = None
    extra: dict | None = None


class PlanUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    attr_enabled: bool | None = None
    topo_config: dict | None = None
    standard_id: str | None = None
    extra: dict | None = None


# ── 列表 ──
@router.get("")
async def list_plans(
    db: AsyncSession = Depends(get_db),
    _key=Depends(authenticated),
):
    result = await db.execute(
        select(QualityPlan).order_by(QualityPlan.name)
    )
    plans = result.scalars().all()
    return {"items": [p.to_dict() for p in plans], "total": len(plans)}


# ── 详情 ──
@router.get("/{plan_id}")
async def get_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    _key=Depends(authenticated),
):
    plan = await db.get(QualityPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    return plan.to_dict()


# ── 创建 ──
@router.post("", status_code=201)
async def create_plan(
    body: PlanCreate,
    db: AsyncSession = Depends(get_db),
    _key=Depends(authenticated),
):
    # 重名检查
    exists = await db.execute(
        select(QualityPlan).where(QualityPlan.name == body.name)
    )
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="方案名称已存在")
    plan = QualityPlan(
        name=body.name,
        description=body.description,
        attr_enabled=body.attr_enabled,
        topo_config=body.topo_config,
        standard_id=body.standard_id,
        extra=body.extra,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    logger.info("质检方案已创建: %s (%s)", plan.name, plan.id)
    return plan.to_dict()


# ── 更新 ──
@router.put("/{plan_id}")
async def update_plan(
    plan_id: str,
    body: PlanUpdate,
    db: AsyncSession = Depends(get_db),
    _key=Depends(authenticated),
):
    plan = await db.get(QualityPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="无更新字段")
    # 名称重复检查
    if "name" in data and data["name"] != plan.name:
        exists = await db.execute(
            select(QualityPlan).where(QualityPlan.name == data["name"])
        )
        if exists.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="方案名称已存在")
    for k, v in data.items():
        setattr(plan, k, v)
    await db.commit()
    await db.refresh(plan)
    return plan.to_dict()


# ── 删除 ──
@router.delete("/{plan_id}")
async def delete_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    _key=Depends(authenticated),
):
    plan = await db.get(QualityPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="方案不存在")
    await db.delete(plan)
    await db.commit()
    return {"ok": True, "deleted": plan.name}


# ── 默认方案初始化（幂等） ──
@router.post("/default")
async def init_default_plan(
    db: AsyncSession = Depends(get_db),
    _key=Depends(authenticated),
):
    exists = await db.execute(
        select(QualityPlan).where(QualityPlan.name == "默认")
    )
    if exists.scalar_one_or_none():
        return {"ok": True, "msg": "默认方案已存在"}
    plan = QualityPlan(
        name="默认",
        description="系统默认质检方案",
        attr_enabled=True,
        topo_config={
            "enabled_rules": [
                "no_self_intersect","no_dangles","boundary_match",
                "no_overlap_polygon","no_gap_polygon","no_self_cross",
                "min_area_check","min_length_check","containment",
                "ring_closed","unique_id","order_valid",
            ],
        },
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return {"ok": True, "plan": plan.to_dict()}
