"""创建源数据管理相关表。"""
import asyncio
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

CREATE_SQL = [
    """CREATE TABLE IF NOT EXISTS source_tables (
        id VARCHAR(36) PRIMARY KEY,
        api_key_id VARCHAR(36) NOT NULL,
        standard_id VARCHAR(36) REFERENCES data_standards(id) ON DELETE SET NULL,
        table_name VARCHAR(256) NOT NULL,
        table_code VARCHAR(64),
        description TEXT,
        geometry_type VARCHAR(32),
        source VARCHAR(64),
        version VARCHAR(32),
        is_active BOOLEAN DEFAULT TRUE,
        ordering INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_st_api_key ON source_tables(api_key_id)",
    """CREATE TABLE IF NOT EXISTS source_fields (
        id VARCHAR(36) PRIMARY KEY,
        table_id VARCHAR(36) NOT NULL REFERENCES source_tables(id) ON DELETE CASCADE,
        field_name VARCHAR(256) NOT NULL,
        field_alias VARCHAR(256),
        field_type VARCHAR(32),
        data_type VARCHAR(32) DEFAULT 'string',
        length INTEGER, precision INTEGER,
        nullable BOOLEAN DEFAULT TRUE,
        default_value VARCHAR(256), description TEXT,
        is_primary_key BOOLEAN DEFAULT FALSE,
        is_required BOOLEAN DEFAULT FALSE,
        allowed_values JSONB,
        ordering INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sf_table ON source_fields(table_id)",
    """CREATE TABLE IF NOT EXISTS source_dictionaries (
        id VARCHAR(36) PRIMARY KEY,
        api_key_id VARCHAR(36) NOT NULL,
        dict_name VARCHAR(256) NOT NULL,
        dict_code VARCHAR(64),
        description TEXT,
        is_system BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sd_api_key ON source_dictionaries(api_key_id)",
    """CREATE TABLE IF NOT EXISTS dictionary_items (
        id VARCHAR(36) PRIMARY KEY,
        dict_id VARCHAR(36) NOT NULL REFERENCES source_dictionaries(id) ON DELETE CASCADE,
        item_code VARCHAR(64) NOT NULL,
        item_name VARCHAR(256) NOT NULL,
        parent_code VARCHAR(64),
        description TEXT,
        ordering INTEGER DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_di_dict ON dictionary_items(dict_id)",
    """CREATE TABLE IF NOT EXISTS field_dict_links (
        id VARCHAR(36) PRIMARY KEY,
        field_id VARCHAR(36) NOT NULL REFERENCES source_fields(id) ON DELETE CASCADE,
        dict_id VARCHAR(36) NOT NULL REFERENCES source_dictionaries(id) ON DELETE CASCADE,
        filter_condition VARCHAR(256),
        ordering INTEGER DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_fdl_field ON field_dict_links(field_id)",
    """CREATE TABLE IF NOT EXISTS standard_versions (
        id VARCHAR(36) PRIMARY KEY,
        standard_id VARCHAR(36) REFERENCES data_standards(id) ON DELETE SET NULL,
        api_key_id VARCHAR(36) NOT NULL,
        version_label VARCHAR(32),
        change_log TEXT,
        file_data BYTEA,
        file_name VARCHAR(512),
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sv_standard ON standard_versions(standard_id)",
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
