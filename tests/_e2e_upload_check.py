# -*- coding: utf-8 -*-
"""端到端验证：通过 upload API 上传真实 MDB，确认入库 SRID 为自定义 CGCS2000 GK 41。

使用 vcs_test 测试库（与 test_api.py 相同的隔离机制），不污染生产库 vcs_db。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

TEST_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vcs_test"

# 关键：必须在 import app 之前设置，否则模块级 AsyncSessionLocal
# （app/db/session.py，由 settings.database_url 绑定）仍指向生产库 vcs_db，
# 导致子任务会话（_load_mdb_features_async 内部）在错误的库里写要素。
os.environ.setdefault("DATABASE_URL", TEST_DB_URL)

MDB_PATH = Path(__file__).resolve().parent / "fixtures" / "mdb" / "sample_migration.mdb"


async def main():
    # 清理此前的测试数据（幂等）
    engine = create_async_engine(TEST_DB_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 1. 建表（复用 test_api.py 的机制）
    from app.main import app as fastapi_app
    from app.db.session import Base, get_db
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 0. 幂等清理：删除历史 sample_migration 残留分区 + stored_files + e2e key
    async with engine.begin() as conn:
        old = (await conn.execute(text(
            "SELECT id FROM stored_files WHERE original_name LIKE '%sample_migration%'"
        ))).scalars().all()
        for sid in old:
            n = sid.replace("-", "_")
            await conn.execute(text(
                f"DROP TABLE IF EXISTS "
                f"point_features_p_{n}, line_features_p_{n}, polygon_features_p_{n} CASCADE"
            ))
            await conn.execute(text(
                "DELETE FROM stored_files WHERE id = :sid"
            ), {"sid": sid})
        # 孤儿分区：stored_files 无对应行（上次失败运行的子任务残留），直接清掉
        orphan_parts = (await conn.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE tablename LIKE 'point_features_p_%' OR tablename LIKE 'line_features_p_%'
               OR tablename LIKE 'polygon_features_p_%'
        """))).scalars().all()
        for p in orphan_parts:
            # 分区名 <table>_p_<stored_file_id_with_underscores>，还原 id 判断存在性
            sid = p.split("_p_", 1)[1].replace("_", "-")
            exists = (await conn.execute(
                text("SELECT 1 FROM stored_files WHERE id = :sid"),
                {"sid": sid},
            )).scalar()
            if not exists:
                await conn.execute(text(f"DROP TABLE IF EXISTS {p} CASCADE"))
                print(f"  [cleanup] dropped orphan partition {p}")
        await conn.execute(text(
            "DELETE FROM api_keys WHERE name = 'e2e_mdb_test'"
        ))

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = override_get_db

    from app.core.security import generate_api_key, hash_api_key
    from app.models.api_key import ApiKey

    async with Session() as db:
        raw, hashed = generate_api_key()
        db.add(ApiKey(key_hash=hashed, name="e2e_mdb_test", is_active=True))
        await db.commit()

    # 2. 通过 API 上传
    import io
    from httpx import AsyncClient, ASGITransport

    content = MDB_PATH.read_bytes()
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/data/upload",
            headers={"X-API-Key": raw},
            files={"file": ("sample_migration.mdb", io.BytesIO(content),
                            "application/x-msaccess")},
        )
        print(f"Upload status: {resp.status_code}")
        body = resp.json()
        print(json.dumps(body, ensure_ascii=False, indent=2)[:1200])

        assert resp.status_code in (200, 201), f"上传失败: {body}"
        assert body["extracted_count"] == 3, f"期望 3 个空间表，实际 {body['extracted_count']}"
        # 元数据回填断言（MDB 分支修复后：feature_count/layer_count/detected_crs 应非空）
        assert body["feature_count"] == 94, f"期望 94 features，实际 {body['feature_count']}"
        assert body["layer_count"] == 3, f"期望 3 layers，实际 {body['layer_count']}"
        assert body["detected_crs"], "detected_crs 未回填"
        assert body["geometry_type"] == "Polygon", f"期望 Polygon，实际 {body['geometry_type']}"
        print(f"  元数据回填 OK: feature_count={body['feature_count']} "
              f"layer_count={body['layer_count']} geom={body['geometry_type']}")

    # 3. 查库验证 SRID 与坐标域（空间要素按几何类型分表存储）
    async with Session() as db:
        rows = (await db.execute(text("""
            SELECT sf.stored_file_id, sf.geometry_type,
                   st_SRID(sf.geometry), count(*) AS n
            FROM (
                SELECT stored_file_id, geometry_type, geometry FROM point_features
                UNION ALL
                SELECT stored_file_id, geometry_type, geometry FROM line_features
                UNION ALL
                SELECT stored_file_id, geometry_type, geometry FROM polygon_features
            ) sf
            GROUP BY sf.stored_file_id, sf.geometry_type, st_SRID(sf.geometry)
            ORDER BY sf.stored_file_id
        """))).all()
        print(f"\n空间要素分组: {len(rows)} 组")
        for r in rows:
            print(f"  stored_file={r[0][:8]}.. geom={r[1]} geo_srid={r[2]} n={r[3]}")

        # 关键断言：入库 SRID 必须 ≥ 910000（自定义注册），不是 4326
        assert rows, "空间要素无数据"
        total = 0
        for r in rows:
            assert r[2] >= 910000, f"入库 SRID={r[2]} 不是自定义 CGCS2000 GK SRID!"
            total += r[3]
        assert total == 94, f"期望 94 要素入库，实际 {total}"

        # 4. 验证坐标可正确变换回 WGS84（落在上海）
        pt = (await db.execute(text("""
            WITH all_geom AS (
                SELECT geometry FROM point_features
                UNION ALL SELECT geometry FROM line_features
                UNION ALL SELECT geometry FROM polygon_features
            )
            SELECT ST_AsText(ST_Transform(ST_Centroid(ST_Collect(geometry)), 4326)) AS p,
                   ST_XMin(ST_Extent(geometry)), ST_XMax(ST_Extent(geometry)),
                   ST_YMin(ST_Extent(geometry)), ST_YMax(ST_Extent(geometry))
            FROM all_geom
        """))).one()
        print(f"\n质心变换到 WGS84: {pt[0]}")
        print(f"X 范围: {pt[1]:.0f} ~ {pt[2]:.0f}, Y 范围: {pt[3]:.0f} ~ {pt[4]:.0f}")
        assert 120.5 < float(pt[0].split()[0].lstrip("POINT(")) < 123.0, "经度异常"
        assert 30.5 < float(pt[0].split()[1].rstrip(")")) < 32.0, "纬度异常"

    await engine.dispose()
    print("\n=== 端到端 MDB 上传验证通过（SRID 正确 + 坐标可变换） ===")


asyncio.run(main())