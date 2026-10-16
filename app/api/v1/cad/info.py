"""CAD — 文件元数据信息。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.schemas.cad import CADInfoResponse

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/info", response_model=CADInfoResponse,
            summary="CAD 文件元数据")
async def get_cad_info(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    sf = await C._load_cad_file(file_id, db, auth)
    meta = C._cad_metadata(sf)
    info = meta.get("info", {})
    return CADInfoResponse(
        file_id=file_id,
        format=(sf.file_format or "").upper(),
        version=info.get("version"),
        units=info.get("units"),
        extent_min=info.get("extent_min"),
        extent_max=info.get("extent_max"),
        entity_counts=info.get("entity_type_counts", {}),
        entity_total=info.get("total_entities", 0),
        layer_count=info.get("layer_count", 0),
        has_text=info.get("has_text", False),
        has_dimensions=info.get("has_dimensions", False),
        has_blocks=info.get("has_blocks", False),
        has_hatches=info.get("has_hatches", False),
        preview_url=f"/api/v1/cad/{file_id}/preview",
    )