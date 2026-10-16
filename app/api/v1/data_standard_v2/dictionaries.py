"""数据标准 v2 — 数据字典管理。"""

import logging
import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.core.deps import authenticated
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_dictionary import StandardDictionary
from app.models.dictionary_item import DictionaryItem

from app.schemas.data_standard_v2 import (
    DictionaryCreate, DictionaryUpdate, DictionaryBrief, DictionaryDetail,
    DictionaryItemCreate, DictionaryItemUpdate, DictionaryItemResponse,
)

router = APIRouter()
logger = logging.getLogger("vcs")


@router.get(
    "/data-standards-v2/{standard_id}/dictionaries",
    response_model=list[DictionaryBrief],
    summary="标准下字典列表",
)
async def list_dictionaries(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(StandardDictionary).where(
        StandardDictionary.standard_id == standard_id,
        StandardDictionary.scope == "standard",
    )

    result = await db.execute(q)
    dicts = result.scalars().all()

    return [
        DictionaryBrief(
            id=d.id, standard_id=d.standard_id, dict_name=d.dict_name,
            dict_code=d.dict_code, description=d.description,
            scope=d.scope, is_system=d.is_system,
            item_count=len(d.items), created_at=d.created_at,
        )
        for d in dicts
    ]


@router.post(
    "/data-standards-v2/{standard_id}/dictionaries",
    response_model=DictionaryDetail,
    status_code=201,
    summary="创建字典",
)
async def create_dictionary(
    standard_id: str,
    body: DictionaryCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    if body.scope not in (None, "standard"):
        raise HTTPException(
            400, "标准级字典 scope 必须为 standard；创建全局字典请调用 POST /api/v1/dictionaries"
        )

    # 预查重
    dup = await db.execute(
        select(StandardDictionary).where(
            StandardDictionary.standard_id == standard_id,
            StandardDictionary.dict_name == body.dict_name,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"字典名称「{body.dict_name}」在当前标准下已存在，请换一个名称",
        )

    dict_obj = StandardDictionary(
        id=str(_uuid.uuid4()),
        standard_id=standard_id,
        api_key_id=api_key.owner_id,
        dict_name=body.dict_name,
        dict_code=body.dict_code,
        description=body.description,
        scope="standard",
    )
    db.add(dict_obj)

    for item in body.items or []:
        dict_item = DictionaryItem(
            id=str(_uuid.uuid4()),
            dict_id=dict_obj.id,
            item_code=item.item_code,
            item_name=item.item_name,
            parent_code=item.parent_code,
            description=item.description,
            ordering=item.ordering,
        )
        db.add(dict_item)

    try:
        await db.flush()
    except IntegrityError as e:  # pragma: no cover - 预查重已拦截；竞态兜底需跨事务并发（测试单事件循环不可稳定复现）
        logger.warning(
            "创建字典失败 standard_id=%s dict_name=%s: %s",
            standard_id, body.dict_name, e.orig,
        )
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"字典名称「{body.dict_name}」在当前标准下已存在，请换一个名称",
        )

    q = select(StandardDictionary).where(StandardDictionary.id == dict_obj.id).options(
        selectinload(StandardDictionary.items)
    )
    return (await db.execute(q)).scalar_one()


@router.get(
    "/dictionaries",
    response_model=list[DictionaryBrief],
    summary="全局字典列表",
)
async def list_global_dictionaries(
    keyword: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(StandardDictionary).where(
        StandardDictionary.scope == "global",
        StandardDictionary.standard_id.is_(None),
    )
    if keyword:
        q = q.where(StandardDictionary.dict_name.ilike(f"%{keyword}%"))

    result = await db.execute(q)
    dicts = result.scalars().all()

    return [
        DictionaryBrief(
            id=d.id, standard_id=d.standard_id, dict_name=d.dict_name,
            dict_code=d.dict_code, description=d.description,
            scope=d.scope, is_system=d.is_system,
            item_count=len(d.items), created_at=d.created_at,
        )
        for d in dicts
    ]


@router.post(
    "/dictionaries",
    response_model=DictionaryDetail,
    status_code=201,
    summary="创建全局字典",
)
async def create_global_dictionary(
    body: DictionaryCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """创建全局共享字典（scope='global'，standard_id 恒为 NULL）。"""
    if body.scope not in (None, "global"):
        raise HTTPException(400, "全局字典 scope 必须为 global")

    dup = await db.execute(
        select(StandardDictionary).where(
            StandardDictionary.standard_id.is_(None),
            StandardDictionary.dict_name == body.dict_name,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(409, f"全局字典名称「{body.dict_name}」已存在，请换一个名称")

    dict_obj = StandardDictionary(
        id=str(_uuid.uuid4()),
        standard_id=None,
        api_key_id=api_key.owner_id,
        dict_name=body.dict_name,
        dict_code=body.dict_code,
        description=body.description,
        scope="global",
    )
    db.add(dict_obj)

    for item in body.items or []:
        dict_item = DictionaryItem(
            id=str(_uuid.uuid4()),
            dict_id=dict_obj.id,
            item_code=item.item_code,
            item_name=item.item_name,
            parent_code=item.parent_code,
            description=item.description,
            ordering=item.ordering,
        )
        db.add(dict_item)

    try:
        await db.flush()
    except IntegrityError as e:  # pragma: no cover - PG 对 standard_id IS NULL 不启用唯一约束，代码级预查重已是唯一防线
        logger.warning(
            "创建全局字典失败 dict_name=%s: %s", body.dict_name, e.orig,
        )
        await db.rollback()
        raise HTTPException(409, f"全局字典名称「{body.dict_name}」已存在，请换一个名称")

    q = select(StandardDictionary).where(StandardDictionary.id == dict_obj.id).options(
        selectinload(StandardDictionary.items)
    )
    return (await db.execute(q)).scalar_one()


@router.get(
    "/dictionaries/{dict_id}",
    response_model=DictionaryDetail,
    summary="字典详情",
)
async def get_dictionary(
    dict_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(StandardDictionary).where(StandardDictionary.id == dict_id).options(
        selectinload(StandardDictionary.items)
    )
    result = await db.execute(q)
    dict_obj = result.scalar_one_or_none()

    if not dict_obj:
        raise HTTPException(404, "字典不存在")

    return dict_obj


@router.put(
    "/dictionaries/{dict_id}",
    response_model=DictionaryDetail,
    summary="更新字典",
)
async def update_dictionary(
    dict_id: str,
    body: DictionaryUpdate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    dict_obj = await db.get(StandardDictionary, dict_id)
    if not dict_obj:
        raise HTTPException(404, "字典不存在")

    data = body.model_dump(exclude_unset=True)
    if "scope" in data:
        if data["scope"] == "global":
            dict_obj.standard_id = None
        elif data["scope"] == "standard" and dict_obj.standard_id is None:
            raise HTTPException(
                400, "标准级字典必须先归属某个数据标准，无法从全局字典直接转换"
            )
    for field, value in data.items():
        setattr(dict_obj, field, value)

    await db.flush()

    q = select(StandardDictionary).where(StandardDictionary.id == dict_id).options(
        selectinload(StandardDictionary.items)
    )
    return (await db.execute(q)).scalar_one()


@router.delete(
    "/dictionaries/{dict_id}",
    status_code=204,
    summary="删除字典",
)
async def delete_dictionary(
    dict_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    dict_obj = await db.get(StandardDictionary, dict_id)
    if not dict_obj:
        raise HTTPException(404, "字典不存在")

    if dict_obj.is_system:
        raise HTTPException(400, "系统预置字典不可删除")

    await db.delete(dict_obj)
    await db.flush()


@router.post(
    "/dictionaries/{dict_id}/items",
    response_model=list[DictionaryItemResponse],
    status_code=201,
    summary="批量添加字典条目",
)
async def add_dict_items(
    dict_id: str,
    body: list[DictionaryItemCreate],
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    dict_obj = await db.get(StandardDictionary, dict_id)
    if not dict_obj:
        raise HTTPException(404, "字典不存在")

    items = []
    for item in body:
        dict_item = DictionaryItem(
            id=str(_uuid.uuid4()),
            dict_id=dict_id,
            item_code=item.item_code,
            item_name=item.item_name,
            parent_code=item.parent_code,
            description=item.description,
            ordering=item.ordering,
        )
        db.add(dict_item)
        items.append(dict_item)

    await db.flush()
    return items


@router.put(
    "/dictionaries/{dict_id}/items",
    response_model=list[DictionaryItemResponse],
    summary="批量更新字典条目",
)
async def update_dict_items(
    dict_id: str,
    body: list[dict],
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    dict_obj = await db.get(StandardDictionary, dict_id)
    if not dict_obj:
        raise HTTPException(404, "字典不存在")

    items = []
    for item_data in body:
        item_id = item_data.get("id")
        if item_id:
            item = await db.get(DictionaryItem, item_id)
            if item and item.dict_id == dict_id:
                for field, value in item_data.items():
                    if field != "id":
                        setattr(item, field, value)
                items.append(item)

    await db.flush()
    return items


@router.delete(
    "/dictionaries/{dict_id}/items/{item_id}",
    status_code=204,
    summary="删除字典条目",
)
async def delete_dict_item(
    dict_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    item = await db.get(DictionaryItem, item_id)
    if not item or item.dict_id != dict_id:
        raise HTTPException(404, "条目不存在")

    await db.delete(item)
    await db.flush()


@router.post(
    "/dictionaries/{dict_id}/share",
    response_model=DictionaryBrief,
    summary="设为全局共享",
)
async def share_dictionary(
    dict_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    dict_obj = await db.get(StandardDictionary, dict_id)
    if not dict_obj:
        raise HTTPException(404, "字典不存在")

    dict_obj.scope = "global"
    dict_obj.standard_id = None
    await db.flush()

    return dict_obj
