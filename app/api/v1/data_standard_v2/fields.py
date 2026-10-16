"""数据标准 v2 — 字段管理。"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.core.deps import authenticated
from app.models.api_key import ApiKey
from app.models.standard_table import StandardTable
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink

from app.schemas.data_standard_v2 import (
    FieldCreate, FieldUpdate, FieldResponse, FieldReorder,
)

from . import _common as C

router = APIRouter()


@router.get(
    "/tables/{table_id}/fields",
    response_model=list[FieldResponse],
    summary="表字段列表",
)
async def list_fields(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(StandardField).where(
        StandardField.table_id == table_id
    ).options(
        selectinload(StandardField.dict_links).selectinload(StandardFieldDictLink.dictionary)
    ).order_by(StandardField.ordering)

    result = await db.execute(q)
    return result.scalars().all()


@router.post(
    "/tables/{table_id}/fields",
    response_model=FieldResponse,
    status_code=201,
    summary="添加字段",
)
async def create_field(
    table_id: str,
    body: FieldCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = await db.get(StandardTable, table_id)
    if not table:
        raise HTTPException(404, "数据表不存在")

    field = StandardField(
        id=str(_uuid.uuid4()),
        table_id=table_id,
        standard_id=table.standard_id,
        field_name=body.field_name,
        field_alias=body.field_alias,
        data_type=body.data_type,
        length=body.length,
        precision=body.precision,
        nullable=body.nullable,
        is_primary_key=body.is_primary_key,
        is_required=body.is_required,
        default_value=body.default_value,
        description=body.description,
        ordering=body.ordering,
    )
    db.add(field)
    await db.flush()

    return await C._reload_field(db, field.id)


@router.put(
    "/fields/{field_id}",
    response_model=FieldResponse,
    summary="更新字段",
)
async def update_field(
    field_id: str,
    body: FieldUpdate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    field = await db.get(StandardField, field_id)
    if not field:
        raise HTTPException(404, "字段不存在")

    for f, value in body.model_dump(exclude_unset=True).items():
        setattr(field, f, value)

    await db.flush()
    return await C._reload_field(db, field.id)


@router.delete(
    "/fields/{field_id}",
    status_code=204,
    summary="删除字段",
)
async def delete_field(
    field_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    field = await db.get(StandardField, field_id)
    if not field:
        raise HTTPException(404, "字段不存在")

    await db.delete(field)
    await db.flush()


@router.post(
    "/tables/{table_id}/fields/reorder",
    status_code=204,
    summary="字段排序",
)
async def reorder_fields(
    table_id: str,
    body: FieldReorder,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    for idx, field_id in enumerate(body.field_ids):
        field = await db.get(StandardField, field_id)
        if field and field.table_id == table_id:
            field.ordering = idx

    await db.flush()
