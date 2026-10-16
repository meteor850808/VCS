"""数据标准 v2 — 层组管理（支持嵌套）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable

from app.schemas.data_standard_v2 import (
    LayerGroupCreate, LayerGroupUpdate, LayerGroupMove, LayerGroupResponse,
    TableBrief,
)

from . import _common as C

router = APIRouter()


@router.get(
    "/data-standards-v2/{standard_id}/tables",
    response_model=list[TableBrief],
    summary="标准下所有表（扁平列表，跨层组）",
)
async def list_standard_tables(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """返回标准下所有标准表的扁平列表，用于质检配置下拉。"""
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    q = select(DataStandard).where(DataStandard.id == standard_id).options(
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.tables),
    )
    result = await db.execute(q)
    std = result.scalar_one()

    tables = []
    for lg in (std.layer_groups or []):
        for t in (lg.tables or []):
            tables.append(t)
    return [TableBrief.model_validate(t) for t in tables]


@router.get(
    "/data-standards-v2/{standard_id}/layers",
    response_model=list[LayerGroupResponse],
    summary="层组树",
)
async def list_layers(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    from sqlalchemy import select
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    q = select(StandardLayerGroup).where(
        StandardLayerGroup.standard_id == standard_id,
        StandardLayerGroup.parent_id.is_(None),
    ).options(
        *C._layer_tree_options()
    ).order_by(StandardLayerGroup.ordering)

    result = await db.execute(q)
    return result.scalars().all()


@router.post(
    "/data-standards-v2/{standard_id}/layers",
    response_model=LayerGroupResponse,
    status_code=201,
    summary="创建层组",
)
async def create_layer(
    standard_id: str,
    body: LayerGroupCreate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    import uuid as _uuid
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    # 检查嵌套深度
    if body.parent_id:
        depth = await C._get_layer_depth(db, body.parent_id)
        if depth >= C.MAX_LAYER_DEPTH:
            raise HTTPException(400, f"层组嵌套深度不能超过 {C.MAX_LAYER_DEPTH} 层")

    layer = StandardLayerGroup(
        id=str(_uuid.uuid4()),
        standard_id=standard_id,
        parent_id=body.parent_id,
        group_name=body.group_name,
        group_code=body.group_code,
        description=body.description,
        ordering=body.ordering,
    )
    db.add(layer)
    await db.flush()

    return await C._reload_layer(db, layer.id)


@router.put(
    "/layers/{layer_id}",
    response_model=LayerGroupResponse,
    summary="更新层组",
)
async def update_layer(
    layer_id: str,
    body: LayerGroupUpdate,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        raise HTTPException(404, "层组不存在")

    std = await db.get(DataStandard, layer.standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(403, "无权限")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(layer, field, value)

    await db.flush()
    return await C._reload_layer(db, layer.id)


@router.delete(
    "/layers/{layer_id}",
    status_code=204,
    summary="删除层组",
)
async def delete_layer(
    layer_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        raise HTTPException(404, "层组不存在")

    std = await db.get(DataStandard, layer.standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(403, "无权限")

    # B5 删除守卫：子树内主归属表若被子树外层组共享引用，须先摘挂
    shared = await C._find_shared_primary_tables(db, layer_id)
    if shared:
        names = "、".join(dict.fromkeys(shared))
        raise HTTPException(
            400,
            f"以下表仍被其他层组引用，请先摘挂（移除次归属）再删除层组：{names}",
        )

    await db.delete(layer)
    await db.flush()


@router.post(
    "/layers/{layer_id}/move",
    response_model=LayerGroupResponse,
    summary="移动层组",
)
async def move_layer(
    layer_id: str,
    body: LayerGroupMove,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        raise HTTPException(404, "层组不存在")

    std = await db.get(DataStandard, layer.standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(403, "无权限")

    # 检查不能移动到自身或子节点
    if body.new_parent_id == layer_id:
        raise HTTPException(400, "不能移动到自身")

    if body.new_parent_id:
        new_parent = await db.get(StandardLayerGroup, body.new_parent_id)
        if not new_parent:
            raise HTTPException(404, "目标父层组不存在")
        # B3 同标准校验：层组树保持单标准，禁止跨标准移动
        if new_parent.standard_id != layer.standard_id:
            raise HTTPException(400, "不能跨标准移动层组（如需跨标准请使用复制到其他标准）")
        # B2 防环：沿目标祖先链向上，遇自身即环
        cur = new_parent
        while cur:
            if cur.id == layer_id:
                raise HTTPException(400, "不能移动到自身或子孙节点下")
            cur = await db.get(StandardLayerGroup, cur.parent_id) if cur.parent_id else None
        # 深度校验（含被移动子树高度）
        depth = await C._get_layer_depth(db, new_parent.id)
        subtree_h = await C._layer_subtree_height(db, layer_id)
        if depth + subtree_h > C.MAX_LAYER_DEPTH:
            raise HTTPException(400, f"目标层级深度不能超过 {C.MAX_LAYER_DEPTH} 层")
    else:
        # 移动到根：校验被移动子树自身高度
        subtree_h = await C._layer_subtree_height(db, layer_id)
        if subtree_h > C.MAX_LAYER_DEPTH:
            raise HTTPException(400, f"层组嵌套深度不能超过 {C.MAX_LAYER_DEPTH} 层")

    layer.parent_id = body.new_parent_id
    await db.flush()

    return await C._reload_layer(db, layer.id)


@router.post(
    "/layers/{layer_id}/copy",
    response_model=LayerGroupResponse,
    status_code=201,
    summary="复制层组",
)
async def copy_layer(
    layer_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        raise HTTPException(404, "层组不存在")

    std = await db.get(DataStandard, layer.standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(403, "无权限")

    # S9：跨标准复制（B1）—— new_standard_id 指定目标标准
    target_standard_id = layer.standard_id
    new_standard_id = body.get("new_standard_id")
    if new_standard_id:
        target_std = await db.get(DataStandard, new_standard_id)
        if not target_std or not api_key.owns(target_std):
            raise HTTPException(404, "目标标准不存在")
        target_standard_id = new_standard_id

    new_parent_id = body.get("new_parent_id") or layer.parent_id
    if new_parent_id:
        new_parent = await db.get(StandardLayerGroup, new_parent_id)
        if not new_parent:
            raise HTTPException(404, "目标父层组不存在")
        # 目标父层组必须属于目标标准
        if new_parent.standard_id != target_standard_id:
            raise HTTPException(400, "目标父层组不属于目标标准")
        depth = await C._get_layer_depth(db, new_parent.id)
        subtree_h = await C._layer_subtree_height(db, layer_id)
        if depth + subtree_h > C.MAX_LAYER_DEPTH:
            raise HTTPException(400, f"目标层级深度不能超过 {C.MAX_LAYER_DEPTH} 层")

    new_name = body.get("new_name", f"{layer.group_name} (副本)")
    new_layer = await C._copy_layer_recursive(
        db, layer, new_name, new_parent_id, target_standard_id
    )
    await db.flush()

    return await C._reload_layer(db, new_layer.id)
