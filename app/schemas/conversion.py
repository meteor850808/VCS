"""转换任务请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobBrief(BaseModel):
    id: str
    original_filename: str
    status: str
    output_format: str
    progress: float | None = None
    current_step: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobDetail(BaseModel):
    id: str
    original_filename: str
    status: str
    output_format: str
    input_path: str
    output_path: str | None = None
    src_crs: str | None = None
    dst_crs: str
    detected_format: str | None = None
    detected_crs: str | None = None
    detection_confidence: float | None = None
    generated_script: str | None = None
    feature_count_input: int | None = None
    feature_count_output: int | None = None
    feature_fixed: int | None = None
    feature_dropped: int | None = None
    error_message: str | None = None
    warnings: list | None = None
    sandbox_stdout: str | None = None
    sandbox_stderr: str | None = None
    batch_job_id: str | None = None
    quality_report_id: str | None = None
    progress: float | None = None
    current_step: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None

    model_config = ConfigDict(from_attributes=True)


class JobBatchDeleteRequest(BaseModel):
    """批量硬删除任务请求。"""
    job_ids: list[str]


class JobDeleteResult(BaseModel):
    id: str
    success: bool
    reason: str | None = None


class JobBatchDeleteResponse(BaseModel):
    results: list[JobDeleteResult]
    deleted_count: int
