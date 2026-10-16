"""数据存储 API v2.4 — 上传、列表、下载、删除、结构预览、元数据编辑。

v2.4 变更：
- 上传时自动检测格式/CRS/字段结构，填充新字段
- 支持 standard_id / standard_table_id / auto_create_standard 参数
- list_files 支持 data_type / file_format / standard_id 过滤
- 使用物化 feature_count 替代 UNION ALL 查询
- 新增 PATCH /data/{file_id} 元数据编辑
- 新增 GET /data/{file_id}/structure 结构预览
- 新增 POST /data/batch-classify 批量关联标准
- 新增 GET /data/upload/check 秒传检查
- 支持大文件切片上传 check/chunk/merge 流程
"""

import asyncio
import gc
import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request, Response,
    UploadFile, status,
)
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import authenticated
from app.core.file_utils import validate_filename
from app.core.rate_limit import limiter
from app.db.session import AsyncSessionLocal, SyncSessionLocal, get_db
from app.models.api_key import ApiKey
from app.models.stored_file import StoredFile
from app.services.feature_store import (
    write_features_batch, write_features_batch_sync, query_features_by_file,
    count_by_file, drop_partition, aggregate_layer_stats,
)
from pydantic import BaseModel as _BaseModel

from app.schemas.storage import (
    FileStructureResponse, InstantUploadResponse, LayerStructure,
    PaginatedFiles, StoredFileBrief, StoredFileDetail, StoredFileUpdateRequest,
    StoredFileUploadResponse,
)
from app.services.archive_extractor import extract_archive, is_archive, classify_extracted_files
from app.services.data_format_helpers import (
    MIME_MAP, VECTOR_EXTENSIONS, _guess_mime, _is_vector_file, _parse_date_bound,
    _empty_feature_stats, _collect_sub_task_results,
)
from app.services.custom_crs import (
    CUSTOM_SRID_BASE,
    resolve_ingest_srid_async,
    validate_loaded_extent,
    validate_loaded_extent_sync,
)
from app.services.format_detection import detect_format, detect_data_type, normalize_stored_geometry_type
from app.services.gdb_processor import gdb_read_lock, prepare_gdb, read_layer_features
from app.services.spatial_loader import extract_features_from_bytes, extract_features_from_file

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 临时目录安全清理（共享模块） ──
from app.core.temp_cleanup import TEMP_DIRS


# ── 辅助函数 ──────────────────────────────────────────────────────────────────




async def _ingest_srid(sf: StoredFile | None, db: AsyncSession) -> int:
    """解析入库 SRID（v2.10.5）— 替代裸 crs_to_srid() 的 4326 盲回退。

    - EPSG 可映射 → 用 EPSG 编码
    - 可解析但无 EPSG（自定义投影如上海地方坐标系）→ 确定性注册自定义 SRID
      到 spatial_ref_sys，保证下游 ST_Transform 可用
    - 解析结果写回 StoredFile.detected_crs_srid / crs_is_custom（"标记数据投影"落库）
    """
    res = await resolve_ingest_srid_async(db, sf.detected_crs if sf else None)
    if sf is not None:
        sf.detected_crs_srid = res.srid
        sf.crs_is_custom = res.is_custom
    return res.srid


def _detect_and_populate(
    sf: StoredFile, content: bytes, filename: str,
    companion_contents: dict[str, bytes] | None = None,
) -> None:
    """对 StoredFile 记录执行格式检测，填充 data_type / file_format / detected_crs / field_schema 等字段。

    Args:
        companion_contents: SHP 伴生文件 {ext: content}（含 .prj）。从压缩包解压的
            Shapefile 用此把 .prj 一并写入临时目录，使 _extract_crs 能读取到
            投影坐标系 WKT（避免 detected_crs 回退为空 → SRID 回退 4326）。
    """
    import tempfile

    ext = Path(filename).suffix.lower()

    # 对于表格格式，先尝试解析 schema（不依赖 Fiona）
    if ext in ('.csv', '.xlsx'):
        _populate_table_schema(sf, content, filename)
        return

    # 对于压缩包，仅标记类型
    if is_archive(filename):
        sf.data_type = "archive"
        sf.file_format = ext.lstrip('.').upper() or "ZIP"
        return

    # 空间格式：写临时文件后用 Fiona 检测
    tmp_path: str | None = None
    tmp_dir: str | None = None
    try:
        if companion_contents:
            # SHP + 伴生文件（尤其 .prj）需同 stem 同目录，才能被 detect_format/_extract_crs 识别
            stem = Path(filename).stem
            tmp_dir = tempfile.mkdtemp(prefix="detect_")
            tmp_path = os.path.join(tmp_dir, f"{stem}{ext}")
            with open(tmp_path, "wb") as tf:
                tf.write(content)
            for comp_ext, comp_data in companion_contents.items():
                if comp_ext.lower() in ('.shp', '.dbf', '.shx', '.prj', '.cpg', '.sbn', '.sbx'):
                    with open(os.path.join(tmp_dir, f"{stem}{comp_ext}"), "wb") as cf:
                        cf.write(comp_data)
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

        result = detect_format(tmp_path)

        sf.file_format = result.format_name.lower() if result.format_name != "Unknown" else ext.lstrip('.')
        sf.data_type = result.data_type
        sf.geometry_type = result.geometry_type
        sf.detected_crs = result.crs_info
        sf.layer_count = result.layer_count
        sf.feature_count = result.feature_count or 0
        sf.field_schema = result.field_schema_json
    except Exception as e:
        logger.warning(f"格式检测失败 {filename}: {e}")
        sf.file_format = ext.lstrip('.') or "unknown"
        sf.data_type = detect_data_type(ext.lstrip('.').upper())
    finally:
        import shutil
        if tmp_dir:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as _clean_e:
                logger.warning(f"格式检测临时目录清理失败 {filename}: {_clean_e}")
        elif tmp_path:
            try:
                os.remove(tmp_path)
            except Exception as _clean_e:
                logger.warning(f"格式检测临时文件清理失败 {filename}: {_clean_e}")


def _populate_table_schema(sf: StoredFile, content: bytes, filename: str) -> None:
    """解析 CSV/XLSX 的字段结构。"""
    ext = Path(filename).suffix.lower()

    try:
        if ext == '.csv':
            from app.services.format_detection import _parse_csv_schema
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='wb') as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                schema, feature_count, layer_count = _parse_csv_schema(tmp_path)
                sf.file_format = "csv"
                sf.data_type = "table"
                sf.geometry_type = "table"
                sf.layer_count = 1
                sf.feature_count = feature_count or 0
                sf.field_schema = [
                    {"name": s.name, "type": s.field_type, "nullable": s.nullable, "sample": s.sample}
                    for s in schema
                ] if schema else None
            finally:
                os.unlink(tmp_path)

        elif ext == '.xlsx':
            from app.services.format_detection import _parse_xlsx_schema
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx', mode='wb') as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                schema, feature_count, layer_count = _parse_xlsx_schema(tmp_path)
                sf.file_format = "xlsx"
                sf.data_type = "table"
                sf.geometry_type = "table"
                sf.layer_count = 1
                sf.feature_count = feature_count or 0
                sf.field_schema = [
                    {"name": s.name, "type": s.field_type, "nullable": s.nullable, "sample": s.sample}
                    for s in schema
                ] if schema else None
            finally:
                os.unlink(tmp_path)
    except Exception as e:
        logger.warning(f"表格 schema 解析失败 {filename}: {e}")
        sf.file_format = ext.lstrip('.')
        sf.data_type = "table"


# ── 上传 API ──────────────────────────────────────────────────────────────────

@router.post(
    "/data/upload",
    response_model=StoredFileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传文件到数据库（支持压缩包自动解压 + 格式检测 + 标准关联）",
)
@limiter.limit("5/minute")
async def upload_file(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="待上传的文件（支持 .zip/.tar.gz/.shp/.geojson/.obj/.xlsx 等）"),
    description: Optional[str] = Form(None),
    standard_id: Optional[str] = Form(None, description="关联数据标准 ID（可选）"),
    standard_table_id: Optional[str] = Form(None, description="关联标准表 ID（可选）"),
    standard_name: Optional[str] = Form(None, description="新增标准时的名称（可选）"),
    layer_group_id: Optional[str] = Form(None, description="关联已有图层组 ID（关联标准时用）"),
    layer_group_name: Optional[str] = Form(None, description="新增图层组名称（新增标准时用）"),
    table_name: Optional[str] = Form(None, description="新增数据表名称（新增标准时用，默认等于图层组名）"),
    geometry_type: Optional[str] = Form(None, description="数据类型（可选，后端自动检测）"),
    auto_create_standard: bool = Form(False, description="是否根据导入结构自动创建标准表"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    # 【v2.9】大小限制按类型分级：
    # - 压缩包（.zip/.tar.gz）：走 max_archive_size_mb（500MB）——不存 BYTEA，仅受内存/解压上限约束
    # - 非压缩包：总大小走 max_data_store_size_mb（200MB），单文件另受 max_single_file_size_mb（100MB，BYTEA 存储上限）
    is_arch = is_archive(file.filename)
    max_bytes = (settings.max_archive_size_mb if is_arch else settings.max_data_store_size_mb) * 1024 * 1024
    single_file_limit = settings.max_single_file_size_mb * 1024 * 1024

    # 分块读取并累积
    sha256 = hashlib.sha256()
    chunks: list[bytes] = []
    total_read = 0
    while chunk := await file.read(1024 * 1024):
        total_read += len(chunk)
        if total_read > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件超过限制 {max_bytes // (1024 * 1024)} MB",
            )
        sha256.update(chunk)
        chunks.append(chunk)
    content = b"".join(chunks)
    file_hash = sha256.hexdigest()

    if not content and max_bytes > 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if not file.filename:  # pragma: no cover - multipart 无 filename 的 part 由 FastAPI 校验拦截（422）先行
        raise HTTPException(status_code=400, detail="文件名为空")
    validate_filename(file.filename, allow_archives=True)

    # 单文件大小限制检查（仅非压缩包 — 压缩包不存 BYTEA，不受此限）
    if not is_arch and len(content) > single_file_limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"单文件大小 {len(content) / 1024 / 1024:.1f} MB 超过限制 {settings.max_single_file_size_mb} MB",
        )

    # 文件哈希去重检查
    if settings.enable_file_dedup:
        existing = await db.execute(
            select(StoredFile).where(
                StoredFile.file_hash == file_hash,
                api_key.scope_clause(StoredFile),
            )
        )
        existing_file = existing.scalar_one_or_none()
        if existing_file:
            return StoredFileUploadResponse(
                id=existing_file.id,
                original_name=existing_file.original_name,
                file_size=existing_file.file_size,
                file_format=existing_file.file_format,
                data_type=existing_file.data_type,
                geometry_type=existing_file.geometry_type,
                detected_crs=existing_file.detected_crs,
                layer_count=existing_file.layer_count,
                feature_count=existing_file.feature_count,
                extracted_count=0,
                data_standard_name=existing_file.data_standard_name,
                standard_id=existing_file.standard_id,
                standard_table_id=existing_file.standard_table_id,  # 【S4】秒传返回关联表
                message=f"文件已存在（哈希去重），返回已有记录 ID: {existing_file.id}",
            )

    # 获取标准名称（冗余存储）
    resolved_standard_name = None
    if standard_id:
        from app.models.data_standard import DataStandard
        ds_result = await db.execute(
            select(DataStandard.name).where(DataStandard.id == standard_id)
        )
        resolved_standard_name = ds_result.scalar_one_or_none()

    # 【规则】同名文件自动删除旧记录再上传
    await _delete_existing_by_name(db, file.filename, api_key)

    # 创建主记录（【优化#5】file_data=None — 上传文件仅做提取，不留存原始字节）
    archive_id = str(uuid.uuid4())
    archive_record = StoredFile(
        id=archive_id,
        api_key_id=api_key.id,
        original_name=file.filename,
        layer_name=Path(file.filename).stem,
        content_type=file.content_type or "application/octet-stream",
        file_size=len(content),
        file_data=None,
        description=description,
        is_archive=is_archive(file.filename),
        file_hash=file_hash,
        standard_id=standard_id,
        standard_table_id=standard_table_id,
        data_standard_name=resolved_standard_name,
    )

    # 格式检测 + 字段填充
    _detect_and_populate(archive_record, content, file.filename)

    # 【v2.18 CAD 快照】DXF/DWG 上传提交前解析并缓存元数据/图层/预览
    # （【优化#5】提交后 file_data 被清空，查询时无法再读字节，故此处一次性缓存）
    if not is_archive(file.filename) and archive_record.file_format in ("dxf", "dwg"):
        try:
            from app.services.cad_cache import cache_cad_snapshot
            await cache_cad_snapshot(archive_record, content, file.filename)
        except Exception as _cad_e:
            logger.warning("CAD 快照缓存失败（不影响上传）: %s", _cad_e)

    # 新增数据标准：根据数据结构自动创建（数据标准→数据层组→数据表）
    if auto_create_standard and archive_record.field_schema:
        from app.models.data_standard import DataStandard
        from app.services.structure_analyzer import auto_create_standard_from_schema

        # 自动检测几何类型：优先使用前端传入，否则使用后端检测值
        GEOM_MAP = {"point": "Point", "line": "LineString", "polygon": "Polygon", "3d": "3d", "table": None}
        if geometry_type and geometry_type in GEOM_MAP:
            db_geom_type = GEOM_MAP[geometry_type]
        else:
            # 使用 _detect_and_populate 已检测到的几何类型
            detected = archive_record.geometry_type
            if detected:
                db_geom_type = detected if detected in ("Point", "LineString", "Polygon", "3d") else None
            else:  # pragma: no cover - 检测结果必填（非矢量→"table"，矢量→具体类型），恒不走到
                db_geom_type = None

        layer_name = layer_group_name or Path(file.filename).stem
        tbl_name = table_name or layer_name

        if standard_id:
            # 在已有标准下新增图层组+数据表
            new_std_id = standard_id
            resolved_standard_name = None
            ds_result = await db.execute(
                select(DataStandard.name).where(DataStandard.id == standard_id)
            )
            resolved_standard_name = ds_result.scalar_one_or_none()
        else:
            # 新建标准（保留但前端已移除此入口）
            new_std_id = str(uuid.uuid4())
            new_std = DataStandard(
                id=new_std_id,
                api_key_id=api_key.id,
                name=standard_name or f"{Path(file.filename).stem} 数据标准",
                description=f"上传 {file.filename} 时自动创建",
                category="auto",
            )
            db.add(new_std)
            await db.flush()
            resolved_standard_name = new_std.name

        # 创建层组 + 标准表 + 标准字段
        await auto_create_standard_from_schema(
            db=db,
            standard_id=new_std_id,
            layer_group_name=layer_name,
            table_name=tbl_name,
            field_schema=archive_record.field_schema,
            geometry_type=db_geom_type,
        )
        standard_id = new_std_id
        # 回写标准信息到主记录
        archive_record.standard_id = standard_id
        archive_record.data_standard_name = resolved_standard_name

    # 关联已有图层组（不创建新标准，仅设置关联）
    elif layer_group_id and standard_id:
        archive_record.standard_id = standard_id
        resolved_standard_name = None
        ds_result = await db.execute(
            select(DataStandard.name).where(DataStandard.id == standard_id)
        )
        resolved_standard_name = ds_result.scalar_one_or_none()
        archive_record.data_standard_name = resolved_standard_name

        # 数据类型验证：上传数据与标准定义不一致时报错停止入库
        # （层组多表时：存在任一几何类型匹配的表即通过）
        if archive_record.geometry_type and not archive_record.is_archive:
            from app.models.standard_table import StandardTable
            from app.models.standard_layer_group import StandardLayerGroup
            std_table_q = await db.execute(
                select(StandardTable.geometry_type)
                .join(StandardLayerGroup, StandardTable.layer_id == StandardLayerGroup.id)
                .where(StandardLayerGroup.id == layer_group_id)
            )
            table_geoms = list(std_table_q.scalars())
            if table_geoms and archive_record.geometry_type not in table_geoms:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"数据类型不匹配：上传数据为 {archive_record.geometry_type}，"
                        f"标准该图层组要求 {set(table_geoms)}"
                    ),
                )

        # 【S4】link 分支回填 standard_table_id：未显式指定时按层组兜底
        # 优先选几何类型匹配的表；无匹配时取第一张（质检端按此表做属性检查）
        if not archive_record.standard_table_id:
            from app.models.standard_table import StandardTable as _ST
            _tbl_rows = (await db.execute(
                select(_ST.id, _ST.geometry_type)
                .where(_ST.layer_id == layer_group_id)
                .order_by(_ST.table_name)
            )).all()
            _picked = None
            if archive_record.geometry_type and not archive_record.is_archive:
                _picked = next((r.id for r in _tbl_rows
                                if r.geometry_type == archive_record.geometry_type), None)
            if not _picked and _tbl_rows:
                _picked = _tbl_rows[0].id
            if _picked:
                archive_record.standard_table_id = str(_picked)

    db.add(archive_record)

    # ── MDB (ESRI Personal Geodatabase) 处理 ──
    if file.filename.lower().endswith('.mdb'):
        from app.services.mdb_processor import extract_mdb_to_stored_files

        try:
            mdb_results = extract_mdb_to_stored_files(
                content, filename=file.filename, api_key_id=api_key.id,
                max_bytes=settings.max_data_store_size_mb * 1024 * 1024,
            )
        except Exception as e:
            logger.error(f"Failed to process MDB file {file.filename}: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to process MDB file: {str(e)}",
            )

        extracted_count = 0
        sub_tasks: list = []

        for result in mdb_results:
            stored_file_data = result["stored_file"]
            features = result["features"]

            # 从字典构造 StoredFile 对象
            sf = StoredFile(**stored_file_data)
            # _detect_and_populate is not needed for MDB sub-files:
            # data_type, geometry_type, file_format are already set by mdb_processor

            if standard_id:
                sf.standard_id = standard_id
                sf.standard_table_id = standard_table_id

            db.add(sf)
            await db.flush()

            extracted_count += 1

            if features:
                # MDB 内部坐标系（GDB_SpatialRefs.SRTEXT）解析入库 SRID，
                # 避免 GK 米制坐标被当作 WGS84 (4326) 写入
                srid = await _ingest_srid(sf, db)
                # Use owner_id (not api_key.id) because _load_mdb_features_async
                # expects the FK reference to the users table for spatial feature ownership.
                # 直接传递 features 列表，跳过 Fiona 重新解析（优化双重 GeoJSON 解析）
                sub_tasks.append(_load_mdb_features_async(
                    sf.id, table_name=Path(sf.original_name).stem,
                    features=features, api_key_id=api_key.owner_id,
                    srid=srid,
                ))

        logger.info(f"Processed MDB file {file.filename}: {extracted_count} tables extracted")

        # 跳过后续的压缩包/非压缩包处理
        stats_by_file: dict = {}
        if sub_tasks:
            results = await asyncio.gather(*sub_tasks, return_exceptions=True)
            stats_by_file, load_errors = _collect_sub_task_results(results)
        else:
            load_errors = []

        await db.flush()
        db.expire_all()
        # expire_all 后须显式 refresh，否则同步上下文中访问 archive_record 属性
        # 会触发 lazy load → MissingGreenlet
        await db.refresh(archive_record)

        # 【MDB 回填】子记录 feature_count + 父记录聚合（与压缩包分支 P0-1 修复对齐）
        mdb_sub_sfs = (await db.execute(
            select(StoredFile).where(StoredFile.source_archive == file.filename)
        )).scalars().all()
        total_features = 0
        for _ssf in mdb_sub_sfs:
            _st = stats_by_file.get(_ssf.id)
            if _st:
                _ssf.feature_count = _st.get("written") or 0
                if not archive_record.geometry_type and _st.get("geometry_type"):
                    archive_record.geometry_type = _st["geometry_type"]
            if _ssf.detected_crs and not archive_record.detected_crs:
                archive_record.detected_crs = _ssf.detected_crs
            total_features += _ssf.feature_count or 0
            # 子记录已保留 GeoJSON 字节，父记录原始 MDB 字节不再留存储
            _ssf.file_data = None
        archive_record.feature_count = total_features
        archive_record.layer_count = extracted_count
        archive_record.file_data = None
        await db.commit()

        load_warning = None
        if load_errors:
            load_warning = f"部分子文件要素展开失败（{len(load_errors)}/{len(sub_tasks)}）: " + "; ".join(load_errors[:3])

        return StoredFileUploadResponse(
            id=archive_id,
            original_name=file.filename,
            file_size=len(content),
            file_format=archive_record.file_format,
            data_type=archive_record.data_type,
            geometry_type=archive_record.geometry_type,
            detected_crs=archive_record.detected_crs,
            layer_count=archive_record.layer_count,
            feature_count=archive_record.feature_count,
            extracted_count=extracted_count,
            data_standard_name=resolved_standard_name,
            standard_id=archive_record.standard_id,
            standard_table_id=archive_record.standard_table_id,
            warning=load_warning,
            message=f"上传成功，从 MDB 提取 {extracted_count} 个空间表",
            extraction_details=None,
        )

    extracted_count = 0
    sub_tasks: list = []
    extraction_details: list = []  # 供前端展示的提取详情
    load_warning_non_archive = None  # 【S3】非压缩包路径的展开异常（回填 warning）

    # ── 压缩包解压处理（统一解压一次，分类后按类型处理）──
    if is_archive(file.filename):
        # ① 统一解压（仅一次）
        ex_result = extract_archive(
            content=content,
            filename=file.filename,
            max_file_size=max_bytes,
            max_total_size=max_bytes * 2,
        )
        if ex_result.errors:
            logger.warning(f"解压警告: {ex_result.errors}")

        # ② 分类文件
        classified = classify_extracted_files(ex_result.files)
        logger.info(
            f"ZIP 分类完成: GDB组={len(classified.gdb_groups)}, "
            f"SHP组={len(classified.shp_groups)}, "
            f"矢量={len(classified.vectors)}, "
            f"表格={len(classified.tables)}, "
            f"忽略={len(classified.ignored)}"
        )

        # ③ 处理 GDB 组（每个 GDB 目录 → 逐 layer 直接入库 PostGIS 原生几何，不再转 GeoJSON）
        gdb_tmp_dirs: list[str] = []
        for gdb_dir, gdb_files in classified.gdb_groups.items():
            gdb_count, gdb_details, gdb_sub, gdb_tmp = await _handle_gdb_group(
                db=db,
                gdb_files=gdb_files,
                gdb_dir=gdb_dir,
                archive_filename=file.filename,
                api_key_id=api_key.id,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                resolved_standard_name=resolved_standard_name,
                archive_record=archive_record,
                api_key_owner_id=api_key.owner_id,
            )
            extracted_count += gdb_count
            extraction_details.extend(gdb_details)
            sub_tasks.extend(gdb_sub)
            if gdb_tmp:
                gdb_tmp_dirs.append(gdb_tmp)

        # ④ 处理 SHP 组（.shp + 伴生文件 → Fiona 整体读取）
        for key, shp_group in classified.shp_groups.items():
            stem = key.split("|")[0]
            if not shp_group.has_shp:
                # 无 .shp 主文件，跳过纯伴生文件
                for ext, ef in shp_group.companions.items():
                    extraction_details.append({
                        "name": ef.original_name, "type": "SHP-companion",
                        "features": 0, "status": "skipped_no_shp",
                    })
                continue

            shp_ef = shp_group.shp
            shp_file_id = str(uuid.uuid4())

            # 创建 StoredFile（仅 .shp 主文件）
            shp_sf = StoredFile(
                id=shp_file_id,
                api_key_id=api_key.id,
                original_name=f"{file.filename}/{shp_ef.original_name}",
                layer_name=Path(shp_ef.original_name).stem,
                content_type=_guess_mime(shp_ef.original_name),
                file_size=shp_ef.file_size,
                file_data=shp_ef.content,
                source_archive=file.filename,
                is_archive=False,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                data_standard_name=resolved_standard_name,
            )
            _detect_and_populate(shp_sf, shp_ef.content, shp_ef.original_name, shp_group.companion_contents)
            db.add(shp_sf)
            extracted_count += 1
            extraction_details.append({
                "file_id": shp_sf.id, "name": shp_sf.original_name, "type": "SHP",
                "features": shp_sf.feature_count, "status": "ok",
            })

            # 写临时目录 → Fiona 整体读取
            sub_tasks.append(_load_shp_group_features_async(
                shp_file_id, shp_ef.original_name, shp_ef.content,
                shp_group.companion_contents,
                api_key.owner_id,
                srid=await _ingest_srid(shp_sf, db),
            ))

        # ⑤ 处理其他矢量文件（GeoJSON/GPKG/KML/DXF 等）
        for ef in classified.vectors:
            stored = StoredFile(
                id=str(uuid.uuid4()),
                api_key_id=api_key.id,
                original_name=f"{file.filename}/{ef.original_name}",
                layer_name=Path(ef.original_name).stem,
                content_type=_guess_mime(ef.original_name),
                file_size=ef.file_size,
                file_data=ef.content,
                source_archive=file.filename,
                is_archive=False,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                data_standard_name=resolved_standard_name,
            )
            _detect_and_populate(stored, ef.content, ef.original_name)
            db.add(stored)
            extracted_count += 1
            extraction_details.append({
                "file_id": stored.id, "name": stored.original_name, "type": stored.file_format.upper(),
                "features": stored.feature_count, "status": "ok",
            })
            sub_tasks.append(_load_extracted_features_async(
                stored.id, ef.original_name, ef.content, api_key.owner_id,
                srid=await _ingest_srid(stored, db),
            ))

        # ⑥ 处理表格文件（CSV/XLSX — 仅存储，不展开 spatial）
        for ef in classified.tables:
            stored = StoredFile(
                id=str(uuid.uuid4()),
                api_key_id=api_key.id,
                original_name=f"{file.filename}/{ef.original_name}",
                layer_name=Path(ef.original_name).stem,
                content_type=_guess_mime(ef.original_name),
                file_size=ef.file_size,
                file_data=ef.content,
                source_archive=file.filename,
                is_archive=False,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                data_standard_name=resolved_standard_name,
            )
            _detect_and_populate(stored, ef.content, ef.original_name)
            db.add(stored)
            extracted_count += 1
            extraction_details.append({
                "file_id": stored.id, "name": stored.original_name, "type": stored.file_format.upper(),
                "features": 0, "status": "table_only",
            })

        # ⑦ 忽略的文件（记录但不存储）
        for ef in classified.ignored:
            extraction_details.append({
                "name": ef.original_name, "type": "ignored",
                "features": 0, "status": "skipped",
            })

    # ── 非压缩包直接展开空间要素 ──
    if not is_archive(file.filename):
        # 【S3 修复】捕获子任务写入量，回填 feature_count（与压缩包聚合语义一致：
        # feature_count = 实际写入 spatial 表的要素数，而非检测阶段的估算值）
        stats = await _load_all_features_to_spatial_async(
            archive_id, file.filename, content, api_key.owner_id,
            srid=await _ingest_srid(archive_record, db),
        )
        archive_record.feature_count = stats.get("written") or 0
        archive_record.geometry_type = stats.get("geometry_type") or archive_record.geometry_type
        load_warning_non_archive = stats.get("error")
        # 图层 × 类型聚合（v2.10.5）— SAVEPOINT 隔离，失败不拖垮上传事务
        try:
            async with db.begin_nested():
                await aggregate_layer_stats(db, archive_id, api_key.owner_id)
        except Exception as agg_e:
            logger.warning("图层统计聚合失败 %s: %s", file.filename, agg_e)

    stats_by_file: dict = {}
    if sub_tasks:
        # 【优化#6】return_exceptions=True 防止单个子任务异常中断全量入库
        results = await asyncio.gather(*sub_tasks, return_exceptions=True)
        stats_by_file, load_errors = _collect_sub_task_results(results)
    else:
        load_errors = []

        # 【v2.9】清理 GDB 组的临时目录（要素已全部读取入库）
        # v2.10.1：先 gc.collect() 促使 fiona 数据集对象释放句柄（GDAL 对象由
        # 扩展类型管理、非引用计数即时回收），再删除；失败不静默，保留目录并记日志。
        for _d in (gdb_tmp_dirs if is_archive(file.filename) else []):  # pragma: no cover - gdb 组有 layers 必生 sub_tasks；无 layers 时 tmp 为 None，循环恒空
            gc.collect()
            try:
                shutil.rmtree(_d)
            except Exception as _clean_e:
                logger.warning(f"GDB 临时目录清理失败（不影响入库，可手工删除）: {_d}: {_clean_e}")

    # 【S1 修复】子任务使用独立会话提交后，失效主会话缓存以读取最新 feature_count
    await db.flush()
    db.expire_all()

    # 【v2.9/v2.10】子记录 feature_count 回填（子任务统计；本会话 flush 后可见自身未提交行）
    # 覆盖 GDB/SHP/GeoJSON 等所有子记录，保证 P0-1 聚合与列表展示一致
    if is_archive(file.filename) and stats_by_file:
        _sub_sfs = (await db.execute(
            select(StoredFile).where(StoredFile.source_archive == file.filename)
        )).scalars().all()
        for _ssf in _sub_sfs:
            _st = stats_by_file.get(_ssf.id)
            if _st:
                _ssf.feature_count = _st.get("written") or 0

    # 【P0-1 修复】聚合子文件的 feature_count 到父 archive 记录
    if is_archive(file.filename) and extracted_count > 0:
        sub_files = await db.execute(
            select(StoredFile).where(StoredFile.source_archive == file.filename)
        )
        total_features = sum(
            (sf.feature_count or 0) for sf in sub_files.scalars().all()
        )
        archive_record.feature_count = total_features

    # 【S1 修复】回填 extraction_details 的真实要素数（子任务实际写入量）
    for detail in extraction_details:
        st = stats_by_file.get(detail.get("file_id"))
        if st is not None and detail.get("type") != "table_only":
            detail["features"] = st.get("written") or 0

    # 【v2.10.5】压缩包子文件的图层 × 类型聚合（逐子文件，SAVEPOINT 隔离）
    if is_archive(file.filename) and stats_by_file:
        for _cid, _st in stats_by_file.items():
            try:
                async with db.begin_nested():
                    await aggregate_layer_stats(db, _cid, api_key.owner_id)
            except Exception as agg_e:
                logger.warning("子文件图层统计聚合失败 %s: %s", _cid, agg_e)

    # 【优化#5】提交前清空所有 file_data — 上传文件仅做提取，不留存原始字节
    archive_record.file_data = None
    if is_archive(file.filename):
        sub_files_result = await db.execute(
            select(StoredFile).where(StoredFile.source_archive == file.filename)
        )
        for sf in sub_files_result.scalars().all():
            sf.file_data = None

    await db.commit()

    # 【优化#6】汇总子任务异常到 warning
    load_warning = None
    if load_errors:
        load_warning = f"部分子文件要素展开失败（{len(load_errors)}/{len(sub_tasks)}）: " + "; ".join(load_errors[:3])
    elif not is_archive(file.filename) and load_warning_non_archive:
        load_warning = load_warning_non_archive

    return StoredFileUploadResponse(
        id=archive_id,
        original_name=file.filename,
        file_size=len(content),
        file_format=archive_record.file_format,
        data_type=archive_record.data_type,
        geometry_type=archive_record.geometry_type,
        detected_crs=archive_record.detected_crs,
        layer_count=archive_record.layer_count,
        feature_count=archive_record.feature_count,
        extracted_count=extracted_count,
        data_standard_name=resolved_standard_name,
        standard_id=archive_record.standard_id,
        standard_table_id=archive_record.standard_table_id,  # 【S4】回填层组关联表
        warning=load_warning or ("SHP文件缺少配套的.dbf/.shx文件，无法提取要素。请重新上传为ZIP压缩包（包含.shp+.dbf+.shx）" if archive_record.feature_count == 0 and archive_record.file_format == "shp" else None),
        message=f"上传成功，解压出 {extracted_count} 个文件" if extracted_count else "上传成功",
        extraction_details=extraction_details if extraction_details else None,
    )


# ── 秒传检查 ──────────────────────────────────────────────────────────────────

@router.get("/data/upload/check", response_model=InstantUploadResponse, summary="秒传检查（按文件哈希）")
async def check_upload(
    hash: str = Query(..., description="文件 SHA-256 哈希"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(StoredFile).where(
            StoredFile.file_hash == hash,
            api_key.scope_clause(StoredFile),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return InstantUploadResponse(
            exists=True,
            file_id=existing.id,
            original_name=existing.original_name,
            file_size=existing.file_size,
            feature_count=existing.feature_count,
        )
    return InstantUploadResponse(exists=False)


# ── 大文件切片上传 ─────────────────────────────────────────────────────────────

CHUNK_DIR = Path(settings.upload_dir) / "chunks"
CHUNK_DIR.mkdir(parents=True, exist_ok=True)
MAX_CHUNKS = 500           # 最大片数 (10MB × 500 = 5GB)
CHUNK_EXPIRE_HOURS = 24    # 未合并切片过期时间


@router.post("/data/upload/chunk", summary="接收单个切片")
async def upload_chunk(
    request: Request,
    upload_id: str = Form(..., description="上传会话 ID（文件 SHA-256）"),
    chunk_index: int = Form(..., ge=0, description="切片序号（从 0 开始）"),
    total_chunks: int = Form(..., ge=1, description="总切片数"),
    chunk: UploadFile = File(..., description="切片数据"),
    api_key: ApiKey = Depends(authenticated),
):
    """接收单个文件切片，存储到临时目录 data/chunks/{upload_id}/"""
    if total_chunks > MAX_CHUNKS:
        raise HTTPException(
            status_code=400,
            detail=f"切片数 {total_chunks} 超过上限 {MAX_CHUNKS}",
        )

    # 创建切片目录
    chunk_subdir = CHUNK_DIR / upload_id
    chunk_subdir.mkdir(parents=True, exist_ok=True)

    # 写入切片文件
    chunk_path = chunk_subdir / f"{chunk_index:06d}.chunk"
    content = await chunk.read()
    # 切片大小以 settings.file_chunk_size_mb 为唯一权威（v2.20 收敛内存联 2MB 漂移）
    expected_max = settings.file_chunk_size_mb * 1024 * 1024
    if len(content) > expected_max * 1.5:  # 允许 50% 余量
        raise HTTPException(
            status_code=413,
            detail=f"切片大小 {len(content) / 1024 / 1024:.1f} MB 超过预期 {settings.file_chunk_size_mb} MB",
        )

    async with aiofiles.open(chunk_path, "wb") as f:
        await f.write(content)

    # 写入元信息（首次切片时创建）
    meta_path = chunk_subdir / "meta.json"
    if not meta_path.exists():
        meta = {
            "upload_id": upload_id,
            "total_chunks": total_chunks,
            "api_key_id": api_key.owner_id,
            "chunks_received": [],
        }
    else:
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            meta = json.loads(await f.read())

    if chunk_index not in meta["chunks_received"]:
        meta["chunks_received"].append(chunk_index)
        meta["chunks_received"].sort()

    async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(meta, ensure_ascii=False))

    return {
        "ok": True,
        "upload_id": upload_id,
        "received": len(meta["chunks_received"]),
        "total": total_chunks,
    }


@router.get("/data/upload/chunk-status", summary="查询切片上传进度")
async def get_chunk_status(
    upload_id: str = Query(..., description="上传会话 ID"),
    api_key: ApiKey = Depends(authenticated),
):
    """返回指定上传会话的切片接收状态。"""
    chunk_subdir = CHUNK_DIR / upload_id
    meta_path = chunk_subdir / "meta.json"

    if not meta_path.exists():
        return {
            "upload_id": upload_id,
            "status": "not_found",
            "uploaded_chunks": [],
            "total_chunks": 0,
        }

    async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
        meta = json.loads(await f.read())

    uploaded = meta.get("chunks_received", [])
    total = meta.get("total_chunks", 0)

    status_str = "uploading"
    if len(uploaded) >= total and total > 0:
        status_str = "ready_to_merge"

    return {
        "upload_id": upload_id,
        "status": status_str,
        "uploaded_chunks": uploaded,
        "total_chunks": total,
        "progress": f"{len(uploaded)}/{total}",
    }


@router.post("/data/upload/merge", response_model=StoredFileUploadResponse, summary="合并切片并入库")
async def merge_chunks(
    body: dict,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """合并所有切片，执行格式检测，存入数据库。"""
    upload_id = body.get("upload_id")
    filename = body.get("filename")
    total_chunks = body.get("total_chunks", 1)
    standard_id = body.get("standard_id")
    standard_table_id = body.get("standard_table_id")
    description = body.get("description")

    if not upload_id or not filename:
        raise HTTPException(status_code=400, detail="upload_id 和 filename 必填")

    chunk_subdir = CHUNK_DIR / upload_id
    meta_path = chunk_subdir / "meta.json"

    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="上传会话不存在")

    async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
        meta = json.loads(await f.read())

    received = meta.get("chunks_received", [])
    if len(received) < total_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"切片不完整：已接收 {len(received)}/{total_chunks}",
        )

    # 按序合并切片
    merged = bytearray()
    for i in range(total_chunks):
        chunk_path = chunk_subdir / f"{i:06d}.chunk"
        if not chunk_path.exists():
            raise HTTPException(status_code=400, detail=f"切片 {i} 文件缺失")
        async with aiofiles.open(chunk_path, "rb") as f:
            merged.extend(await f.read())

    content = bytes(merged)
    file_hash = hashlib.sha256(content).hexdigest()

    # 【v2.9】大小限制按类型分级：压缩包走 max_archive_size_mb（不存 BYTEA），非压缩包走 max_data_store_size_mb
    is_arch = is_archive(filename)
    max_bytes = (settings.max_archive_size_mb if is_arch else settings.max_data_store_size_mb) * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"合并后文件大小 {len(content) / 1024 / 1024:.1f} MB 超过限制 {max_bytes // (1024 * 1024)} MB",
        )
    if not is_arch and len(content) > settings.max_single_file_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"单文件大小 {len(content) / 1024 / 1024:.1f} MB 超过限制 {settings.max_single_file_size_mb} MB",
        )

    # 获取标准名称
    standard_name = None
    if standard_id:
        from app.models.data_standard import DataStandard
        ds_result = await db.execute(
            select(DataStandard.name).where(DataStandard.id == standard_id)
        )
        standard_name = ds_result.scalar_one_or_none()

    # 【规则】同名文件自动删除旧记录再上传
    await _delete_existing_by_name(db, filename, api_key)

    # 创建 StoredFile 记录（【优化#5】file_data=None — 上传文件仅做提取，不留存原始字节）
    file_id = str(uuid.uuid4())
    sf = StoredFile(
        id=file_id,
        api_key_id=api_key.id,
        original_name=filename,
        layer_name=Path(filename).stem,
        content_type=_guess_mime(filename),
        file_size=len(content),
        file_data=None,
        description=description,
        is_archive=is_archive(filename),
        file_hash=file_hash,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                data_standard_name=standard_name,
        upload_id=upload_id,
    )

    # 格式检测
    _detect_and_populate(sf, content, filename)

    db.add(sf)

    # 非压缩包直接展开空间要素
    if not is_archive(filename):
        await _load_all_features_to_spatial_async(
            file_id, filename, content, api_key.owner_id,
            srid=await _ingest_srid(sf, db),
        )
        # 图层 × 类型聚合（v2.10.5）— SAVEPOINT 隔离，失败不拖垮上传事务
        try:
            async with db.begin_nested():
                await aggregate_layer_stats(db, file_id, api_key.owner_id)
        except Exception as agg_e:
            logger.warning("图层统计聚合失败 %s: %s", filename, agg_e)

    # ── 压缩包解压（统一解压一次，分类后按类型处理）──
    extracted_count = 0
    extraction_details: list = []
    load_errors: list = []
    stats_by_file: dict = {}
    if is_archive(filename):
        # ① 统一解压
        ex_result = extract_archive(
            content=content, filename=filename,
            max_file_size=max_bytes, max_total_size=max_bytes * 2,
        )

        # ② 分类文件
        classified = classify_extracted_files(ex_result.files)
        sub_tasks: list = []

        # ③ 处理 GDB 组（每个 GDB 目录 → 逐 layer 直接入库 PostGIS 原生几何，不再转 GeoJSON）
        gdb_tmp_dirs: list[str] = []
        for gdb_dir, gdb_files in classified.gdb_groups.items():
            gdb_count, gdb_details, gdb_sub, gdb_tmp = await _handle_gdb_group(
                db=db,
                gdb_files=gdb_files,
                gdb_dir=gdb_dir,
                archive_filename=filename,
                api_key_id=api_key.id,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                resolved_standard_name=standard_name,
                archive_record=sf,
                api_key_owner_id=api_key.owner_id,
            )
            extracted_count += gdb_count
            extraction_details.extend(gdb_details)
            sub_tasks.extend(gdb_sub)
            if gdb_tmp:
                gdb_tmp_dirs.append(gdb_tmp)

        # ④ 处理 SHP 组
        for key, shp_group in classified.shp_groups.items():
            if not shp_group.has_shp:
                continue
            shp_ef = shp_group.shp
            shp_file_id = str(uuid.uuid4())
            shp_sf = StoredFile(
                id=shp_file_id,
                api_key_id=api_key.id,
                original_name=f"{filename}/{shp_ef.original_name}",
                layer_name=Path(shp_ef.original_name).stem,
                content_type=_guess_mime(shp_ef.original_name),
                file_size=shp_ef.file_size,
                file_data=shp_ef.content,
                source_archive=filename,
                is_archive=False,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                data_standard_name=standard_name,
            )
            _detect_and_populate(shp_sf, shp_ef.content, shp_ef.original_name, shp_group.companion_contents)
            db.add(shp_sf)
            extracted_count += 1
            extraction_details.append({
                "file_id": shp_sf.id, "name": shp_sf.original_name, "type": "SHP",
                "features": shp_sf.feature_count, "status": "ok",
            })
            sub_tasks.append(_load_shp_group_features_async(
                shp_file_id, shp_ef.original_name, shp_ef.content,
                shp_group.companion_contents,
                api_key.owner_id,
                srid=await _ingest_srid(shp_sf, db),
            ))

        # ⑤ 处理其他矢量文件

        # ⑤ 处理其他矢量文件
        for ef in classified.vectors:
            stored = StoredFile(
                id=str(uuid.uuid4()),
                api_key_id=api_key.id,
                original_name=f"{filename}/{ef.original_name}",
                layer_name=Path(ef.original_name).stem,
                content_type=_guess_mime(ef.original_name),
                file_size=ef.file_size,
                file_data=ef.content,
                source_archive=filename,
                is_archive=False,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                data_standard_name=standard_name,
            )
            _detect_and_populate(stored, ef.content, ef.original_name)
            db.add(stored)
            extracted_count += 1
            extraction_details.append({
                "file_id": stored.id, "name": stored.original_name, "type": stored.file_format.upper(),
                "features": stored.feature_count, "status": "ok",
            })
            sub_tasks.append(_load_extracted_features_async(
                stored.id, ef.original_name, ef.content, api_key.owner_id,
                srid=await _ingest_srid(stored, db),
            ))

        # ⑥ 处理表格文件
        for ef in classified.tables:
            stored = StoredFile(
                id=str(uuid.uuid4()),
                api_key_id=api_key.id,
                original_name=f"{filename}/{ef.original_name}",
                layer_name=Path(ef.original_name).stem,
                content_type=_guess_mime(ef.original_name),
                file_size=ef.file_size,
                file_data=ef.content,
                source_archive=filename,
                is_archive=False,
                standard_id=standard_id,
                standard_table_id=standard_table_id,
                data_standard_name=standard_name,
            )
            _detect_and_populate(stored, ef.content, ef.original_name)
            db.add(stored)
            extracted_count += 1
            # 【P0-2 修复】表格文件也写入 extraction_details
            extraction_details.append({
                "file_id": stored.id, "name": stored.original_name, "type": stored.file_format.upper(),
                "features": 0, "status": "table_only",
            })

        if sub_tasks:
            # 【优化#6】return_exceptions=True 防止单个子任务异常中断全量入库
            results = await asyncio.gather(*sub_tasks, return_exceptions=True)
            stats_by_file, load_errors = _collect_sub_task_results(results)
        else:
            load_errors = []
            stats_by_file = {}

        # 【v2.9】清理 GDB 组的临时目录（要素已全部读取入库）
        # v2.10.1：先 gc.collect() 促使 fiona 数据集对象释放句柄，删除失败不静默。
        for _d in gdb_tmp_dirs:
            gc.collect()
            try:
                shutil.rmtree(_d)
            except Exception as _clean_e:
                logger.warning(f"GDB 临时目录清理失败（不影响入库，可手工删除）: {_d}: {_clean_e}")

        # 【S1 修复】子任务使用独立会话提交后，失效主会话缓存以读取最新 feature_count
        await db.flush()
        db.expire_all()

        # 【v2.9/v2.10】子记录 feature_count 回填（子任务统计；本会话 flush 后可见自身未提交行）
        # 覆盖 GDB/SHP/GeoJSON 等所有子记录，保证 P0-1 聚合与列表展示一致
        if stats_by_file:
            _sub_sfs = (await db.execute(
                select(StoredFile).where(StoredFile.source_archive == filename)
            )).scalars().all()
            for _ssf in _sub_sfs:
                _st = stats_by_file.get(_ssf.id)
                if _st:
                    _ssf.feature_count = _st.get("written") or 0

        # 【P0-1 修复】聚合子文件的 feature_count 到父 archive 记录
        if extracted_count > 0:
            sub_files = await db.execute(
                select(StoredFile).where(StoredFile.source_archive == filename)
            )
            total_features = sum(
                (sf.feature_count or 0) for sf in sub_files.scalars().all()
            )
            sf.feature_count = total_features

        # 【S1 修复】回填 extraction_details 的真实要素数（子任务实际写入量）
        for detail in extraction_details:
            st = stats_by_file.get(detail.get("file_id"))
            if st is not None and detail.get("type") != "table_only":
                detail["features"] = st.get("written") or 0

        # 【v2.10.5】压缩包子文件的图层 × 类型聚合（逐子文件，SAVEPOINT 隔离）
        if stats_by_file:
            for _cid, _st in stats_by_file.items():
                try:
                    async with db.begin_nested():
                        await aggregate_layer_stats(db, _cid, api_key.owner_id)
                except Exception as agg_e:
                    logger.warning("子文件图层统计聚合失败 %s: %s", _cid, agg_e)

        # 【优化#5】提交前清空所有子文件 file_data — 上传文件仅做提取，不留存原始字节
        sub_files_result = await db.execute(
            select(StoredFile).where(StoredFile.source_archive == filename)
        )
        for sub_sf in sub_files_result.scalars().all():
            sub_sf.file_data = None

    # 【S1 修复】失效缓存，让主记录读取到独立会话已提交的 feature_count（非压缩包路径）
    await db.flush()
    db.expire_all()

    # 【优化#5】主记录 file_data 也确保为 None
    sf.file_data = None

    await db.commit()

    # 清理切片目录
    try:
        shutil.rmtree(chunk_subdir, ignore_errors=True)
    except Exception as _clean_e:
        logger.warning(f"切片目录清理失败 {chunk_subdir}: {_clean_e}")

    # 【优化#6】汇总子任务异常到 warning
    load_warning = None
    if load_errors:
        load_warning = f"部分子文件要素展开失败（{len(load_errors)}/{len(sub_tasks)}）: " + "; ".join(load_errors[:3])

    return StoredFileUploadResponse(
        id=file_id,
        original_name=filename,
        file_size=len(content),
        file_format=sf.file_format,
        data_type=sf.data_type,
        geometry_type=sf.geometry_type,
        detected_crs=sf.detected_crs,
        layer_count=sf.layer_count,
        feature_count=sf.feature_count,
        extracted_count=extracted_count,
        data_standard_name=sf.data_standard_name,
        standard_id=sf.standard_id,
        standard_table_id=sf.standard_table_id,  # 【S4】切片合并返回关联表
        warning=load_warning or ("SHP文件缺少配套的.dbf/.shx文件，无法提取要素。请重新上传为ZIP压缩包（包含.shp+.dbf+.shx）" if sf.feature_count == 0 and sf.file_format == "shp" else None),
        message=f"切片合并成功，解压出 {extracted_count} 个文件" if extracted_count else "切片合并成功",
        extraction_details=extraction_details if extraction_details else None,
    )


@router.delete("/data/upload/chunk", summary="取消上传，清理临时切片")
async def cancel_upload(
    upload_id: str = Query(..., description="上传会话 ID"),
    api_key: ApiKey = Depends(authenticated),
):
    """删除指定上传会话的所有临时切片文件。"""
    chunk_subdir = CHUNK_DIR / upload_id
    if chunk_subdir.exists():
        shutil.rmtree(chunk_subdir, ignore_errors=True)
    return {"ok": True, "message": f"已清理上传会话 {upload_id}"}


# ── 列表 API ──────────────────────────────────────────────────────────────────

@router.get("/data", response_model=PaginatedFiles, summary="分页查询已存储文件（v2.4 增强）")
async def list_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),  # 【S4】放宽上限（原 100），质检页文件选择列表常用大分页
    search: Optional[str] = Query(None, description="按文件名搜索"),
    data_type: Optional[str] = Query(None, description="按数据类型过滤: geometry/table/archive/unknown"),
    geometry_type: Optional[str] = Query(None, description="按几何子类型过滤: Point/LineString/Polygon/3d/table"),
    file_format: Optional[str] = Query(None, description="按文件格式过滤: shp/geojson/gpkg/..."),
    standard_id: Optional[str] = Query(None, description="按关联标准过滤"),
    from_date: Optional[str] = Query(None, alias="from", description="开始日期 YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, alias="to", description="结束日期 YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    base = select(StoredFile).where(api_key.scope_clause(StoredFile))

    if search:
        base = base.where(StoredFile.original_name.ilike(f"%{search}%"))
    if data_type:
        base = base.where(StoredFile.data_type == data_type)
    if geometry_type:
        # 【v2.10.5】计数驱动的类型筛选：mixed 文件按三维计数列命中，
        # 不再依赖单一 geometry_type 标签（多类型文件此前会被漏掉）
        _cnt_col = {
            "Point": StoredFile.point_count,
            "LineString": StoredFile.line_count,
            "Polygon": StoredFile.polygon_count,
        }.get(geometry_type)
        if _cnt_col is not None:
            base = base.where(_cnt_col > 0)
        else:
            base = base.where(StoredFile.geometry_type == geometry_type)
    if file_format:
        base = base.where(StoredFile.file_format == file_format.lower())
    if standard_id:
        if standard_id == "none":
            base = base.where(StoredFile.standard_id.is_(None))
        else:
            base = base.where(StoredFile.standard_id == standard_id)
    if from_date:
        base = base.where(StoredFile.created_at >= _parse_date_bound(from_date, day_start=True))
    if to_date:
        base = base.where(StoredFile.created_at <= _parse_date_bound(to_date, day_start=False))

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    rows_q = base.order_by(StoredFile.created_at.desc())
    rows_q = rows_q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(rows_q)).scalars().all()

    items = [StoredFileBrief.model_validate(sf) for sf in rows]

    return PaginatedFiles(items=items, total=total, page=page, page_size=page_size)


# ── 统计 API ──────────────────────────────────────────────────────────────────

@router.get("/data/stats", summary="文件存储统计（排除临时压缩包）")
async def get_storage_stats(
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    # 统计排除 is_archive=True 的临时压缩包记录
    base_filter = and_(api_key.scope_clause(StoredFile), StoredFile.is_archive == False)  # noqa: E712

    total_count_q = select(func.count()).select_from(StoredFile).where(base_filter)
    total_size_q = select(func.coalesce(func.sum(StoredFile.file_size), 0)).where(base_filter)
    total_count = (await db.execute(total_count_q)).scalar() or 0
    total_size = (await db.execute(total_size_q)).scalar() or 0

    # 按数据类型分布
    type_dist_q = (
        select(StoredFile.data_type, func.count())
        .where(base_filter)
        .group_by(StoredFile.data_type)
    )
    type_dist_rows = (await db.execute(type_dist_q)).all()
    by_data_type = {r[0]: r[1] for r in type_dist_rows}

    # 按几何子类型分布
    geom_dist_q = (
        select(StoredFile.geometry_type, func.count())
        .where(base_filter, StoredFile.geometry_type.isnot(None))
        .group_by(StoredFile.geometry_type)
    )
    geom_dist_rows = (await db.execute(geom_dist_q)).all()
    by_geometry_type = {r[0]: r[1] for r in geom_dist_rows}

    # 按格式分布
    fmt_dist_q = (
        select(StoredFile.file_format, func.count())
        .where(base_filter)
        .group_by(StoredFile.file_format)
    )
    fmt_dist_rows = (await db.execute(fmt_dist_q)).all()
    by_format = {r[0]: r[1] for r in fmt_dist_rows}

    # 关联标准统计
    std_count_q = select(func.count()).select_from(StoredFile).where(
        base_filter,
        StoredFile.standard_id.isnot(None),
    )
    std_count = (await db.execute(std_count_q)).scalar() or 0

    # 【v2.10.5】要素类型总量（聚合冗余列求和）
    def _sum_col(col):
        return select(func.coalesce(func.sum(col), 0)).where(base_filter)

    total_points = (await db.execute(_sum_col(StoredFile.point_count))).scalar() or 0
    total_lines = (await db.execute(_sum_col(StoredFile.line_count))).scalar() or 0
    total_polygons = (await db.execute(_sum_col(StoredFile.polygon_count))).scalar() or 0

    return {
        "total_files": total_count,
        "total_size": total_size,
        "by_data_type": by_data_type,
        "by_geometry_type": by_geometry_type,
        "by_format": by_format,
        "with_standard": std_count,
        "without_standard": total_count - std_count,
        "feature_totals": {
            "point": total_points,
            "line": total_lines,
            "polygon": total_polygons,
            "total": total_points + total_lines + total_polygons,
        },
    }


# ── 详情 / 下载 / 删除 ───────────────────────────────────────────────────────

@router.get("/data/{file_id}/features", summary="查询文件的空间要素（GeoJSON）")
async def get_file_features(
    file_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(2000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    offset = (page - 1) * page_size
    rows, total = await query_features_by_file(
        db, file_id, api_key.owner_id, offset=offset, limit=page_size,
    )
    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "id": r["id"],
                "feature_index": r["feature_index"],
                "layer_name": r["layer_name"],
                "source_name": r["source_name"],
                "geometry_type": r["geometry_type"],
                **(r["properties"] or {}),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"total": total, "returned": len(features), "file_id": file_id},
    }


@router.get("/data/{file_id}", response_model=StoredFileDetail, summary="查询文件元数据（v2.4 增强）")
async def get_file_detail(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")
    return sf


@router.get("/data/{file_id}/structure", response_model=FileStructureResponse, summary="查看文件结构（图层/字段）")
async def get_file_structure(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """返回文件的完整结构信息，用于前端结构预览弹窗。

    v2.10.5：图层清单来自 stored_file_layers 聚合表（真实图层 × 类型计数）；
    无聚合行的历史数据回退为单图层伪结构（保持兼容）。
    """
    from app.models.stored_file_layer import StoredFileLayer

    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")

    # 真实图层行（按要素数降序）
    layer_rows = (await db.execute(
        select(StoredFileLayer)
        .where(StoredFileLayer.stored_file_id == file_id)
        .order_by(StoredFileLayer.feature_count.desc())
    )).scalars().all()

    fields = sf.field_schema if isinstance(sf.field_schema, list) else []
    layers: list[LayerStructure] = []
    if layer_rows:
        for lr in layer_rows:
            layers.append(LayerStructure(
                name=lr.layer_name,
                geometry_type=lr.geometry_type or ("mixed" if sf.data_type == "geometry" else None),
                field_count=len(fields),
                feature_count=lr.feature_count,
                point_count=lr.point_count,
                line_count=lr.line_count,
                polygon_count=lr.polygon_count,
                fields=fields if len(layer_rows) == 1 else [],
            ))
    elif sf.field_schema:
        # 历史数据兜底：无聚合行时维持旧的单图层伪结构
        layers.append(LayerStructure(
            name=sf.original_name,
            geometry_type="mixed" if sf.data_type == "geometry" else None,
            field_count=len(fields),
            feature_count=sf.feature_count,
            fields=fields,
        ))

    return FileStructureResponse(
        file_id=sf.id,
        file_format=sf.file_format,
        data_type=sf.data_type,
        detected_crs=sf.detected_crs,
        layer_count=len(layers) if layers else sf.layer_count,
        feature_count=sf.feature_count,
        point_count=sf.point_count,
        line_count=sf.line_count,
        polygon_count=sf.polygon_count,
        layers=layers,
    )


_EXPORT_FORMATS = {"geojson", "shp", "gpkg", "csv", "mdb", "gdb"}


async def _resolve_export_wkt(db, srid: int | None, detected_crs: str | None) -> str | None:
    """解析导出用 CRS WKT：spatial_ref_sys.srtext → detected_crs(WKT) → pyproj → None。"""
    if srid:
        row = (
            await db.execute(
                text("SELECT srtext FROM spatial_ref_sys WHERE srid = :s"), {"s": srid}
            )
        ).mappings().first()
        if row and row["srtext"]:
            return row["srtext"]
        if srid < CUSTOM_SRID_BASE:  # 标准 EPSG，pyproj 兜底
            try:
                from pyproj import CRS
                return CRS.from_epsg(srid).to_wkt()
            except Exception as _crs_e:
                logger.warning(f"pyproj EPSG 解析失败 srid={srid}: {_crs_e}")
    if detected_crs and "PROJCS" in detected_crs:
        try:
            from pyproj import CRS
            CRS.from_wkt(detected_crs)  # 验证合法性，非法 WKT 不传给 fiona（避免 500）
            return detected_crs
        except Exception as _crs_e:
            logger.warning(f"pyproj WKT 校验失败，不采用 detected_crs: {_crs_e}")
    return None


def _run_export_sync(func, *args):
    """在线程池执行同步导出函数，避免阻塞 FastAPI 事件循环。"""
    import asyncio
    import functools
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, functools.partial(func, *args))


@router.get("/data/{file_id}/download", summary="下载原始文件 / 导出特征数据")
async def download_file(
    file_id: str,
    format: str | None = Query(None, description="导出格式: geojson/shp/gpkg/csv/mdb/gdb"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """下载文件内容。

    v2.19.0 语义（下载 → 导出）：
    - 未显式传 `format` 且记录留有原始字节（file_data）→ 返回原始文件（向后兼容）；
    - 其余情况：按 stored_file_id 聚合三张特征分区表，实时导出为
      geojson / shp(zip) / gpkg / csv / mdb（MDB 分层 Access 库）。
    - ZIP 壳（is_archive）→ 400 提示展开；无原始字节且无特征 → 410。
    """
    from urllib.parse import quote

    from app.services.export_service import (
        _safe_stem,
        export_csv,
        export_gdb,
        export_gpkg,
        export_mdb,
        export_shapefile_zip,
    )
    from app.services.feature_store import load_file_features_geojson

    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")

    fmt = (format or "geojson").lower()
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail="不支持的导出格式: 可选 geojson / shp / gpkg / csv / mdb / gdb",
        )

    # ── 分支 1：未显式指定格式且留原始字节 → 下载原始文件（向后兼容）──────
    if not format and sf.file_data:
        encoded_name = quote(sf.original_name)
        return Response(
            content=sf.file_data,
            media_type=sf.content_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
                "Content-Length": str(sf.file_size),
            },
        )

    # ── 分支 2：导出管线 ────────────────────────────────────────────────────
    if sf.is_archive:
        raise HTTPException(
            status_code=400,
            detail="压缩包无独立特征数据，请展开后对内部文件导出",
        )

    features = await load_file_features_geojson(db, sf.id)
    if not features:
        raise HTTPException(
            status_code=410,
            detail="原始文件数据未留存且无可导出要素，请重新上传",
        )
    payload = json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
    ).encode("utf-8")
    srid = sf.detected_crs_srid or 4326
    stem = _safe_stem(sf.original_name)
    base = f"{stem}_epsg_{srid}"

    try:
        if fmt == "geojson":
            # 复用转换结果的 CRS 声明逻辑（自定义 SRID 写 VCS-CUSTOM 命名空间）
            from app.api.v1.spatial import _inject_crs_member
            body = await _run_export_sync(_inject_crs_member, payload, srid)
            media_type, filename = "application/geo+json", f"{stem}_epsg_{srid}.geojson"
        elif fmt == "shp":
            wkt = await _resolve_export_wkt(db, srid, sf.detected_crs)
            body = await _run_export_sync(export_shapefile_zip, payload, wkt, base)
            media_type, filename = "application/zip", f"{base}.zip"
        elif fmt == "gpkg":
            from app.services.export_service import _group_by_geometry
            wkt = await _resolve_export_wkt(db, srid, sf.detected_crs)
            multi = len(_group_by_geometry(features)) > 1
            body = await _run_export_sync(export_gpkg, payload, wkt, base)
            media_type, filename = (
                ("application/zip", f"{base}_gpkg.zip") if multi
                else ("application/geopackage+sqlite3", f"{base}.gpkg")
            )
        elif fmt == "mdb":
            body = await _run_export_sync(export_mdb, payload, base)
            media_type, filename = "application/msaccess", f"{base}.mdb"
        elif fmt == "gdb":
            wkt = await _resolve_export_wkt(db, srid, sf.detected_crs)
            body = await _run_export_sync(export_gdb, payload, wkt, base)
            # 目录格式统一 zip 交付（解压后 *.gdb 目录可直接拖入 ArcGIS Pro）
            media_type, filename = "application/zip", f"{base}_gdb.zip"
        else:  # csv
            body = await _run_export_sync(export_csv, payload, base)
            media_type, filename = "text/csv; charset=utf-8", f"{base}.csv"
    except Exception:
        logger.exception("export_failed", extra={"export_file_id": file_id, "export_fmt": fmt, "export_srid": srid})
        raise HTTPException(
            status_code=500,
            detail="导出失败，请稍后重试或联系管理员",
        ) from None

    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(body)),
        },
    )


# ── 元数据编辑 ────────────────────────────────────────────────────────────────

@router.patch("/data/{file_id}", response_model=StoredFileDetail, summary="更新文件元数据")
async def update_file_metadata(
    file_id: str,
    body: StoredFileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """更新文件的 description、standard_id、standard_table_id。"""
    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")

    if body.description is not None:
        sf.description = body.description
    if body.standard_id is not None:
        sf.standard_id = body.standard_id
        # 更新标准名称
        if body.standard_id:
            from app.models.data_standard import DataStandard
            ds_result = await db.execute(
                select(DataStandard.name).where(DataStandard.id == body.standard_id)
            )
            sf.data_standard_name = ds_result.scalar_one_or_none()
        else:  # pragma: no cover - standard_id 空串（""）会违反 standard_id FK，commit 必失败，无法作为成功路径到达
            sf.data_standard_name = None
    if body.standard_table_id is not None:
        sf.standard_table_id = body.standard_table_id
    if body.data_standard_name is not None:
        sf.data_standard_name = body.data_standard_name

    await db.commit()
    await db.refresh(sf)
    return sf


# ── 批量关联标准 ──────────────────────────────────────────────────────────────

class BatchClassifyRequest(_BaseModel):
    file_ids: list[str]
    standard_id: str | None = None
    standard_table_id: str | None = None


@router.post("/data/batch-classify", summary="批量关联数据标准")
async def batch_classify(
    body: BatchClassifyRequest,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """批量设置文件关联的数据标准。"""
    if not body.file_ids:
        raise HTTPException(status_code=400, detail="file_ids 不能为空")

    # 获取标准名称
    standard_name = None
    if body.standard_id:
        from app.models.data_standard import DataStandard
        ds_result = await db.execute(
            select(DataStandard.name).where(DataStandard.id == body.standard_id)
        )
        standard_name = ds_result.scalar_one_or_none()

    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id.in_(body.file_ids),
            api_key.scope_clause(StoredFile),
        )
    )
    files = result.scalars().all()
    updated = 0
    for sf in files:
        sf.standard_id = body.standard_id
        sf.standard_table_id = body.standard_table_id
        sf.data_standard_name = standard_name
        updated += 1

    await db.commit()
    return {"updated": updated}


# ── 删除 API ──────────────────────────────────────────────────────────────────

@router.delete("/data/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")
    await _delete_file_recursive(db, api_key, sf)
    await db.commit()


async def _delete_file_recursive(
    db: AsyncSession, api_key: ApiKey, sf: StoredFile,
) -> None:
    """删除记录及其子记录（archive 的子文件），每个记录先物理删分区再删记录。"""
    # 父记录：递归收集子记录
    if sf.source_archive is None:
        subs = (await db.execute(
            select(StoredFile).where(
                StoredFile.source_archive == sf.original_name,
                api_key.scope_clause(StoredFile),
            )
        )).scalars().all()
        for sub in subs:
            await _delete_file_recursive(db, api_key, sub)
    # 本记录：物理删分区（秒级 DETACH+DROP）再删记录
    await drop_partition(db, sf.id)
    await db.delete(sf)


async def _delete_existing_by_name(
    db: AsyncSession, filename: str, api_key: ApiKey,
) -> int:
    """上传时同名文件自动删除旧记录（含子记录和分区数据），返回删除数量。

    规则：如果已存在同 original_name 的文件记录（不限 archive 非 archive），
    递归删除该记录及其所有子记录和空间分区数据。
    """
    existing = (await db.execute(
        select(StoredFile).where(
            StoredFile.original_name == filename,
            api_key.scope_clause(StoredFile),
        )
    )).scalars().all()

    for sf in existing:
        await _delete_file_recursive(db, api_key, sf)
        logger.info(f"[同名覆盖] 删除旧记录 {sf.original_name} (id={sf.id})")

    return len(existing)


@router.post("/data/batch-delete", status_code=status.HTTP_204_NO_CONTENT)
async def batch_delete_files(
    file_ids: list[str],
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    if not file_ids:
        raise HTTPException(status_code=400, detail="file_ids 不能为空")
    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id.in_(file_ids),
            api_key.scope_clause(StoredFile),
        )
    )
    sf_list = result.scalars().all()
    for sf in sf_list:
        # 【v2.10 分区化】递归删除子记录 + 物理删分区（DETACH+DROP 秒级）
        await _delete_file_recursive(db, api_key, sf)
    await db.commit()


# ── POST /data/cleanup-archives ──────────────────────────────────────────────

@router.post("/data/cleanup-archives", status_code=status.HTTP_200_OK, summary="清理过期压缩包壳记录（保留解压数据）")
async def cleanup_old_archives(
    days: int = Query(1, ge=1, le=30, description="清理 N 天前的压缩包记录"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """删除当前用户有权限的、创建时间超过 N 天的压缩包壳记录（is_archive=True）。

    【v2.19.6 方案 A】只删除压缩包壳记录本身，**保留其子文件记录与
    PostGIS 分区数据**：
      - 压缩包只是"上传批次"外壳，真实数据属于解压出的子记录；
      - 清理时先将子记录 source_archive 解除关联（提升为独立文件记录），
        再删除壳记录——数据永不因自动清理丢失；
      - 显式删除（DELETE /data/{id}、batch-delete、同名覆盖）仍走
        _delete_file_recursive 递归删除语义，不受影响。
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    archives = (await db.execute(
        select(StoredFile).where(
            StoredFile.is_archive == True,  # noqa: E712
            StoredFile.created_at < cutoff,
            api_key.scope_clause(StoredFile),
        )
    )).scalars().all()

    deleted = 0
    for arc in archives:
        # ① 解除子记录关联 → 提升为独立文件记录（数据保留）
        subs = (await db.execute(
            select(StoredFile).where(
                StoredFile.source_archive == arc.original_name,
                api_key.scope_clause(StoredFile),
            )
        )).scalars().all()
        for sub in subs:
            sub.source_archive = None
        # ② 只删壳记录（不删分区、不删子记录）
        await db.delete(arc)
        deleted += 1
        logger.info(
            f"[cleanup-archives] 删除过期压缩包壳 {arc.original_name} (id={arc.id}), "
            f"保留 {len(subs)} 个子文件记录及其分区数据"
        )

    await db.commit()
    return {"deleted": deleted, "cutoff_days": days}


# ── 要素展开辅助 ──────────────────────────────────────────────────────────────

async def _handle_gdb_group(
    db: AsyncSession,
    gdb_files: list,
    gdb_dir: str,
    archive_filename: str,
    api_key_id: str,
    standard_id: Optional[str],
    standard_table_id: Optional[str],
    resolved_standard_name: Optional[str],
    archive_record: StoredFile,
    api_key_owner_id: Optional[str] = None,
) -> tuple[int, list, list, Optional[str]]:
    """处理一个 GDB 组：准备临时目录 + 创建每层子记录（file_data=None）→ 返回逐层展开子任务。

    【v2.9】不再生成 GeoJSON 中间产物：GDB layer 由 _load_gdb_layer_async 直接
    以原生几何（POINT/LINESTRING/POLYGON/Multi* 等）写入 PostGIS 分表。

    Returns:
        (extracted_count, extraction_details, sub_tasks, tmp_dir)
        tmp_dir 非空时由调用方在全部子任务完成后清理（shutil.rmtree）。
    """
    extracted_count = 0
    extraction_details: list = []
    sub_tasks: list = []

    gdb_info = await asyncio.to_thread(prepare_gdb, gdb_files, gdb_dir, None)
    if not gdb_info.gdb_path or not gdb_info.layers:
        if gdb_info.tmp_dir:
            try:
                shutil.rmtree(gdb_info.tmp_dir, ignore_errors=True)
            except Exception as _clean_e:
                logger.warning(f"GDB 无效时临时目录清理失败 {gdb_info.tmp_dir}: {_clean_e}")
        return extracted_count, extraction_details, sub_tasks, None

    # 父 archive 记录累加层数（聚合语义：layer_count = GDB 层数）
    archive_record.layer_count = (archive_record.layer_count or 0) + len(gdb_info.layers)

    # 【v2.10.2】GDB 是目录结构无单文件大小，子记录 file_size 按目录总字节数均摊到各层，
    # 避免列表/详情"大小"显示 0 B；均摊保证 stats.total_size 聚合不虚高不丢失。
    gdb_total_size = sum(getattr(ef, "file_size", 0) or 0 for ef in gdb_files)
    layer_count = len(gdb_info.layers)
    base_size = gdb_total_size // layer_count if layer_count else 0
    remainder = gdb_total_size % layer_count if layer_count else 0

    for i, layer in enumerate(gdb_info.layers):
        sf_id = str(uuid.uuid4())
        layer_sf = StoredFile(
            id=sf_id,
            api_key_id=api_key_id,
            original_name=f"{archive_filename}/{gdb_info.gdb_basename}_{layer.name}",
            layer_name=layer.name,
            content_type="application/octet-stream",
            file_size=base_size + (1 if i < remainder else 0),
            file_data=None,
            source_archive=archive_filename,
            is_archive=False,
            standard_id=standard_id,
            standard_table_id=standard_table_id,
            data_standard_name=resolved_standard_name,
            file_format="gdb",
            geometry_type=layer.geometry_type,
            detected_crs=layer.crs_wkt,
        )
        db.add(layer_sf)
        extracted_count += 1
        extraction_details.append({
            "file_id": sf_id,
            "name": layer_sf.original_name,
            "type": "GDB",
            "features": 0,
            "status": "ok",
        })
        # v2.10.5：SRID 在主会话（异步）解析并注册自定义坐标系，
        # 结果落库 layer_sf.detected_crs_srid / crs_is_custom 后随主事务提交；
        # 同步线程只接收现成 SRID，避免线程内重复注册与跨会话写元数据的竞态。
        layer_srid = await _ingest_srid(layer_sf, db)
        sub_tasks.append(_load_gdb_layer_async(
            sf_id, gdb_info.gdb_path, layer.name, api_key_owner_id, srid=layer_srid,
        ))

    return extracted_count, extraction_details, sub_tasks, gdb_info.tmp_dir


def _sync_load_gdb_layer(
    stored_file_id: str,
    gdb_path: str,
    layer_name: str,
    api_key_id: str,
    srid: int = 4326,
) -> dict:
    """（同步线程内）将单个 GDB layer 要素流式写入分区表。

    v2.10：read_layer_features 为生成器（流式）→ write_features_batch_sync 直接消费，
    大图层内存恒定、无截断；整个流程在 asyncio.to_thread 线程池执行，不阻塞事件循环。

    v2.10.1：读取全程持有 gdb_read_lock(gdb_path) —— GDAL 数据源非线程安全，
    多个 GDB 层子任务并发 to_thread 同时 fiona.open 同一 GDB 目录会抛
    Permission denied（CPLE_OpenFailedError）导致要素静默为 0；
    以 GDB 路径为粒度互斥，保证同一 GDB 的读取串行化。
    v2.10.1：api_key_id 参数实际接收 api_key.owner_id（super admin → None）——
    分表 api_key_id 列对外键校验 api_keys 表，虚拟 id 不存在会 FK 失败；
    StoredFile 记录则始终用 api_key.id（见 _handle_gdb_group）。
    v2.10.5：srid 由调用方（_handle_gdb_group 主会话）预先解析注册后传入，
    本函数不再自行 crs_to_srid；写入完成后做坐标域一致性校验。
    """
    stats = _empty_feature_stats(stored_file_id)
    try:
        with SyncSessionLocal() as sub_db:
            lock = gdb_read_lock(gdb_path)
            with lock:
                batch = write_features_batch_sync(
                    sub_db, stored_file_id, api_key_id,
                    f"{Path(gdb_path).stem}_{layer_name}",
                    read_layer_features(gdb_path, layer_name),
                    srid=srid,
                )
                stats["written"] = batch["written"]
                stats["skipped_empty"] = batch["skipped_empty"]
                stats["skipped_no_geometry"] = batch["skipped_no_geometry"]
                stats["total"] = batch.get("total", 0)
                # 入库后坐标域校验（v2.10.5 A3 防线）
                crs_warn = validate_loaded_extent_sync(sub_db, stored_file_id, srid)
                if crs_warn:
                    logger.warning("CRS 坐标域校验告警 GDB layer %s: %s", layer_name, crs_warn)
                    stats["crs_warning"] = crs_warn
            sub_db.commit()
    except Exception as e:
        logger.warning(f"GDB layer 要素展开失败 {layer_name}: {e}", exc_info=True)
        stats["error"] = f"{layer_name}: {e}"
    return stats


async def _load_gdb_layer_async(
    stored_file_id: str,
    gdb_path: str,
    layer_name: str,
    api_key_id: str,
    srid: int = 4326,
) -> dict:
    """将单个 GDB layer 的要素直接写入 PostGIS 分区表（原生几何）。

    独立会话（线程池同步会话）；仅写分表并返回统计（元数据回填由主流程在
    gather 后统一完成，避免跨会话读取未提交子记录的时序问题）。单层失败不影响整单。
    """
    return await asyncio.to_thread(
        _sync_load_gdb_layer, stored_file_id, gdb_path, layer_name, api_key_id, srid
    )


async def _load_extracted_features_async(
    stored_file_id: str, filename: str,
    content: bytes, api_key_id: str, srid: int = 4326,
) -> dict:
    """异步将解压出的矢量子文件写入分表（独立会话，单文件失败不影响整单）。"""
    from app.db.session import db_task_semaphore
    stats = _empty_feature_stats(stored_file_id)
    if not _is_vector_file(filename):
        return stats
    try:
        async with db_task_semaphore:
            async with AsyncSessionLocal() as sub_db:
                features = await asyncio.to_thread(extract_features_from_bytes, content, filename)
                batch = await write_features_batch(
                    sub_db, stored_file_id, api_key_id,
                    Path(filename).name, features,
                    srid=srid,
                )
                stats["written"] = batch["written"]
                stats["skipped_empty"] = batch["skipped_empty"]
                stats["skipped_no_geometry"] = batch["skipped_no_geometry"]
                if features:
                    stats["geometry_type"] = normalize_stored_geometry_type(
                        features[0].get("geometry", {}).get("type")
                    )
                await sub_db.commit()
                # 入库后坐标域校验（v2.10.5 A3 防线：标称坐标系类型与实际坐标矛盾时告警）
                crs_warn = await validate_loaded_extent(sub_db, stored_file_id, srid)
                if crs_warn:
                    logger.warning("CRS 坐标域校验告警 %s: %s", filename, crs_warn)
                    stats["crs_warning"] = crs_warn
    except Exception as e:
        logger.warning(f"子文件要素展开失败 {filename}: {e}")
        stats["error"] = f"{filename}: {e}"
    return stats


async def _load_shp_group_features_async(
    stored_file_id: str, shp_filename: str,
    shp_content: bytes, companion_contents: dict[str, bytes], api_key_id: str,
    srid: int = 4326,
) -> dict:
    """异步将 shapefile 组（.shp+.dbf+.shx 等）写入临时目录后用 Fiona 整体读取。

    使用独立会话，单文件失败不影响整单入库。

    Args:
        stored_file_id: 目标 StoredFile id
        shp_filename: .shp 文件名
        shp_content: .shp 二进制内容
        companion_contents: {'.dbf': bytes, '.shx': bytes, ...} 伴生文件内容
        api_key_id: 所属 API Key id
        srid: 源数据 SRID（由 detected_crs 解析，默认 4326）

    Returns:
        统计 dict（_empty_feature_stats 结构）
    """
    import tempfile, os
    from app.db.session import db_task_semaphore
    stats = _empty_feature_stats(stored_file_id)
    logger.info(f"[_load_shp_group_features] 开始: {shp_filename}, stored_file_id={stored_file_id[:12]}..., companions={list(companion_contents.keys())}")

    stem = Path(shp_filename).stem
    tmp_dir = tempfile.mkdtemp(prefix="shp_")
    _tmp_path = Path(tmp_dir)
    TEMP_DIRS.add(_tmp_path)
    try:
        # 写入所有组件到临时目录
        shp_path = os.path.join(tmp_dir, f"{stem}.shp")
        with open(shp_path, "wb") as f:
            f.write(shp_content)
        for ext, data in companion_contents.items():
            if ext == '.shp':
                continue
            comp_path = os.path.join(tmp_dir, f"{stem}{ext}")
            with open(comp_path, "wb") as f:
                f.write(data)

        written = os.listdir(tmp_dir)
        logger.info(f"Shapefile组临时目录: {tmp_dir}, 文件: {written}")

        # Fiona 读取（会自动查找同目录下的 .dbf/.shx）
        features = await asyncio.to_thread(
            extract_features_from_file, shp_path, "ESRI Shapefile"
        )
        logger.info(f"Shapefile组提取 {shp_filename}: {len(features)} 要素")
        async with db_task_semaphore:
            async with AsyncSessionLocal() as sub_db:
                batch = await write_features_batch(
                    sub_db, stored_file_id, api_key_id,
                    Path(shp_filename).name, features,
                    srid=srid,
                )
                stats["written"] = batch["written"]
                stats["skipped_empty"] = batch["skipped_empty"]
                stats["skipped_no_geometry"] = batch["skipped_no_geometry"]
                if features:
                    stats["geometry_type"] = normalize_stored_geometry_type(
                        features[0].get("geometry", {}).get("type")
                    )
                await sub_db.commit()
                # 入库后坐标域校验（v2.10.5 A3 防线）
                crs_warn = await validate_loaded_extent(sub_db, stored_file_id, srid)
                if crs_warn:
                    logger.warning("CRS 坐标域校验告警 %s: %s", shp_filename, crs_warn)
                    stats["crs_warning"] = crs_warn
    except Exception as e:
        logger.warning(f"Shapefile 组展开失败 {shp_filename}: {e}")
        stats["error"] = f"{shp_filename}: {e}"
    finally:
        TEMP_DIRS.discard(_tmp_path)
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as _clean_e:  # pragma: no cover - ignore_errors=True 已吞绝大多数失败，此为防御性兜底
            logger.warning(f"Shapefile 组临时目录清理失败 {tmp_dir}: {_clean_e}")
    return stats


async def _load_all_features_to_spatial_async(
    stored_file_id: str, filename: str,
    content: bytes, api_key_id: str, srid: int = 4326,
) -> dict:
    """异步将上传的矢量文件展开写入 spatial_features 表（独立会话）。

    Args:
        srid: 源数据 SRID（由 detected_crs 解析，默认 4326）

    Returns:
        统计 dict（_empty_feature_stats 结构）；非矢量扩展名直接返回空统计。
    """
    from app.db.session import db_task_semaphore
    stats = _empty_feature_stats(stored_file_id)
    ext = Path(filename).suffix.lower()
    # 注: .dwg 不在跳过名单——经 LibreDWG dwg2dxf 转 DXF 后可解析要素
    # （tools/libredwg 缺失时 dwg_converter 优雅降级为空，不报错）
    if ext in ('.exe', '.bat', '.py', '.sh', '.zip', '.tar', '.gz', '.7z', '.rar', '.txt', '.pdf', '.html', '.csv', '.xlsx', '.xls'):
        return stats

    try:
        features = await asyncio.to_thread(extract_features_from_bytes, content, filename)
        async with db_task_semaphore:
            async with AsyncSessionLocal() as sub_db:
                batch = await write_features_batch(
                    sub_db, stored_file_id, api_key_id,
                    Path(filename).name, features,
                    srid=srid,
                )
                stats["written"] = batch["written"]
                stats["skipped_empty"] = batch["skipped_empty"]
                stats["skipped_no_geometry"] = batch["skipped_no_geometry"]
                if features:
                    stats["geometry_type"] = normalize_stored_geometry_type(
                        features[0].get("geometry", {}).get("type")
                    )
                await sub_db.commit()
                # 入库后坐标域校验（v2.10.5 A3 防线：标称坐标系类型与实际坐标矛盾时告警）
                crs_warn = await validate_loaded_extent(sub_db, stored_file_id, srid)
                if crs_warn:
                    logger.warning("CRS 坐标域校验告警 %s: %s", filename, crs_warn)
                    stats["crs_warning"] = crs_warn
    except Exception as e:
        logger.warning(f"空间要素展开失败 {filename}: {e}")
        stats["error"] = f"{filename}: {e}"
    return stats


async def _load_mdb_features_async(
    stored_file_id: str, table_name: str,
    features: list[dict], api_key_id: str, srid: int = 4326,
) -> dict:
    """异步将 MDB 提取的要素直接写入 spatial_features 表（跳过 Fiona 解析）。

    Args:
        stored_file_id: StoredFile ID
        table_name: MDB 表名
        features: 已转换的 GeoJSON Feature 列表（由 mdb_processor 提供）
        api_key_id: API Key ID (owner_id)
        srid: 源数据 SRID（默认 4326）

    Returns:
        统计 dict（_empty_feature_stats 结构）
    """
    from app.db.session import db_task_semaphore
    stats = _empty_feature_stats(stored_file_id)

    if not features:
        return stats

    try:
        # 直接使用已转换的 features，跳过 Fiona 重新解析
        async with db_task_semaphore:
            async with AsyncSessionLocal() as sub_db:
                batch = await write_features_batch(
                    sub_db, stored_file_id, api_key_id,
                    table_name, features,
                    srid=srid,
                )
                stats["written"] = batch["written"]
                stats["skipped_empty"] = batch["skipped_empty"]
                stats["skipped_no_geometry"] = batch["skipped_no_geometry"]
                if features:
                    stats["geometry_type"] = normalize_stored_geometry_type(
                        features[0].get("geometry", {}).get("type")
                    )
                await sub_db.commit()
                # 入库后坐标域校验
                crs_warn = await validate_loaded_extent(sub_db, stored_file_id, srid)
                if crs_warn:
                    logger.warning("CRS 坐标域校验告警 %s: %s", table_name, crs_warn)
                    stats["crs_warning"] = crs_warn
    except Exception as e:
        logger.warning(f"MDB 要素展开失败 {table_name}: {e}")
        stats["error"] = f"{table_name}: {e}"
    return stats


@router.post("/data/{file_id}/reindex", summary="重新索引已有文件的空间要素")
async def reindex_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """从 stored_files.file_data 重新提取并写入 spatial_features，修复 features=0 的历史数据。"""
    sf = await db.get(StoredFile, file_id)
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not api_key.owns(sf):
        raise HTTPException(status_code=403, detail="无权操作此文件")
    # 【优化#5】上传文件仅做提取，不留存原始字节 — 无法重新索引
    if not sf.file_data:
        raise HTTPException(
            status_code=410,
            detail="原始文件数据已清理（仅做数据入库），无法重新索引。请重新上传。",
        )

    # 先清除旧要素
    from app.models.spatial_feature import PointFeature, LineFeature, PolygonFeature
    from sqlalchemy import delete
    for model in (PointFeature, LineFeature, PolygonFeature):
        await db.execute(
            delete(model).where(model.stored_file_id == file_id)
        )

    # 重新提取并写入（独立会话）
    stats = await _load_all_features_to_spatial_async(
        file_id, sf.original_name, sf.file_data, sf.api_key_id,
        srid=await _ingest_srid(sf, db),
    )
    # 【S1 修复】子任务独立会话已提交，失效主会话缓存后读取最新计数
    await db.flush()
    # 图层 × 类型聚合（v2.10.5）— SAVEPOINT 隔离，失败不阻断 reindex 主流程
    try:
        async with db.begin_nested():
            await aggregate_layer_stats(db, file_id, sf.api_key_id or "")
    except Exception as agg_e:
        logger.warning("reindex 图层统计聚合失败 %s: %s", file_id, agg_e)
    # expire_all 会失效 sf 的属性，先缓存文件名，避免后续同步懒加载 → MissingGreenlet
    original_name = sf.original_name
    db.expire_all()
    await db.commit()

    # 统计结果
    feat_count = stats.get("written") or 0
    warning = stats.get("error")
    warning = warning or (
        "SHP文件缺少配套的.dbf/.shx文件，无法提取要素。请重新上传为ZIP压缩包（包含.shp+.dbf+.shx）"
        if feat_count == 0 and original_name.lower().endswith('.shp') else None
    )

    return {
        "file_id": file_id,
        "filename": original_name,
        "features_indexed": feat_count,
        "skipped_empty": stats.get("skipped_empty") or 0,
        "warning": warning,
    }


# ── 质检 API（保留原有逻辑） ──────────────────────────────────────────────────

@router.post(
    "/data/{file_id}/quality-check",
    status_code=status.HTTP_200_OK,
    summary="对已存储文件执行质量检查（v2 五维评分）",
)
async def check_stored_file_quality(
    file_id: str,
    data_standard_id: Optional[str] = Form(None, description="数据标准 ID（可选，优先于文件关联的标准）"),
    standard_table_id: Optional[str] = Form(None, description="标准表 ID（可选，指定后仅检查该图层）"),
    geometry_type: Optional[str] = Form(None, description="几何类型 Point/LineString/Polygon（可选）"),
    topology_config: Optional[str] = Form(None, description="拓扑检查配置 JSON（可选）"),
    layers: Optional[str] = Form(None, description="质检圈定图层名 JSON 数组，如 [\"图层A\",\"图层B\"]；空=全部图层"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    from app.models.quality_report import QualityReport
    from app.services.quality_checker import run_quality_check
    from app.services.topology_checker import TopologyConfig

    result = await db.execute(
        select(StoredFile).where(
            StoredFile.id == file_id,
            api_key.scope_clause(StoredFile),
        )
    )
    sf = result.scalar_one_or_none()
    if not sf:
        raise HTTPException(status_code=404, detail="文件不存在或无权限访问")

    # 立即缓存文件名：run_quality_check 运行期间 AsyncSession 会过期 sf 属性，
    # 延后访问 sf.original_name 触发同步懒加载 → MissingGreenlet（异步会话禁止同步 IO）
    file_name = sf.original_name

    effective_standard_id = data_standard_id or sf.standard_id
    effective_table_id = standard_table_id or sf.standard_table_id

    if not effective_standard_id:
        raise HTTPException(
            status_code=400,
            detail="请提供 data_standard_id（或在文件上传时关联数据标准）",
        )

    topology_cfg = None
    if topology_config:
        try:
            tc = json.loads(topology_config)
            topology_cfg = TopologyConfig(
                enabled_rules=tc.get("enabled_rules", TopologyConfig().enabled_rules),
                tolerances=tc.get("tolerances", TopologyConfig().tolerances),
            )
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail="topology_config 不是有效的 JSON")

    # ── 图层圈定解析与校验（v2.10.5）────────────────────────────────
    layer_names: Optional[list[str]] = None
    if layers:
        try:
            parsed_layers = json.loads(layers)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=400, detail="layers 不是有效的 JSON 数组")
        if not isinstance(parsed_layers, list) or not all(isinstance(x, str) for x in parsed_layers):
            raise HTTPException(status_code=400, detail="layers 必须是字符串数组")
        cleaned = sorted({x.strip() for x in parsed_layers if x.strip()})
        if cleaned:
            # 以 stored_file_layers 为准校验图层名合法性（防拼写错误导致静默空检）
            from app.models.stored_file_layer import StoredFileLayer as _SFL
            known = set((await db.execute(
                select(_SFL.layer_name).where(_SFL.stored_file_id == file_id)
            )).scalars().all())
            unknown = [n for n in cleaned if n not in known]
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"未知图层: {unknown}；文件可用图层: {sorted(known)}",
                )
            layer_names = cleaned

    # 立即缓存 api_key_id：run_quality_check 运行期间 topology_checker 可能
    # 触发 db.rollback()，导致 session 内所有 ORM 对象过期 → MissingGreenlet
    api_key_id = api_key.id

    check_result = await run_quality_check(
        db=db,
        standard_id=effective_standard_id,
        stored_file_id=file_id,
        api_key_id=api_key_id,
        geometry_type=geometry_type,
        topology_config=topology_cfg,
        standard_table_id=effective_table_id,
        layer_names=layer_names,
    )

    report_id = str(uuid.uuid4())
    report_dir = Path(settings.quality_report_dir) / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = str(report_dir / "report.json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(check_result, f, ensure_ascii=False, indent=2, default=str)

    scores = check_result["scores"]

    report = QualityReport(
        id=report_id,
        api_key_id=api_key_id,
        file_name=file_name,
        detected_format="stored_file",
        total_features=check_result["attr_summary"].get("total", 0),
        total_fields=0,
        completeness_score=scores["completeness"],
        constraint_violations=check_result["attr_results"],
        topology_violations=check_result["topo_results"],
        data_standard_id=effective_standard_id,
        standard_table_id=effective_table_id,
        attribute_summary=check_result["attr_summary"],
        topology_summary=check_result["topo_summary"],
        checked_layers=layer_names,
        score_completeness=scores["completeness"],
        score_accuracy=scores["accuracy"],
        score_consistency=scores["consistency"],
        score_uniqueness=scores["uniqueness"],
        score_topology=scores["topology"],
        score_overall=scores["overall"],
        json_report_path=json_path,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    from app.schemas.quality import QualityReportDetail
    return QualityReportDetail(
        id=report.id,
        file_name=report.file_name,
        detected_format=report.detected_format,
        total_features=report.total_features,
        total_fields=report.total_fields,
        completeness_score=report.completeness_score,
        constraint_violations=report.constraint_violations,
        topology_violations=report.topology_violations,
        data_standard_id=report.data_standard_id,
        standard_table_id=report.standard_table_id,
        attribute_summary=report.attribute_summary,
        topology_summary=report.topology_summary,
        score_completeness=report.score_completeness,
        score_accuracy=report.score_accuracy,
        score_consistency=report.score_consistency,
        score_uniqueness=report.score_uniqueness,
        score_topology=report.score_topology,
        score_overall=report.score_overall,
        html_report_path=report.html_report_path,
        json_report_path=report.json_report_path,
        checked_layers=report.checked_layers,
        created_at=report.created_at,
    )
