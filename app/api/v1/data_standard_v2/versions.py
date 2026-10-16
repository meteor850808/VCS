"""数据标准 v2 — 版本管理 + 快照/diff/回滚 + QC 摘要。"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete as sa_delete, or_
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
    VersionResponse, VersionDetail, VersionDiff, PublishRequest,
)

from . import _common as C

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# 标准概览摘要（给质检页面用）
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/data-standards-v2/{standard_id}/qc-summary",
    summary="标准概览摘要（含字段/字典/关联）",
)
async def qc_summary(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """返回标准的表→字段→字典关联 + 可用字典列表，供质检页面展示。"""
    q = select(DataStandard).where(DataStandard.id == standard_id).options(
        selectinload(DataStandard.layer_groups)
            .selectinload(StandardLayerGroup.children),
        selectinload(DataStandard.layer_groups)
            .selectinload(StandardLayerGroup.tables)
            .selectinload(StandardTable.fields)
            .selectinload(StandardField.dict_links)
            .selectinload(StandardFieldDictLink.dictionary),
        selectinload(DataStandard.standard_dictionaries)
            .selectinload(StandardDictionary.items),
    )
    result = await db.execute(q)
    std = result.scalar_one_or_none()
    if not std:
        raise HTTPException(404, "标准不存在")

    # 收集表→字段
    tables = []
    def _collect(layer):
        for t in layer.tables:
            fields = []
            for f in t.fields:
                dicts = []
                for link in (f.dict_links or []):
                    if link.dictionary:
                        dicts.append({
                            "link_id": link.id,
                            "dict_id": link.dictionary.id,
                            "dict_name": link.dictionary.dict_name,
                            "dict_code": link.dictionary.dict_code,
                            "item_count": len(link.dictionary.items or []),
                            "items": [
                                {"code": it.item_code, "label": it.item_name}
                                for it in (link.dictionary.items or [])[:20]
                            ],
                        })
                fields.append({
                    "field_id": str(f.id),
                    "field_name": f.field_name,
                    "data_type": f.data_type,
                    "length": f.length,
                    "nullable": f.nullable,
                    "is_required": f.is_required,
                    "dict_links": dicts,
                })
            tables.append({
                "table_id": str(t.id),
                "table_name": t.table_name,
                "table_code": t.table_code,
                "geometry_type": t.geometry_type,
                "field_count": len(fields),
                "fields": fields,
            })
        for child in (layer.children or []):
            _collect(child)
    for lg in (std.layer_groups or []):
        _collect(lg)

    # 标准级字典
    std_dicts = []
    for d in (std.standard_dictionaries or []):
        std_dicts.append({
            "dict_id": str(d.id),
            "dict_name": d.dict_name,
            "dict_code": d.dict_code,
            "scope": d.scope,
            "item_count": len(d.items or []),
        })

    # 全局字典（跨标准共享）
    gq = select(StandardDictionary).where(
        StandardDictionary.scope == "global",
        StandardDictionary.standard_id.is_(None),
    ).options(selectinload(StandardDictionary.items))
    gresult = await db.execute(gq)
    global_dicts = []
    for d in gresult.scalars().all():
        global_dicts.append({
            "dict_id": str(d.id),
            "dict_name": d.dict_name,
            "dict_code": d.dict_code,
            "scope": "global",
            "item_count": len(d.items or []),
        })

    # 统计
    total_fields = sum(t["field_count"] for t in tables)
    linked_fields = sum(
        1 for t in tables for f in t["fields"] if f["dict_links"]
    )

    return {
        "standard_id": str(std.id),
        "standard_name": std.name,
        "tables": tables,
        "standard_dictionaries": std_dicts,
        "global_dictionaries": global_dicts,
        "stats": {
            "table_count": len(tables),
            "field_count": total_fields,
            "linked_field_count": linked_fields,
            "dict_count": len(std_dicts) + len(global_dicts),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# 版本管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/data-standards-v2/{standard_id}/publish",
    response_model=VersionResponse,
    status_code=201,
    summary="发布标准（创建版本快照）",
)
async def publish_standard(
    standard_id: str,
    body: PublishRequest,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    snapshot = await _build_standard_snapshot(db, standard_id)

    prev_version = await _get_previous_version(db, standard_id)
    diff = _compute_diff(prev_version.get("snapshot_data", {}) if prev_version else {}, snapshot)

    from app.models.source_data import StandardVersion
    version = StandardVersion(
        id=str(_uuid.uuid4()),
        standard_id=standard_id,
        api_key_id=api_key.id,
        version_number=std.version,
        version_label=f"v{std.version}",
        snapshot_data=snapshot,
        diff_data=diff,
        change_log=body.change_log,
    )
    db.add(version)

    std.version += 1
    std.status = "published"

    await db.flush()

    return version


@router.get(
    "/data-standards-v2/{standard_id}/versions",
    response_model=list[VersionResponse],
    summary="版本历史",
)
async def list_versions(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    from app.models.source_data import StandardVersion
    q = select(StandardVersion).where(
        StandardVersion.standard_id == standard_id
    ).order_by(StandardVersion.version_number.desc())

    result = await db.execute(q)
    return result.scalars().all()


@router.get(
    "/data-standards-v2/{standard_id}/versions/{v1}/diff/{v2}",
    response_model=VersionDiff,
    summary="版本对比",
)
async def diff_versions(
    standard_id: str,
    v1: int,
    v2: int,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """对比两个版本的差异。"""
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    from app.models.source_data import StandardVersion
    q1 = select(StandardVersion).where(
        StandardVersion.standard_id == standard_id,
        StandardVersion.version_number == v1,
    ).order_by(StandardVersion.created_at.desc()).limit(1)
    q2 = select(StandardVersion).where(
        StandardVersion.standard_id == standard_id,
        StandardVersion.version_number == v2,
    ).order_by(StandardVersion.created_at.desc()).limit(1)
    r1 = await db.execute(q1)
    r2 = await db.execute(q2)
    ver1 = r1.scalar_one_or_none()
    ver2 = r2.scalar_one_or_none()

    if not ver1:
        raise HTTPException(404, f"版本 {v1} 不存在")
    if not ver2:
        raise HTTPException(404, f"版本 {v2} 不存在")

    snap1 = ver1.snapshot_data or {}
    snap2 = ver2.snapshot_data or {}

    diff = _deep_diff(snap1, snap2)
    stats = _diff_entity_stats(diff)
    return VersionDiff(
        from_version=v1, to_version=v2, **diff,
        summary=_diff_summary(stats), stats=stats,
        labels=_collect_diff_labels(snap1, snap2),
    )


@router.post(
    "/data-standards-v2/{standard_id}/rollback/{version_number}",
    response_model=VersionResponse,
    status_code=201,
    summary="回滚到指定版本",
)
async def rollback_version(
    standard_id: str,
    version_number: int,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    from app.models.source_data import StandardVersion
    q = select(StandardVersion).where(
        StandardVersion.standard_id == standard_id,
        StandardVersion.version_number == version_number,
    ).order_by(StandardVersion.created_at.desc()).limit(1)
    result = await db.execute(q)
    target_version = result.scalar_one_or_none()

    if not target_version:
        raise HTTPException(404, f"版本 {version_number} 不存在")

    await C._delete_standard_children(db, standard_id)

    if target_version.snapshot_data:
        try:
            await _restore_from_snapshot(db, standard_id, target_version.snapshot_data)
        except Exception as ex:
            raise HTTPException(500, f"快照恢复失败: {ex}")

    new_version_number = std.version
    rollback_version_rec = StandardVersion(
        id=str(_uuid.uuid4()),
        standard_id=standard_id,
        api_key_id=api_key.id,
        version_number=new_version_number,
        version_label=f"v{new_version_number}",
        snapshot_data=target_version.snapshot_data,
        diff_data={"rollback_from": std.version, "rollback_to": version_number},
        change_log=f"回滚到 v{version_number}",
        is_rollback=True,
    )
    db.add(rollback_version_rec)

    std.version = new_version_number + 1

    await db.flush()

    return rollback_version_rec


# ══════════════════════════════════════════════════════════════════════════════
# 快照构建
# ══════════════════════════════════════════════════════════════════════════════

async def _build_standard_snapshot(db: AsyncSession, standard_id: str) -> dict:
    """构建标准的完整快照。

    **保真度约定**：凡回滚后应恢复的列，这里都必须写进去。早前这个函数
    只存了实体主干，漏掉「边」（字段-字典绑定、S9 次归属）和一批列
    （`dict_code` / `parent_code` / 各 `description` / `ordering`），而
    `_delete_standard_children` 会把它们全删 —— 于是回滚 = 静默丢数据。
    绑定一丢，值域质检（`VALUE_IN_SET` / A5 值域）整链失效。
    """
    q = select(DataStandard).where(DataStandard.id == standard_id).options(
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.children),
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.tables)
            .selectinload(StandardTable.fields),
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.table_links),
        selectinload(DataStandard.standard_dictionaries).selectinload(StandardDictionary.items),
        selectinload(DataStandard.standard_dictionaries)
            .selectinload(StandardDictionary.field_links),
    ).execution_options(populate_existing=True)
    result = await db.execute(q)
    # 调用方（publish_standard）已先 db.get + 404，这里必命中；
    # 不做 None 分支 —— 那是测不到的防御代码，scalar_one 语义更诚实。
    std = result.scalar_one()

    def layer_to_dict(layer):
        return {
            "id": layer.id,
            "group_name": layer.group_name,
            "group_code": layer.group_code,
            "parent_id": layer.parent_id,
            "description": layer.description,
            "ordering": layer.ordering,
            "tables": [
                {
                    "id": t.id,
                    "table_name": t.table_name,
                    "table_code": t.table_code,
                    "table_type": t.table_type,
                    "geometry_type": t.geometry_type,
                    "description": t.description,
                    "ordering": t.ordering,
                    "fields": [
                        {
                            "id": f.id,
                            "field_name": f.field_name,
                            "field_alias": f.field_alias,
                            "data_type": f.data_type,
                            "length": f.length,
                            "precision": f.precision,
                            "nullable": f.nullable,
                            "is_primary_key": f.is_primary_key,
                            "is_required": f.is_required,
                            "default_value": f.default_value,
                            "description": f.description,
                            "ordering": f.ordering,
                        }
                        for f in t.fields
                    ],
                }
                for t in layer.tables
            ],
            "children": [layer_to_dict(c) for c in layer.children],
        }

    # S9 层组-表关联：含次归属。仅靠 layer.tables 只能还原主归属那条边。
    table_links = [
        {
            "id": lk.id,
            "layer_group_id": lk.layer_group_id,
            "table_id": lk.table_id,
            "is_primary": lk.is_primary,
            "ordering": lk.ordering,
        }
        for lg in std.layer_groups
        for lk in (lg.table_links or [])
    ]

    return {
        "standard": {
            "name": std.name, "code": std.code,
            "category": std.category, "version": std.version,
            "description": getattr(std, "description", None),
        },
        "layer_groups": [
            layer_to_dict(lg) for lg in std.layer_groups if lg.parent_id is None
        ],
        "dictionaries": [
            {
                "id": d.id, "dict_name": d.dict_name, "dict_code": d.dict_code,
                "scope": d.scope, "description": d.description,
                "is_system": getattr(d, "is_system", False),
                "items": [
                    {
                        "item_code": i.item_code,
                        "item_name": i.item_name,
                        # 字典层级靠 parent_code 表达，丢了就是平铺列表
                        "parent_code": i.parent_code,
                        "description": i.description,
                        "ordering": i.ordering,
                    }
                    for i in d.items
                ],
            }
            for d in std.standard_dictionaries
        ],
        "field_dict_links": [
            {
                "id": lk.id,
                "field_id": lk.field_id,
                "dict_id": lk.dict_id,
                "is_primary": lk.is_primary,
                "filter_condition": lk.filter_condition,
                "ordering": lk.ordering,
            }
            for d in std.standard_dictionaries
            for lk in (d.field_links or [])
        ],
        "layer_group_table_links": table_links,
    }


async def _get_previous_version(db: AsyncSession, standard_id: str) -> dict | None:
    """获取上一版本。"""
    from app.models.source_data import StandardVersion
    q = select(StandardVersion).where(
        StandardVersion.standard_id == standard_id
    ).order_by(StandardVersion.version_number.desc()).limit(1)

    result = await db.execute(q)
    version = result.scalar_one_or_none()
    return {"snapshot_data": version.snapshot_data} if version else None


def _compute_diff(old_snapshot: dict, new_snapshot: dict) -> dict:
    """计算两个快照之间的差异 — 使用深度递归对比（【S8】自然键对齐）。"""
    if not old_snapshot:
        return {"added": new_snapshot, "removed": {}, "modified": {}}
    if not new_snapshot:  # pragma: no cover - 快照永远非空（standard 键必有）
        return {"added": {}, "removed": old_snapshot, "modified": {}}
    diff = _deep_diff(old_snapshot, new_snapshot)
    if "standard/version" in diff.get("modified", {}):
        del diff["modified"]["standard/version"]
    return diff


# ══════════════════════════════════════════════════════════════════════════════
# diff 辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def _deep_diff(old, new, path: str = "") -> dict:
    """递归对比两个数据结构（dict/list/值），返回 added/removed/modified。

    【S8】列表分支按自然键对齐而非下标：
      layer_groups/children → group_code（回退 id）
      tables                → table_code（回退 id）
      fields                → field_name（回退 id）
      dictionaries          → dict_name（回退 id）
      items                 → item_code
    路径使用自然键（如 `layer_groups/BG/tables/CTRL_PT/fields/pt_id`），
    中间插入元素不再产生级联 modified 漂移噪音。
    """
    added = {}
    removed = {}
    modified = {}

    if old is None and new is None:
        return {"added": added, "removed": removed, "modified": modified}
    if old is None:
        return {"added": {path or "(root)": new}, "removed": {}, "modified": {}}
    if new is None:
        return {"added": {}, "removed": {path or "(root)": old}, "modified": {}}

    # 两个都是列表
    if isinstance(old, list) and isinstance(new, list):
        keys = _LIST_KEY_CHAIN.get(path.rsplit("/", 1)[-1])
        if keys:
            def _key(item):
                if not isinstance(item, dict):
                    return None
                for k in keys:
                    v = item.get(k)
                    if v is not None:
                        return str(v)
                return None

            old_map = {k: v for k, v in ((_key(x), x) for x in old) if k}
            new_map = {k: v for k, v in ((_key(x), x) for x in new) if k}
            for key in set(list(old_map) + list(new_map)):
                full_path = f"{path}/{key}" if path else key
                if key not in old_map:
                    added[full_path] = new_map[key]
                elif key not in new_map:
                    removed[full_path] = old_map[key]
                else:
                    sub = _deep_diff(old_map[key], new_map[key], full_path)
                    added.update(sub["added"])
                    removed.update(sub["removed"])
                    modified.update(sub["modified"])
            return {"added": added, "removed": removed, "modified": modified}

        # 通用列表（无自然键）→ 按下标对比
        max_len = max(len(old), len(new))
        for i in range(max_len):
            idx_path = f"{path}[{i}]"
            if i >= len(old):
                added[idx_path] = new[i]
            elif i >= len(new):
                removed[idx_path] = old[i]
            else:
                sub = _deep_diff(old[i], new[i], idx_path)
                added.update(sub["added"])
                removed.update(sub["removed"])
                modified.update(sub["modified"])
        return {"added": added, "removed": removed, "modified": modified}

    # 两个都是 dict — 递归键对比
    if isinstance(old, dict) and isinstance(new, dict):
        all_keys = set(list(old.keys()) + list(new.keys()))
        for key in all_keys:
            if key in _IGNORED_KEYS:
                continue
            full_path = f"{path}/{key}" if path else key
            if key not in old:
                added[full_path] = new[key]
            elif key not in new:
                removed[full_path] = old[key]
            else:
                sub = _deep_diff(old[key], new[key], full_path)
                added.update(sub["added"])
                removed.update(sub["removed"])
                modified.update(sub["modified"])
        return {"added": added, "removed": removed, "modified": modified}

    # 类型不同或值不同
    if type(old) != type(new) or old != new:
        modified[path or "(value)"] = {"old": old, "new": new}

    return {"added": added, "removed": removed, "modified": modified}


# 【S8】快照 list 字段 → 自然键链（用于对齐消除漂移）
_LIST_KEY_CHAIN = {
    "layer_groups": ("group_code", "id"),
    "children": ("group_code", "id"),
    "tables": ("table_code", "id"),
    "fields": ("field_name", "id"),
    "dictionaries": ("dict_name", "id"),
    "items": ("item_code",),
}

# 【S8】不参与内容对比的键
_IGNORED_KEYS = {"id", "ordering"}

# 【S8】实体类型（用于实体级统计）
_ENTITY_TYPES = ("layer_groups", "tables", "fields", "dictionaries", "items")
_ENTITY_LABELS = {
    "layer_groups": "层组",
    "tables": "表",
    "fields": "字段",
    "dictionaries": "字典",
    "items": "字典项",
}


def _innermost_entity(path: str):
    """返回路径最内层命中的实体 (type, key)；standard 元信息等返回 (None, None)。"""
    segs = path.split("/")
    last_type, last_key = None, None
    for i, seg in enumerate(segs):
        if seg in _ENTITY_TYPES and i + 1 < len(segs):
            last_type, last_key = seg, segs[i + 1]
    return last_type, last_key


def _diff_entity_stats(diff: dict) -> dict:
    """按实体统计变更数（同实体多条路径去重）。"""
    stats = {t: {"added": 0, "removed": 0, "modified": 0} for t in _ENTITY_TYPES}
    for op in ("added", "removed", "modified"):
        seen = set()
        for path in diff.get(op, {}):
            t, key = _innermost_entity(path)
            if t and key and (t, key) not in seen:
                seen.add((t, key))
                stats[t][op] += 1
    return stats


def _diff_summary(stats: dict) -> list[str]:
    """由实体统计生成中文摘要行。"""
    parts = []
    for op, label in (("added", "新增"), ("modified", "修改"), ("removed", "删除")):
        names = [f"{_ENTITY_LABELS[t]} {stats[t][op]}" for t in _ENTITY_TYPES if stats[t][op]]
        if names:
            parts.append(f"{label}：{'、'.join(names)}")
    return parts if parts else ["无变更"]


def _collect_diff_labels(old_snapshot: dict, new_snapshot: dict) -> dict:
    """收集实体自然键 → 中文名映射。"""
    labels = {"layer_groups": {}, "tables": {}, "fields": {}, "dictionaries": {}, "items": {}}

    def _walk_layer(lg):
        if not isinstance(lg, dict):
            return
        code = lg.get("group_code")
        if code:
            labels["layer_groups"][str(code)] = lg.get("group_name") or str(code)
        for t in lg.get("tables") or []:
            if not isinstance(t, dict):
                continue
            tcode = t.get("table_code")
            if tcode:
                labels["tables"][str(tcode)] = t.get("table_name") or str(tcode)
            for f in t.get("fields") or []:
                if isinstance(f, dict) and f.get("field_name"):
                    labels["fields"][str(f["field_name"])] = f.get("field_alias") or str(f["field_name"])
        for c in lg.get("children") or []:
            _walk_layer(c)

    for snap in (old_snapshot, new_snapshot):
        if not isinstance(snap, dict):
            continue
        for lg in snap.get("layer_groups") or []:
            _walk_layer(lg)
        for d in snap.get("dictionaries") or []:
            if not isinstance(d, dict):
                continue
            dname = d.get("dict_name")
            if dname:
                labels["dictionaries"][str(dname)] = str(dname)
            for it in d.get("items") or []:
                if isinstance(it, dict) and it.get("item_code"):
                    labels["items"][str(it["item_code"])] = it.get("item_name") or str(it["item_code"])
    return labels


# ══════════════════════════════════════════════════════════════════════════════
# 回滚辅助
# ══════════════════════════════════════════════════════════════════════════════

async def C_delete_standard_children(db: AsyncSession, standard_id: str):
    """删除标准的所有子记录（用于回滚）。"""
    # 注意：此函数委托给 _common._delete_standard_children
    await C._delete_standard_children(db, standard_id)


async def _restore_from_snapshot(db: AsyncSession, standard_id: str, snapshot: dict):
    """从快照恢复标准数据。

    顺序要求：**先建完所有实体，再建边**（字段-字典绑定、S9 关联）——
    边两端都是外键，实体没落库就插边会撞 FK。
    """

    def _restore_layer(lg_data: dict, parent_id: str | None = None):
        layer = StandardLayerGroup(
            id=lg_data["id"],
            standard_id=standard_id,
            parent_id=parent_id or lg_data.get("parent_id"),
            group_name=lg_data["group_name"],
            group_code=lg_data.get("group_code"),
            description=lg_data.get("description"),
            ordering=lg_data.get("ordering") or 0,
        )
        db.add(layer)

        for t_data in lg_data.get("tables", []):
            table = StandardTable(
                id=t_data["id"],
                layer_id=layer.id,
                standard_id=standard_id,
                table_name=t_data["table_name"],
                table_code=t_data.get("table_code"),
                table_type=t_data.get("table_type", "spatial"),
                geometry_type=t_data.get("geometry_type"),
                description=t_data.get("description"),
                ordering=t_data.get("ordering") or 0,
            )
            db.add(table)
            # 注意：这里**不**建主归属边。S9 关联统一在下方「边」段恢复 ——
            # 建两处会导致同一 (layer_group_id, table_id) 出现两条主归属，
            # 撞 uq_slgt_layer_table。

            for f_data in t_data.get("fields", []):
                field = StandardField(
                    id=f_data["id"],
                    table_id=table.id,
                    standard_id=standard_id,
                    field_name=f_data["field_name"],
                    field_alias=f_data.get("field_alias"),
                    data_type=f_data.get("data_type", "string"),
                    length=f_data.get("length"),
                    precision=f_data.get("precision"),
                    nullable=f_data.get("nullable", True),
                    is_primary_key=f_data.get("is_primary_key", False),
                    is_required=f_data.get("is_required", False),
                    default_value=f_data.get("default_value"),
                    description=f_data.get("description"),
                    ordering=f_data.get("ordering") or 0,
                )
                db.add(field)

        for child_data in lg_data.get("children", []):
            _restore_layer(child_data, parent_id=layer.id)

    for lg_data in snapshot.get("layer_groups", []):
        _restore_layer(lg_data)

    for d_data in snapshot.get("dictionaries", []):
        dict_obj = StandardDictionary(
            id=d_data["id"],
            standard_id=standard_id,
            dict_name=d_data["dict_name"],
            # dict_code 是 v2.22.3 的字段绑定主键，丢它等于废掉绑定能力
            dict_code=d_data.get("dict_code"),
            description=d_data.get("description"),
            scope=d_data.get("scope", "standard"),
            is_system=d_data.get("is_system", False),
        )
        db.add(dict_obj)

        for i_data in d_data.get("items", []):
            item = DictionaryItem(
                dict_id=dict_obj.id,
                item_code=i_data["item_code"],
                item_name=i_data["item_name"],
                # 字典层级：parent_code 丢了父子关系就没了
                parent_code=i_data.get("parent_code"),
                description=i_data.get("description"),
                ordering=i_data.get("ordering") or 0,
            )
            db.add(item)

    # ── 边：字段-字典绑定 ──────────────────────────────────────────────
    # 整张表在回滚前已被 _delete_standard_children 清空，不重建就等于
    # 绑定全丢 → 值域质检（VALUE_IN_SET / A5 值域）失效。
    for lk_data in snapshot.get("field_dict_links", []):
        db.add(StandardFieldDictLink(
            id=lk_data["id"],
            field_id=lk_data["field_id"],
            dict_id=lk_data["dict_id"],
            is_primary=lk_data.get("is_primary", False),
            filter_condition=lk_data.get("filter_condition"),
            ordering=lk_data.get("ordering") or 0,
        ))

    # ── 边：S9 层组-表关联（含次归属）─────────────────────────────────
    # 放在最后：边依赖实体已 db.add，否则撞 FK。
    explicit_links = snapshot.get("layer_group_table_links")
    if explicit_links:
        for lk_data in explicit_links:
            db.add(StandardLayerGroupTableLink(
                id=lk_data["id"],
                layer_group_id=lk_data["layer_group_id"],
                table_id=lk_data["table_id"],
                is_primary=lk_data.get("is_primary", False),
                ordering=lk_data.get("ordering") or 0,
            ))
    else:
        # 老快照没有这张边表 —— 按表的主归属（layer_id）补一条，
        # 保证「每表恰有一个主归属」这条不变量仍然成立。
        for lg_data in snapshot.get("layer_groups", []):
            stack = [lg_data]
            while stack:
                cur = stack.pop()
                for t_data in cur.get("tables", []):
                    db.add(C._primary_table_link(StandardTable(
                        id=t_data["id"], layer_id=cur["id"],
                        ordering=t_data.get("ordering"),
                    )))
                stack.extend(cur.get("children", []))
