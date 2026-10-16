"""API v1 路由聚合。"""

from fastapi import APIRouter
from app.api.v1.convert import router as convert_router
from app.api.v1.admin import router as admin_router
from app.api.v1.batch import router as batch_router
from app.api.v1.quality import router as quality_router
from app.api.v1.ws import router as ws_router
from app.api.v1.data import router as data_router
from app.api.v1.data_standards import router as data_standards_router
from app.api.v1.spatial import router as spatial_router
from app.api.v1.quality_rules import router as quality_rules_router
from app.api.v1.source_data import router as source_data_router
from app.api.v1.data_standard_v2 import router as data_standard_v2_router
from app.api.v1.quality_plans import router as quality_plans_router
from app.api.v1.cad import router as cad_router

router = APIRouter(prefix="/api/v1")
router.include_router(convert_router)
router.include_router(admin_router)
router.include_router(batch_router)
router.include_router(quality_router)
router.include_router(ws_router)
router.include_router(data_router)
router.include_router(data_standards_router)
router.include_router(spatial_router)
router.include_router(quality_rules_router)
# v2 路由必须在 source_data 之前注册，否则旧路由会拦截同名路径
router.include_router(data_standard_v2_router)
router.include_router(source_data_router)
# v2.10 质检方案 CRUD
router.include_router(quality_plans_router)
# v2.18 CAD 功能子包
router.include_router(cad_router)
