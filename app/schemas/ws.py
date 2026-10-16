"""WebSocket 消息模型。"""

from datetime import datetime

from pydantic import BaseModel


class WSMessage(BaseModel):
    type: str
    job_id: str | None = None
    batch_job_id: str | None = None
    status: str | None = None
    progress: float | None = None
    step: str | None = None
    step_index: int | None = None
    total_steps: int | None = None
    message: str | None = None
    timestamp: datetime = None
