"""数据标准管理 API — 导入 Excel/JSON 标准定义、查询、删除。"""

import uuid
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import authenticated
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.data_standard import DataStandard, DataStandardField, DataStandardSheet
from app.models.source_data import SourceTable, SourceField, StandardVersion
from app.schemas.data_standards import (
    DataStandardBrief, DataStandardDetail, DataStandardFieldResponse,
    DataStandardSheetResponse,
)
from app.services.data_standard_parser import parse_standard_excel

router = APIRouter()


_TEMPLATE_PATH = Path("static/templates/data_standard_template.xlsx")


@router.get("/data-standards/template", summary="下载数据标准导入模板（Excel）")
async def download_standard_template(
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """下载预填表头的数据标准 Excel 模板。"""
    if not _TEMPLATE_PATH.exists():
        raise HTTPException(status_code=404, detail="模板文件不存在")
    return FileResponse(
        str(_TEMPLATE_PATH),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="数据标准模板.xlsx",
    )


@router.post(
    "/data-standards/import",
    response_model=DataStandardDetail,
    status_code=status.HTTP_201_CREATED,
    summary="导入数据标准定义（Excel .xlsx）",
)
async def import_data_standard(
    file: UploadFile = File(..., description="数据标准 Excel 文件"),
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    if not file.filename or not file.filename.lower().endswith('.xlsx'):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 格式")
    if ".." in file.filename or "/" in file.filename or "\\" in file.filename:
        raise HTTPException(status_code=400, detail="文件名非法")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="标准文件超过 10 MB 限制")

    upload_dir = Path(settings.upload_dir) / "data_standards" / str(uuid.uuid4())
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = str(upload_dir / file.filename)
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)

    try:
        parsed = parse_standard_excel(file_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Excel 解析失败: {e}")

    if not parsed.sheets:
        raise HTTPException(status_code=400, detail="Excel 中未找到有效的字段定义")

    std_name = name or Path(file.filename).stem
    standard = DataStandard(
        id=str(uuid.uuid4()),
        api_key_id=api_key.id,
        name=std_name,
        description=description,
        file_name=file.filename,
        sheet_count=len(parsed.sheets),
    )
    db.add(standard)

    sheets_response = []
    for s_idx, sd in enumerate(parsed.sheets):
        sheet = DataStandardSheet(
            id=str(uuid.uuid4()),
            standard_id=standard.id,
            sheet_name=sd.sheet_name,
            ordering=sd.ordering,
        )
        db.add(sheet)

        # 同时创建 SourceTable (源数据表)
        source_table = SourceTable(
            id=str(uuid.uuid4()),
            api_key_id=api_key.id,
            standard_id=standard.id,
            table_name=sd.sheet_name,
            table_code=None,
            description=f"从标准 '{std_name}' 导入",
            source=std_name,
            version="v1",
            is_active=True,
            ordering=s_idx,
        )
        db.add(source_table)

        fields_response = []
        for fd in sd.fields:
            field = DataStandardField(
                id=str(uuid.uuid4()),
                sheet_id=sheet.id,
                field_name=fd.field_name,
                field_type=fd.field_type,
                length=fd.length,
                nullable=fd.nullable,
                allowed_values=fd.allowed_values,
                description=fd.description,
                ordering=fd.ordering,
            )
            db.add(field)
            fields_response.append(DataStandardFieldResponse(
                id=field.id, field_name=fd.field_name, field_type=fd.field_type,
                length=fd.length, nullable=fd.nullable,
                allowed_values=fd.allowed_values, description=fd.description,
                ordering=fd.ordering,
            ))

            # 同时创建 SourceField (源数据字段)
            source_field = SourceField(
                id=str(uuid.uuid4()),
                table_id=source_table.id,
                field_name=fd.field_name,
                field_alias=fd.field_name,
                data_type=fd.field_type or "string",
                length=fd.length,
                nullable=fd.nullable,
                description=fd.description,
                is_required=not fd.nullable,
                ordering=fd.ordering,
            )
            db.add(source_field)

        sheets_response.append(DataStandardSheetResponse(
            id=sheet.id, sheet_name=sd.sheet_name,
            ordering=sd.ordering, fields=fields_response,
        ))

    # 保存 Excel 文件到标准版本
    std_version = StandardVersion(
        id=str(uuid.uuid4()),
        standard_id=standard.id,
        api_key_id=api_key.id,
        version_label="v1",
        change_log="初始导入",
        file_data=content,
        file_name=file.filename,
    )
    db.add(std_version)

    await db.commit()
    return DataStandardDetail(
        id=standard.id, name=standard.name, description=standard.description,
        file_name=standard.file_name, sheet_count=standard.sheet_count,
        sheets=sheets_response, created_at=standard.created_at,
    )


@router.get("/data-standards", response_model=list[DataStandardBrief], summary="查询数据标准列表")
async def list_data_standards(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(DataStandard)
        .where(api_key.scope_clause(DataStandard))
        .order_by(DataStandard.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return result.scalars().all()


@router.get("/data-standards/{standard_id}", response_model=DataStandardDetail, summary="查询数据标准详情（含字段定义）")
async def get_data_standard(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(status_code=404, detail="标准不存在或无权限访问")

    sheets_r = await db.execute(
        select(DataStandardSheet)
        .where(DataStandardSheet.standard_id == standard_id)
        .order_by(DataStandardSheet.ordering)
    )
    sheets = sheets_r.scalars().all()

    sheet_responses = []
    for sheet in sheets:
        fields_r = await db.execute(
            select(DataStandardField)
            .where(DataStandardField.sheet_id == sheet.id)
            .order_by(DataStandardField.ordering)
        )
        fields = fields_r.scalars().all()
        sheet_responses.append(DataStandardSheetResponse(
            id=sheet.id, sheet_name=sheet.sheet_name, ordering=sheet.ordering,
            fields=[DataStandardFieldResponse(
                id=f.id, field_name=f.field_name, field_type=f.field_type,
                length=f.length, nullable=f.nullable,
                allowed_values=f.allowed_values, description=f.description,
                ordering=f.ordering,
            ) for f in fields],
        ))

    return DataStandardDetail(
        id=std.id, name=std.name, description=std.description,
        file_name=std.file_name, sheet_count=std.sheet_count,
        sheets=sheet_responses, created_at=std.created_at,
    )


@router.delete("/data-standards/{standard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_standard(
    standard_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    std = await db.get(DataStandard, standard_id)
    if not std or not api_key.owns(std):
        raise HTTPException(status_code=404, detail="标准不存在或无权限访问")

    # 级联删除 sheets → fields
    from sqlalchemy import delete as sa_delete
    sheets_r = await db.execute(
        select(DataStandardSheet).where(DataStandardSheet.standard_id == standard_id)
    )
    for sheet in sheets_r.scalars().all():
        await db.execute(
            sa_delete(DataStandardField).where(DataStandardField.sheet_id == sheet.id)
        )
        await db.delete(sheet)

    await db.delete(std)
    await db.commit()