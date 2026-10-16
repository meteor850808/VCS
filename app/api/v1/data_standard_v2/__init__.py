"""数据标准 v2 路由包 — 聚合所有子模块路由，保持路径注册顺序。

注册顺序约束（FastAPI 按注册顺序匹配路由）：
  /data-standards-v2/stats          ← 静态路径必须在 /{standard_id} 之前
  /data-standards-v2/import/*       ← 同上
  /data-standards-v2/template/*     ← 同上
  /data-standards-v2/{standard_id}  ← 参数路径放最后
"""
from fastapi import APIRouter

from .standards import router as standards_router
from .layers import router as layers_router
from .tables import router as tables_router
from .fields import router as fields_router
from .dictionaries import router as dictionaries_router
from .field_links import router as field_links_router
from .versions import router as versions_router
from .graph import router as graph_router
from .import_export import router as import_export_router

router = APIRouter()

# ── 静态路径优先注册（避免被 /{standard_id} 拦截） ──

# import/export: 模板 + 导入 (全部静态路径)
router.include_router(import_export_router, tags=["导入导出"])

# ── 层组 / 表 / 字段 / 字典 CRUD ──

router.include_router(layers_router, tags=["层组管理"])
router.include_router(tables_router, tags=["数据表管理"])
router.include_router(fields_router, tags=["字段管理"])
router.include_router(dictionaries_router, tags=["字典管理"])
router.include_router(field_links_router, tags=["字段字典关联"])

# ── 版本管理 (含参数路径) ──

router.include_router(versions_router, tags=["版本管理"])

# ── 知识图谱纯视图 (参数路径，须在标准 CRUD 的 /{standard_id} 之前) ──

router.include_router(graph_router, tags=["知识图谱"])

# ── 标准 CRUD（stats 等静态路径在 {standard_id} 参数路径之前，内部已保证顺序） ──

router.include_router(standards_router, tags=["标准管理"])
