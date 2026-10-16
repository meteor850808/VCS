# -*- coding: utf-8 -*-
"""CAD 路由子包 — 共享工具。

架构说明（v2.18）:
项目【优化#5】上传后清空 file_data（原始字节不落库），因此 CAD 路由
一律读取上传时缓存的 sf.cad_metadata (JSONB) 与磁盘预览文件，不依赖
file_data 重新解析。缓存由 app/services/cad_cache.py 在上传流程构建。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.stored_file import StoredFile

logger = logging.getLogger("vcs")

CAD_FORMATS = {"dxf", "dwg"}


async def _load_cad_file(file_id: str, db: AsyncSession, auth) -> StoredFile:
    """加载 CAD 文件记录并校验权限（含 group 共享 + 超级管理员）。"""
    stmt = sa_select(StoredFile).where(
        StoredFile.id == file_id,
        auth.scope_clause(StoredFile),
    )
    result = await db.execute(stmt)
    sf = result.scalar_one_or_none()
    if sf is None:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")
    if (sf.file_format or "").lower() not in CAD_FORMATS:
        raise HTTPException(status_code=400, detail="文件不是 CAD 格式（dxf/dwg）")
    return sf


def _cad_metadata(sf: StoredFile) -> dict:
    """读取缓存的 CAD 元数据；未缓存时抛 421 提示（自治缓存缺失）。"""
    if not sf.cad_metadata:
        raise HTTPException(
            status_code=421,
            detail="该文件未生成 CAD 元数据缓存（可能上传时解析失败）",
        )
    if isinstance(sf.cad_metadata, str):
        return json.loads(sf.cad_metadata)
    return sf.cad_metadata


def _preview_dir() -> Path:
    return Path(settings.upload_dir).parent / "previews"


def _preview_file(sf: StoredFile, fmt: str) -> Path | None:
    """返回预览文件路径；不存在返回 None。fmt: svg/png。"""
    p = _preview_dir() / f"{sf.id}.{fmt}"
    return p if p.is_file() else None