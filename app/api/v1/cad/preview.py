"""CAD — 预览图（SVG/PNG，从磁盘缓存返回）。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/preview", summary="CAD 预览图")
async def get_cad_preview(
    file_id: str,
    format: str = Query("svg", description="svg/png"),
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    if format not in ("svg", "png"):
        raise HTTPException(status_code=400, detail="format 仅支持 svg/png")
    sf = await C._load_cad_file(file_id, db, auth)
    p = C._preview_file(sf, format)
    if p is None:
        raise HTTPException(
            status_code=404,
            detail=f"{format.upper()} 预览文件不存在（可能上传时生成失败）",
        )
    media_type = "image/svg+xml" if format == "svg" else "image/png"
    return Response(content=p.read_bytes(), media_type=media_type)