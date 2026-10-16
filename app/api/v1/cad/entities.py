"""CAD — 实体列表（分页）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.schemas.cad import CADEntityPageResponse, CADEntityResponse

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/entities", response_model=CADEntityPageResponse,
            summary="CAD 实体列表")
async def get_cad_entities(
    file_id: str,
    layer: str | None = Query(None, description="图层名过滤"),
    entity_type: str | None = Query(None, description="实体类型过滤"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    sf = await C._load_cad_file(file_id, db, auth)
    meta = C._cad_metadata(sf)
    entities = meta.get("entities", [])

    # 内存过滤（缓存量 ≤ 2000，可靠且简单）
    filtered = entities
    if layer:
        filtered = [e for e in filtered if e.get("layer") == layer]
    if entity_type:
        filtered = [e for e in filtered
                    if e.get("entity_type") == entity_type]

    total = len(filtered)
    page_items = filtered[offset:offset + limit]
    return CADEntityPageResponse(
        total=total,
        offset=offset,
        limit=limit,
        entities=[CADEntityResponse(**e) for e in page_items],
    )