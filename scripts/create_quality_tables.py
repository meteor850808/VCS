"""创建质检新表（直接 SQL，绕过 Alembic 多 head 问题）。"""
import asyncio
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

CREATE_SQL = [
    """CREATE TABLE IF NOT EXISTS quality_rules (
        id VARCHAR(36) PRIMARY KEY,
        api_key_id VARCHAR(36) NOT NULL,
        standard_field_id VARCHAR(36),
        rule_type VARCHAR(32) NOT NULL,
        rule_name VARCHAR(256) NOT NULL,
        field_pattern VARCHAR(256) DEFAULT '',
        severity VARCHAR(16) DEFAULT 'warning',
        params JSONB,
        enabled BOOLEAN DEFAULT TRUE,
        is_auto BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_qrules_api_key ON quality_rules(api_key_id)",
    """CREATE TABLE IF NOT EXISTS quality_rule_results (
        id VARCHAR(36) PRIMARY KEY,
        report_id VARCHAR(36) REFERENCES quality_reports(id) ON DELETE CASCADE,
        rule_id VARCHAR(36) REFERENCES quality_rules(id) ON DELETE SET NULL,
        rule_type VARCHAR(32) NOT NULL, field_name VARCHAR(256) NOT NULL,
        passed BOOLEAN NOT NULL, severity VARCHAR(16) NOT NULL,
        total_checked INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0,
        detail JSONB, created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_qrr_report ON quality_rule_results(report_id)",
    """CREATE TABLE IF NOT EXISTS quality_violations (
        id VARCHAR(36) PRIMARY KEY,
        report_id VARCHAR(36) REFERENCES quality_reports(id) ON DELETE CASCADE,
        rule_result_id VARCHAR(36) REFERENCES quality_rule_results(id) ON DELETE CASCADE,
        field_name VARCHAR(256) NOT NULL, issue_type VARCHAR(32) NOT NULL,
        severity VARCHAR(16) NOT NULL, feature_index INTEGER,
        expected TEXT, actual TEXT, message TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_qv_report ON quality_violations(report_id)",
    "CREATE INDEX IF NOT EXISTS idx_qv_field ON quality_violations(field_name)",
    """CREATE TABLE IF NOT EXISTS quality_daily_snapshots (
        id VARCHAR(36) PRIMARY KEY,
        api_key_id VARCHAR(36) NOT NULL,
        snapshot_date DATE NOT NULL,
        total_files INTEGER DEFAULT 0, checked_files INTEGER DEFAULT 0,
        completeness NUMERIC(5,4), uniqueness NUMERIC(5,4),
        consistency NUMERIC(5,4), violations_total INTEGER DEFAULT 0,
        score_overall NUMERIC(5,4), created_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(api_key_id, snapshot_date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_qds_date ON quality_daily_snapshots(snapshot_date)",
]


async def run():
    async with AsyncSessionLocal() as db:
        for sql in CREATE_SQL:
            try:
                await db.execute(text(sql))
                print(".", end="", flush=True)
            except Exception as e:
                print(f"\nERR: {e}")
        await db.commit()
    print("\nDone")


if __name__ == "__main__":
    asyncio.run(run())
