"""通用响应模型。"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    postgres: str
    redis: str
