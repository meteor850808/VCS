"""数据标准 v2 路由包 — 共享辅助函数、常量与公共依赖。"""

from __future__ import annotations

import logging
import re
import urllib.parse
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select, func, delete as sa_delete, and_, or_, text as sa_text
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
from app.models.standard_field_dict_link import StandardFieldDictLink
from app.models.standard_layer_group_table_link import StandardLayerGroupTableLink

from app.schemas.data_standard_v2 import (
    LayerGroupBrief, TableLayerLink,
)

MAX_LAYER_DEPTH = 5  # 最大嵌套深度

logger = logging.getLogger("vcs")


# ══════════════════════════════════════════════════════════════════════════════
# 约束冲突 → 用户可读文案
# ══════════════════════════════════════════════════════════════════════════════

# PG 把违反的约束名放在 UniqueViolationError 的文本里（asyncpg 不暴露
# 结构化属性，sqlstate=23505 只能区分「唯一冲突」不能说明是哪一条）。
# 这里按实测的 UniqueConstraint 名称建映射：把「撞了哪个实体、哪个字段」
# 说出来，而不是一律报「标准名称已存在」——后者会把用户引向错误的字段。
_CONSTRAINT_ENTITY: dict[str, str] = {
    "uq_ds_api_name": "标准名称",
    "uq_ds_api_code": "标准编码",
    "uq_slg_std_parent_name": "层组名称",
    "uq_slg_std_parent_code": "层组编码",
    "uq_st_layer_name": "数据表名称",
    "uq_st_layer_code": "数据表编码",
    "uq_sf_table_field": "字段名称",
    "uq_sd_standard_name": "字典名称",
    "uq_di_dict_code": "字典项编码",
    "uq_slgt_layer_table": "层组与数据表的关联",
    "uq_sfdl_field_dict": "字段与字典的绑定",
}

_CONSTRAINT_RE = re.compile(r'唯一约束\s*"([^"]+)"')


def _extract_constraint_name(err: IntegrityError) -> str | None:
    """从 IntegrityError 里取出 PG 唯一约束名；取不到返回 None。

    asyncpg 的错误对象没有 `constraint_name` 属性，约束名只在 `str(e)` 里，
    因此走文本匹配。这不是理想方案，但比谎报冲突原因安全得多 —— 取不到
    时上层会退到通用文案。
    """
    text = str(err.orig) if getattr(err, "orig", None) is not None else str(err)
    m = _CONSTRAINT_RE.search(text)
    return m.group(1) if m else None


def _conflict_detail(constraint: str | None, value: str | None = None) -> str:
    """把约束冲突翻译成指明实体与字段值的文案。

    未知约束**绝不**猜测成「标准名称已存在」——那会让用户去改一个没冲突的
    字段，改到天荒地老也改不通。退回通用文案并回显约束名，便于排查。
    """
    shown = f"「{value}」" if value else ""
    if constraint is None:
        return "存在约束冲突（唯一性/外键），创建失败，请检查是否有重名或编码重复"
    entity = _CONSTRAINT_ENTITY.get(constraint)
    if entity is None:
        return f"存在约束冲突（数据库约束 {constraint}），创建失败"
    return f"{entity}{shown}已存在或重复（数据库约束 {constraint}），创建失败"


def _raise_conflict(err: IntegrityError, value: str | None = None) -> HTTPException:
    """IntegrityError → 精确 409。记日志保留原始 DB 报错，便于排查。"""
    constraint = _extract_constraint_name(err)
    logger.warning("约束冲突 constraint=%s value=%r: %s", constraint, value, err.orig)
    return HTTPException(status_code=409, detail=_conflict_detail(constraint, value))


# ══════════════════════════════════════════════════════════════════════════════
# 基础辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def _primary_table_link(table: StandardTable) -> StandardLayerGroupTableLink:
    """为新建表生成主归属关联行（S9 表多分类：每表唯一主归属）。"""
    return StandardLayerGroupTableLink(
        id=str(uuid.uuid4()),
        layer_group_id=table.layer_id,
        table_id=table.id,
        is_primary=True,
        ordering=table.ordering if table.ordering is not None else 0,
    )


def _layer_tree_options(depth: int = MAX_LAYER_DEPTH):
    """构造层组树深载 options：children 逐层 selectinload 至最大深度（async 下懒加载不可用）。

    tables/table_links 的 selectinload 会对本次查询加载到的所有层组实例生效
    （含嵌套子层组），因此只需声明一次。
    """
    opts = [
        selectinload(StandardLayerGroup.tables).selectinload(StandardTable.fields),
        selectinload(StandardLayerGroup.table_links).selectinload(StandardLayerGroupTableLink.table),
    ]
    child_chain = selectinload(StandardLayerGroup.children)
    for _ in range(depth):
        opts.append(child_chain)
        child_chain = child_chain.selectinload(StandardLayerGroup.children)
    return opts


async def _reload_layer(db: AsyncSession, layer_id: str) -> StandardLayerGroup:
    """重新加载层组（含子层组和表），解决 async 场景下 selectin 未加载的问题。"""
    q = select(StandardLayerGroup).where(StandardLayerGroup.id == layer_id).options(
        *_layer_tree_options()
    )
    result = await db.execute(q)
    return result.scalar_one()


async def _reload_table(db: AsyncSession, table_id: str) -> StandardTable:  # pragma: no cover - 当前无调用方（tables.py 内联查询），保留作公共 helper
    """重新加载数据表（含字段和字段-字典关联）。"""
    q = select(StandardTable).where(StandardTable.id == table_id).options(
        selectinload(StandardTable.fields).selectinload(StandardField.dict_links),
    )
    result = await db.execute(q)
    return result.scalar_one()


async def _reload_field(db: AsyncSession, field_id: str) -> StandardField:
    """重新加载字段（含字典关联）。"""
    q = select(StandardField).where(StandardField.id == field_id).options(
        selectinload(StandardField.dict_links).selectinload(StandardFieldDictLink.dictionary),
    )
    result = await db.execute(q)
    return result.scalar_one()


# ══════════════════════════════════════════════════════════════════════════════
# 上下文校验辅助
# ══════════════════════════════════════════════════════════════════════════════

async def _get_layer_ctx(db: AsyncSession, api_key: ApiKey, layer_id: str) -> StandardLayerGroup:
    """层组存在 + 权限校验，返回层组。"""
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        raise HTTPException(404, "层组不存在")
    std = await db.get(DataStandard, layer.standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(403, "无权限")
    return layer


async def _get_table_ctx(db: AsyncSession, api_key: ApiKey, table_id: str) -> StandardTable:
    """表存在 + 权限校验，返回表。"""
    table = await db.get(StandardTable, table_id)
    if not table:
        raise HTTPException(404, "数据表不存在")
    std = await db.get(DataStandard, table.standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(403, "无权限")
    return table


def _to_table_layer_link(layer: StandardLayerGroup, is_primary: bool, ordering: int | None) -> TableLayerLink:
    return TableLayerLink(
        layer_group=LayerGroupBrief(
            id=layer.id, group_name=layer.group_name, group_code=layer.group_code,
            standard_id=layer.standard_id,
        ),
        is_primary=is_primary,
        ordering=ordering,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 层组深度/子树辅助
# ══════════════════════════════════════════════════════════════════════════════

async def _get_layer_depth(db: AsyncSession, layer_id: str) -> int:
    """获取层组的当前嵌套深度。"""
    layer = await db.get(StandardLayerGroup, layer_id)
    if not layer:
        return 0

    depth = 0
    current = layer
    while current.parent_id:
        depth += 1
        current = await db.get(StandardLayerGroup, current.parent_id)
        if not current:  # pragma: no cover - parent_id 有 FK 约束，悬挂引用不可达
            break
    return depth


async def _layer_subtree_height(db: AsyncSession, layer_id: str) -> int:
    """计算以 layer_id 为根的子树高度（层组数）。"""
    children = (await db.execute(
        select(StandardLayerGroup).where(StandardLayerGroup.parent_id == layer_id)
    )).scalars().all()
    if not children:
        return 1
    heights = [await _layer_subtree_height(db, c.id) for c in children]
    return 1 + max(heights)


async def _collect_subtree_ids(db: AsyncSession, layer_id: str) -> set[str]:
    """收集层组子树的所有 id（含自身）——用显式查询逐层展开，避免 async 下懒加载。"""
    ids: set[str] = {layer_id}
    frontier = [layer_id]
    while frontier:
        kids = (await db.execute(
            select(StandardLayerGroup.id).where(
                StandardLayerGroup.parent_id.in_(frontier)
            )
        )).scalars().all()
        kids = [k for k in kids if k not in ids]
        ids.update(kids)
        frontier = kids
    return ids


async def _find_shared_primary_tables(
    db: AsyncSession, layer_id: str
) -> list[str]:
    """B5 删除守卫：查找子树内主归属表中被子树外层组共享引用的表名。"""
    subtree_ids = await _collect_subtree_ids(db, layer_id)
    subtree_tables = (await db.execute(
        select(StandardTable).where(StandardTable.layer_id.in_(subtree_ids))
    )).scalars().all()

    shared: list[str] = []
    for table in subtree_tables:
        other_links = (await db.execute(
            select(StandardLayerGroupTableLink).where(
                StandardLayerGroupTableLink.table_id == table.id,
                StandardLayerGroupTableLink.layer_group_id.notin_(subtree_ids),
            )
        )).scalars().all()
        if other_links:
            shared.append(table.table_name)
    return shared


# ══════════════════════════════════════════════════════════════════════════════
# 层组递归复制
# ══════════════════════════════════════════════════════════════════════════════

async def _copy_layer_recursive(
    db: AsyncSession, layer: StandardLayerGroup,
    new_name: str, new_parent_id: str | None,
    target_standard_id: str | None = None, layer_map: dict | None = None,
) -> StandardLayerGroup:
    """递归复制层组。

    S9：target_standard_id 支持跨标准复制（默认同标准）；
    layer_map 记录 源层组 id → 新层组 id，用于将源表的次归属映射到新层组。
    同标准复制保留子树外次归属；跨标准复制仅保留子树内次归属。
    注意：async 下 db.get 不触发 selectin 懒加载，本函数一律用显式查询取关系数据。
    """
    target_standard_id = target_standard_id or layer.standard_id
    layer_map = layer_map or {}
    is_cross_standard = target_standard_id != layer.standard_id

    # 显式加载本层子层组与表（含字段、多挂关联）
    children = (await db.execute(
        select(StandardLayerGroup).where(
            StandardLayerGroup.parent_id == layer.id
        ).order_by(StandardLayerGroup.ordering)
    )).scalars().all()
    tables = (await db.execute(
        select(StandardTable).where(StandardTable.layer_id == layer.id).options(
            selectinload(StandardTable.fields),
            selectinload(StandardTable.table_links),
        ).order_by(StandardTable.ordering)
    )).scalars().all()

    new_layer = StandardLayerGroup(
        id=str(uuid.uuid4()),
        standard_id=target_standard_id,
        parent_id=new_parent_id,
        group_name=new_name,
        group_code=layer.group_code,
        description=layer.description,
        ordering=layer.ordering,
    )
    db.add(new_layer)
    layer_map[layer.id] = new_layer.id

    # 复制子层组
    for child in children:
        await _copy_layer_recursive(
            db, child, child.group_name, new_layer.id, target_standard_id, layer_map
        )

    # 复制表和字段
    for table in tables:
        new_table = StandardTable(
            id=str(uuid.uuid4()),
            layer_id=new_layer.id,
            standard_id=target_standard_id,
            table_name=table.table_name,
            table_code=table.table_code,
            table_type=table.table_type,
            geometry_type=table.geometry_type,
            description=table.description,
            ordering=table.ordering,
        )
        db.add(new_table)
        # 主归属（新层组）
        db.add(_primary_table_link(new_table))

        # S9 复制次归属：跳过主归属，其余按映射/同标准规则保留
        for link in table.table_links:
            if link.is_primary:
                continue
            mapped_id = layer_map.get(link.layer_group_id)
            if mapped_id:
                target_layer_id = mapped_id
            elif not is_cross_standard:
                target_layer_id = link.layer_group_id
            else:
                continue  # 跨标准复制：子树外次归属无法映射，丢弃
            db.add(StandardLayerGroupTableLink(
                id=str(uuid.uuid4()),
                layer_group_id=target_layer_id,
                table_id=new_table.id,
                is_primary=False,
                ordering=link.ordering,
            ))

        for field in table.fields:
            new_field = StandardField(
                id=str(uuid.uuid4()),
                table_id=new_table.id,
                standard_id=target_standard_id,
                field_name=field.field_name,
                field_alias=field.field_alias,
                data_type=field.data_type,
                length=field.length,
                precision=field.precision,
                nullable=field.nullable,
                is_primary_key=field.is_primary_key,
                is_required=field.is_required,
                default_value=field.default_value,
                description=field.description,
                ordering=field.ordering,
            )
            db.add(new_field)

    return new_layer


# ══════════════════════════════════════════════════════════════════════════════
# 标准子记录删除（回滚/导入更新用）
# ══════════════════════════════════════════════════════════════════════════════

async def _delete_standard_children(db: AsyncSession, standard_id: str):
    """删除标准的所有子记录（用于回滚）。"""
    # 删除字段-字典关联
    await db.execute(sa_delete(StandardFieldDictLink).where(
        StandardFieldDictLink.field_id.in_(
            select(StandardField.id).where(StandardField.standard_id == standard_id)
        )
    ))

    # 删除字段
    await db.execute(sa_delete(StandardField).where(StandardField.standard_id == standard_id))

    # 删除表
    await db.execute(sa_delete(StandardTable).where(StandardTable.standard_id == standard_id))

    # 删除层组
    await db.execute(sa_delete(StandardLayerGroup).where(StandardLayerGroup.standard_id == standard_id))

    # 删除字典条目
    await db.execute(sa_delete(DictionaryItem).where(
        DictionaryItem.dict_id.in_(
            select(StandardDictionary.id).where(StandardDictionary.standard_id == standard_id)
        )
    ))

    # 删除字典
    await db.execute(sa_delete(StandardDictionary).where(StandardDictionary.standard_id == standard_id))
