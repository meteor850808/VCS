# -*- coding: utf-8 -*-
"""CAD 快照缓存 — 上传时解析 DXF/DWG 并缓存元数据/图层/实体/预览。

动机（架构约束）: 项目【优化#5】上传后清空 file_data（原始字节不落库），
download 返回 410。因此 CAD 信息无法在"查询时从 file_data 重新解析"，
必须在**上传提交前**一次性解析并缓存：
  - sf.cad_metadata (JSONB): info + layers + entities(≤上限) + total
  - data/previews/{file_id}.svg / .png: 预览文件（cad_preview_path 指向）
上传失败仅记告警，不阻塞主流程（CAD 能力为增强项，不兜底）。
"""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

from app.config import settings
from app.models.stored_file import StoredFile

logger = logging.getLogger(__name__)

# 实体明细缓存上限（超出部分仅记 total，前端分页受限于缓存量）
MAX_CACHED_ENTITIES = 2000


async def cache_cad_snapshot(sf: StoredFile, content: bytes, filename: str) -> None:
    """上传时解析 CAD 文件并填充 sf.cad_metadata / sf.cad_preview_path。

    同步耗时（ezdxf 解析 + 预览渲染）放入线程池，避免阻塞事件循环；
    ORM 属性只在主线程赋值。任何失败仅记录告警。
    """
    if not content:
        return
    try:
        result = await asyncio.to_thread(_build_snapshot, content, filename, str(sf.id))
    except Exception as e:
        logger.warning("CAD 快照构建异常 %s: %s", filename, e)
        return
    if not result:
        return
    meta_dict, preview_path = result
    sf.cad_metadata = meta_dict
    sf.cad_preview_path = preview_path


def _build_snapshot(content: bytes, filename: str, file_id: str):
    """线程池内构建快照，返回 (meta_dict, preview_path_or_None)。失败返回 None。"""
    from app.services.cad_parser import CADParser
    from app.services.cad_preview import CADPreviewGenerator

    ext = Path(filename).suffix.lower()
    cad_path: str | None = None
    extra_tmp: str | None = None
    try:
        if ext == ".dwg":
            from app.services.dwg_converter import dwg_to_dxf
            with tempfile.NamedTemporaryFile(suffix=".dwg", delete=False) as tmp:
                tmp.write(content)
                extra_tmp = tmp.name
            dxf_path = dwg_to_dxf(extra_tmp)
            if dxf_path is None:
                logger.warning("DWG→DXF 转换失败，跳过 CAD 快照: %s", filename)
                return None
            cad_path = dxf_path
        else:
            suffix = ext if ext in (".dxf", ".dwg") else ".dxf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                cad_path = tmp.name

        parser = CADParser()
        doc = parser._open_doc(cad_path)
        meta = parser.extract_metadata(doc)
        layers = parser.extract_layer_tree(doc)
        page = parser.extract_entities(doc, offset=0, limit=MAX_CACHED_ENTITIES)

        meta_dict = {
            "info": {
                "version": meta.version,
                "units": meta.units,
                "extent_min": list(meta.extent_min),
                "extent_max": list(meta.extent_max),
                "total_entities": meta.total_entities,
                "entity_type_counts": meta.entity_type_counts,
                "layer_count": meta.layer_count,
                "has_text": meta.has_text,
                "has_dimensions": meta.has_dimensions,
                "has_blocks": meta.has_blocks,
                "has_hatches": meta.has_hatches,
            },
            "layers": [
                {
                    "name": l.name,
                    "color_index": l.color_index,
                    "color_rgb": list(l.color_rgb) if l.color_rgb else None,
                    "linetype": l.linetype,
                    "is_visible": l.is_visible,
                    "is_frozen": l.is_frozen,
                    "is_locked": l.is_locked,
                    "entity_count": l.entity_count,
                    "geometry_type": l.geometry_type,
                }
                for l in layers
            ],
            "entities": [
                {
                    "handle": e.handle,
                    "entity_type": e.entity_type,
                    "layer": e.layer,
                    "color_index": e.color_index,
                    "start_point": list(e.start_point) if e.start_point else None,
                    "end_point": list(e.end_point) if e.end_point else None,
                    "length": e.length,
                    "area": e.area,
                    "text_content": e.text_content,
                }
                for e in page.entities
            ],
            "entities_total": page.total,
            "entities_cached": len(page.entities),
        }

        preview_path = _generate_previews(cad_path, file_id)
        return meta_dict, preview_path
    except Exception as e:
        logger.warning("CAD 快照解析失败 %s: %s", filename, e)
        return None
    finally:
        for p in (cad_path, extra_tmp):
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass


def _generate_previews(cad_path: str, file_id: str) -> str | None:
    """生成 SVG + PNG 预览到 data/previews/{file_id}.*，返回 SVG 路径。"""
    from app.services.cad_preview import CADPreviewGenerator

    preview_dir = Path(settings.upload_dir).parent / "previews"
    try:
        preview_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("预览目录创建失败 %s: %s", preview_dir, e)
        return None

    gen = CADPreviewGenerator()
    svg_path = preview_dir / f"{file_id}.svg"
    png_path = preview_dir / f"{file_id}.png"
    try:
        svg = gen.generate_svg(cad_path)
        if svg:
            svg_path.write_bytes(svg)
        png = gen.generate_png(cad_path)
        if png:
            png_path.write_bytes(png)
    except Exception as e:
        logger.warning("预览生成失败 %s: %s", cad_path, e)
    return str(svg_path) if svg_path.exists() else None