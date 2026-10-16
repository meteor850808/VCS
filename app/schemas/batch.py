"""批量任务请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BatchJobSubmitResponse(BaseModel):
    batch_job_id: str
    status: str
    file_count: int
    message: str


class BatchJobBrief(BaseModel):
    id: str
    name: str
    status: str
    total_count: int
    success_count: int
    failed_count: int
    output_format: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BatchJobDetail(BaseModel):
    id: str
    name: str
    status: str
    output_format: str
    dst_crs: str
    total_count: int
    success_count: int
    failed_count: int
    cancelled_count: int
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None

    model_config = ConfigDict(from_attributes=True)
