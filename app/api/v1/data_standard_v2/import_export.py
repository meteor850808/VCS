"""数据标准 v2 — 多格式导入/导出/模板 + 自动建表。"""

import csv
import io
import json
import logging
import os
import tempfile
import urllib.parse
import uuid as _uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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

from app.schemas.data_standard_v2 import ImportResult
from app.services.data_standard_parser import _split_dict_codes

from . import _common as C
from .versions import (
    _build_standard_snapshot, _get_previous_version, _compute_diff,
    _restore_from_snapshot,
)

router = APIRouter()
logger = logging.getLogger("vcs")

VALID_IMPORT_MODES = ("create", "update", "upsert")


# ══════════════════════════════════════════════════════════════════════════════
# 导入公共逻辑
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_import_target(
    db: AsyncSession, api_key: ApiKey, mode: str,
    code: str | None, name: str | None,
) -> DataStandard | None:
    """按 mode 解析导入目标标准。"""
    if mode not in VALID_IMPORT_MODES:
        raise HTTPException(400, f"mode 必须是 {'/'.join(VALID_IMPORT_MODES)} 之一")

    if mode == "create":
        return None

    base_q = select(DataStandard).where(api_key.scope_clause(DataStandard))
    target: DataStandard | None = None
    if code:
        r = await db.execute(base_q.where(DataStandard.code == code))
        target = r.scalar_one_or_none()
    if target is None and name:
        r = await db.execute(base_q.where(DataStandard.name == name))
        target = r.scalar_one_or_none()

    if mode == "update" and target is None:
        raise HTTPException(
            404,
            f"未找到可更新的标准（标准编码={code or '无'}，标准名称={name or '无'}）",
        )
    return target


async def _import_version_snapshot(
    db: AsyncSession, api_key: ApiKey, target: DataStandard,
    change_log: str, extra_diff: dict | None = None,
) -> int:
    """导入更新后：构建新快照 → 对比旧版本 → 写入 StandardVersion → 版本号 +1。"""
    from app.models.source_data import StandardVersion

    snapshot = await _build_standard_snapshot(db, target.id)
    prev = await _get_previous_version(db, target.id)
    diff = _compute_diff(prev.get("snapshot_data", {}) if prev else {}, snapshot)
    if extra_diff:
        diff = {k: {**diff.get(k, {}), **extra_diff.get(k, {})} for k in ("added", "removed", "modified")}

    version = StandardVersion(
        id=str(_uuid.uuid4()),
        standard_id=target.id,
        api_key_id=api_key.owner_id,
        version_number=target.version,
        version_label=f"v{target.version}",
        snapshot_data=snapshot,
        diff_data=diff,
        change_log=change_log,
    )
    db.add(version)
    target.version += 1
    return target.version


# ══════════════════════════════════════════════════════════════════════════════
# JSON 导入
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/data-standards-v2/import/json",
    response_model=ImportResult,
    status_code=201,
    summary="JSON 格式导入数据标准",
)
async def import_standard_json(
    body: dict,
    mode: str = Query("create", description="导入模式: create/update/upsert"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """从 JSON 数据导入数据标准。"""
    warnings = []

    code = (body.get("code") or None) and str(body.get("code")).strip() or None
    name = body.get("name", "未命名标准") or "未命名标准"
    category = body.get("category", "custom")
    description = body.get("description")

    target = await _resolve_import_target(db, api_key, mode, code, name)
    is_update = target is not None

    if is_update:
        std = target
        std.code = code if code is not None else std.code
        std.name = name
        std.description = description if description is not None else std.description
        std.category = category if category != "custom" else std.category
        await C._delete_standard_children(db, std.id)
        # update/upsert 分支同样要兜 IntegrityError —— 否则结构级冲突
        # （如同层组重名表）直接 500 而非 409 精确归因（用户实测报告）。
        try:
            tables_count, fields_count, dicts_count = await _create_json_children(
                db, std.id, body, warnings, api_key.owner_id)
            await db.flush()
        except IntegrityError as e:
            await db.rollback()
            raise C._raise_conflict(e, name or code)
        version = await _import_version_snapshot(db, api_key, std, f"JSON 导入更新（{mode}）")
        await db.flush()
        return ImportResult(
            standard_id=std.id, name=std.name, mode=mode, updated=True, version=version,
            layer_groups_count=tables_count, tables_count=tables_count,
            fields_count=fields_count, dictionaries_count=dicts_count,
            warnings=warnings,
            message=f"更新成功：{tables_count} 个表，{fields_count} 个字段，{dicts_count} 个字典（版本 v{version}）",
        )

    std = DataStandard(
        id=str(_uuid.uuid4()),
        api_key_id=api_key.id,
        code=code,
        name=name,
        description=description,
        category=category,
    )
    db.add(std)

    try:
        tables_count, fields_count, dicts_count = await _create_json_children(db, std.id, body, warnings, api_key.owner_id)
        await db.flush()
    except IntegrityError as e:
        # 子实体冲突（字段重名/表重名/字典重名）不得报成「标准名称已存在」——
        # 那是另一个实体，按提示改标准名解决不了。按实际约束说话。
        await db.rollback()
        raise C._raise_conflict(e, name or code)
    return ImportResult(
        standard_id=std.id, name=std.name, mode=mode, updated=False, version=None,
        layer_groups_count=tables_count, tables_count=tables_count,
        fields_count=fields_count, dictionaries_count=dicts_count,
        warnings=warnings,
        message=f"导入成功：{tables_count} 个表，{fields_count} 个字段，{dicts_count} 个字典",
    )


async def _create_json_children(
    db, standard_id: str, body: dict, warnings: list[str], api_key_id: str | None = None,
) -> tuple[int, int, int]:
    """在指定标准下按 JSON 结构创建层组/表/字段/字典/字段-字典关联。"""
    tables_count = 0
    fields_count = 0
    # 字典在层组之后创建，故字段的 dict_codes 先攒着，等 dict_code→id 可解析了再建链。
    pending_links: list[tuple[StandardField, list[str]]] = []
    # 实际创建计数（去重后的真值，不按源文件字面统计）
    stats: dict[str, int] = {"tables": 0, "fields": 0}

    for lg_data in body.get("layer_groups", []):
        await _create_layer_from_json(db, standard_id, lg_data, None,
                                      pending_links, warnings, stats)
    tables_count = stats["tables"]
    fields_count = stats["fields"]

    dicts_count = 0
    dict_id_by_code: dict[str, str] = {}
    for d_data in body.get("dictionaries", []):
        dict_obj = StandardDictionary(
            id=str(_uuid.uuid4()),
            standard_id=standard_id,
            api_key_id=api_key_id,
            dict_name=d_data.get("dict_name", "未命名字典"),
            dict_code=d_data.get("dict_code"),
            description=d_data.get("description"),
            scope=d_data.get("scope", "standard"),
        )
        db.add(dict_obj)
        if d_data.get("dict_code"):
            dict_id_by_code[d_data["dict_code"]] = dict_obj.id

        # 同字典内重复 item_code 保留首个并告警 —— uq_di_dict_code 唯一约束
        # 会拦，但整单 409 比丢几行更糟（真实源数据就带重复：一厅八室
        # LAND CT47 '214'×4）。告警而非失败是 v2.22.3 的既有原则。
        seen_item_codes: set[str] = set()
        for item_data in d_data.get("items", []):
            item_code = item_data.get("item_code", "")
            if item_code in seen_item_codes:
                warnings.append(
                    f"字典「{d_data.get('dict_name', '未命名')}」条目编码 "
                    f"'{item_code}' 重复，已保留首个、丢弃后续"
                )
                continue
            seen_item_codes.add(item_code)
            item = DictionaryItem(
                id=str(_uuid.uuid4()),
                dict_id=dict_obj.id,
                item_code=item_code,
                item_name=item_data.get("item_name", ""),
                parent_code=item_data.get("parent_code"),
            )
            db.add(item)
        dicts_count += 1

    # 字段-字典关联（v2.22.3）—— 语义与 Excel v2 路径一致：首个编码为主字典。
    for field_obj, codes in pending_links:
        for i, code in enumerate(codes):
            dict_id = dict_id_by_code.get(code)
            if not dict_id:
                warnings.append(
                    f"字段 '{field_obj.field_name}' 的字典编码 '{code}' 未在 dictionaries 中声明，该绑定已跳过"
                )
                continue
            db.add(StandardFieldDictLink(
                id=str(_uuid.uuid4()),
                field_id=field_obj.id,
                dict_id=dict_id,
                is_primary=(i == 0),
                ordering=i,
            ))

    return tables_count, fields_count, dicts_count


async def _create_layer_from_json(
    db: AsyncSession, standard_id: str,
    lg_data: dict, parent_id: str | None,
    pending_links: list[tuple[StandardField, list[str]]] | None = None,
    warnings: list[str] | None = None,
    stats: dict | None = None,
) -> StandardLayerGroup:
    """从 JSON 数据创建层组（递归）。

    pending_links：跨层组/跨表累积的待建链字段，交给调用方在字典创建后统一处理。
    warnings / stats：同表重复 field_name 保首个、丢弃后续并告警 ——
    uq_sf_table_field 唯一约束会拦，但整单 409 比丢几行更糟（真实源数据
    就带重复：一厅八室五份全有，PLAN 达 116 处）。stats 记实际创建数，
    计数不说谎（源 1348 字段去重后实际 1324）。
    """
    layer = StandardLayerGroup(
        id=str(_uuid.uuid4()),
        standard_id=standard_id,
        parent_id=parent_id,
        group_name=lg_data.get("group_name", "未命名层组"),
        group_code=lg_data.get("group_code"),
        description=lg_data.get("description"),
    )
    db.add(layer)

    for t_data in lg_data.get("tables", []):
        table = StandardTable(
            id=str(_uuid.uuid4()),
            layer_id=layer.id,
            standard_id=standard_id,
            table_name=t_data.get("table_name", "未命名表"),
            table_code=t_data.get("table_code"),
            table_type=t_data.get("table_type", "spatial"),
            geometry_type=t_data.get("geometry_type"),
        )
        db.add(table)
        db.add(C._primary_table_link(table))
        if stats is not None:
            stats["tables"] = stats.get("tables", 0) + 1

        seen_field_names: set[str] = set()
        for f_data in t_data.get("fields", []):
            field_name = f_data.get("field_name", "")
            if field_name in seen_field_names:
                if warnings is not None:
                    warnings.append(
                        f"表「{t_data.get('table_name', '未命名表')}」字段名 "
                        f"'{field_name}' 重复，已保留首个、丢弃后续"
                    )
                continue
            seen_field_names.add(field_name)
            field = StandardField(
                id=str(_uuid.uuid4()),
                table_id=table.id,
                standard_id=standard_id,
                field_name=field_name,
                field_alias=f_data.get("field_alias"),
                data_type=f_data.get("data_type", "string"),
                length=f_data.get("length"),
                precision=f_data.get("precision"),
                nullable=f_data.get("nullable", True),
                is_primary_key=f_data.get("is_primary_key", False),
                is_required=f_data.get("is_required", False),
            )
            db.add(field)
            if stats is not None:
                stats["fields"] = stats.get("fields", 0) + 1
            codes = _split_dict_codes(f_data.get("dict_codes"))
            if codes and pending_links is not None:
                pending_links.append((field, codes))

    for child_data in lg_data.get("children", []):
        await _create_layer_from_json(db, standard_id, child_data, layer.id,
                                      pending_links, warnings, stats)

    return layer


# ══════════════════════════════════════════════════════════════════════════════
# Excel 导入
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/data-standards-v2/import/excel",
    response_model=ImportResult,
    status_code=201,
    summary="Excel 格式导入数据标准",
)
async def import_standard_excel(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(None),
    category: str = Form("custom"),
    mode: str = Form("create", description="导入模式: create/update/upsert"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """从 Excel 文件导入数据标准。"""
    if mode not in VALID_IMPORT_MODES:
        raise HTTPException(400, f"mode 必须是 {'/'.join(VALID_IMPORT_MODES)} 之一")
    if not file.filename or not file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(400, "请上传 .xlsx 或 .xls 文件")
    if file.filename.lower().endswith('.xls'):
        raise HTTPException(
            400,
            "旧版 .xls 格式暂不支持（解析器基于 openpyxl，仅支持 .xlsx），请在 Excel 中另存为 .xlsx 后重试",
        )

    content = await file.read()

    from app.services.data_standard_parser import (
        parse_standard_excel,
        parse_standard_excel_v2,
    )

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        v2_result = parse_standard_excel_v2(tmp_path)
        if v2_result.recognized:
            return await _import_from_v2(
                db, api_key, mode, name, description, category,
                file.filename, v2_result,
            )
        result = parse_standard_excel(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not result.sheets:
        raise HTTPException(400, "Excel 文件中没有找到有效的工作表")

    # 旧扁平结构
    target = await _resolve_import_target(db, api_key, mode, None, name)
    is_update = target is not None

    if is_update:
        std = target
        std.name = name
        std.description = description if description is not None else std.description
        std.category = category if category != "custom" else std.category
        std.file_name = file.filename
        await C._delete_standard_children(db, std.id)
        tables_count, fields_count = await _create_flat_children(
            db, std.id, result.sheets, result.warnings)
        await db.flush()
        version = await _import_version_snapshot(db, api_key, std, f"Excel 导入更新（旧扁平结构，{mode}）")
        await db.flush()
        return ImportResult(
            standard_id=std.id, name=std.name, mode=mode, updated=True, version=version,
            layer_groups_count=len(result.sheets), tables_count=tables_count,
            fields_count=fields_count, warnings=result.warnings,
            message=f"更新成功：{tables_count} 个工作表，{fields_count} 个字段（版本 v{version}）",
        )

    std = DataStandard(
        id=str(_uuid.uuid4()),
        api_key_id=api_key.id,
        name=name,
        description=description,
        category=category,
        file_name=file.filename,
    )
    db.add(std)

    try:
        tables_count, fields_count = await _create_flat_children(
            db, std.id, result.sheets, result.warnings)
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise C._raise_conflict(e, name)

    return ImportResult(
        standard_id=std.id,
        name=std.name,
        mode=mode,
        layer_groups_count=len(result.sheets),
        tables_count=tables_count,
        fields_count=fields_count,
        warnings=result.warnings,
        message=f"导入成功：{tables_count} 个工作表，{fields_count} 个字段",
    )


async def _create_flat_children(
    db, standard_id: str, sheets, warnings: list[str] | None = None,
) -> tuple[int, int]:
    """旧扁平结构：为每个 Sheet 创建层组 + 表 + 字段。

    行级去重：同 Sheet 重复字段名保首个 + 告警（uq_sf_table_field）。
    """
    tables_count = 0
    fields_count = 0

    for sheet in sheets:
        layer = StandardLayerGroup(
            id=str(_uuid.uuid4()),
            standard_id=standard_id,
            group_name=sheet.sheet_name,
            ordering=tables_count,
        )
        db.add(layer)

        table = StandardTable(
            id=str(_uuid.uuid4()),
            layer_id=layer.id,
            standard_id=standard_id,
            table_name=sheet.sheet_name,
            table_type="attribute",
        )
        db.add(table)
        db.add(C._primary_table_link(table))

        seen_flat_fields: set[str] = set()
        for field_def in sheet.fields:
            if field_def.field_name in seen_flat_fields:
                if warnings is not None:
                    warnings.append(
                        f"表 '{sheet.sheet_name}' 字段名 '{field_def.field_name}' "
                        f"重复，已保留首个、丢弃后续"
                    )
                continue
            seen_flat_fields.add(field_def.field_name)
            field = StandardField(
                id=str(_uuid.uuid4()),
                table_id=table.id,
                standard_id=standard_id,
                field_name=field_def.field_name,
                data_type=field_def.field_type or "string",
                length=field_def.length,
                nullable=field_def.nullable if field_def.nullable is not None else True,
                description=field_def.description,
            )
            db.add(field)
            fields_count += 1

        tables_count += 1

    return tables_count, fields_count


async def _import_from_v2(db, api_key, mode, name, description, category, filename, v2_result):
    """按 v2 四 Sheet 模板导入。"""
    from app.services.data_standard_parser import V2GroupDef

    code = (v2_result.code or None) and str(v2_result.code).strip() or None
    import_name = name or v2_result.name or "未命名标准"

    target = await _resolve_import_target(db, api_key, mode, code, import_name)
    is_update = target is not None

    if is_update:
        std = target
        std.code = code if code is not None else std.code
        std.name = import_name
        std.description = description or v2_result.description or std.description
        std.category = category if category != "custom" else (v2_result.category or std.category)
        std.file_name = filename
        await C._delete_standard_children(db, std.id)
        # update/upsert 分支兜底：结构级冲突 409 精确归因，不是 500（用户实测报告）
        try:
            counts = await _create_v2_children(db, std.id, v2_result, api_key.owner_id)
            await db.flush()
        except IntegrityError as e:
            await db.rollback()
            raise C._raise_conflict(e, import_name or code)
        version = await _import_version_snapshot(db, api_key, std, f"Excel 导入更新（{mode}）")
        await db.flush()
        return ImportResult(
            standard_id=std.id, name=std.name, mode=mode, updated=True, version=version,
            layer_groups_count=counts["groups"], tables_count=counts["tables"],
            fields_count=counts["fields"], dictionaries_count=counts["dicts"],
            warnings=v2_result.warnings,
            message=(
                f"更新成功：{counts['groups']} 个层组，{counts['tables']} 个表，"
                f"{counts['fields']} 个字段，{counts['dicts']} 个字典，{counts['items']} 个字典项（版本 v{version}）"
            ),
        )

    std = DataStandard(
        id=str(_uuid.uuid4()),
        api_key_id=api_key.id,
        code=code,
        name=import_name,
        description=description or v2_result.description,
        category=category if category != "custom" else (v2_result.category or "custom"),
        file_name=filename,
    )
    db.add(std)

    try:
        counts = await _create_v2_children(db, std.id, v2_result, api_key.owner_id)
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise C._raise_conflict(e, import_name or code)

    return ImportResult(
        standard_id=std.id, name=std.name, mode=mode,
        layer_groups_count=counts["groups"], tables_count=counts["tables"],
        fields_count=counts["fields"], dictionaries_count=counts["dicts"],
        warnings=v2_result.warnings,
        message=(
            f"导入成功：{counts['groups']} 个层组，{counts['tables']} 个表，"
            f"{counts['fields']} 个字段，{counts['dicts']} 个字典，{counts['items']} 个字典项"
        ),
    )


async def _create_v2_children(db, standard_id: str, v2_result, api_key_id: str | None = None) -> dict:
    """按 v2 解析结果在指定标准下重建层组树/表/字段/字典。"""
    from app.services.data_standard_parser import V2GroupDef

    tables_count = 0
    fields_count = 0

    group_map: dict[str, StandardLayerGroup] = {}
    group_children: dict[str, list[V2GroupDef]] = {}

    for g_def in v2_result.groups:
        group = StandardLayerGroup(
            id=str(_uuid.uuid4()),
            standard_id=standard_id,
            group_name=g_def.group_name,
            group_code=g_def.group_code or None,
            description=g_def.description,
        )
        db.add(group)
        group_map[g_def.group_code] = group
        if g_def.parent_code:
            group_children.setdefault(g_def.parent_code, []).append(g_def)

    for code, group in group_map.items():
        for child_def in group_children.get(code, []):
            child = group_map.get(child_def.group_code)
            if child:
                child.parent_id = group.id

    root_groups = [
        g for g_def, g in zip(v2_result.groups, group_map.values())
        if not g_def.parent_code or g_def.parent_code not in group_map
    ]
    for i, g in enumerate(root_groups):
        g.ordering = i

    table_map: dict[str, StandardTable] = {}
    fallback_group: StandardLayerGroup | None = None
    for t_def in v2_result.tables:
        layer = group_map.get(t_def.layer_code)
        if layer is None and fallback_group is None:
            fallback_group = StandardLayerGroup(
                id=str(_uuid.uuid4()),
                standard_id=standard_id,
                group_name="未分组",
                description="导入时层组编码未匹配的表自动归入此组",
            )
            db.add(fallback_group)
            v2_result.warnings.append(
                f"表 '{t_def.table_name}' 的层组编码 '{t_def.layer_code}' 未在「层组定义」中声明，已归入「未分组」"
            )
        table = StandardTable(
            id=str(_uuid.uuid4()),
            standard_id=standard_id,
            layer_id=(layer or fallback_group).id,
            table_name=t_def.table_name,
            table_code=t_def.table_code or None,
            table_type=t_def.table_type if t_def.table_type in ("spatial", "attribute") else "attribute",
            geometry_type=t_def.geometry_type,
            description=t_def.description,
        )
        db.add(table)
        db.add(C._primary_table_link(table))
        table_map[t_def.table_code] = table
        tables_count += 1

    pending_links: list[tuple[StandardField, list[str]]] = []
    # 行级去重（与 JSON 路径同一契约）：同表重复字段名保首个 + 告警。
    # 真实源数据普遍带重复（一厅八室五份全有），否则撞 uq_sf_table_field 整单 409。
    seen_field_names: dict[str, set[str]] = {}
    for f_def in v2_result.fields:
        table = table_map.get(f_def.table_code)
        if not table:
            v2_result.warnings.append(
                f"字段 '{f_def.field_name}' 的表编码 '{f_def.table_code}' 未在「数据表定义」中声明，已跳过"
            )
            continue
        dup_guard = seen_field_names.setdefault(f_def.table_code, set())
        if f_def.field_name in dup_guard:
            v2_result.warnings.append(
                f"表 '{table.table_name}' 字段名 '{f_def.field_name}' 重复，已保留首个、丢弃后续"
            )
            continue
        dup_guard.add(f_def.field_name)
        field = StandardField(
            id=str(_uuid.uuid4()),
            table_id=table.id,
            standard_id=standard_id,
            field_name=f_def.field_name,
            field_alias=f_def.field_alias,
            data_type=f_def.data_type,
            length=f_def.length,
            precision=f_def.precision,
            nullable=not f_def.is_required,
            is_required=f_def.is_required,
            is_primary_key=f_def.is_primary_key,
            default_value=f_def.default_value,
            description=f_def.description,
            ordering=f_def.ordering,
        )
        db.add(field)
        fields_count += 1
        if f_def.dict_codes:
            pending_links.append((field, f_def.dict_codes))

    dict_count = 0
    item_count = 0
    dict_id_by_code: dict[str, str] = {}
    for d_def in v2_result.dictionaries:
        dict_obj = StandardDictionary(
            id=str(_uuid.uuid4()),
            standard_id=standard_id,
            api_key_id=api_key_id,
            dict_name=d_def.dict_name or d_def.dict_code or "未命名字典",
            dict_code=d_def.dict_code or None,
            description=d_def.description,
            scope=d_def.scope if d_def.scope in ("standard", "global") else "standard",
        )
        db.add(dict_obj)
        dict_count += 1
        if d_def.dict_code:
            dict_id_by_code[d_def.dict_code] = dict_obj.id
        # 行级去重：同字典重复条目码保首个 + 告警（uq_di_dict_code）
        seen_item_codes: set[str] = set()
        for i, (item_code, item_name) in enumerate(d_def.items):
            if item_code in seen_item_codes:
                v2_result.warnings.append(
                    f"字典「{d_def.dict_name or d_def.dict_code or '未命名'}」条目码 "
                    f"'{item_code}' 重复，已保留首个、丢弃后续"
                )
                continue
            seen_item_codes.add(item_code)
            db.add(DictionaryItem(
                id=str(_uuid.uuid4()),
                dict_id=dict_obj.id,
                item_code=item_code,
                item_name=item_name or item_code,
                ordering=i,
            ))
            item_count += 1

    # 字段-字典关联（v2.22.3）—— 必须排在字典创建之后：编码→dict_id 才能解析。
    # 列表首个编码记为 is_primary（主字典）；filter_condition 不表达
    # （该列全仓 0 处消费，属死字段，不臆造语义）。
    links_count = 0
    for field_obj, codes in pending_links:
        for i, code in enumerate(codes):
            dict_id = dict_id_by_code.get(code)
            if not dict_id:
                v2_result.warnings.append(
                    f"字段 '{field_obj.field_name}' 的字典编码 '{code}' 未在「字典定义」中声明，该绑定已跳过"
                )
                continue
            db.add(StandardFieldDictLink(
                id=str(_uuid.uuid4()),
                field_id=field_obj.id,
                dict_id=dict_id,
                is_primary=(i == 0),
                ordering=i,
            ))
            links_count += 1

    return {"groups": len(v2_result.groups), "tables": tables_count,
            "fields": fields_count, "dicts": dict_count, "items": item_count,
            "links": links_count}


# ══════════════════════════════════════════════════════════════════════════════
# CSV 导入
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# CSV 归档格式（v2.22.3）
#
# 形态：_manifest.json + 6 个规范化角色 CSV。manifest 只做**格式标识**（format /
# version / encoding / roles / diagnostics），**不含任何结构数据** —— CSV 本身
# 是唯一数据源，因此可在 Excel 里改 CSV 再导回，且不存在双源不一致。
#
# 为什么不按「一表一文件」：表名在标准内不唯一（实测存在两个层组下各一张「路面」），
# 靠文件名单独标识无法消歧；且表 CSV 装不下层组层级/表编码/字段类型/字典项/绑定。
#
# 连接键与 Excel 模板同构：层组编码 / 表编码 / 字典编码；行顺序即 ordering。
# 缺 manifest 的普通 CSV 走 legacy 路径（每文件一张表、字段取首行），零破坏。
# ══════════════════════════════════════════════════════════════════════════════

_CSV_FORMAT = "vcs-standard-csv"
_CSV_VERSION = 1
_CSV_ENCODING = "utf-8-sig"
_CSV_MANIFEST_NAME = "_manifest.json"
_CSV_BOM = b"\xef\xbb\xbf"

_CSV_ROLES = {
    "standard": "标准信息.csv",
    "layer_groups": "层组.csv",
    "tables": "数据表.csv",
    "fields": "字段.csv",
    "dictionaries": "字典.csv",
    "dictionary_items": "字典项.csv",
}

# 列名与 Excel 模板刻意保持一致，让两个格式只需学一次。
# 字典/字典项拆两个文件：Excel 是合并表，拆开后同一字典名称不可能在多行里互相冲突。
# 字段表比 Excel 多一列「默认值」—— Excel 模板没有该列，往返会丢 StandardField.default_value。
_CSV_HEADERS = {
    "standard": ["标准名称", "标准描述", "分类"],
    "layer_groups": ["层组名", "层组编码", "父级编码", "描述"],
    "tables": ["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"],
    "fields": ["表编码", "字段名", "中文名", "数据类型", "长度", "精度",
               "必填", "主键", "描述", "字典编码", "默认值"],
    "dictionaries": ["字典编码", "字典名称", "适用范围", "字典描述"],
    "dictionary_items": ["字典编码", "字典项编码", "字典项名称", "父级编码", "字典项描述", "排序"],
}

# 缺这两个角色 → 归档结构非法，直接 400。缺字典只降级不报错（可能是纯结构标准）。
_CSV_REQUIRED_ROLES = ("tables", "fields")
_CSV_TRUE_TOKENS = {"是", "y", "yes", "true", "1"}


def _csv_bytes(rows) -> bytes:
    """序列化为带 BOM 的 UTF-8 字节。

    BOM 是必需的：中文用户在 Excel 里双击无 BOM 的 CSV 会看到乱码。
    """
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return _CSV_BOM + out.getvalue().encode("utf-8")


def _csv_cell(row, index) -> str:
    """安全取单元格（越界/None → 空串）。"""
    try:
        value = row[index]
    except IndexError:
        return ""
    return "" if value is None else str(value).strip()


def _csv_flag(row, index) -> bool:
    return _csv_cell(row, index).lower() in _CSV_TRUE_TOKENS


def _csv_int(row, index):
    """整数列，空/非法 → None（而不是 0，避免把「没填」变成「零」）。"""
    text = _csv_cell(row, index)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _sorted_by_ordering(rows) -> list:
    """按 ordering 稳定排序；ordering 缺失/并列时保持关系返回的原始次序。"""
    return sorted(rows, key=lambda r: r.ordering if r.ordering is not None else 0)


@router.post(
    "/data-standards-v2/import/csv",
    response_model=ImportResult,
    status_code=201,
    summary="CSV 格式导入数据标准",
)
async def import_standard_csv(
    files: list[UploadFile] = File(...),
    name: str = Form(...),
    description: str = Form(None),
    category: str = Form("custom"),
    mode: str = Form("create", description="导入模式: create/update/upsert"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """从 CSV 导入数据标准。

    三条接收形态（v2.22.3 §六·补七 起）：
    - **整包 ZIP**：单个 .zip part（或 PK 魔数内容）→ 自动解包进 blobs，
      再按下面两条路径分派 —— 导出的归档原样回导零拆包。
    - **归档路径**：解包后含 `_manifest.json` → 按 roles 映射读 6 个规范化 CSV，
      可无损还原层组/表/字段/字典/字典项/字段-字典绑定。
    - **legacy 路径**：没有 manifest 的普通 CSV → 每个文件一张表、字段取首行，
      保持 v2.22.2 之前的行为，外部作者零破坏。
    """
    if mode not in VALID_IMPORT_MODES:
        raise HTTPException(400, f"mode 必须是 {'/'.join(VALID_IMPORT_MODES)} 之一")

    blobs, unnamed = await _read_upload_blobs(files)
    zip_warnings: list[str] = []
    blobs = _expand_zip_blobs(blobs, zip_warnings)

    manifest = None
    if _CSV_MANIFEST_NAME in blobs:
        try:
            manifest = json.loads(blobs.pop(_CSV_MANIFEST_NAME).decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, f"{_CSV_MANIFEST_NAME} 不是合法 JSON: {exc}")
        manifest = _validated_manifest(manifest)

    # 标准信息.csv 提供 name/description/category 的默认值，表单显式值优先。
    # 放在 _resolve_import_target 之前：description/category 要在建标准前定下来。
    meta_warnings: list[str] = []
    if manifest is not None:
        name, description, category = _merge_standard_meta(
            name, description, category,
            _archive_standard_meta(blobs, manifest, meta_warnings), meta_warnings)

    target = await _resolve_import_target(db, api_key, mode, None, name)
    is_update = target is not None

    async def _build(standard_id: str):
        if manifest is not None:
            return await _create_csv_children_from_archive(
                db, standard_id, manifest, blobs, api_key)
        return await _create_csv_children_legacy(db, standard_id, blobs, unnamed)

    def _prepend_warnings(warnings: list[str]) -> list[str]:
        return [*zip_warnings, *meta_warnings, *warnings]

    if is_update:
        std = target
        std.name = name
        std.description = description if description is not None else std.description
        std.category = category if category != "custom" else std.category
        await C._delete_standard_children(db, std.id)
        # update/upsert 分支兜底：归档路径表/字典编码行已宽容去重，但
        # 字典**重名**行（不同 code 同名）仍会撞 uq_sd_standard_name ——
        # 必须 409 精确归因，不是 500（用户实测报告）。
        try:
            tables_count, fields_count, warnings = await _build(std.id)
            await db.flush()
        except IntegrityError as e:
            await db.rollback()
            raise C._raise_conflict(e, name)
        version_no = await _import_version_snapshot(db, api_key, std, f"CSV 导入更新（{mode}）")
        await db.flush()
        return ImportResult(
            standard_id=std.id, name=std.name, mode=mode, updated=True, version=version_no,
            tables_count=tables_count, fields_count=fields_count,
            warnings=_prepend_warnings(warnings),
            message=f"更新成功：{tables_count} 个表，{fields_count} 个字段（版本 v{version_no}）",
        )

    std = DataStandard(
        id=str(_uuid.uuid4()),
        api_key_id=api_key.id,
        name=name,
        description=description,
        category=category,
    )
    db.add(std)

    try:
        tables_count, fields_count, warnings = await _build(std.id)
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise C._raise_conflict(e, name)

    return ImportResult(
        standard_id=std.id, name=std.name, mode=mode,
        tables_count=tables_count, fields_count=fields_count,
        warnings=_prepend_warnings(warnings),
        message=f"导入成功：{tables_count} 个表，{fields_count} 个字段",
    )


async def _create_csv_children_legacy(
    db, standard_id: str, blobs: dict[str, bytes], unnamed: int = 0,
) -> tuple[int, int, list[str]]:
    """legacy CSV 导入：无 manifest 的普通 CSV，每个文件 → 层组 + 表 + 字段。

    行为与 v2.22.2 之前完全一致（外部作者零破坏）：表名取文件名，字段取首行，
    字段类型恒为 string，没有字典/编码/绑定概念。
    """
    tables_count = 0
    fields_count = 0
    warnings = []
    for _ in range(unnamed):
        warnings.append("跳过无文件名文件: <未命名>")

    for filename, content in blobs.items():
        if not filename.endswith('.csv'):
            warnings.append(f"跳过非 CSV 文件: {filename}")
            continue

        text = content.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)

        if not rows:
            warnings.append(f"空文件: {filename}")
            continue

        table_name = filename.replace('.csv', '')
        field_names = rows[0] if rows else []

        layer = StandardLayerGroup(
            id=str(_uuid.uuid4()),
            standard_id=standard_id,
            group_name=f"导入层组 - {table_name}",
            ordering=tables_count,
        )
        db.add(layer)

        table = StandardTable(
            id=str(_uuid.uuid4()),
            layer_id=layer.id,
            standard_id=standard_id,
            table_name=table_name,
            table_type="attribute",
        )
        db.add(table)
        db.add(C._primary_table_link(table))

        seen_columns: set[str] = set()
        for idx, field_name in enumerate(field_names):
            cleaned = field_name.strip()
            if cleaned in seen_columns:
                warnings.append(
                    f"文件 '{filename}' 表头列名 '{cleaned}' 重复，已保留首个、丢弃后续"
                )
                continue
            seen_columns.add(cleaned)
            field = StandardField(
                id=str(_uuid.uuid4()),
                table_id=table.id,
                standard_id=standard_id,
                field_name=cleaned,
                data_type="string",
                ordering=idx,
            )
            db.add(field)
            fields_count += 1

        tables_count += 1

    return tables_count, fields_count, warnings


async def _read_upload_blobs(files) -> tuple[dict[str, bytes], int]:
    """把上传流读成 {文件名: 字节}，并返回无文件名的 part 数量。

    分派需要先看文件名才知道走归档还是 legacy，而流只能读一次，所以统一先落盘到内存。
    无文件名的 part 计数后透传给 legacy 出告警（multipart 里 filename 为空实际会被
    FastAPI 先判 422，这个分支只在服务层直调时可达）。
    """
    blobs: dict[str, bytes] = {}
    unnamed = 0
    for file in files:
        if not file.filename:
            unnamed += 1
            continue
        blobs[file.filename] = await file.read()
    return blobs, unnamed


#: ZIP 文件头魔数（空包/分卷变体一并识别，避免只认 PK\x03\x04 漏掉合法空 ZIP）
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _expand_zip_blobs(
    blobs: dict[str, bytes], warnings: list[str],
) -> dict[str, bytes]:
    """把整包 ZIP part 自动解包进 blobs（v2.22.3 §六·补七 归档回导缺口）。

    导出产出 ZIP 归档，用户/前端可能把整个 ZIP 当单个文件上传 —— 此前会被
    legacy 路径当「非 CSV 文件」跳过，造成 **201 + 0 表的静默空导入**。

    - 识别：文件名 .zip 后缀，或内容以 ZIP 魔数开头（前端可改名不改内容）。
    - 坏 ZIP → 400（带文件名）：坏了就明说，不猜、不静默跳过。
    - 内层条目按 basename 展平（用户打包整个文件夹也能回导）；与已有 part
      同名 → 保留先者 + 告警（散装 part 先读入，优先级天然更高）。
    - 跳过目录条目与 `.` 开头的系统垃圾（.DS_Store / ._xxx / __MACOSX 内层）。
    - 只展开一层：归档内不再嵌套 ZIP（导出侧从不产出嵌套包）。
    - 解包后移除 ZIP part 本身，避免它再以「非 CSV 文件」或「未声明文件」
      的身份污染后续告警。
    """
    out = dict(blobs)
    for fname, data in list(blobs.items()):
        if not (fname.lower().endswith(".zip") or data[:4] in _ZIP_MAGICS):
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise HTTPException(400, f"{fname} 不是合法的 ZIP 文件")
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                if not base or base.startswith("."):
                    continue
                if base in out:
                    warnings.append(
                        f"ZIP 内 {base} 与已有上传文件重名，保留先者")
                    continue
                out[base] = zf.read(info)
        out.pop(fname, None)
    return out


def _validated_manifest(manifest) -> dict:
    """校验 marker manifest 的格式标识，坏了就是 400 —— 不能猜用户的归档是什么格式。"""
    if not isinstance(manifest, dict) or manifest.get("format") != _CSV_FORMAT:
        actual = (manifest.get("format") if isinstance(manifest, dict)
                  else type(manifest).__name__)
        raise HTTPException(400, f"{_CSV_MANIFEST_NAME} 的 format 应为 {_CSV_FORMAT}，实际 {actual}")
    version = manifest.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise HTTPException(400, f"{_CSV_MANIFEST_NAME} 缺少整数 version 字段")
    if version > _CSV_VERSION:
        raise HTTPException(
            400, f"CSV 归档版本 {version} 高于本服务支持的 {_CSV_VERSION}，请升级服务后再导入")
    return manifest


def _archive_rows(blobs: dict[str, bytes], manifest: dict, role: str, warnings: list[str]):
    """按 manifest.roles 定位并读取某个角色的 CSV 数据行。

    返回 None 表示该角色在归档里缺席（调用方决定降级还是报错）。
    """
    roles = manifest.get("roles")
    if not isinstance(roles, dict) or role not in roles:
        if role in _CSV_REQUIRED_ROLES:
            # 必需角色既没声明也没文件 → 归档不是有效的 v1 归档，不能静默建空标准
            raise HTTPException(400, f"CSV 归档缺少必需角色 {role}（manifest.roles 未声明）")
        return None
    filename = roles[role]
    if filename not in blobs:
        if role in _CSV_REQUIRED_ROLES:
            raise HTTPException(400, f"CSV 归档缺少必需角色 {role}（声明的文件 {filename} 不存在）")
        warnings.append(f"归档未提供角色 {role}（{filename}），按空处理")
        return None
    try:
        text = blobs[filename].decode(_CSV_ENCODING)
    except UnicodeDecodeError as exc:
        raise HTTPException(400, f"{filename} 不是合法 UTF-8: {exc}")
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] == _CSV_HEADERS[role]:
        rows = rows[1:]           # 去掉表头
    return rows


def _archive_declared_files(manifest: dict) -> set[str]:
    roles = manifest.get("roles")
    return set(roles.values()) if isinstance(roles, dict) else set()


def _archive_standard_meta(
    blobs: dict[str, bytes], manifest: dict, warnings: list[str],
) -> dict[str, str]:
    """读 `标准信息.csv` → {'name','description','category'}（缺项不出现）。

    归档里带着这个角色却没人消费 = 往返丢元信息。表单仍然优先：
    这里是**默认值来源**，不是权威值。
    """
    rows = _archive_rows(blobs, manifest, "standard", warnings)
    if not rows:
        return {}
    if len(rows) > 1:
        warnings.append(
            f"归档的 {_CSV_ROLES['standard']} 有 {len(rows)} 行数据，"
            f"标准信息是单行语义，仅取首行（{_csv_cell(rows[0], 0) or '空'}）")
    return {
        "name": _csv_cell(rows[0], 0),
        "description": _csv_cell(rows[0], 1),
        "category": _csv_cell(rows[0], 2),
    }


def _merge_standard_meta(
    form_name: str, form_description: str | None, form_category: str,
    meta: dict[str, str], warnings: list[str],
) -> tuple[str, str | None, str]:
    """把归档里的标准信息当默认值，表单显式值覆盖它。

    名称**永不**被归档覆盖：名称是导入目标解析键（update/upsert 靠它找标准），
    归档改名会让 update 打到一个用户没指定的记录上。冲突只告警。
    """
    archive_name = meta.get("name") or ""
    if archive_name and archive_name != form_name:
        warnings.append(
            f"归档 {_CSV_ROLES['standard']} 的标准名称为「{archive_name}」，"
            f"与导入名称「{form_name}」不一致，以导入名称为准")

    description = form_description if form_description is not None else (meta.get("description") or None)
    # category 的表单默认值是 'custom'，无法区分「显式传 custom」与「没传」。
    # 沿用本模块既有约定：非 'custom' 视为显式指定，否则回落到归档值。
    category = form_category if form_category != "custom" else (meta.get("category") or "custom")
    return form_name, description, category


async def _create_csv_children_from_archive(
    db, standard_id: str, manifest: dict, blobs: dict[str, bytes], api_key: ApiKey,
) -> tuple[int, int, list[str]]:
    """归档路径导入：按编码连接键重建全部结构，做到往返无损。

    顺序有讲究：
    1. 层组先全建，再回填 parent（父级编码可能指向后面才出现的行）；
    2. 字典先建，字典项才能挂上去；
    3. 字段-字典关联最后建 —— 字段和字典此时才都有 id。
    """
    warnings: list[str] = list(manifest.get("diagnostics") or [])

    # 必需角色：tables / fields，缺席即 400（归档结构非法，硬建空标准没有意义）
    for role in _CSV_REQUIRED_ROLES:
        _archive_rows(blobs, manifest, role, warnings)

    declared = _archive_declared_files(manifest) | {_CSV_MANIFEST_NAME}
    for filename in blobs:
        if filename not in declared:
            warnings.append(f"归档未声明该文件，已忽略: {filename}")

    # ── 层组 ──────────────────────────────────────────────
    group_rows = _archive_rows(blobs, manifest, "layer_groups", warnings)
    group_by_code: dict[str, StandardLayerGroup] = {}
    created_groups: list[tuple[StandardLayerGroup, list]] = []
    fallback_group = None

    def _fallback():
        """层组缺失或编码对不上时的兜底组：宁可落进兜底组，也不静默丢表。"""
        nonlocal fallback_group
        if fallback_group is None:
            fallback_group = StandardLayerGroup(
                id=str(_uuid.uuid4()), standard_id=standard_id,
                group_name="导入层组 - 未匹配层组编码", ordering=len(group_by_code))
            db.add(fallback_group)
        return fallback_group

    if group_rows is None:
        group = StandardLayerGroup(
            id=str(_uuid.uuid4()), standard_id=standard_id, group_name="导入层组", ordering=0)
        db.add(group)
        # 登记为兜底组：归档里所有表引用的层组编码都已无从解析，应全部落进这一个组，
        # 而不是每张表各建一个「未匹配层组」。
        fallback_group = group
        warnings.append("归档未提供 layer_groups，已合成默认层组「导入层组」")
    else:
        # 先建全部层组（父级编码可能指向后面才出现的行），建完再回填 parent_id。
        # 重复行直接跳过而不只是告警：DB 上有 uq_slg_std_parent_code / uq_slg_std_parent_name，
        # 插入重复行会撞约束把整个导入打成 500/409。跳过才符合「首次出现生效」。
        seen_codes: set[str] = set()
        seen_names: set[tuple[str, str]] = set()
        for idx, row in enumerate(group_rows):
            code = _csv_cell(row, 1)
            name = _csv_cell(row, 0) or code or f"层组{idx + 1}"
            parent_code = _csv_cell(row, 2)
            if code and code in seen_codes:
                warnings.append(f"层组编码重复: {code}（第 {idx + 1} 行），已按首次出现跳过")
                continue
            if (parent_code, name) in seen_names:
                warnings.append(f"层组 {name} 在同一父级下重复（第 {idx + 1} 行），已按首次出现跳过")
                continue
            seen_codes.add(code)
            seen_names.add((parent_code, name))
            group = StandardLayerGroup(
                id=str(_uuid.uuid4()), standard_id=standard_id,
                group_name=name, group_code=code,
                description=_csv_cell(row, 3) or None, ordering=idx)
            db.add(group)
            created_groups.append((group, row))
            if code:
                group_by_code[code] = group
        for group, row in created_groups:
            parent_code = _csv_cell(row, 2)
            if parent_code:
                parent = group_by_code.get(parent_code)
                if parent is not None:
                    group.parent_id = parent.id
                else:
                    warnings.append(f"层组 {_csv_cell(row, 1)} 的父级编码 {parent_code} 未声明，已按顶级层组处理")

    # ── 字典（含空字典）────────────────────────────────────
    dict_rows = _archive_rows(blobs, manifest, "dictionaries", warnings)
    dict_by_code: dict[str, StandardDictionary] = {}
    if dict_rows is not None:
        for idx, row in enumerate(dict_rows):
            code = _csv_cell(row, 0)
            if not code:
                warnings.append(f"字典第 {idx + 1} 行缺少字典编码，已跳过")
                continue
            dictionary = StandardDictionary(
                id=str(_uuid.uuid4()), standard_id=standard_id, api_key_id=api_key.id,
                dict_code=code, dict_name=_csv_cell(row, 1) or code,
                scope=_csv_cell(row, 2) or "standard",
                description=_csv_cell(row, 3) or None)
            db.add(dictionary)
            if code in dict_by_code:
                warnings.append(f"字典编码重复: {code}（第 {idx + 1} 行），字段绑定按首次出现生效")
            else:
                dict_by_code[code] = dictionary

    item_rows = _archive_rows(blobs, manifest, "dictionary_items", warnings)
    if item_rows is not None:
        # 行级去重：同字典重复条目码保首个 + 告警（uq_di_dict_code）
        seen_item_codes: dict[str, set[str]] = {}
        for idx, row in enumerate(item_rows):
            code = _csv_cell(row, 0)
            dictionary = dict_by_code.get(code)
            if dictionary is None:
                warnings.append(f"字典项第 {idx + 1} 行的字典编码 {code or '(空)'} 无对应字典，已跳过")
                continue
            item_code = _csv_cell(row, 1)
            item_guard = seen_item_codes.setdefault(code, set())
            if item_code in item_guard:
                warnings.append(
                    f"字典「{dictionary.dict_name}」条目码 '{item_code}' 重复，已保留首个、丢弃后续"
                )
                continue
            item_guard.add(item_code)
            db.add(DictionaryItem(
                id=str(_uuid.uuid4()), dict_id=dictionary.id,
                item_code=item_code, item_name=_csv_cell(row, 2),
                parent_code=_csv_cell(row, 3) or None,
                description=_csv_cell(row, 4) or None,
                ordering=_csv_int(row, 5) if _csv_int(row, 5) is not None else idx))

    # ── 表 ────────────────────────────────────────────────
    table_rows = _archive_rows(blobs, manifest, "tables", warnings) or []
    table_by_code: dict[str, StandardTable] = {}
    tables_count = 0
    # 与层组同理：uq_st_layer_name / uq_st_layer_code 都在 DB 上，重复行必须跳过。
    # 去重键用**实际落到的层组 id**而不是层组编码：多张表编码对不上时会共用兜底层组，
    # 此时同名的表在 DB 眼里仍在同一层组内，必须去重。
    seen_table_codes: set[tuple[str, str]] = set()
    seen_table_names: set[tuple[str, str]] = set()
    for idx, row in enumerate(table_rows):
        group_code = _csv_cell(row, 0)
        group = group_by_code.get(group_code) or _fallback()
        if group_code and group_code not in group_by_code and group_rows is not None:
            warnings.append(f"表 {_csv_cell(row, 2) or _csv_cell(row, 1)} 的层组编码 {group_code} 未声明，"
                            f"已归入兜底层组「{group.group_name}」")
        code = _csv_cell(row, 2)
        name = _csv_cell(row, 1) or code or f"表{idx + 1}"
        if code and (group.id, code) in seen_table_codes:
            warnings.append(f"表编码重复: {code}（第 {idx + 1} 行），已按首次出现跳过")
            continue
        if (group.id, name) in seen_table_names:
            warnings.append(f"表 {name} 在同一层组下重复（第 {idx + 1} 行），已按首次出现跳过")
            continue
        seen_table_codes.add((group.id, code))
        seen_table_names.add((group.id, name))
        table = StandardTable(
            id=str(_uuid.uuid4()), standard_id=standard_id, layer_id=group.id,
            table_name=name, table_code=code, table_type=_csv_cell(row, 3) or "attribute",
            geometry_type=_csv_cell(row, 4) or None,
            description=_csv_cell(row, 5) or None, ordering=idx)
        db.add(table)
        db.add(C._primary_table_link(table))
        if code:
            table_by_code.setdefault(code, table)
        tables_count += 1

    # ── 字段 + 绑定 ────────────────────────────────────────
    field_rows = _archive_rows(blobs, manifest, "fields", warnings) or []
    fields_count = 0
    pending_links: list[tuple[StandardField, StandardDictionary, bool, int]] = []
    # 行级去重：同表重复字段名保首个 + 告警（与三格式统一契约；
    # 手工改归档 CSV 很容易复制出行，撞 uq_sf_table_field 不该整单失败）
    seen_field_names: dict[str, set[str]] = {}
    for idx, row in enumerate(field_rows):
        table_code = _csv_cell(row, 0)
        table = table_by_code.get(table_code)
        if table is None:
            warnings.append(f"字段 {_csv_cell(row, 1) or '(空)'} 的表编码 "
                            f"{table_code or '(空)'} 无对应表，已跳过")
            continue
        field_name = _csv_cell(row, 1)
        dup_guard = seen_field_names.setdefault(table_code, set())
        if field_name in dup_guard:
            warnings.append(
                f"表 '{table.table_name}' 字段名 '{field_name}' 重复，已保留首个、丢弃后续"
            )
            continue
        dup_guard.add(field_name)
        field = StandardField(
            id=str(_uuid.uuid4()), standard_id=standard_id, table_id=table.id,
            field_name=field_name, field_alias=_csv_cell(row, 2) or None,
            data_type=_csv_cell(row, 3) or "string",
            length=_csv_int(row, 4), precision=_csv_int(row, 5),
            is_required=_csv_flag(row, 6), is_primary_key=_csv_flag(row, 7),
            description=_csv_cell(row, 8) or None,
            default_value=_csv_cell(row, 10) or None, ordering=idx)
        db.add(field)
        fields_count += 1
        for order, code in enumerate(_split_dict_codes(_csv_cell(row, 9))):
            dictionary = dict_by_code.get(code)
            if dictionary is None:
                warnings.append(f"字段 {field.field_name} 的字典编码 {code} 未在字典中声明，已忽略")
                continue
            pending_links.append((field, dictionary, order == 0, order))

    for field, dictionary, is_primary, order in pending_links:
        db.add(StandardFieldDictLink(
            id=str(_uuid.uuid4()), field_id=field.id, dict_id=dictionary.id,
            is_primary=is_primary, ordering=order))

    return tables_count, fields_count, warnings


# ══════════════════════════════════════════════════════════════════════════════
# 模板下载
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/data-standards-v2/template/json",
    summary="下载 JSON 导入模板",
)
async def download_json_template():
    """下载 JSON 格式的导入模板。"""
    template = {
        "code": "STD_CODE",
        "name": "数据标准模板",
        "category": "custom",
        "description": "这是一个数据标准导入模板",
        "layer_groups": [
            {
                "group_name": "基础地理要素",
                "group_code": "BG",
                "description": "基础地理控制点",
                "tables": [
                    {
                        "table_name": "控制点",
                        "table_code": "CTRL_PT",
                        "table_type": "spatial",
                        "geometry_type": "Point",
                        "fields": [
                            {"field_name": "pt_id", "field_alias": "点号", "data_type": "string", "length": 20, "nullable": False, "is_primary_key": True},
                            {"field_name": "pt_name", "field_alias": "点名", "data_type": "string", "length": 50, "nullable": False},
                            {"field_name": "pt_type", "field_alias": "点类型", "data_type": "string", "length": 10, "nullable": False,
                             "dict_codes": ["CTRL_TYPE"]},
                        ]
                    }
                ],
                "children": []
            }
        ],
        "dictionaries": [
            {
                "dict_name": "控制点类型",
                "dict_code": "CTRL_TYPE",
                "scope": "standard",
                "items": [
                    {"item_code": "GPS", "item_name": "GPS控制点"},
                    {"item_code": "TRI", "item_name": "三角点"},
                    {"item_code": "LVL", "item_name": "水准点"},
                ]
            }
        ]
    }

    content = json.dumps(template, ensure_ascii=False, indent=2)
    return StreamingResponse(
        io.BytesIO(content.encode('utf-8')),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=standard_template.json"},
    )


@router.get(
    "/data-standards-v2/template/excel",
    summary="下载 Excel 导入模板",
)
async def download_excel_template():
    """下载 Excel 格式的导入模板。"""
    import openpyxl
    wb = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "标准信息"
    ws1.append(["字段名", "值"])
    ws1.append(["标准名称", "示例标准"])
    ws1.append(["标准编码", "STD_CODE"])
    ws1.append(["分类", "custom"])
    ws1.append(["描述", "标准描述"])

    ws2 = wb.create_sheet("层组定义")
    ws2.append(["层组名", "层组编码", "父级编码", "描述"])
    ws2.append(["基础地理要素", "BG", "", "基础地理控制点"])

    ws3 = wb.create_sheet("数据表定义")
    ws3.append(["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"])
    ws3.append(["BG", "控制点", "CTRL_PT", "spatial", "Point", "各级控制点"])

    ws4 = wb.create_sheet("字段定义")
    ws4.append(["表编码", "字段名", "中文名", "数据类型", "长度",
                "精度", "必填", "主键", "描述", "字典编码"])
    ws4.append(["CTRL_PT", "pt_id", "点号", "string", "20", "", "是", "是", "唯一标识", ""])
    ws4.append(["CTRL_PT", "pt_name", "点名", "string", "50", "", "是", "", "控制点名称", ""])
    ws4.append(["CTRL_PT", "pt_type", "点类型", "string", "10", "", "是", "", "",
                "PT_TYPE,PT_LEVEL"])   # 多个字典编码用逗号分隔；首个即主字典
    ws4.append(["", "", "", "", "", "", "", "", "",
                "编码须与「字典定义」Sheet 的字典编码一致；多个用逗号分隔，顺序即主次。"])

    ws5 = wb.create_sheet("字典定义")
    ws5.append(["字典编码", "字典名称", "适用范围", "字典项编码", "字典项名称", "描述"])
    ws5.append(["PT_TYPE", "点类型", "standard", "A", "平面点", "点类型字典"])
    ws5.append(["PT_TYPE", "点类型", "standard", "B", "高程点", ""])
    ws5.append(["PT_LEVEL", "点等级", "standard", "1", "一级", ""])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=standard_template.xlsx"},
    )


def _field_dict_codes(field) -> list[str]:
    """字段绑定的字典编码，顺序即主次（与 Excel / JSON 导出同构）。"""
    return [
        link.dictionary.dict_code
        for link in sorted(field.dict_links or [], key=lambda l: l.ordering)
        if link.dictionary and link.dictionary.dict_code
    ]


def _duplicate_codes(codes: list[str]) -> list[str]:
    """返回重复出现的编码（去重、保序）。

    编码在归档里是连接键，重复会让「字段 → 表」「字段 → 字典」指向不确定。
    导出侧无法在二进制 ZIP 之外传告警，所以写进 manifest.diagnostics 供导入期回放。
    """
    seen, dupes = set(), []
    for code in codes:
        if not code:
            continue
        if code in seen and code not in dupes:
            dupes.append(code)
        seen.add(code)
    return dupes


def _manifest_bytes(roles: dict[str, str], diagnostics: list[str] | None = None) -> bytes:
    """序列化 marker manifest。刻意只含格式元信息，不含任何结构数据。"""
    manifest = {
        "format": _CSV_FORMAT,
        "version": _CSV_VERSION,
        "encoding": _CSV_ENCODING,
        "generator": "VCS 空间质检 2.22.3",
        "roles": dict(roles),
        "diagnostics": list(diagnostics or []),
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


def _build_csv_archive(std) -> io.BytesIO:
    """构建 CSV 归档 ZIP：manifest + 6 个规范化角色 CSV。

    编码是唯一的连接键（层组编码 / 表编码 / 字典编码），行顺序即 ordering ——
    因此归档不依赖任何服务端 id，导到别的环境再导回来结构不变。
    """
    groups = _sorted_by_ordering(std.layer_groups)
    code_by_group_id = {g.id: (g.group_code or "") for g in groups}

    group_rows = [_CSV_HEADERS["layer_groups"]]
    table_rows = [_CSV_HEADERS["tables"]]
    field_rows = [_CSV_HEADERS["fields"]]
    all_table_codes: list[str] = []

    for group in groups:
        parent_code = code_by_group_id.get(group.parent_id, "") if group.parent_id else ""
        group_rows.append([group.group_name, group.group_code or "",
                           parent_code, group.description or ""])
        for table in _sorted_by_ordering(group.tables):
            all_table_codes.append(table.table_code or "")
            table_rows.append([group.group_code or "", table.table_name,
                               table.table_code or "", table.table_type or "attribute",
                               table.geometry_type or "", table.description or ""])
            for field in _sorted_by_ordering(table.fields):
                field_rows.append([
                    table.table_code or "", field.field_name, field.field_alias or "",
                    field.data_type or "string",
                    "" if field.length is None else field.length,
                    "" if field.precision is None else field.precision,
                    "是" if field.is_required else "", "是" if field.is_primary_key else "",
                    field.description or "", " ".join(_field_dict_codes(field)),
                    field.default_value or "",
                ])

    # 字典按编码排序导出：StandardDictionary 没有 ordering 列，而字典项挂在
    # dict_code 之下，稳定的字典顺序让 CSV 在版本间 diff 稳定。
    dictionaries = sorted(std.standard_dictionaries or [],
                          key=lambda d: (d.dict_code or "", d.dict_name or ""))
    dict_rows = [_CSV_HEADERS["dictionaries"]]
    item_rows = [_CSV_HEADERS["dictionary_items"]]
    for dictionary in dictionaries:
        dict_rows.append([dictionary.dict_code or "", dictionary.dict_name,
                          dictionary.scope or "", dictionary.description or ""])
        # 不加 `if d.items` —— 空字典必须出现在归档里，否则往返会悄悄丢掉它
        for item in _sorted_by_ordering(dictionary.items or []):
            item_rows.append([dictionary.dict_code or "", item.item_code, item.item_name,
                              item.parent_code or "", item.description or "",
                              "" if item.ordering is None else item.ordering])

    diagnostics: list[str] = []
    for label, dupes in (
        ("层组编码", _duplicate_codes([g.group_code or "" for g in groups])),
        ("表编码", _duplicate_codes(all_table_codes)),
        ("字典编码", _duplicate_codes([d.dict_code or "" for d in dictionaries])),
    ):
        for code in dupes:
            diagnostics.append(f"{label}重复: {code}，字段/绑定导入时按首次出现生效")

    payloads = {
        # 表头 + 单行数据（DataStandard 的 name/description/category 也要能往返）
        "standard": [_CSV_HEADERS["standard"],
                     [std.name or "", std.description or "", std.category or ""]],
        "layer_groups": group_rows,
        "tables": table_rows,
        "fields": field_rows,
        "dictionaries": dict_rows,
        "dictionary_items": item_rows,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for role, filename in _CSV_ROLES.items():
            zf.writestr(filename, _csv_bytes(payloads[role]))
        zf.writestr(_CSV_MANIFEST_NAME, _manifest_bytes(_CSV_ROLES, diagnostics))
    buffer.seek(0)
    return buffer


@router.get(
    "/data-standards-v2/template/csv",
    summary="下载 CSV 导入模板",
)
async def download_csv_template():
    """下载 CSV 归档模板（ZIP）。

    模板 = 空表头的 6 个角色 CSV + manifest + 每角色一行自洽示例。
    示例行刻意做成一条完整链路（层组 → 表 → 字段 → 字典 → 字典项 + 绑定），
    这样新作者能一眼看出编码该怎么互相引用，也能直接导入验证。
    """
    samples = {
        "standard": [["城市道路检查标准", "示例描述：改完直接导入即可", "custom"]],
        "layer_groups": [["道路设施", "RD", "", "道路相关层组"]],
        "tables": [["RD", "路面", "SURF", "attribute", "polygon", "路面要素表"]],
        "fields": [["SURF", "mat", "材料", "string", "32", "", "是", "",
                    "面层材料", "MAT", "沥青"]],
        "dictionaries": [["MAT", "面层材料", "standard", "路面材料字典"]],
        "dictionary_items": [["MAT", "AC", "沥青混凝土", "", "", "0"]],
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for role, filename in _CSV_ROLES.items():
            zf.writestr(filename, _csv_bytes([_CSV_HEADERS[role], *samples[role]]))
        zf.writestr(_CSV_MANIFEST_NAME, _manifest_bytes(_CSV_ROLES))

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=standard_templates.zip"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# 导出
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/data-standards-v2/{standard_id}/export",
    summary="导出数据标准",
)
async def export_standard(
    standard_id: str,
    format: str = Query("json", description="导出格式: json/excel/csv"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """导出数据标准为指定格式。"""
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(404, "标准不存在")

    q = select(DataStandard).where(DataStandard.id == standard_id).options(
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.children),
        selectinload(DataStandard.layer_groups).selectinload(StandardLayerGroup.tables)
            .selectinload(StandardTable.fields)
            # v2.22.3：导出需带出字段的字典编码，必须显式预加载 dict_links
            # 及其 dictionary —— async 下隐式 selectin 会抛 MissingGreenlet。
            .selectinload(StandardField.dict_links)
            .selectinload(StandardFieldDictLink.dictionary),
        selectinload(DataStandard.standard_dictionaries).selectinload(StandardDictionary.items),
    )
    result = await db.execute(q)
    std = result.scalar_one()

    if format == "json":
        export_data = {
            "code": std.code,
            "name": std.name,
            "category": std.category,
            "description": std.description,
            "layer_groups": [
                _export_layer(lg) for lg in std.layer_groups if lg.parent_id is None
            ],
            "dictionaries": [
                {
                    "dict_name": d.dict_name,
                    "dict_code": d.dict_code,
                    "scope": d.scope,
                    "items": [{"item_code": i.item_code, "item_name": i.item_name} for i in d.items],
                }
                for d in std.standard_dictionaries
            ],
        }
        content = json.dumps(export_data, ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.BytesIO(content.encode('utf-8')),
            media_type="application/json",
            headers={"Content-Disposition": _content_disposition(f"{std.name}.json")},
        )
    elif format == "excel":
        import openpyxl
        wb = openpyxl.Workbook()

        ws = wb.active
        ws.title = "标准信息"
        ws.append(["字段名", "值"])
        ws.append(["标准名称", std.name])
        ws.append(["标准编码", std.code or ""])
        ws.append(["分类", std.category])
        ws.append(["描述", std.description or ""])

        group_code_by_id: dict[str, str] = {}
        for lg in std.layer_groups:
            group_code_by_id[lg.id] = lg.group_code or ""

        ws2 = wb.create_sheet("层组定义")
        ws2.append(["层组名", "层组编码", "父级编码", "描述"])
        for lg in std.layer_groups:
            parent_code = group_code_by_id.get(str(lg.parent_id), "") if lg.parent_id else ""
            ws2.append([lg.group_name, lg.group_code or "", parent_code, lg.description or ""])

        ws3 = wb.create_sheet("数据表定义")
        ws3.append(["层组编码", "表名", "表编码", "表类型", "几何类型", "描述"])
        for lg in std.layer_groups:
            for t in lg.tables:
                ws3.append([lg.group_code or "", t.table_name, t.table_code or "",
                            t.table_type, t.geometry_type or "", t.description or ""])

        ws4 = wb.create_sheet("字段定义")
        ws4.append(["表编码", "字段名", "中文名", "数据类型", "长度", "精度",
                    "必填", "主键", "描述", "字典编码"])
        for lg in std.layer_groups:
            for t in lg.tables:
                for f in t.fields:
                    # 字典编码（v2.22.3）：逗号分隔多值，顺序即主次，与导入侧对称。
                    codes = [
                        link.dictionary.dict_code
                        for link in sorted(f.dict_links or [], key=lambda l: l.ordering)
                        if link.dictionary and link.dictionary.dict_code
                    ]
                    ws4.append([t.table_code or "", f.field_name, f.field_alias or "", f.data_type,
                                f.length or "", f.precision or "", "是" if f.is_required else "",
                                "是" if f.is_primary_key else "", f.description or "",
                                ",".join(codes)])

        ws5 = wb.create_sheet("字典定义")
        ws5.append(["字典编码", "字典名称", "适用范围", "字典项编码", "字典项名称", "描述"])
        for d in std.standard_dictionaries:
            if d.items:
                for item in d.items:
                    ws5.append([d.dict_code or "", d.dict_name, d.scope or "standard",
                                item.item_code, item.item_name, d.description or ""])
            else:
                ws5.append([d.dict_code or "", d.dict_name, d.scope or "standard", "", "", d.description or ""])

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": _content_disposition(f"{std.name}.xlsx")},
        )

    elif format == "csv":
        # v2.22.3 归档形态：manifest + 6 个规范化 CSV，与 template/csv（ZIP）、
        # import/csv（files: list）三方对称，且与 Excel/JSON 一样可无损往返
        # （层组层级 / 表编码 / 字段类型 / 字典 / 字典项 / 字段-字典绑定）。
        return StreamingResponse(
            _build_csv_archive(std),
            media_type="application/zip",
            headers={"Content-Disposition": _content_disposition(f"{std.name}.zip")},
        )

    else:
        raise HTTPException(400, f"不支持的导出格式: {format}")


# ── 导出辅助函数 ──────────────────────────────────────────────────────────────

def _content_disposition(filename: str) -> str:
    """生成安全的 Content-Disposition 头（支持中文文件名）。"""
    ascii_name = filename.encode("ascii", errors="ignore").decode("ascii").strip()
    if not ascii_name:
        ascii_name = "standard_export"
    quoted = urllib.parse.quote(filename)
    return f"attachment; filename={ascii_name}; filename*=UTF-8''{quoted}"


def _export_layer(layer) -> dict:
    """递归导出层组。"""
    return {
        "group_name": layer.group_name,
        "group_code": layer.group_code,
        "description": layer.description,
        "tables": [
            {
                "table_name": t.table_name,
                "table_code": t.table_code,
                "table_type": t.table_type,
                "geometry_type": t.geometry_type,
                "fields": [
                    {
                        "field_name": f.field_name,
                        "field_alias": f.field_alias,
                        "data_type": f.data_type,
                        "length": f.length,
                        "precision": f.precision,
                        "nullable": f.nullable,
                        "is_primary_key": f.is_primary_key,
                        "is_required": f.is_required,
                        # 字典绑定（v2.22.3）—— 按 ordering 还原主次顺序，
                        # 首个即主字典，与导入侧 _split_dict_codes 语义对称。
                        "dict_codes": [
                            link.dictionary.dict_code
                            for link in sorted(f.dict_links or [], key=lambda l: l.ordering)
                            if link.dictionary and link.dictionary.dict_code
                        ],
                    }
                    for f in t.fields
                ],
            }
            for t in layer.tables
        ],
        "children": [_export_layer(c) for c in layer.children],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 自动创建标准表（从上传文件的 field_schema）
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/data-standards-v2/{standard_id}/auto-create-table",
    summary="根据文件结构自动创建标准表（Phase 3）",
)
async def auto_create_table_from_file(
    standard_id: str,
    file_id: str = Form(..., description="上传文件 ID（用于读取 field_schema）"),
    table_name: str = Form(..., description="标准表名称"),
    layer_group_name: Optional[str] = Form(None, description="层组名称（默认使用文件名）"),
    geometry_type: Optional[str] = Form(None, description="几何类型 Point/LineString/Polygon"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """根据上传文件的 field_schema 自动创建标准层组 + 标准表 + 标准字段。"""
    from app.models.stored_file import StoredFile
    from app.services.structure_analyzer import auto_create_standard_from_schema

    sf_result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = sf_result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")

    if not sf.field_schema:
        raise HTTPException(
            status_code=400,
            detail="文件没有字段结构信息（field_schema），可能格式不支持自动检测",
        )

    try:
        result = await auto_create_standard_from_schema(
            db=db,
            standard_id=standard_id,
            layer_group_name=layer_group_name or Path(sf.original_name).stem,
            table_name=table_name,
            field_schema=sf.field_schema,
            geometry_type=geometry_type,
            description=f"从文件 {sf.original_name} 自动创建",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    return {
        "message": f"标准表创建成功",
        "layer_group_id": result["layer_group_id"],
        "layer_group_name": result["layer_group_name"],
        "table_id": result["table_id"],
        "table_name": result["table_name"],
        "field_count": result["field_count"],
        "file_id": file_id,
    }