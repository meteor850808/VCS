"""Alembic 环境配置 — 支持异步 SQLAlchemy。"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from app.db.session import Base

# 导入所有模型以注册到 Base.metadata
import app.models.api_key         # noqa: F401
import app.models.conversion_job  # noqa: F401
import app.models.batch_job       # noqa: F401
import app.models.quality_report  # noqa: F401
import app.models.stored_file     # noqa: F401
import app.models.data_standard   # noqa: F401
import app.models.spatial_feature # noqa: F401
import app.models.quality_rule # noqa: F401
import app.models.quality_rule_result # noqa: F401
import app.models.quality_daily_snapshot # noqa: F401
import app.models.source_data # noqa: F401
# v2.2 新模型
import app.models.standard_layer_group # noqa: F401
import app.models.standard_table # noqa: F401
import app.models.standard_field # noqa: F401
import app.models.standard_dictionary # noqa: F401
import app.models.dictionary_item # noqa: F401
import app.models.standard_field_dict_link # noqa: F401
import app.models.transform_result # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式迁移（生成 SQL 脚本）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    """在线模式迁移（直接执行）。"""
    connectable = create_async_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
