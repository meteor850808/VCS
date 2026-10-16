"""Quick DB state check — standalone script."""
import asyncio
from app.db.session import AsyncSessionLocal
from app.models.stored_file import StoredFile
from app.models.spatial_feature import PointFeature, LineFeature, PolygonFeature
from sqlalchemy import select, func

async def check():
    async with AsyncSessionLocal() as db:
        # 列出所有 stored_files
        result = await db.execute(
            select(StoredFile.id, StoredFile.original_name, StoredFile.is_archive,
                   StoredFile.feature_count, StoredFile.file_data, StoredFile.source_archive)
            .order_by(StoredFile.created_at.desc())
            .limit(20)
        )
        rows = result.all()
        print('=== stored_files (latest 20) ===')
        for r in rows:
            fd = 'HAS_DATA' if r.file_data else 'None'
            print(f'  {r.id[:12]}... | {r.original_name[:50]:50s} | archive={r.is_archive} | fc={r.feature_count} | fd={fd} | src={r.source_archive}')

        # 统计 spatial 表行数
        print()
        for label, model in [('point_features', PointFeature), ('line_features', LineFeature), ('polygon_features', PolygonFeature)]:
            r = await db.execute(select(func.count()).select_from(model))
            cnt = r.scalar() or 0
            print(f'{label}: {cnt} rows')

        # 检查 spatial 表中是否有数据但 stored_file 的 feature_count=0
        print()
        print('=== spatial features per stored_file ===')
        for label, model in [('point', PointFeature), ('line', LineFeature), ('polygon', PolygonFeature)]:
            r = await db.execute(
                select(model.stored_file_id, func.count())
                .group_by(model.stored_file_id)
            )
            for sfid, cnt in r.all():
                print(f'  {label}: {sfid[:12]}... => {cnt} features')

asyncio.run(check())
