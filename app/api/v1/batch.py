"""批量转换 API。"""

import logging
import uuid
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.cache import redis_available
from app.core.constants import OUTPUT_SUFFIX_MAP
from app.core.deps import authenticated
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.batch_job import BatchJob, BatchJobStatus
from app.models.conversion_job import ConversionJob, JobStatus
from app.schemas.batch import BatchJobBrief, BatchJobDetail, BatchJobSubmitResponse
from app.schemas.conversion import JobBrief
from app.workers.batch_tasks import orchestrate_batch
from app.workers.tasks import run_conversion

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/batch/convert",
    response_model=BatchJobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="批量上传文件并提交转换",
)
async def submit_batch_conversion(
    files: list[UploadFile] = File(..., description="矢量文件列表（最多50个）"),
    dst_crs: str = Form("EPSG:4490"),
    output_format: str = Form("GeoJSON"),
    run_quality_check: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    if len(files) > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"单批次最多 {settings.max_batch_size} 个文件",
        )

    batch_id = str(uuid.uuid4())
    batch_dir = Path(settings.upload_dir) / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    # 创建 BatchJob
    batch = BatchJob(
        id=batch_id,
        api_key_id=api_key.id,
        name=f"批量转换 {len(files)} 文件",
        total_count=len(files),
        status=BatchJobStatus.PENDING,
        output_format=output_format,
        dst_crs=dst_crs,
    )
    db.add(batch)

    job_ids = []

    for file in files:
        content = await file.read()
        job_id = str(uuid.uuid4())
        input_path = str(batch_dir / file.filename)
        out_name = Path(file.filename).stem + OUTPUT_SUFFIX_MAP.get(output_format, '.geojson')
        output_path = str(Path(settings.output_dir) / batch_id / out_name)

        # 保存文件
        async with aiofiles.open(input_path, 'wb') as f:
            await f.write(content)

        # 创建子任务
        job = ConversionJob(
            id=job_id,
            api_key_id=api_key.id,
            batch_job_id=batch_id,
            original_filename=file.filename,
            input_path=input_path,
            output_path=output_path,
            dst_crs=dst_crs,
            output_format=output_format,
            status=JobStatus.PENDING,
        )
        db.add(job)
        job_ids.append(job_id)

    await db.commit()

    # 提交 Celery 任务。Redis 不可用时降级为进程内同步执行（与 convert.py 一致）。
    if not redis_available():
        logger.warning("[batch/%s] Redis 不可用，降级为进程内同步执行 (%d 个任务)", batch_id, len(job_ids))
        batch.status = BatchJobStatus.RUNNING
        await db.commit()
        old_max_retries = run_conversion.max_retries
        run_conversion.max_retries = 0
        try:
            for jid in job_ids:
                # 标记子任务为 RUNNING
                j = await db.execute(select(ConversionJob).where(ConversionJob.id == jid))
                job_obj = j.scalar_one_or_none()
                if job_obj:
                    job_obj.status = JobStatus.RUNNING
                await db.commit()
                try:
                    run_conversion.apply(args=[jid], task_id=jid)
                except Exception as inner:
                    logger.error("[batch/%s] 任务 %s 降级执行异常: %s", batch_id, jid, inner)
        finally:
            run_conversion.max_retries = old_max_retries
        # 刷新 batch 汇总状态
        await db.refresh(batch)
        return BatchJobSubmitResponse(
            batch_job_id=batch_id,
            status=batch.status,
            file_count=len(files),
            message=f"Redis 不可用，已进程内执行完成（{batch.status}）",
        )

    # 并行提交所有任务
    for jid in job_ids:
        run_conversion.apply_async(args=[jid], task_id=jid)

    # 更新 batch 状态
    batch.status = BatchJobStatus.RUNNING
    await db.commit()

    return BatchJobSubmitResponse(
        batch_job_id=batch_id,
        status=BatchJobStatus.RUNNING,
        file_count=len(files),
        message=f"已提交 {len(files)} 个转换任务，使用 GET /api/v1/batch/{batch_id} 查看进度",
    )


@router.get("/batch/{batch_id}", response_model=BatchJobDetail, summary="查询批量任务详情")
async def get_batch_job(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(BatchJob).where(
            BatchJob.id == batch_id,
            api_key.scope_clause(BatchJob),
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在或无权限访问")
    return batch


@router.get("/batch", response_model=list[BatchJobBrief], summary="查询批量任务列表")
async def list_batch_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    query = (
        select(BatchJob)
        .where(api_key.scope_clause(BatchJob))
        .order_by(BatchJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.delete("/batch/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_batch_job(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    result = await db.execute(
        select(BatchJob).where(
            BatchJob.id == batch_id,
            api_key.scope_clause(BatchJob),
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    batch.status = BatchJobStatus.CANCELLED

    # 取消所有子任务
    sub_result = await db.execute(
        select(ConversionJob).where(ConversionJob.batch_job_id == batch_id)
    )
    for job in sub_result.scalars().all():
        if job.status == JobStatus.RUNNING and job.celery_task_id:
            from app.workers.celery_app import celery_app
            celery_app.control.revoke(job.celery_task_id, terminate=True)
        job.status = JobStatus.CANCELLED

    await db.commit()


@router.get("/batch/{batch_id}/jobs", response_model=list[JobBrief])
async def list_batch_sub_jobs(
    batch_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    api_key: ApiKey = Depends(authenticated),
):
    query = (
        select(ConversionJob)
        .where(
            ConversionJob.batch_job_id == batch_id,
            api_key.scope_clause(ConversionJob),
        )
        .order_by(ConversionJob.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    return result.scalars().all()
