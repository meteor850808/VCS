"""CAD — 图层树。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.deps import authenticated
from app.schemas.cad import CADLayersResponse, CADLayerResponse

from . import _common as C

router = APIRouter()


@router.get("/cad/{file_id}/layers", response_model=CADLayersResponse,
            summary="CAD 图层树")
async def get_cad_layers(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    auth=Depends(authenticated),
):
    sf = await C._load_cad_file(file_id, db, auth)
    meta = C._cad_metadata(sf)
    layers = meta.get("layers", [])
    return CADLayersResponse(layers=[
        CADLayerResponse(**layer) for layer in layers
    ])