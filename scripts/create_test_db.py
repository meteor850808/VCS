"""创建 VCS 测试数据库（vcs_test）并启用 PostGIS 扩展。

用途: 运行 pytest 前初始化独立测试库，避免污染生产库 vcs_db。
用法:
    python scripts/create_test_db.py               # 使用 .env 中的 DATABASE_URL_SYNC
    python scripts/create_test_db.py --url postgresql://user:pass@localhost:5432/postgres

说明:
    - 测试表结构由 tests/test_api.py 的 autouse fixture (setup_db) 通过
      Base.metadata.create_all 自动创建，本脚本只负责建库 + 扩展。
    - 幂等: 数据库已存在 / 扩展已启用时静默跳过，可重复执行。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 允许直接运行（python scripts/create_test_db.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_DB_NAME = "vcs_test"


async def main() -> None:
    parser = argparse.ArgumentParser(description="创建 VCS 测试数据库")
    parser.add_argument(
        "--url",
        default=None,
        help="PostgreSQL 管理连接 URL（默认取 app.config.DATABASE_URL_SYNC 的服务器部分）",
    )
    args = parser.parse_args()

    if args.url:
        admin_url = args.url
    else:
        from app.config import settings

        # 从同步连接串解析服务器部分（丢弃 db 名，连到 postgres 维护库）
        base = settings.database_url_sync
        admin_url = base.rsplit("/", 1)[0] + "/postgres"

    try:
        import asyncpg
    except ImportError:
        print("缺少依赖 asyncpg，请先安装: pip install asyncpg")
        sys.exit(1)

    conn = await asyncpg.connect(admin_url)
    try:
        # 创建测试库（不存在时）
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
            print(f"[OK] 已创建数据库 {TEST_DB_NAME}")
        else:
            print(f"[SKIP] 数据库 {TEST_DB_NAME} 已存在，跳过")

        # 启用 PostGIS 扩展（vcs_test 库必须支持 JSONB/GIS/LIST 分区表）
        test_url = admin_url.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"
        test_conn = await asyncpg.connect(test_url)
        try:
            await test_conn.execute('CREATE EXTENSION IF NOT EXISTS postgis')
            print("[OK] PostGIS 扩展已就绪")
        finally:
            await test_conn.close()

        print("\n完成。现在可以运行: pytest tests/ -v")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())