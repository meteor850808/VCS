"""结构分析器 — 从上传文件的 field_schema 自动生成标准表定义。

Phase 3: 支持自动创建标准层组 + 标准表 + 标准字段。
"""

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_standard import DataStandard
from app.models.standard_field import StandardField
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable

logger = logging.getLogger(__name__)

# 类型映射：format_detection 的类型 → 标准字段类型
TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "float": "float",
    "date": "date",
    "geometry": "geometry",
}


async def auto_create_standard_from_schema(
    db: AsyncSession,
    standard_id: str,
    layer_group_name: str,
    table_name: str,
    field_schema: list[dict],
    geometry_type: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """根据 field_schema 自动创建标准层组 + 标准表 + 标准字段。

    Args:
        db: 数据库会话
        standard_id: 数据标准 ID
        layer_group_name: 层组名称（如文件名或图层名）
        table_name: 标准表名称
        field_schema: 字段结构 [{"name": "xxx", "type": "string", "nullable": true, "sample": "..."}]
        geometry_type: 几何类型 (Point/LineString/Polygon/null)
        description: 描述

    Returns:
        {"layer_group_id": ..., "table_id": ..., "field_count": ...}
    """
    # 验证标准存在
    ds = await db.get(DataStandard, standard_id)
    if not ds:
        raise ValueError(f"数据标准 {standard_id} 不存在")

    # 创建层组
    layer_group = StandardLayerGroup(
        id=str(uuid.uuid4()),
        standard_id=standard_id,
        group_name=layer_group_name,
        group_code=_to_code(layer_group_name),
        description=description or f"从上传文件自动创建的层组: {layer_group_name}",
        ordering=0,
    )
    db.add(layer_group)
    await db.flush()

    # 创建标准表
    std_table = StandardTable(
        id=str(uuid.uuid4()),
        layer_id=layer_group.id,
        standard_id=standard_id,
        table_name=table_name,
        table_code=_to_code(table_name),
        table_type="spatial" if geometry_type else "attribute",
        geometry_type=geometry_type,
        description=description or f"从上传文件自动创建的标准表: {table_name}",
        ordering=0,
    )
    db.add(std_table)
    await db.flush()

    # 创建标准字段
    field_count = 0
    for i, fs in enumerate(field_schema):
        fname = fs.get("name", f"field_{i+1}")
        ftype = TYPE_MAP.get(fs.get("type", "string"), "string")
        nullable = fs.get("nullable", True)

        std_field = StandardField(
            id=str(uuid.uuid4()),
            table_id=std_table.id,
            standard_id=standard_id,
            field_name=fname,
            field_alias=fname,
            data_type=ftype,
            nullable=nullable,
            is_primary_key=(i == 0),  # 第一个字段默认为主键
            is_required=(not nullable),
            ordering=i,
        )
        db.add(std_field)
        field_count += 1

    await db.flush()

    logger.info(
        f"自动创建标准表完成: 标准={standard_id}, "
        f"层组={layer_group.id}, 表={std_table.id}, 字段数={field_count}"
    )

    return {
        "layer_group_id": layer_group.id,
        "layer_group_name": layer_group_name,
        "table_id": std_table.id,
        "table_name": table_name,
        "field_count": field_count,
    }


def _to_code(name: str) -> str:
    """将中文名称转换为英文代码（简单拼音首字母或直接使用）。"""
    import re
    # 如果已经是英文，直接使用
    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        return name.lower().replace(" ", "_")
    # 中文名称：取首字拼音（简化处理，使用 hash 作为 code）
    import hashlib
    h = hashlib.md5(name.encode()).hexdigest()[:8]
    return f"auto_{h}"
