"""baseline v2.1 — 全表重建单基线。

Revision ID: 0001_baseline_v2_1
Create Date: 2026-07-15

旧迁移链已归档至 alembic/versions/archived/。
本迁移从 ORM 模型生成，包含：
  - 外键约束
  - 审计字段 (created_at / updated_at)
  - 几何有效性检查约束 (ST_IsValid)
  - 空几何支持 (geometry nullable=True)
  - 分片上传字段 (file_hash / chunk_index / total_chunks)
"""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers
revision = "0001_baseline_v2_1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── api_keys ──
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_created_at", "api_keys", ["created_at"])

    # ── batch_jobs ──
    op.create_table(
        "batch_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("total_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("success_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cancelled_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("output_format", sa.String(64), nullable=False, server_default="GeoJSON"),
        sa.Column("dst_crs", sa.String(256), nullable=False, server_default="EPSG:4490"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── stored_files ──
    op.create_table(
        "stored_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(256), nullable=False, server_default="application/octet-stream"),
        sa.Column("file_size", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("file_data", sa.LargeBinary, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source_archive", sa.String(512), nullable=True),
        sa.Column("is_archive", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("file_hash", sa.String(128), nullable=True, comment="文件 SHA-256 哈希，用于去重"),
        sa.Column("chunk_index", sa.BigInteger, nullable=False, server_default=sa.text("0"), comment="分片序号，0 表示完整文件"),
        sa.Column("total_chunks", sa.BigInteger, nullable=False, server_default=sa.text("1"), comment="总分片数"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_stored_files_api_created", "stored_files", ["api_key_id", "created_at"])

    # ── conversion_jobs ──
    op.create_table(
        "conversion_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("celery_task_id", sa.String(128), nullable=True, index=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("batch_job_id", sa.String(36), sa.ForeignKey("batch_jobs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("input_path", sa.String(1024), nullable=False),
        sa.Column("output_path", sa.String(1024), nullable=True),
        sa.Column("src_crs", sa.String(256), nullable=True),
        sa.Column("dst_crs", sa.String(256), nullable=False),
        sa.Column("output_format", sa.String(64), nullable=False, server_default="GeoJSON"),
        sa.Column("select_fields", sa.JSON, nullable=True),
        sa.Column("where_clause", sa.Text, nullable=True),
        sa.Column("repair_strategy", sa.String(32), nullable=False, server_default="auto_fix"),
        sa.Column("script_type", sa.String(32), nullable=False, server_default="ogr2ogr"),
        sa.Column("target_encoding", sa.String(32), nullable=False, server_default="UTF-8"),
        sa.Column("detected_format", sa.String(64), nullable=True),
        sa.Column("detected_encoding", sa.String(32), nullable=True),
        sa.Column("detected_crs", sa.String(256), nullable=True),
        sa.Column("detection_confidence", sa.Float, nullable=True),
        sa.Column("generated_script", sa.Text, nullable=True),
        sa.Column("generated_script_type", sa.String(32), nullable=True),
        sa.Column("feature_count_input", sa.Integer, nullable=True),
        sa.Column("feature_count_output", sa.Integer, nullable=True),
        sa.Column("feature_fixed", sa.Integer, nullable=True),
        sa.Column("feature_dropped", sa.Integer, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("warnings", sa.JSON, nullable=True),
        sa.Column("sandbox_stdout", sa.Text, nullable=True),
        sa.Column("sandbox_stderr", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_report_id", sa.String(36), nullable=True),
        sa.Column("progress", sa.Float, nullable=True, server_default=sa.text("0.0")),
        sa.Column("current_step", sa.String(64), nullable=True),
    )

    # ── data_standards ──
    op.create_table(
        "data_standards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("sheet_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── data_standard_sheets ──
    op.create_table(
        "data_standard_sheets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sheet_name", sa.String(256), nullable=False),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── data_standard_fields ──
    op.create_table(
        "data_standard_fields",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sheet_id", sa.String(36), sa.ForeignKey("data_standard_sheets.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("field_name", sa.String(256), nullable=False),
        sa.Column("field_type", sa.String(32), nullable=False, server_default="string"),
        sa.Column("length", sa.Integer, nullable=True),
        sa.Column("nullable", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("allowed_values", sa.JSON, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── quality_reports ──
    op.create_table(
        "quality_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("conversion_jobs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("batch_job_id", sa.String(36), sa.ForeignKey("batch_jobs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("detected_format", sa.String(64), nullable=False),
        sa.Column("total_features", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("total_fields", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("completeness_score", sa.Float, nullable=False, server_default=sa.text("1.0")),
        sa.Column("missing_fields", sa.JSON, nullable=True),
        sa.Column("null_value_counts", sa.JSON, nullable=True),
        sa.Column("critical_fields_status", sa.JSON, nullable=True),
        sa.Column("issues", sa.JSON, nullable=True),
        sa.Column("constraint_violations", sa.JSON, nullable=True),
        sa.Column("topology_violations", sa.JSON, nullable=True),
        sa.Column("data_standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("html_report_path", sa.String(1024), nullable=True),
        sa.Column("json_report_path", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── quality_rules ──
    op.create_table(
        "quality_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id"), nullable=False, index=True),
        sa.Column("standard_field_id", sa.String(36), sa.ForeignKey("data_standard_fields.id", ondelete="CASCADE"), nullable=True),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("rule_name", sa.String(256), nullable=False),
        sa.Column("field_pattern", sa.String(256), nullable=False, server_default=""),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("params", sa.JSON, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_auto", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── quality_rule_results ──
    op.create_table(
        "quality_rule_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("quality_reports.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("quality_rules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("field_name", sa.String(256), nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("total_checked", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("detail", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── quality_violations ──
    op.create_table(
        "quality_violations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("quality_reports.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("rule_result_id", sa.String(36), sa.ForeignKey("quality_rule_results.id", ondelete="CASCADE"), nullable=True),
        sa.Column("field_name", sa.String(256), nullable=False, index=True),
        sa.Column("issue_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("feature_index", sa.Integer, nullable=True),
        sa.Column("expected", sa.Text, nullable=True),
        sa.Column("actual", sa.Text, nullable=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ── quality_daily_snapshots ──
    op.create_table(
        "quality_daily_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("total_files", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("checked_files", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("completeness", sa.Numeric(5, 4), nullable=True),
        sa.Column("uniqueness", sa.Numeric(5, 4), nullable=True),
        sa.Column("consistency", sa.Numeric(5, 4), nullable=True),
        sa.Column("violations_total", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("score_overall", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("api_key_id", "snapshot_date", name="uq_qds_api_date"),
    )

    # ── source_tables ──
    op.create_table(
        "source_tables",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("table_name", sa.String(256), nullable=False),
        sa.Column("table_code", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("geometry_type", sa.String(32), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("version", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── source_fields ──
    op.create_table(
        "source_fields",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("table_id", sa.String(36), sa.ForeignKey("source_tables.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("field_name", sa.String(256), nullable=False),
        sa.Column("field_alias", sa.String(256), nullable=True),
        sa.Column("field_type", sa.String(32), nullable=True),
        sa.Column("data_type", sa.String(32), nullable=False, server_default="string"),
        sa.Column("length", sa.Integer, nullable=True),
        sa.Column("precision", sa.Integer, nullable=True),
        sa.Column("nullable", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("default_value", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_primary_key", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("allowed_values", sa.JSON, nullable=True),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── source_dictionaries ──
    op.create_table(
        "source_dictionaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("dict_name", sa.String(256), nullable=False),
        sa.Column("dict_code", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── dictionary_items ──
    op.create_table(
        "dictionary_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dict_id", sa.String(36), sa.ForeignKey("source_dictionaries.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("item_code", sa.String(64), nullable=False),
        sa.Column("item_name", sa.String(256), nullable=False),
        sa.Column("parent_code", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── field_dict_links ──
    op.create_table(
        "field_dict_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("field_id", sa.String(36), sa.ForeignKey("source_fields.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("dict_id", sa.String(36), sa.ForeignKey("source_dictionaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filter_condition", sa.String(256), nullable=True),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── standard_versions ──
    op.create_table(
        "standard_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("version_label", sa.String(32), nullable=True),
        sa.Column("change_log", sa.Text, nullable=True),
        sa.Column("file_data", sa.LargeBinary, nullable=True),
        sa.Column("file_name", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── PostGIS 空间表 (point_features) ──
    op.create_table(
        "point_features",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stored_file_id", sa.String(36), sa.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("layer_name", sa.String(256), nullable=False),
        sa.Column("source_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("feature_index", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("properties", sa.JSON, nullable=True),
        sa.Column("geometry_type", sa.String(16), nullable=False, server_default="Point", index=True),
        sa.Column("geometry", Geometry("GEOMETRY", srid=4326), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("geometry IS NULL OR ST_IsValid(geometry)", name="ck_point_features_valid"),
    )
    op.create_index("ix_point_features_geometry", "point_features", ["geometry"], postgresql_using="gist")
    op.create_index("ix_point_features_source_name", "point_features", ["source_name"])

    # ── PostGIS 空间表 (line_features) ──
    op.create_table(
        "line_features",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stored_file_id", sa.String(36), sa.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("layer_name", sa.String(256), nullable=False),
        sa.Column("source_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("feature_index", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("properties", sa.JSON, nullable=True),
        sa.Column("geometry_type", sa.String(16), nullable=False, server_default="LineString", index=True),
        sa.Column("geometry", Geometry("GEOMETRY", srid=4326), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("geometry IS NULL OR ST_IsValid(geometry)", name="ck_line_features_valid"),
    )
    op.create_index("ix_line_features_geometry", "line_features", ["geometry"], postgresql_using="gist")

    # ── PostGIS 空间表 (polygon_features) ──
    op.create_table(
        "polygon_features",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stored_file_id", sa.String(36), sa.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("layer_name", sa.String(256), nullable=False),
        sa.Column("source_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("feature_index", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("properties", sa.JSON, nullable=True),
        sa.Column("geometry_type", sa.String(16), nullable=False, server_default="Polygon", index=True),
        sa.Column("geometry", Geometry("GEOMETRY", srid=4326), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("geometry IS NULL OR ST_IsValid(geometry)", name="ck_polygon_features_valid"),
    )
    op.create_index("ix_polygon_features_geometry", "polygon_features", ["geometry"], postgresql_using="gist")


def downgrade() -> None:
    op.drop_table("polygon_features")
    op.drop_table("line_features")
    op.drop_table("point_features")
    op.drop_table("standard_versions")
    op.drop_table("field_dict_links")
    op.drop_table("dictionary_items")
    op.drop_table("source_dictionaries")
    op.drop_table("source_fields")
    op.drop_table("source_tables")
    op.drop_table("quality_daily_snapshots")
    op.drop_table("quality_violations")
    op.drop_table("quality_rule_results")
    op.drop_table("quality_rules")
    op.drop_table("quality_reports")
    op.drop_table("data_standard_fields")
    op.drop_table("data_standard_sheets")
    op.drop_table("data_standards")
    op.drop_table("conversion_jobs")
    op.drop_table("stored_files")
    op.drop_table("batch_jobs")
    op.drop_index("ix_api_keys_created_at", table_name="api_keys")
    op.drop_table("api_keys")
