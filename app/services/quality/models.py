"""数据质量检查数据模型。"""

from dataclasses import dataclass, field


@dataclass
class QualityIssue:
    severity: str         # "critical" | "warning" | "info"
    field: str
    issue_type: str       # "missing_field" | "null_values" | "type_mismatch" | "low_completeness"
    message: str
    affected_count: int


@dataclass
class QualityResult:
    file_name: str
    detected_format: str
    total_features: int
    total_fields: int
    completeness_score: float
    missing_fields: list[dict] = field(default_factory=list)
    null_value_counts: dict[str, int] = field(default_factory=dict)
    critical_fields_status: dict[str, dict] = field(default_factory=dict)
    issues: list[QualityIssue] = field(default_factory=list)
    attribute_schema: list = field(default_factory=list)
    constraint_violations: list[dict] = field(default_factory=list)
    topology_violations: list[dict] = field(default_factory=list)
    data_standard_id: str | None = None


@dataclass
class QualityCheckConfig:
    """质量检查配置 — 替代 check_quality() 的 5 个散参数。"""
    file_path: str
    detected_format: str | None = None
    data_standard_id: str | None = None
    standard_fields: dict[str, dict] | None = None
    topology_config: dict | None = None
