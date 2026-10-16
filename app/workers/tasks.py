"""
Celery 转换任务。
Worker 接收 job_id → 读 PG → 执行 5 个子服务 → 写回 PG。
"""

import json
import logging
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from celery import Task
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.conversion_job import ConversionJob, JobStatus
from app.services.format_detection   import detect_format
from app.services.crs_transform      import resolve_crs
from app.services.attribute_processing import process_attributes
from app.services.geometry_repair    import repair_features, RepairStrategy, compare_feature_counts
from app.services.progress_tracker import publish_progress
from app.services.script_generation  import (
    ConversionParams, generate_ogr2ogr_script, generate_fiona_script,
    sandbox_execute,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Celery Worker 使用同步 SQLAlchemy（Worker 进程无 asyncio loop）
# keepalive 参数防止长时间空闲后连接被 PG 回收导致 BrokenPipeError
_sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,  # 30 分钟回收连接
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
)
SyncSession = sessionmaker(bind=_sync_engine)


def _get_job(session: Session, job_id: str) -> ConversionJob:
    job = session.get(ConversionJob, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} 不存在")
    return job


@celery_app.task(
    bind=True,
    name="vcs.convert",
    max_retries=2,
    default_retry_delay=10,
)
def run_conversion(self: Task, job_id: str) -> dict:
    """
    矢量转换主任务。

    Args:
        job_id: ConversionJob.id（UUID 字符串）

    Returns:
        {'status': 'success'|'failed', 'job_id': ...}
    """
    logger.info(f"[{job_id}] 任务开始")

    warnings: list[str] = []

    with SyncSession() as session:
        job: ConversionJob = _get_job(session, job_id)
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        job.celery_task_id = self.request.id
        session.commit()

        try:
            # ── Step 1: 格式识别 ──────────────────────────────────────────────
            fmt_result = detect_format(job.input_path)
            job.detected_format     = fmt_result.format_name
            job.detected_encoding   = fmt_result.encoding
            job.detected_crs        = fmt_result.crs_info
            job.detection_confidence = fmt_result.confidence
            if fmt_result.confidence < 0.5:
                warnings.append(f"格式置信度低: {fmt_result.confidence:.0%}")
            job.progress = 0.2
            job.current_step = "format_detection"
            publish_progress(job_id, "格式识别", 1, 6, "running",
                             f"检测到 {fmt_result.format_name} (置信度 {fmt_result.confidence:.0%})",
                             job.batch_job_id)
            logger.info(f"[{job_id}] 格式识别: {fmt_result.format_name} ({fmt_result.confidence:.0%})")

            # ── Step 2: CRS 解析 ──────────────────────────────────────────────
            src_crs_str = job.src_crs or fmt_result.crs_info or 'EPSG:4326'
            src_crs = resolve_crs(src_crs_str)
            dst_crs = resolve_crs(job.dst_crs)
            job.progress = 0.4
            job.current_step = "crs_resolution"
            publish_progress(job_id, "坐标系解析", 2, 6, "running",
                             f"EPSG:{src_crs.epsg} → EPSG:{dst_crs.epsg}", job.batch_job_id)
            logger.info(f"[{job_id}] CRS: EPSG:{src_crs.epsg} → EPSG:{dst_crs.epsg}")

            # ── Step 3: 属性处理 ──────────────────────────────────────────────
            attr_path = _find_attr_file(job.input_path, fmt_result.format_name)
            if attr_path:
                try:
                    attr = process_attributes(
                        file_path=attr_path,
                        select_fields=job.select_fields,
                        target_encoding=job.target_encoding,
                    )
                    warnings.extend(attr.warnings)
                    job.progress = 0.5
                    job.current_step = "attribute_processing"
                    publish_progress(job_id, "属性处理", 3, 6, "running",
                                     f"{attr.total_input} → {attr.total_output} 条", job.batch_job_id)
                    logger.info(f"[{job_id}] 属性处理: {attr.total_input} → {attr.total_output} 条")
                except Exception as e:
                    warnings.append(f"属性处理跳过: {e}")

            # ── Step 4: 几何修复（GeoJSON）────────────────────────────────────
            if fmt_result.format_name == 'GeoJSON':
                try:
                    features = _load_geojson_features(job.input_path)
                    input_count = len(features)
                    strategy = RepairStrategy(job.repair_strategy)
                    repair = repair_features(features, strategy=strategy)
                    job.feature_count_input  = repair.total_input
                    job.feature_count_output = repair.total_output
                    job.feature_fixed        = repair.total_fixed
                    job.feature_dropped      = repair.total_dropped
                    check = compare_feature_counts(input_count, repair.total_output)
                    if not check['passed']:
                        warnings.append(check['message'])
                    job.progress = 0.5
                    job.current_step = "geometry_repair"
                    publish_progress(job_id, "几何修复", 4, 6, "running",
                                     f"有效={repair.total_valid} 修复={repair.total_fixed}", job.batch_job_id)
                    logger.info(f"[{job_id}] 几何修复: 有效={repair.total_valid} 修复={repair.total_fixed}")
                except Exception as e:
                    warnings.append(f"几何修复跳过: {e}")

            # ── Step 5: 脚本生成 + 沙箱预执行 ────────────────────────────────
            params = ConversionParams(
                input_path=job.input_path,
                output_path=job.output_path,
                src_crs=f"EPSG:{src_crs.epsg}" if src_crs.epsg else (src_crs.wkt or src_crs_str),
                dst_crs=f"EPSG:{dst_crs.epsg}" if dst_crs.epsg else (dst_crs.wkt or job.dst_crs),
                output_format=job.output_format,
                select_fields=job.select_fields,
                where_clause=job.where_clause,
                input_encoding=fmt_result.encoding,
            )

            script = (generate_fiona_script(params)
                      if job.script_type == 'python_fiona'
                      else generate_ogr2ogr_script(params))

            job.generated_script      = script.code
            job.generated_script_type = script.script_type

            # 沙箱预执行
            sandbox = sandbox_execute(script, limit=settings.sandbox_limit)
            job.sandbox_stdout = sandbox.stdout
            job.sandbox_stderr = sandbox.stderr
            if not sandbox.success:
                warnings.append(f"沙箱预执行失败: {sandbox.stderr[:200]}")
            job.progress = 0.7
            job.current_step = "sandbox_check"
            publish_progress(job_id, "沙箱预检查", 5, 6, "running",
                             "通过" if sandbox.success else f"失败: {sandbox.stderr[:100]}", job.batch_job_id)
            logger.info(f"[{job_id}] 沙箱: {'通过' if sandbox.success else '失败'}")

            # 确保输出目录存在
            Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)

            # 完整执行
            _run_full_conversion(script, job)

            # ── 完成 ──────────────────────────────────────────────────────────
            job.status      = JobStatus.SUCCESS
            job.finished_at = datetime.now(timezone.utc)
            job.warnings    = warnings
            job.progress    = 1.0
            job.current_step = "done"
            session.commit()
            publish_progress(job_id, "执行转换", 6, 6, "success",
                             f"转换成功 — {job.output_path}", job.batch_job_id)
            logger.info(f"[{job_id}] 任务完成 ✓")
            return {'status': 'success', 'job_id': job_id}

        except Exception as exc:
            logger.error(f"[{job_id}] 任务失败: {exc}", exc_info=True)
            job.status        = JobStatus.FAILED
            job.error_message = str(exc)
            job.finished_at   = datetime.now(timezone.utc)
            job.warnings      = warnings
            session.commit()
            raise self.retry(exc=exc) if self.request.retries < self.max_retries else exc


def _run_full_conversion(script, job: ConversionJob) -> None:
    """执行完整转换（调用 ogr2ogr 或 python 脚本）。"""
    if script.script_type == 'ogr2ogr':
        args = shlex.split(script.code)
        result = subprocess.run(args, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"ogr2ogr 失败: {result.stderr[:500]}")
    else:
        tmp = Path(tempfile.gettempdir()) / f'vcs_full_{job.id[:8]}.py'
        tmp.write_text(script.code, encoding='utf-8')
        try:
            result = subprocess.run([sys.executable, str(tmp)], capture_output=True,
                                    text=True, timeout=3600)
            if result.returncode != 0:
                raise RuntimeError(f"Fiona 脚本失败: {result.stderr[:500]}")
        finally:
            tmp.unlink(missing_ok=True)


def _find_attr_file(input_path: str, fmt: str) -> str | None:
    p = Path(input_path)
    if fmt == 'SHP':
        dbf = p.with_suffix('.dbf')
        return str(dbf) if dbf.exists() else None
    if p.suffix.lower() in ('.csv', '.dbf', '.xlsx', '.xls'):
        return input_path
    return None


def _load_geojson_features(file_path: str) -> list[dict]:
    with open(file_path, encoding='utf-8', errors='replace') as f:
        data = json.load(f)
    if data.get('type') == 'FeatureCollection':
        return data.get('features', [])
    return [data] if data.get('type') == 'Feature' else []
