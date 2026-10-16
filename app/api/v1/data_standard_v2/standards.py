"""数据标准 v2 — 标准 CRUD + 统计。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.core.deps import authenticated
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from app.models.standard_field import StandardField
from app.models.standard_dictionary import StandardDictionary
from app.models.dictionary_item import DictionaryItem

from app.schemas.data_standard_v2 import (
    DataStandardCreate, DataStandardUpdate, DataStandardBrief, DataStandardDetail,
    PaginatedResponse, DataStandardV2Stats,
)

from app.api.v1.data_standard_v2._common import _raise_conflict, _extract_constraint_name

router = APIRouter()


@router.get(
    "/data-standards-v2",
    response_model=PaginatedResponse,
    summary="数据标准列表",
)
async def list_standards(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    category: Optional[str] = Query(None, description="分类筛选"),
    status: Optional[str] = Query(None, description="状态筛选"),
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(DataStandard).where(api_key.scope_clause(DataStandard))

    if category:
        q = q.where(DataStandard.category == category)
    if status:
        q = q.where(DataStandard.status == status)
    if keyword:
        q = q.where(DataStandard.name.ilike(f"%{keyword}%"))

    # 总数
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # 分页
    q = q.order_by(DataStandard.created_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    items = result.scalars().all()

    return PaginatedResponse(
        items=[DataStandardBrief.model_validate(s) for s in items],
        total=total, page=page, page_size=page_size,
    )


@router.get(
    "/data-standards-v2/stats",
    response_model=DataStandardV2Stats,
    summary="数据标准统计（任务看板汇总）",
)
async def standard_v2_stats(
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """一次请求返回数据标准 v2 的汇总统计（scope 隔离）。"""
    std_ids = select(DataStandard.id).where(api_key.scope_clause(DataStandard))

    total = (await db.execute(
        select(func.count()).select_from(DataStandard).where(api_key.scope_clause(DataStandard))
    )).scalar() or 0

    async def _dist(col):
        q = (
            select(col, func.count())
            .where(api_key.scope_clause(DataStandard))
            .group_by(col)
        )
        rows = (await db.execute(q)).all()
        return {r[0]: r[1] for r in rows if r[0] is not None}

    by_category = await _dist(DataStandard.category)
    by_status = await _dist(DataStandard.status)

    async def _count(model, col):
        return (await db.execute(
            select(func.count()).select_from(model).where(col.in_(std_ids))
        )).scalar() or 0

    layers_total = await _count(StandardLayerGroup, StandardLayerGroup.standard_id)
    tables_total = await _count(StandardTable, StandardTable.standard_id)
    fields_total = await _count(StandardField, StandardField.standard_id)

    global_q = and_(
        StandardDictionary.scope == "global",
        StandardDictionary.standard_id.is_(None),
    )
    global_dicts = (await db.execute(
        select(func.count()).select_from(StandardDictionary).where(global_q)
    )).scalar() or 0
    standard_dicts = (await db.execute(
        select(func.count()).select_from(StandardDictionary)
        .where(StandardDictionary.standard_id.in_(std_ids))
    )).scalar() or 0

    dict_ids = select(StandardDictionary.id).where(or_(global_q, StandardDictionary.standard_id.in_(std_ids)))
    dict_items_total = (await db.execute(
        select(func.count()).select_from(DictionaryItem).where(DictionaryItem.dict_id.in_(dict_ids))
    )).scalar() or 0

    from app.models.source_data import StandardVersion
    versions_total = (await db.execute(
        select(func.count()).select_from(StandardVersion)
        .where(StandardVersion.standard_id.in_(std_ids))
    )).scalar() or 0

    return DataStandardV2Stats(
        standards_total=total,
        by_category=by_category,
        by_status=by_status,
        layers_total=layers_total,
        tables_total=tables_total,
        fields_total=fields_total,
        global_dicts=global_dicts,
        standard_dicts=standard_dicts,
        dict_items_total=dict_items_total,
        versions_total=versions_total,
    )


@router.post(
    "/data-standards-v2",
    response_model=DataStandardBrief,
    status_code=201,
    summary="创建数据标准",
)
async def create_standard(
    body: DataStandardCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    import uuid as _uuid
    std = DataStandard(
        id=str(_uuid.uuid4()),
        api_key_id=api_key.id,
        code=body.code,
        name=body.name,
        description=body.description,
        category=body.category,
    )
    db.add(std)
    try:
        await db.flush()
    except IntegrityError as e:
        # 按实际撞的约束说话：编码冲突不能说「名称已存在」，否则用户
        # 改名字永远改不通（v2.22.3 前的已知债）。回显值与约束对齐 ——
        # 名称冲突回显 name，编码冲突回显 code。
        await db.rollback()
        shown = body.code if _extract_constraint_name(e) == "uq_ds_api_code" else body.name
        raise _raise_conflict(e, shown)

    return DataStandardBrief.model_validate(std)


@router.get(
    "/data-standards-v2/{standard_id}",
    response_model=DataStandardDetail,
    summary="数据标准详情",
)
async def get_standard(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    q = select(DataStandard).where(DataStandard.id == standard_id).options(
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.children),
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.tables)
            .selectinload(StandardTable.fields),
        selectinload(DataStandard.standard_dictionaries).selectinload(StandardDictionary.items),
    )
    result = await db.execute(q)
    std = result.scalar_one()

    return DataStandardDetail.model_validate(std)


@router.put(
    "/data-standards-v2/{standard_id}",
    response_model=DataStandardBrief,
    summary="更新数据标准",
)
async def update_standard(
    standard_id: str,
    body: DataStandardUpdate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(std, field, value)

    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise _raise_conflict(e, body.name)
    return DataStandardBrief.model_validate(std)


@router.delete(
    "/data-standards-v2/{standard_id}",
    status_code=204,
    summary="删除数据标准",
)
async def delete_standard(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    await db.delete(std)
    await db.flush()
