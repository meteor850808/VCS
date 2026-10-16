"""用真实 service 层跑一遍 CSV 归档往返，肉眼校验交付物（人工验证用）。

独立库 vcs_verify（自动建/删，启用 PostGIS），**不碰 vcs / vcs_test**，可安全反复跑。

用法：python scripts/verify_csv_archive.py
"""
import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app import models  # noqa: F401  注册全部 ORM
from app.models.api_key import ApiKey
from app.core.security import generate_api_key
from app.models.data_standard import DataStandard
from app.models.standard_layer_group import StandardLayerGroup
from app.models.standard_table import StandardTable
from app.models.standard_field import StandardField
from app.models.standard_dictionary import StandardDictionary
from app.models.dictionary_item import DictionaryItem
from app.api.v1.data_standard_v2 import import_export as ie

URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/vcs_verify"


async def seed(session):
    raw, hashed = generate_api_key()
    key = ApiKey(key_hash=hashed, name="verify", is_active=True)
    session.add(key)
    await session.flush()

    std = DataStandard(id="std-v1", api_key_id=key.id, name="道路检查标准",
                       description="演示标准", category="custom")
    session.add(std)
    g1 = StandardLayerGroup(id="g1", standard_id=std.id, group_name="道路",
                            group_code="RD", description="道路相关层组", ordering=0)
    g2 = StandardLayerGroup(id="g2", standard_id=std.id, parent_id=g1.id,
                            group_name="路面细节", group_code="RD_SUB", ordering=1)
    session.add_all([g1, g2])
    t1 = StandardTable(id="t1", standard_id=std.id, layer_id=g1.id, table_name="路面",
                       table_code="SURF", table_type="attribute", geometry_type="polygon",
                       description="路面要素表", ordering=0)
    session.add(t1)
    f1 = StandardField(id="f1", standard_id=std.id, table_id=t1.id, field_name="mat",
                       field_alias="材料", data_type="string", length=32,
                       is_required=True, description="面层材料", default_value="沥青",
                       ordering=0)
    session.add(f1)
    d1 = StandardDictionary(id="d1", standard_id=std.id, api_key_id=key.id,
                            dict_code="MAT", dict_name="面层材料", scope="standard",
                            description="路面材料字典")
    d2 = StandardDictionary(id="d2", standard_id=std.id, api_key_id=key.id,
                            dict_code="EMPTY", dict_name="空字典", scope="standard")
    session.add_all([d1, d2])
    session.add(DictionaryItem(id="i1", dict_id=d1.id, item_code="AC",
                               item_name="沥青混凝土", description="常用材料", ordering=0))
    await session.flush()
    session.add(ie.StandardFieldDictLink(id="l1", field_id=f1.id, dict_id=d1.id,
                                          is_primary=True, ordering=0))
    await session.commit()
    return std, f1


async def _ensure_database():
    """建库 + 开 PostGIS。建完即用，跑完就删，不留残留。"""
    import asyncpg
    admin = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/postgres")
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname='vcs_verify'"):
            await admin.execute("CREATE DATABASE vcs_verify")
        await admin.execute(
            'CREATE EXTENSION IF NOT EXISTS postgis')  # 建在 postgres 库上时无效，见下
    except Exception:
        pass
    finally:
        await admin.close()

    # PostGIS 必须建在目标库里
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5432/vcs_verify")
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    finally:
        await conn.close()


async def _drop_database():
    import asyncpg
    admin = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/postgres")
    try:
        await admin.execute("DROP DATABASE IF EXISTS vcs_verify")
    finally:
        await admin.close()


async def main():
    await _ensure_database()
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        std, field = await seed(session)
        await session.refresh(std)
        buf = ie._build_csv_archive(std)
        raw = buf.getvalue()

        print("=" * 68)
        print("归档内容：")
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                body = zf.read(name)
                bom = "BOM" if body.startswith(b"\xef\xbb\xbf") else "无BOM"
                print(f"\n--- {name}  ({len(body)} bytes, {bom}) ---")
                if name.endswith(".json"):
                    print(body.decode("utf-8"))
                else:
                    print(body.decode("utf-8-sig").rstrip())

        # 导回新标准
        blobs = {n: zipfile.ZipFile(io.BytesIO(raw)).read(n)
                 for n in zipfile.ZipFile(io.BytesIO(raw)).namelist()}
        manifest = ie._validated_manifest(
            json.loads(blobs.pop(ie._CSV_MANIFEST_NAME).decode("utf-8")))
        std2 = DataStandard(id="std-v2", api_key_id=std.api_key_id, name="回导标准")
        session.add(std2)
        tables, fields, warnings = await ie._create_csv_children_from_archive(
            session, std2.id, manifest, blobs,
            (await session.execute(select(ApiKey).where(ApiKey.id == std.api_key_id))).scalar_one())
        await session.commit()

        print("=" * 68)
        print(f"回导：{tables} 表 / {fields} 字段，告警={warnings}")
        new_groups = (await session.execute(
            select(StandardLayerGroup).where(
                StandardLayerGroup.standard_id == std2.id).order_by(
                StandardLayerGroup.ordering))).scalars().all()
        code_of = {g.id: g.group_code for g in new_groups}
        for g in new_groups:
            print(f"  层组 {g.group_name}({g.group_code}) "
                  f"parent={code_of.get(g.parent_id, '顶级') if g.parent_id else '顶级'}")
        new_fields = (await session.execute(
            select(StandardField).where(
                StandardField.standard_id == std2.id).order_by(
                StandardField.ordering))).scalars().all()
        for f in new_fields:
            print(f"  字段 {f.field_name} alias={f.field_alias} "
                  f"type={f.data_type} len={f.length} default={f.default_value}")
        new_dicts = (await session.execute(
            select(StandardDictionary).where(
                StandardDictionary.standard_id == std2.id))).scalars().all()
        dict_code = {d.id: d.dict_code for d in new_dicts}
        print(f"  字典 {sorted(dict_code.values())}")
        for l in (await session.execute(
                select(ie.StandardFieldDictLink).where(
                    ie.StandardFieldDictLink.field_id.in_(
                        [f.id for f in new_fields] or [""])))).scalars().all():
            owner = next(f.field_name for f in new_fields if f.id == l.field_id)
            print(f"  绑定 {owner} → {dict_code.get(l.dict_id)} primary={l.is_primary}")

    await engine.dispose()
    await _drop_database()
    print("=" * 68)
    print("OK（vcs_verify 已清理）")


asyncio.run(main())
