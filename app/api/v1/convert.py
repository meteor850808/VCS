"""
转换任务 API
POST /api/v1/convert          — 上传文件，提交转换任务
GET  /api/v1/jobs/{job_id}    — 查询任务状态与结果
GET  /api/v1/jobs             — 分页查询任务列表
DELETE /api/v1/jobs/{job_id}  — 取消/删除任务
POST /api/v1/detect           — 格式预检
GET  /api/v1/capabilities     — 查询支持格式与坐标系
"""

import uuid
import logging
import shutil
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.cache import redis_available
from app.core.constants import OUTPUT_SUFFIX_MAP
from app.core.deps import authenticated
from app.core.file_utils import validate_filename
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.conversion_job import ConversionJob, JobStatus
from app.models.quality_report import QualityReport
from app.schemas.conversion import JobBrief, JobDetail, JobSubmitResponse
from app.schemas.conversion import JobBatchDeleteRequest, JobBatchDeleteResponse, JobDeleteResult
from app.schemas.detection import FormatDetectionResponse, FieldSchemaResponse
from app.services.crs_transform import get_supported_crs_list
from app.services.format_detection import detect_format
from app.workers.celery_app import celery_app
from app.workers.tasks import run_conversion

router = APIRouter()

logger = logging.getLogger(__name__)

# 上传文件类型白名单
ALLOWED_EXTENSIONS = frozenset({
    '.shp', '.shx', '.dbf', '.prj', '.geojson', '.json',
    '.kml', '.kmz', '.gml', '.dxf', '.dwg', '.dgn',
    '.tab', '.mif', '.gpkg', '.tif', '.tiff', '.csv',
})


@router.post(
    "/convert",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="上传矢量文件并提交转换任务",
)
@limiter.limit("10/minute")
async def submit_conversion(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="矢量文件（SHP/GeoJSON/KML/KMZ/DXF/...）"),
    dst_crs: str = Form(..., description="目标坐标系，如 EPSG:4490 或 国家2000"),
    output_format: str = Form("GeoJSON"),
    src_crs: Optional[str] = Form(None),
    select_fields: Optional[str] = Form(None, description="逗号分隔字段名，如 NAME,CODE"),
    where_clause: Optional[str] = Form(None),
    repair_strategy: str = Form("auto_fix"),
    script_type: str = Form("ogr2ogr"),
    target_encoding: str = Form("UTF-8"),
    run_sandbox: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    # 校验文件名
    if not file.filename:  # pragma: no cover - multipart 无 filename 的 part 由 FastAPI 校验拦截（422）先行
        raise HTTPException(status_code=400, detail="文件名为空")
    validate_filename(file.filename)

    # 流式写入磁盘，分块检查大小（避免全量读入内存）
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    job_id = str(uuid.uuid4())
    upload_dir = Path(settings.upload_dir) / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    input_path = str(upload_dir / file.filename)

    total_size = 0
    async with aiofiles.open(input_path, 'wb') as f:
        while chunk := await file.read(1024 * 1024):  # 1 MB 分块
            total_size += len(chunk)
            if total_size > max_bytes:  # pragma: no cover - security_middleware 按同阈值预检 content-length，先拦截
                await f.close()
                Path(input_path).unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"文件超过限制 {settings.max_upload_size_mb} MB",
                )
            await f.write(chunk)

    out_name = Path(file.filename).stem + OUTPUT_SUFFIX_MAP.get(output_format, '.geojson')
    output_path = str(Path(settings.output_dir) / job_id / out_name)

    # 解析 select_fields
    fields_list = [f.strip() for f in select_fields.split(',')] if select_fields else None

    # 写入 DB
    job = ConversionJob(
        id=job_id,
        api_key_id=api_key.id,
        original_filename=file.filename,
        input_path=input_path,
        output_path=output_path,
        src_crs=src_crs,
        dst_crs=dst_crs,
        output_format=output_format,
        select_fields=fields_list,
        where_clause=where_clause,
        repair_strategy=repair_strategy,
        script_type=script_type,
        target_encoding=target_encoding,
        status=JobStatus.PENDING,
    )
    db.add(job)
    await db.commit()

    # 提交 Celery 任务。Redis 不可用（裸机本地部署未启动 Redis / Worker 未运行）时，
    # kombu 对 broker/result backend 的重试连接会使 apply_async 挂起数分钟占住请求，
    # 故先主动探测 Redis：不可用则降级为当前进程内同步执行，任务仍能完成。
    if not redis_available():
        logger.warning(f"[{job_id}] Redis 不可用，降级为进程内同步执行")
        job.status = JobStatus.RUNNING
        await db.commit()
        old_max_retries = run_conversion.max_retries
        run_conversion.max_retries = 0  # 防止任务失败时 self.retry 再走 apply_async（会再次连 Redis 挂起）
        try:
            run_conversion.apply(args=[job_id], task_id=job_id)
        except Exception as inner:
            logger.error(f"[{job_id}] 降级执行异常: {inner}")
        finally:
            run_conversion.max_retries = old_max_retries
        await db.refresh(job)
        return JobSubmitResponse(
            job_id=job_id,
            status=job.status,
            message=f"Redis 不可用，已进程内执行完成（{job.status}）",
        )

    run_conversion.apply_async(args=[job_id], task_id=job_id)
    return JobSubmitResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message=f"任务已提交，使用 GET /api/v1/jobs/{job_id} 查询进度",
    )


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="查询转换任务详情")
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(ConversionJob).where(
            ConversionJob.id == job_id,
            api_key.scope_clause(ConversionJob),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或无权限访问")
    return job


@router.get("/jobs", response_model=list[JobBrief], summary="查询任务列表（分页）")
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    query = select(ConversionJob).where(api_key.scope_clause(ConversionJob))
    if status_filter:
        query = query.where(ConversionJob.status == status_filter)
    query = query.order_by(ConversionJob.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(ConversionJob).where(
            ConversionJob.id == job_id,
            api_key.scope_clause(ConversionJob),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status in (JobStatus.RUNNING,):
        try:
            celery_app.control.revoke(job.celery_task_id, terminate=True)
        except Exception as e:
            # Redis/Celery broker 不可用时 control.revoke 同样会因连接失败抛异常
            logger.warning(f"取消任务 {job_id} 时 control.revoke 失败（broker 不可用?）: {e}")
    job.status = JobStatus.CANCELLED
    await db.commit()


def _rm_tree(path: str) -> None:
    """安全删除目录/文件，忽略不存在与删除失败。"""
    try:
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"清理任务文件失败 {path}: {e}")


async def _hard_delete_job(
    db: AsyncSession, job: ConversionJob, api_key: ApiKey
) -> tuple[bool, str | None]:
    """真正删除一个已完成/失败/已取消任务的记录、磁盘产物与关联质检报告。

    返回 (成功?, 失败原因)。运行中/待处理任务禁止硬删除（应先取消）。
    """
    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        return False, "运行中的任务不能直接删除，请先取消"
    if not api_key.owns(job):  # pragma: no cover - scope_clause 已按 access 过滤，可达即有权
        return False, "无权限删除此任务"

    # 1. 清理磁盘产物（上传目录/输出目录 — 二者均按 job_id 组织）
    if job.input_path:
        _rm_tree(str(Path(job.input_path).parent))
    if job.output_path:
        _rm_tree(str(Path(job.output_path).parent))

    # 2. 清理关联质检报告（DB 行 + 磁盘报告文件；子表 quality_rule_results/quality_violations 由 DB CASCADE 删除）
    if job.quality_report_id:
        report = await db.get(QualityReport, job.quality_report_id)
        if report:
            if report.html_report_path:
                _rm_tree(report.html_report_path)
            if report.json_report_path:
                _rm_tree(report.json_report_path)
            await db.delete(report)
    # 兼容以 job_id 关联但 quality_report_id 未回填的报告
    else:
        result = await db.execute(
            select(QualityReport).where(QualityReport.job_id == job.id)
        )
        for report in result.scalars().all():
            if report.html_report_path:
                _rm_tree(report.html_report_path)
            if report.json_report_path:
                _rm_tree(report.json_report_path)
            await db.delete(report)

    # 3. 删除任务记录
    await db.delete(job)
    return True, None


@router.post("/jobs/delete", response_model=JobBatchDeleteResponse, summary="批量硬删除任务（仅终态）")
async def batch_delete_jobs(
    payload: JobBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """批量硬删除任务。仅允许删除终态任务（success/failed/cancelled）；
    待处理/运行中任务返回失败提示（应先取消）。删除会同时清理磁盘产物与关联质检报告。"""
    results: list[JobDeleteResult] = []
    deleted_count = 0
    failed_any = False
    for job_id in payload.job_ids:
        result = await db.execute(
            select(ConversionJob).where(
                ConversionJob.id == job_id,
                api_key.scope_clause(ConversionJob),
            )
        )
        job = result.scalar_one_or_none()
        if not job:
            results.append(JobDeleteResult(id=job_id, success=False, reason="任务不存在或无权限"))
            failed_any = True
            continue
        ok, reason = await _hard_delete_job(db, job, api_key)
        if ok:
            deleted_count += 1
            results.append(JobDeleteResult(id=job_id, success=True))
        else:
            failed_any = True
            results.append(JobDeleteResult(id=job_id, success=False, reason=reason))

    try:
        await db.commit()
    except Exception as e:  # pragma: no cover - 防御性兜底（本轮删除无自然失败路径）
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"删除任务失败: {e}")

    if failed_any and deleted_count == 0:
        raise HTTPException(
            status_code=400,
            detail=next((r.reason for r in results if not r.success), "无可删除任务"),
        )
    return JobBatchDeleteResponse(results=results, deleted_count=deleted_count)


@router.post("/detect", response_model=FormatDetectionResponse, summary="格式预检（不上传全量文件）")
@limiter.limit("20/minute")
async def detect_file_format(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="待检测的矢量文件（仅读取头部）"),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    """上传文件并执行格式检测，返回格式名、编码、CRS、字段 schema 等。"""
    content = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:  # pragma: no cover - security_middleware 按同阈值预检 content-length，先拦截
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"文件超过限制 {settings.max_upload_size_mb} MB")
    if not file.filename:  # pragma: no cover - 同上：无 filename 的 part 被 FastAPI 校验拦截（422）先行
        raise HTTPException(status_code=400, detail="文件名为空")
    validate_filename(file.filename)

    tmp_dir = Path(settings.upload_dir) / "detect"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = str(tmp_dir / file.filename)
    async with aiofiles.open(tmp_path, 'wb') as f:
        await f.write(content)

    try:
        result = detect_format(tmp_path)
        return FormatDetectionResponse(
            format_name=result.format_name,
            encoding=result.encoding,
            crs_info=result.crs_info,
            confidence=result.confidence,
            magic_bytes=result.magic_bytes,
            attribute_schema=[
                FieldSchemaResponse(name=f.name, field_type=f.field_type, nullable=f.nullable)
                for f in result.attribute_schema
            ],
            feature_count=result.feature_count,
        )
    finally:
        try:
            Path(tmp_path).unlink()
        except Exception:  # pragma: no cover - 防御性清理（Windows 文件占用等）
            pass


@router.get("/capabilities", summary="查询服务支持的格式与坐标系")
async def get_capabilities():
    return {
        "input_formats":  ["SHP", "GeoJSON", "KML", "KMZ", "GML", "DWG", "DXF", "DGN", "TAB", "GPKG"],
        "output_formats": ["SHP", "GeoJSON", "GML", "GPKG", "KML", "DXF"],
        "supported_crs":  get_supported_crs_list(),
        "encodings":      ["UTF-8", "GBK", "GB2312（自动检测）"],
        "script_types":   ["ogr2ogr", "python_fiona"],
        "repair_strategies": ["auto_fix", "isolate", "drop", "raise"],
    }
