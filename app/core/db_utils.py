"""数据库查询工具函数。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


async def load_standard_fields(db: AsyncSession, standard_id: str, api_key_id: str) -> dict | None:
    """从数据库加载数据标准字段定义（一次性 JOIN 加载，避免 N+1）。

    Args:
        db: 异步数据库会话。
        standard_id: 数据标准 ID。
        api_key_id: API Key ID（用于权限校验）。

    Returns:
        {field_name: {field_type, length, nullable, allowed_values}} 或 None。
    """
    from app.models.data_standard import DataStandard, DataStandardField, DataStandardSheet
    std = await db.get(DataStandard, standard_id)
    if not std or std.api_key_id != api_key_id:
        return None

    # 一次性 JOIN 加载 sheets + fields，避免 N+1 查询
    sheets_r = await db.execute(
        select(DataStandardSheet)
        .where(DataStandardSheet.standard_id == standard_id)
        .options(selectinload(DataStandardSheet.fields))
        .order_by(DataStandardSheet.ordering)
    )
    fields = {}
    for sheet in sheets_r.scalars().all():
        for field in sheet.fields:
            fields[field.field_name] = {
                "field_type": field.field_type,
                "length": field.length,
                "nullable": field.nullable,
                "allowed_values": field.allowed_values,
            }
    return fields
