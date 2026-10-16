"""数据标准 v2 — 数据表管理 + S9 挂载/摘挂。"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.core.deps import authenticated
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group_table_link import StandardLayerGroupTableLink

from app.schemas.data_standard_v2 import (
    TableCreate, TableUpdate, TableBrief, TableDetail,
    TableAttachRequest, TableLayerLink,
)

from . import _common as C

router = APIRouter()


@router.get(
    "/layers/{layer_id}/tables",
    response_model=list[TableBrief],
    summary="层组下表列表",
)
async def list_tables(
    layer_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        raise HTTPException(404, "层组不存在")

    # 子查询计算字段数
    field_count_subq = (
        select(func.count()).select_from(StandardField)
        .where(StandardField.table_id == StandardTable.id)
        .correlate(StandardTable)
        .scalar_subquery()
    )

    # S9 表多分类：经关联表取本层组下所有表（含次归属），并携带 is_primary
    q = select(StandardTable, StandardLayerGroupTableLink.is_primary, field_count_subq.label("fc")).join(
        StandardLayerGroupTableLink,
        StandardLayerGroupTableLink.table_id == StandardTable.id,
    ).where(
        StandardLayerGroupTableLink.layer_group_id == layer_id
    ).order_by(
        StandardLayerGroupTableLink.ordering,
        StandardTable.ordering,
    )

    result = await db.execute(q)
    rows = result.all()

    return [
        TableBrief(
            id=t.id, layer_id=t.layer_id, table_name=t.table_name,
            table_code=t.table_code, table_type=t.table_type,
            geometry_type=t.geometry_type, description=t.description,
            ordering=t.ordering, field_count=fc or 0, created_at=t.created_at,
            is_primary=is_primary,
        )
        for t, is_primary, fc in rows
    ]


@router.post(
    "/layers/{layer_id}/tables",
    response_model=TableDetail,
    status_code=201,
    summary="创建数据表",
)
async def create_table(
    layer_id: str,
    body: TableCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        raise HTTPException(404, "层组不存在")

    table = StandardTable(
        id=str(_uuid.uuid4()),
        layer_id=layer_id,
        standard_id=layer.standard_id,
        table_name=body.table_name,
        table_code=body.table_code,
        table_type=body.table_type,
        geometry_type=body.geometry_type,
        description=body.description,
        ordering=body.ordering,
    )
    db.add(table)
    # S9 表多分类：创建表即挂主归属
    db.add(C._primary_table_link(table))

    # 创建字段
    for fd in body.fields or []:
        field = StandardField(
            id=str(_uuid.uuid4()),
            table_id=table.id,
            standard_id=layer.standard_id,
            field_name=fd.field_name,
            field_alias=fd.field_alias,
            data_type=fd.data_type,
            length=fd.length,
            precision=fd.precision,
            nullable=fd.nullable,
            is_primary_key=fd.is_primary_key,
            is_required=fd.is_required,
            default_value=fd.default_value,
            description=fd.description,
            ordering=fd.ordering,
        )
        db.add(field)

    await db.flush()

    # 重新加载
    q = select(StandardTable).where(StandardTable.id == table.id).options(
        selectinload(StandardTable.fields).selectinload(StandardField.dict_links)
    )
    return (await db.execute(q)).scalar_one()


@router.get(
    "/tables/{table_id}",
    response_model=TableDetail,
    summary="数据表详情",
)
async def get_table(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = await db.get(StandardTable, table_id)
    if not table:
        raise HTTPException(404, "数据表不存在")

    q = select(StandardTable).where(StandardTable.id == table_id).options(
        selectinload(StandardTable.fields).selectinload(StandardField.dict_links)
            .selectinload(StandardFieldDictLink.dictionary)
    )
    result = await db.execute(q)
    return result.scalar_one()


@router.post(
    "/layers/{layer_id}/tables/{table_id}/attach",
    response_model=TableLayerLink,
    status_code=201,
    summary="挂载数据表到层组（次归属/主归属转移）",
)
async def attach_table(
    layer_id: str,
    table_id: str,
    body: TableAttachRequest,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    layer = await C._get_layer_ctx(db, api_key, layer_id)
    table = await C._get_table_ctx(db, api_key, table_id)

    if layer.standard_id != table.standard_id:
        raise HTTPException(400, "层组与表不属于同一标准，不能挂载")

    # 已存在关联
    existing = (await db.execute(
        select(StandardLayerGroupTableLink).where(
            StandardLayerGroupTableLink.layer_group_id == layer_id,
            StandardLayerGroupTableLink.table_id == table_id,
        )
    )).scalars().first()

    if body.is_primary:
        # 主归属转移：旧主 link 降级，standard_tables.layer_id 同步改指（唯一真源）
        old_primary = (await db.execute(
            select(StandardLayerGroupTableLink).where(
                StandardLayerGroupTableLink.table_id == table_id,
                StandardLayerGroupTableLink.is_primary.is_(True),
            )
        )).scalars().first()

        if existing:
            if existing.is_primary:
                raise HTTPException(409, "该表已以主归属挂载到此层组")
            # 次归属原位升级为主归属
            if old_primary:
                old_primary.is_primary = False
            table.layer_id = layer_id
            existing.is_primary = True
            if body.ordering is not None:
                existing.ordering = body.ordering
            await db.flush()
            return C._to_table_layer_link(layer, True, existing.ordering)

        if old_primary:
            old_primary.is_primary = False
        table.layer_id = layer_id

        ordering = body.ordering if body.ordering is not None else 0
        link = StandardLayerGroupTableLink(
            id=str(_uuid.uuid4()),
            layer_group_id=layer_id,
            table_id=table_id,
            is_primary=True,
            ordering=ordering,
        )
        db.add(link)
        await db.flush()
        return C._to_table_layer_link(layer, True, ordering)

    # 次归属挂载
    if existing:
        raise HTTPException(409, "该表已挂载到此层组")

    ordering = body.ordering if body.ordering is not None else 0
    link = StandardLayerGroupTableLink(
        id=str(_uuid.uuid4()),
        layer_group_id=layer_id,
        table_id=table_id,
        is_primary=False,
        ordering=ordering,
    )
    db.add(link)
    await db.flush()

    return C._to_table_layer_link(layer, False, ordering)


@router.delete(
    "/layers/{layer_id}/tables/{table_id}/attach",
    status_code=204,
    summary="摘挂数据表（移除次归属）",
)
async def detach_table(
    layer_id: str,
    table_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    await C._get_layer_ctx(db, api_key, layer_id)
    await C._get_table_ctx(db, api_key, table_id)

    link = (await db.execute(
        select(StandardLayerGroupTableLink).where(
            StandardLayerGroupTableLink.layer_group_id == layer_id,
            StandardLayerGroupTableLink.table_id == table_id,
        )
    )).scalars().first()
    if not link:
        raise HTTPException(404, "该表未挂载到此层组")

    if link.is_primary:
        raise HTTPException(400, "主归属层组不能摘挂（请先将主归属转移到其他层组，或删除整表）")

    await db.delete(link)
    await db.flush()


@router.get(
    "/tables/{table_id}/layers",
    response_model=list[TableLayerLink],
    summary="数据表归属列表（主归属 + 次归属）",
)
async def list_table_layers(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    await C._get_table_ctx(db, api_key, table_id)

    q = select(StandardLayerGroupTableLink, StandardLayerGroup).join(
        StandardLayerGroup,
        StandardLayerGroup.id == StandardLayerGroupTableLink.layer_group_id,
    ).where(
        StandardLayerGroupTableLink.table_id == table_id
    ).order_by(
        StandardLayerGroupTableLink.is_primary.desc(),
        StandardLayerGroupTableLink.ordering,
    )
    rows = (await db.execute(q)).all()
    return [
        C._to_table_layer_link(layer, link.is_primary, link.ordering)
        for link, layer in rows
    ]


@router.put(
    "/tables/{table_id}",
    response_model=TableDetail,
    summary="更新数据表",
)
async def update_table(
    table_id: str,
    body: TableUpdate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = await db.get(StandardTable, table_id)
    if not table:
        raise HTTPException(404, "数据表不存在")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(table, field, value)

    await db.flush()

    q = select(StandardTable).where(StandardTable.id == table_id).options(
        selectinload(StandardTable.fields).selectinload(StandardField.dict_links)
    )
    return (await db.execute(q)).scalar_one()


@router.delete(
    "/tables/{table_id}",
    status_code=204,
    summary="删除数据表",
)
async def delete_table(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = await db.get(StandardTable, table_id)
    if not table:
        raise HTTPException(404, "数据表不存在")

    await db.delete(table)
    await db.flush()
