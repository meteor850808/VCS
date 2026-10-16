"""数据标准 v2 — 知识图谱纯视图（A 档）。

不引入图数据库：本仓模型本已是一张图（standard → 层组(自引用) → 表 →
字段，字段 N:M 字典，表 N:M 层组），ORM 一次深载即可出全图。
为什么不上 Neo4j 见 docs/adr/ADR-0003：层级深度硬上限 5（实测参照物
深度 2）、双写面横跨 20+ 写入口且 update/upsert 走全删重建、且与
v2.22.3 确立的「PG 是唯一真源」方向冲突。

载荷策略（真实参照物驱动）：SHGT_SURVEY 有 255 表 / 3288 字段 / 2031
条目，一次全给约 1.5MB —— 摘要端点只出节点徽章 + 聚合边，明细懒加载。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard
from app.models.dictionary_item import DictionaryItem
from app.models.standard_dictionary import StandardDictionary
from app.models.standard_field import StandardField
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable

from app.schemas.data_standard_v2 import (
    StandardGraphResponse, GraphNode, GraphEdge, GraphNodeDetailResponse,
)

from . import _common as C

router = APIRouter()


async def _owned_standard(
    db: AsyncSession, standard_id: str, api_key: ApiKey
) -> DataStandard:
    """取标准并做归属校验 —— 不存在与不属于自己都回 404（不泄露存在性）。"""
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")
    return std


def _referenced_dict_ids_subq(standard_id: str):
    """本标准字段绑定引用到的字典 id 子查询（含全局字典）。"""
    return (
        select(StandardFieldDictLink.dict_id)
        .join(StandardField, StandardFieldDictLink.field_id == StandardField.id)
        .where(StandardField.standard_id == standard_id)
    )


@router.get(
    "/data-standards-v2/{standard_id}/graph",
    response_model=StandardGraphResponse,
    summary="标准知识图谱（纯视图摘要）",
)
async def get_standard_graph(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await _owned_standard(db, standard_id, api_key)

    # ── 层组树（含 tables → fields 与 S9 table_links 深载）────────────
    groups = (await db.execute(
        select(StandardLayerGroup)
        .where(StandardLayerGroup.standard_id == standard_id)
        .options(*C._layer_tree_options())
    )).scalars().all()

    # ── 绑定明细（聚合到 表↔字典 级）─────────────────────────────────
    links = (await db.execute(
        select(StandardFieldDictLink.field_id,
               StandardFieldDictLink.dict_id,
               StandardFieldDictLink.is_primary,
               StandardField.table_id)
        .join(StandardField, StandardFieldDictLink.field_id == StandardField.id)
        .where(StandardField.standard_id == standard_id)
    )).all()
    # (table_id, dict_id) → {field_count, primary}
    binds: dict[tuple[str, str], dict] = {}
    linked_field_ids: set[str] = set()
    referenced_dict_ids: set[str] = set()
    for field_id, dict_id, is_primary, table_id in links:
        linked_field_ids.add(field_id)
        referenced_dict_ids.add(dict_id)
        slot = binds.setdefault((table_id, dict_id),
                                {"field_count": 0, "primary": False})
        slot["field_count"] += 1
        slot["primary"] = slot["primary"] or bool(is_primary)

    # ── 字典：本标准的 + 被本标准字段引用的全局字典 ──────────────────
    # 可见性规则与 qc-summary 一致：global + standard_id IS NULL。
    # 未被引用的全局字典不进图 —— 否则任何标准都拖着一堆无关节点。
    dicts = (await db.execute(
        select(StandardDictionary)
        .where(
            (StandardDictionary.standard_id == standard_id)
            | (StandardDictionary.id.in_(referenced_dict_ids)
               & (StandardDictionary.scope == "global")
               & StandardDictionary.standard_id.is_(None))
        )
        .options(selectinload(StandardDictionary.items))
    )).scalars().all()
    dict_ids = {d.id for d in dicts}

    # ── 组装 nodes / edges ────────────────────────────────────────────
    nodes: list[GraphNode] = [
        GraphNode(id=str(std.id), kind="standard", name=std.name, code=std.code)
    ]
    edges: list[GraphEdge] = []

    table_count = 0
    field_count = 0
    all_tables: list = []

    def walk(group: StandardLayerGroup, depth: int):
        nonlocal table_count, field_count
        nodes.append(GraphNode(
            id=str(group.id), kind="group", name=group.group_name,
            code=group.group_code, parent_id=group.parent_id and str(group.parent_id),
            depth=depth,
        ))
        edges.append(GraphEdge(
            kind="contains",
            source=str(group.parent_id) if group.parent_id else str(std.id),
            target=str(group.id),
        ))
        # 主归属（layer_id 经 tables 关系到达）
        for t in group.tables:
            table_count += 1
            field_count += len(t.fields or [])
            all_tables.append(t)
            nodes.append(GraphNode(
                id=str(t.id), kind="table", name=t.table_name, code=t.table_code,
                field_count=len(t.fields or []),
            ))
            edges.append(GraphEdge(
                kind="contains", source=str(group.id), target=str(t.id),
                primary=True,
            ))
        # S9 次归属（table_links 里 is_primary=False 的行）
        for lk in (group.table_links or []):
            if not lk.is_primary:
                edges.append(GraphEdge(
                    kind="contains", source=str(group.id),
                    target=str(lk.table_id), primary=False,
                ))
        for child in (group.children or []):
            walk(child, depth + 1)

    for g in groups:
        if g.parent_id is None:
            walk(g, 1)

    for d in dicts:
        nodes.append(GraphNode(
            id=str(d.id), kind="dict", name=d.dict_name, code=d.dict_code,
            item_count=len(d.items or []), scope=d.scope,
        ))

    for (table_id, dict_id), info in binds.items():
        if dict_id in dict_ids:  # 引用的字典不可见时跳过（防悬挂边）
            edges.append(GraphEdge(
                kind="binds", source=str(table_id), target=str(dict_id),
                field_count=info["field_count"], primary=info["primary"],
            ))

    return StandardGraphResponse(
        standard_id=str(std.id),
        standard_name=std.name,
        nodes=nodes,
        edges=edges,
        stats={
            "group_count": sum(1 for n in nodes if n.kind == "group"),
            "table_count": table_count,
            "field_count": field_count,
            "dict_count": len(dicts),
            "linked_field_count": len(linked_field_ids),
            "binds_edge_count": sum(1 for e in edges if e.kind == "binds"),
        },
    )


@router.get(
    "/data-standards-v2/{standard_id}/graph/nodes/{node_id}",
    response_model=GraphNodeDetailResponse,
    summary="图谱节点展开明细（表→字段 / 字典→条目）",
)
async def get_graph_node_detail(
    standard_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await _owned_standard(db, standard_id, api_key)

    # ── 表节点 → 字段列表 ────────────────────────────────────────────
    table = (await db.execute(
        select(StandardTable).where(
            StandardTable.id == node_id,
            StandardTable.standard_id == standard_id,
        ).options(selectinload(StandardTable.fields))
    )).scalar_one_or_none()
    if table:
        fields = sorted(table.fields or [], key=lambda f: (f.ordering is None, f.ordering or 0))
        field_ids = [f.id for f in fields]
        # dict_codes 保序：主绑定在前（与 v2.22.3 模板契约一致）
        code_map: dict[str, list[str]] = {}
        if field_ids:
            rows = (await db.execute(
                select(StandardFieldDictLink.field_id,
                       StandardDictionary.dict_code)
                .join(StandardDictionary,
                      StandardFieldDictLink.dict_id == StandardDictionary.id)
                .where(StandardFieldDictLink.field_id.in_(field_ids))
                .order_by(StandardFieldDictLink.is_primary.desc(),
                          StandardFieldDictLink.ordering)
            )).all()
            for fid, dcode in rows:
                if dcode:
                    code_map.setdefault(fid, []).append(dcode)
        return GraphNodeDetailResponse(
            node_id=str(table.id), kind="table", name=table.table_name,
            fields=[{
                "id": str(f.id),
                "field_name": f.field_name,
                "field_alias": f.field_alias,
                "data_type": f.data_type,
                "length": f.length,
                "is_required": f.is_required,
                "is_primary_key": f.is_primary_key,
                "dict_codes": code_map.get(f.id, []),
                "ordering": f.ordering,
            } for f in fields],
        )

    # ── 字典节点 → 条目列表 ──────────────────────────────────────────
    # 本标准的字典，或被本标准字段引用到的全局字典（与摘要口径一致）。
    ref_ids = _referenced_dict_ids_subq(standard_id)
    d = (await db.execute(
        select(StandardDictionary).where(
            StandardDictionary.id == node_id,
            ((StandardDictionary.standard_id == standard_id)
             | (StandardDictionary.id.in_(ref_ids)
                & (StandardDictionary.scope == "global")
                & StandardDictionary.standard_id.is_(None))),
        ).options(selectinload(StandardDictionary.items))
    )).scalar_one_or_none()
    if d:
        items = sorted(d.items or [],
                       key=lambda i: (i.ordering is None, i.ordering or 0))
        return GraphNodeDetailResponse(
            node_id=str(d.id), kind="dict", name=d.dict_name,
            items=[{
                "item_code": i.item_code,
                "item_name": i.item_name,
                "parent_code": i.parent_code,
                "description": i.description,
                "ordering": i.ordering,
            } for i in items],
        )

    # 层组节点不支持展开（层级在摘要里已完整表达），统一 404
    raise HTTPException(404, "节点不存在或不属于该标准的图谱")
