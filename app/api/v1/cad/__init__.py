"""CAD 路由子包 — 聚合所有 CAD 子模块路由。"""
from fastapi import APIRouter

from .info import router as info_router
from .layers import router as layers_router
from .entities import router as entities_router
from .preview import router as preview_router

router = APIRouter()

router.include_router(info_router, tags=["CAD-信息"])
router.include_router(layers_router, tags=["CAD-图层"])
router.include_router(entities_router, tags=["CAD-实体"])
router.include_router(preview_router, tags=["CAD-预览"])