"""质量检查请求/响应模型 — v2 五维评分版。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── 请求 ─────────────────────────────────────────────────────────────────────

class QualityCheckRequest(BaseModel):
    """质检请求体（两种调用方式均可）。"""
    standard_id: str | None = Field(None, description="数据标准 ID")
    standard_table_id: str | None = Field(None, description="标准表 ID（指定后仅检查该图层）")
    geometry_type: str | None = Field(None, description="几何类型 Point/LineString/Polygon（可自动检测）")
    topology_config: dict | None = Field(None, description="拓扑检查配置覆盖")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "standard_id": "std_123",
                "standard_table_id": "tbl_456",
                "topology_config": {
                    "enabled_rules": ["empty_geometry", "polygon_overlap"],
                    "tolerances": {"min_area": 0.001},
                },
            }
        }
    )


class QualityCheckFileRequest(QualityCheckRequest):
    """上传文件 + 质检配置（multipart/form-data 使用）。"""
    pass


# ── 响应：单条检查项 ─────────────────────────────────────────────────────────

class CheckItemResult(BaseModel):
    """单条属性检查项结果。"""
    rule_code: str
    rule_name: str
    status: str = Field(description="pass / fail / skip")
    severity: str = Field(description="error / warning / info")
    field: str | None = None
    layer: str | None = None
    failed_count: int = 0
    total_checked: int = 0
    expected: Any = None
    actual: Any = None
    message: str = ""
    samples: list = Field(default_factory=list)  # 【S5】放宽：int=feature_index，其它为样本值


class TopoCheckItemResult(BaseModel):
    """单条拓扑检查项结果。"""
    rule_code: str
    rule_name: str
    status: str
    severity: str
    failed_count: int = 0
    total_checked: int = 0
    message: str = ""
    feature_indices: list[int] = Field(default_factory=list)
    pairs: list[list[int]] = Field(default_factory=list)


class CheckSummary(BaseModel):
    """属性或拓扑检查汇总。"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    by_rule: dict[str, dict] = Field(default_factory=dict)


class DimensionScores(BaseModel):
    """五维评分。"""
    completeness: float = Field(0.0, ge=0, le=1, description="必填项完整率")
    accuracy: float = Field(0.0, ge=0, le=1, description="值域/类型正确率")
    consistency: float = Field(0.0, ge=0, le=1, description="属性结构一致性")
    uniqueness: float = Field(0.0, ge=0, le=1, description="主键唯一性")
    topology: float = Field(0.0, ge=0, le=1, description="拓扑合规率")
    overall: float = Field(0.0, ge=0, le=1, description="加权综合分")


class ViolationSample(BaseModel):
    """违规样本条目。"""
    rule_code: str = ""
    rule_name: str = ""
    message: str = ""
    layer: str | None = None
    field: str | None = None
    feature_index: int | None = None
    sample: Any = None  # 【S5】放宽：int=要素索引 / 值字符串 / 其它


# ── 响应：质检完整结果 ──────────────────────────────────────────────────────

class QualityCheckResponse(BaseModel):
    """质检结果响应（同步）。"""
    report_id: str
    stored_file_id: str
    file_name: str
    standard_id: str | None = None
    standard_table_id: str | None = None

    attr_results: list[CheckItemResult] = Field(default_factory=list)
    attr_summary: CheckSummary = Field(default_factory=CheckSummary)
    topo_results: list[TopoCheckItemResult] = Field(default_factory=list)
    topo_summary: CheckSummary = Field(default_factory=CheckSummary)
    scores: DimensionScores = Field(default_factory=DimensionScores)

    total_violations: int = 0
    violation_samples: list[ViolationSample] = Field(default_factory=list)

    # 【v2.10.5】质检圈定的图层清单（NULL=全部图层）
    checked_layers: list[str] | None = None

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QualityReportBrief(BaseModel):
    """报告列表条目（v2.5 五维评分 + 违规明细）。"""
    id: str
    file_name: str
    detected_format: str
    completeness_score: float
    total_features: int
    total_fields: int
    issues_count: int = 0
    created_at: datetime
    # v2.5 五维评分
    score_completeness: float = 0.0
    score_accuracy: float = 0.0
    score_consistency: float = 0.0
    score_uniqueness: float = 0.0
    score_topology: float = 0.0
    # 违规明细（前端图表/筛选需要）
    constraint_violations: list | None = None
    topology_violations: list | None = None
    topology_violations_count: int = 0
    constraint_violations_count: int = 0
    # 【v2.10.5】质检圈定的图层清单（NULL=全部图层）
    checked_layers: list[str] | None = None
    # 关联
    stored_file_id: str | None = None
    data_standard_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class QualityReportDetail(BaseModel):
    """报告详情（向后兼容旧字段 + 新五维评分 + 违规样本明细）。"""
    id: str
    job_id: str | None = None
    batch_job_id: str | None = None
    file_name: str
    detected_format: str
    total_features: int
    total_fields: int
    completeness_score: float
    missing_fields: list | None = None
    null_value_counts: dict | None = None
    critical_fields_status: dict | None = None
    issues: list | None = None
    constraint_violations: list | None = None
    topology_violations: list | None = None
    data_standard_id: str | None = None
    standard_table_id: str | None = None
    stored_file_id: str | None = None
    # 新增五维评分
    attribute_summary: dict | None = None
    topology_summary: dict | None = None
    score_completeness: float = 0.0
    score_accuracy: float = 0.0
    score_consistency: float = 0.0
    score_uniqueness: float = 0.0
    score_topology: float = 0.0
    # v2.20: 综合分（前端不再自行按权重硬编码计算；唯一来源 quality_checker.WEIGHTS）
    score_overall: float = 0.0
    html_report_path: str | None = None
    json_report_path: str | None = None
    # 【v2.10.5】质检圈定的图层清单（NULL=全部图层）
    checked_layers: list[str] | None = None
    created_at: datetime
    # 【S5】违规明细增强（详情端构建，含 feature_index/样本值）
    attr_results: list[CheckItemResult] = Field(default_factory=list)
    topo_results: list[TopoCheckItemResult] = Field(default_factory=list)
    total_violations: int = 0
    violation_samples: list[ViolationSample] = Field(default_factory=list)
    severity_dist: dict[str, int] = Field(default_factory=dict)  # {error:N, warning:N}

    model_config = ConfigDict(from_attributes=True)
