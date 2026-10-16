#!/usr/bin/env python3
"""
初始化数据库并创建第一个 API Key。

使用方法：
    # 在 Docker Compose 中执行：
    docker compose exec api python scripts/init_db.py

    # 本地执行（需设置环境变量）：
    python scripts/init_db.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.core.security import generate_api_key
from app.db.session import Base
from app.models.api_key import ApiKey
from app.models.conversion_job import ConversionJob  # noqa: F401
from app.models.batch_job import BatchJob  # noqa: F401
from app.models.quality_report import QualityReport  # noqa: F401
from app.models.stored_file import StoredFile  # noqa: F401
from app.models.data_standard import DataStandard, DataStandardSheet, DataStandardField  # noqa: F401
from app.models.standard_layer_group import StandardLayerGroup  # noqa: F401
from app.models.standard_table import StandardTable  # noqa: F401
from app.models.standard_field import StandardField  # noqa: F401
from app.models.standard_dictionary import StandardDictionary  # noqa: F401
from app.models.dictionary_item import DictionaryItem  # noqa: F401
from app.models.standard_field_dict_link import StandardFieldDictLink  # noqa: F401
from app.models.spatial_feature import PointFeature, LineFeature, PolygonFeature  # noqa: F401

_engine_kwargs = {}
if settings.database_url_sync.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(settings.database_url_sync, **_engine_kwargs)
Session = sessionmaker(bind=engine)


def init():
    print("▶ 正在创建数据库表...")
    Base.metadata.create_all(engine)
    print("  ✓ 数据库表已就绪")

    with Session() as session:
        existing = session.execute(select(ApiKey).limit(1)).scalar_one_or_none()
        if existing:
            count = session.execute(select(func.count()).select_from(ApiKey)).scalar()
            print(f"  ℹ 已存在 {count} 个 API Key，跳过初始化")
            return

        raw_key, key_hash = generate_api_key()
        api_key = ApiKey(
            key_hash=key_hash,
            name="default",
            description="初始化自动创建",
            is_active=True,
        )
        session.add(api_key)
        session.commit()

        print("\n" + "=" * 60)
        print("✅ 初始化完成！请保存以下信息（仅显示一次）：")
        print(f"\n   X-API-Key: {raw_key}\n")
        print("=" * 60)
        print("使用示例：")
        print(f'   curl -H "X-API-Key: {raw_key}" http://localhost:8000/health')
        print("=" * 60 + "\n")


if __name__ == "__main__":
    init()
