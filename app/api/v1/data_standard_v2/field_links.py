"""数据标准 v2 — 字段-字典关联（值域控制）。"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.core.deps import authenticated
from app.models.standard_field import StandardField
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field_dict_link import StandardFieldDictLink

from app.schemas.data_standard_v2 import (
    FieldDictLinkCreate, FieldDictLinkUpdate, FieldDictLinkResponse,
)

router = APIRouter()


@router.get(
    "/fields/{field_id}/dict-links",
    response_model=list[FieldDictLinkResponse],
    summary="查看字段的字典关联",
)
async def list_field_dict_links(
    field_id: str,
    db: AsyncSession = Depends(get_db),
    api_key=None,
):
    q = select(StandardFieldDictLink).where(
        StandardFieldDictLink.field_id == field_id
    ).options(
        selectinload(StandardFieldDictLink.dictionary)
    )

    result = await db.execute(q)
    links = result.scalars().all()

    return [
        FieldDictLinkResponse(
            id=link.id, field_id=link.field_id, dict_id=link.dict_id,
            dict_name=link.dictionary.dict_name if link.dictionary else None,
            filter_condition=link.filter_condition,
            is_primary=link.is_primary, ordering=link.ordering,
            created_at=link.created_at,
        )
        for link in links
    ]


@router.post(
    "/fields/{field_id}/dict-links",
    response_model=FieldDictLinkResponse,
    status_code=201,
    summary="关联字典",
)
async def create_field_dict_link(
    field_id: str,
    body: FieldDictLinkCreate,
    db: AsyncSession = Depends(get_db),
    api_key=None,
):
    field = await db.get(StandardField, field_id)
    if not field:
        raise HTTPException(404, "字段不存在")

    dict_obj = await db.get(StandardDictionary, body.dict_id)
    if not dict_obj:
        raise HTTPException(404, "字典不存在")

    link = StandardFieldDictLink(
        id=str(_uuid.uuid4()),
        field_id=field_id,
        dict_id=body.dict_id,
        filter_condition=body.filter_condition,
        is_primary=body.is_primary,
    )
    db.add(link)
    await db.flush()

    return FieldDictLinkResponse(
        id=link.id, field_id=link.field_id, dict_id=link.dict_id,
        dict_name=dict_obj.dict_name,
        filter_condition=link.filter_condition,
        is_primary=link.is_primary, ordering=link.ordering,
        created_at=link.created_at,
    )


@router.put(
    "/field-dict-links/{link_id}",
    response_model=FieldDictLinkResponse,
    summary="更新关联",
)
async def update_field_dict_link(
    link_id: str,
    body: FieldDictLinkUpdate,
    db: AsyncSession = Depends(get_db),
    api_key=None,
):
    link = await db.get(StandardFieldDictLink, link_id)
    if not link:
        raise HTTPException(404, "关联不存在")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(link, field, value)

    await db.flush()

    q = select(StandardFieldDictLink).where(StandardFieldDictLink.id == link_id).options(
        selectinload(StandardFieldDictLink.dictionary)
    )
    result = await db.execute(q)
    updated_link = result.scalar_one()

    return FieldDictLinkResponse(
        id=updated_link.id, field_id=updated_link.field_id,
        dict_id=updated_link.dict_id,
        dict_name=updated_link.dictionary.dict_name if updated_link.dictionary else None,
        filter_condition=updated_link.filter_condition,
        is_primary=updated_link.is_primary, ordering=updated_link.ordering,
        created_at=updated_link.created_at,
    )


@router.delete(
    "/field-dict-links/{link_id}",
    status_code=204,
    summary="解除关联",
)
async def delete_field_dict_link(
    link_id: str,
    db: AsyncSession = Depends(get_db),
    api_key=None,
):
    # 使用原生 SQL 验证存在性 — 避免 ORM selectin 级联加载导致崩溃
    result = await db.execute(
        sa_text("SELECT id FROM standard_field_dict_links WHERE id = :lid"),
        {"lid": link_id},
    )
    if not result.scalar_one_or_none():
        raise HTTPException(404, "关联不存在")

    # 原生 SQL 删除 — 彻底绕过 ORM identity map 与 cascade 冲突
    await db.execute(
        sa_text("DELETE FROM standard_field_dict_links WHERE id = :lid"),
        {"lid": link_id},
    )
    await db.flush()
