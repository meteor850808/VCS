"""批量任务编排 — 并行调度多个子任务并汇总结果。"""

import logging
from datetime import datetime, timezone

from celery import group

from app.workers.celery_app import celery_app
from app.workers.tasks import run_conversion

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="vcs.batch_orchestrate",
    max_retries=1,
    default_retry_delay=30,
)
def orchestrate_batch(self, batch_job_id: str, job_ids: list[str]) -> dict:
    """并行调度一批子任务，并更新 BatchJob 状态。"""
    from app.config import settings
    from app.models.batch_job import BatchJob, BatchJobStatus
    from app.services.progress_tracker import publish_batch_update

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(settings.database_url_sync)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        batch: BatchJob = session.get(BatchJob, batch_job_id)
        if not batch:
            return {"status": "error", "message": f"BatchJob {batch_job_id} 不存在"}

        batch.status = BatchJobStatus.RUNNING
        batch.started_at = datetime.now(timezone.utc)
        session.commit()

        publish_batch_update(batch_job_id, "running", len(job_ids), 0, 0)

    # 并行提交所有子任务
    try:
        job_group = group(run_conversion.s(job_id) for job_id in job_ids)
        result = job_group.apply_async()
        result_set = result.get(propagate=False, timeout=7200)

        # 汇总结果
        success = sum(1 for r in result_set if isinstance(r, dict) and r.get('status') == 'success')
        failed = len(job_ids) - success

        with Session() as session:
            batch = session.get(BatchJob, batch_job_id)
            batch.success_count = success
            batch.failed_count = failed
            batch.finished_at = datetime.now(timezone.utc)
            if failed == 0:
                batch.status = BatchJobStatus.SUCCESS
            elif success == 0:
                batch.status = BatchJobStatus.FAILED
                batch.error_message = "所有子任务均失败"
            else:
                batch.status = BatchJobStatus.PARTIAL
            session.commit()

        publish_batch_update(batch_job_id, batch.status, len(job_ids), success, failed)
        return {"status": "success", "batch_job_id": batch_job_id, "success": success, "failed": failed}

    except Exception as exc:
        logger.error(f"[{batch_job_id}] 批量任务失败: {exc}")
        with Session() as session:
            batch = session.get(BatchJob, batch_job_id)
            batch.status = BatchJobStatus.FAILED
            batch.error_message = str(exc)
            batch.finished_at = datetime.now(timezone.utc)
            session.commit()
        return {"status": "error", "message": str(exc)}
