"""源数据管理 API — 源数据表、字段、字典 CRUD。"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from app.core.deps import authenticated
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.source_data import (
    SourceTable, SourceField,
    FieldDictLink,
)
from app.models.standard_dictionary import StandardDictionary
from app.models.dictionary_item import DictionaryItem
from app.schemas.source_data import (
    SourceTableResponse, SourceTableBrief, SourceTableCreate, SourceTableUpdate,
    SourceFieldResponse, SourceFieldCreate,
    SourceDictionaryResponse, SourceDictionaryBrief, SourceDictionaryCreate,
    DictionaryItemResponse, DictionaryItemCreate,
)

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# 源数据表 API
# ═══════════════════════════════════════════════════════════════

@router.get("/source-tables", response_model=list[SourceTableBrief], summary="源数据表列表")
async def list_source_tables(
    search: Optional[str] = Query(None, description="按表名/编码搜索"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    # 子查询计算字段数，避免 N+1 和 async greenlet 问题
    field_count_subq = (
        select(func.count()).select_from(SourceField)
        .where(SourceField.table_id == SourceTable.id)
        .correlate(SourceTable)
        .scalar_subquery()
    )
    q = select(SourceTable, field_count_subq.label("fc")).where(
        api_key.scope_clause(SourceTable)
    )
    if search:
        like = f"%{search}%"
        q = q.where(
            SourceTable.table_name.ilike(like) | SourceTable.table_code.ilike(like)
        )
    q = q.order_by(SourceTable.ordering, SourceTable.table_name)
    result = await db.execute(q)
    rows = result.all()
    return [
        SourceTableBrief(
            id=t.id, table_name=t.table_name, table_code=t.table_code,
            geometry_type=t.geometry_type, source=t.source,
            field_count=fc or 0,
            is_active=t.is_active, created_at=t.created_at,
        )
        for t, fc in rows
    ]


@router.get("/source-tables/{table_id}", response_model=SourceTableResponse, summary="源数据表详情")
async def get_source_table(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(SourceTable).where(
        SourceTable.id == table_id, api_key.scope_clause(SourceTable)
    ).options(
        selectinload(SourceTable.fields).selectinload(SourceField.dict_links)
    )
    result = await db.execute(q)
    table = result.scalar_one_or_none()
    if not table:
        raise HTTPException(status_code=404, detail="源数据表不存在")
    return table


@router.post("/source-tables", response_model=SourceTableResponse, status_code=201, summary="创建源数据表")
async def create_source_table(
    body: SourceTableCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = SourceTable(
        id=str(uuid.uuid4()), api_key_id=api_key.id,
        table_name=body.table_name, table_code=body.table_code,
        description=body.description, geometry_type=body.geometry_type,
        source=body.source, version=body.version,
        is_active=body.is_active, ordering=body.ordering,
    )
    db.add(table)
    for i, fd in enumerate(body.fields or []):
        field = SourceField(
            id=str(uuid.uuid4()), table_id=table.id,
            field_name=fd.field_name, field_alias=fd.field_alias,
            field_type=fd.field_type, data_type=fd.data_type,
            length=fd.length, precision=fd.precision,
            nullable=fd.nullable, default_value=fd.default_value,
            description=fd.description,
            is_primary_key=fd.is_primary_key, is_required=fd.is_required,
            allowed_values=fd.allowed_values, ordering=fd.ordering or i,
        )
        db.add(field)
    await db.commit()
    # 重新加载含 fields
    q = select(SourceTable).where(SourceTable.id == table.id).options(
        selectinload(SourceTable.fields).selectinload(SourceField.dict_links)
    )
    return (await db.execute(q)).scalar_one()


@router.put("/source-tables/{table_id}", response_model=SourceTableResponse, summary="更新源数据表")
async def update_source_table(
    table_id: str, body: SourceTableUpdate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = await db.get(SourceTable, table_id)
    if not table or not api_key.owns(table):
        raise HTTPException(status_code=404, detail="源数据表不存在")
    if body.table_name is not None: table.table_name = body.table_name
    if body.table_code is not None: table.table_code = body.table_code
    if body.description is not None: table.description = body.description
    if body.geometry_type is not None: table.geometry_type = body.geometry_type
    if body.source is not None: table.source = body.source
    if body.version is not None: table.version = body.version
    if body.is_active is not None: table.is_active = body.is_active
    if body.ordering is not None: table.ordering = body.ordering
    await db.commit()
    q = select(SourceTable).where(SourceTable.id == table.id).options(
        selectinload(SourceTable.fields).selectinload(SourceField.dict_links)
    )
    return (await db.execute(q)).scalar_one()


@router.delete("/source-tables/{table_id}", status_code=204, summary="删除源数据表")
async def delete_source_table(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = await db.get(SourceTable, table_id)
    if not table or not api_key.owns(table):
        raise HTTPException(status_code=404, detail="源数据表不存在")
    await db.delete(table)
    await db.commit()


@router.post("/source-tables/{table_id}/fields", response_model=SourceFieldResponse,
             status_code=201, summary="添加字段")
async def add_source_field(
    table_id: str, body: SourceFieldCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    table = await db.get(SourceTable, table_id)
    if not table or not api_key.owns(table):
        raise HTTPException(status_code=404, detail="源数据表不存在")
    field = SourceField(
        id=str(uuid.uuid4()), table_id=table_id,
        field_name=body.field_name, field_alias=body.field_alias,
        field_type=body.field_type, data_type=body.data_type,
        length=body.length, precision=body.precision,
        nullable=body.nullable, default_value=body.default_value,
        description=body.description,
        is_primary_key=body.is_primary_key, is_required=body.is_required,
        allowed_values=body.allowed_values, ordering=body.ordering,
    )
    db.add(field)
    await db.commit()
    q = select(SourceField).where(SourceField.id == field.id).options(
        selectinload(SourceField.dict_links)
    )
    return (await db.execute(q)).scalar_one()


# ═══════════════════════════════════════════════════════════════
# 源数据字典 API
# ═══════════════════════════════════════════════════════════════

@router.get("/source-dictionaries", response_model=list[SourceDictionaryBrief], summary="字典列表")
async def list_dictionaries(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(StandardDictionary).where(
        (api_key.scope_clause(StandardDictionary)) | (StandardDictionary.is_system == True)
    )
    if search:
        q = q.where(StandardDictionary.dict_name.ilike(f"%{search}%"))
    q = q.order_by(StandardDictionary.dict_name)
    # 子查询计算条目数（避免 async greenlet 问题）
    item_subq = (
        select(func.count()).select_from(DictionaryItem)
        .where(DictionaryItem.dict_id == StandardDictionary.id)
        .correlate(StandardDictionary)
        .scalar_subquery()
    )
    q = q.add_columns(item_subq.label("ic"))
    result = await db.execute(q)
    rows = result.all()
    return [
        SourceDictionaryBrief(
            id=d.id, dict_name=d.dict_name, dict_code=d.dict_code,
            description=d.description, item_count=ic or 0,
            is_system=d.is_system, created_at=d.created_at,
        )
        for d, ic in rows
    ]


@router.get("/source-dictionaries/{dict_id}", response_model=SourceDictionaryResponse, summary="字典详情")
async def get_dictionary(
    dict_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    q = select(StandardDictionary).where(StandardDictionary.id == dict_id).options(
        selectinload(StandardDictionary.items)
    )
    result = await db.execute(q)
    d = result.scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="字典不存在")
    if not api_key.owns(d) and not d.is_system:
        raise HTTPException(status_code=403, detail="无权限")
    return d


@router.post("/source-dictionaries", response_model=SourceDictionaryResponse,
             status_code=201, summary="创建字典")
async def create_dictionary(
    body: SourceDictionaryCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    d = StandardDictionary(
        id=str(uuid.uuid4()), api_key_id=api_key.id,
        dict_name=body.dict_name, dict_code=body.dict_code,
        description=body.description,
    )
    db.add(d)
    for i, item in enumerate(body.items or []):
        di = DictionaryItem(
            id=str(uuid.uuid4()), dict_id=d.id,
            item_code=item.item_code, item_name=item.item_name,
            parent_code=item.parent_code, description=item.description,
            ordering=item.ordering or i,
        )
        db.add(di)
    await db.commit()
    q = select(StandardDictionary).where(StandardDictionary.id == d.id).options(
        selectinload(StandardDictionary.items)
    )
    return (await db.execute(q)).scalar_one()


@router.post("/source-dictionaries/{dict_id}/items", response_model=list[DictionaryItemResponse],
             status_code=201, summary="批量添加字典条目")
async def add_dict_items(
    dict_id: str, items: list[DictionaryItemCreate],
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    d = await db.get(StandardDictionary, dict_id)
    if not d:
        raise HTTPException(status_code=404, detail="字典不存在")
    if not api_key.owns(d) and not d.is_system:
        raise HTTPException(status_code=403, detail="无权限")
    created = []
    for item in items:
        di = DictionaryItem(
            id=str(uuid.uuid4()), dict_id=dict_id,
            item_code=item.item_code, item_name=item.item_name,
            parent_code=item.parent_code, description=item.description,
            ordering=item.ordering,
        )
        db.add(di)
        created.append(di)
    await db.commit()
    return created


@router.delete("/source-dictionaries/{dict_id}", status_code=204, summary="删除字典")
async def delete_dictionary(
    dict_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    d = await db.get(StandardDictionary, dict_id)
    if not d or not api_key.owns(d):
        raise HTTPException(status_code=404, detail="字典不存在")
    await db.delete(d)
    await db.commit()


# ═══════════════════════════════════════════════════════════════
# 字段-字典关联 API
# ═══════════════════════════════════════════════════════════════

@router.post("/field-dict-links", status_code=201, summary="关联字段与字典")
async def link_field_dict(
    field_id: str = Query(...),
    dict_id: str = Query(...),
    filter_condition: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    field = await db.get(SourceField, field_id)
    if not field:
        raise HTTPException(status_code=404, detail="字段不存在")
    link = FieldDictLink(
        id=str(uuid.uuid4()), field_id=field_id, dict_id=dict_id,
        filter_condition=filter_condition,
    )
    db.add(link)
    await db.commit()
    return {"message": "关联成功"}


@router.delete("/field-dict-links/{link_id}", status_code=204, summary="解除字段字典关联")
async def unlink_field_dict(
    link_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    link = await db.get(FieldDictLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="关联不存在")
    await db.delete(link)
    await db.commit()
