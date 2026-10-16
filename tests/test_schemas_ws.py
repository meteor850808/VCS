"""P0: WebSocket 消息模型（app/schemas/ws.py）单测。"""

import pytest

from pydantic import ValidationError

from app.schemas.ws import WSMessage


class TestWSMessage:
    def test_type_required(self):
        with pytest.raises(ValidationError):
            WSMessage()

    def test_mintype(self):
        m = WSMessage(type="progress")
        assert m.type == "progress"
        assert m.job_id is None
        assert m.progress is None

    def test_full_fields(self):
        m = WSMessage(
            type="progress",
            job_id="j-1",
            batch_job_id="b-1",
            status="running",
            progress=50.0,
            step="pipeline",
            step_index=2,
            total_steps=5,
            message="working",
        )
        assert m.job_id == "j-1"
        assert m.batch_job_id == "b-1"
        assert m.status == "running"
        assert m.progress == 50.0
        assert m.step_index == 2
        assert m.message == "working"

    def test_timestamp_datetime_coerce(self):
        m = WSMessage(type="connected", timestamp="2026-09-04T12:00:00Z")
        assert m.timestamp is not None

    def test_to_dict_roundtrip(self):
        m = WSMessage(type="heartbeat")
        d = m.model_dump()
        assert d["type"] == "heartbeat"
        assert set(d.keys()) >= {"type", "job_id", "batch_job_id", "status",
                                 "progress", "step", "step_index", "total_steps",
                                 "message", "timestamp"}