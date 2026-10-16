# VCS v2.1 数据库架构升级技术方案

## 1. 项目概述

### 1.1 项目背景
VCS（矢量转换服务中心）是一个基于 FastAPI + PostgreSQL + PostGIS 的空间数据处理平台。随着业务发展，数据库架构暴露出以下问题：
- 双基线迁移导致维护困难
- 缺失外键约束影响数据完整性
- BYTEA 大文件存储缺乏限制
- 空间数据缺少有效性检查

### 1.2 升级目标
1. 建立清晰的单基线迁移链
2. 添加完整的外键约束体系
3. 优化大文件存储策略
4. 增强空间数据质量保障
5. 完善审计追踪能力

### 1.3 技术栈
- **后端**: FastAPI + SQLAlchemy 2.0 (async) + Alembic
- **数据库**: PostgreSQL 15 + PostGIS 3.4
- **缓存**: Redis 3+（RESP2 兼容）
- **任务队列**: Celery + Redis
- **容器化**: Docker + Docker Compose

---

## 2. 架构设计

### 2.1 数据库架构

#### 2.1.1 表结构概览
```
api_keys (认证)
├── stored_files (文件存储)
│   └── point_features / line_features / polygon_features (空间要素)
├── batch_jobs (批量任务)
│   └── conversion_jobs (转换任务)
│       └── quality_reports (质量报告)
│           ├── quality_rule_results (规则结果)
│           └── quality_violations (违规明细)
├── data_standards (数据标准)
│   ├── data_standard_sheets (标准表)
│   │   └── data_standard_fields (标准字段)
│   └── standard_versions (版本记录)
├── quality_rules (质检规则)
├── quality_daily_snapshots (每日快照)
└── source_data (源数据管理)
    ├── source_tables (源表)
    │   └── source_fields (源字段)
    ├── source_dictionaries (数据字典)
    │   └── dictionary_items (字典条目)
    └── field_dict_links (字段字典关联)
```

#### 2.1.2 外键约束体系
```sql
-- 核心外键关系
stored_files.api_key_id → api_keys.id
conversion_jobs.api_key_id → api_keys.id
conversion_jobs.batch_job_id → batch_jobs.id
quality_reports.job_id → conversion_jobs.id
quality_reports.batch_job_id → batch_jobs.id
quality_reports.api_key_id → api_keys.id
quality_reports.data_standard_id → data_standards.id

-- 空间表外键
point_features.stored_file_id → stored_files.id
point_features.api_key_id → api_keys.id
line_features.stored_file_id → stored_files.id
polygon_features.stored_file_id → stored_files.id

-- ON DELETE 策略
-- SET NULL: 保留子记录，外键置空
-- CASCADE: 级联删除子记录
```

#### 2.1.3 约束体系
```sql
-- 几何有效性检查
ALTER TABLE point_features ADD CONSTRAINT ck_point_features_valid
    CHECK (geometry IS NULL OR ST_IsValid(geometry));

ALTER TABLE line_features ADD CONSTRAINT ck_line_features_valid
    CHECK (geometry IS NULL OR ST_IsValid(geometry));

ALTER TABLE polygon_features ADD CONSTRAINT ck_polygon_features_valid
    CHECK (geometry IS NULL OR ST_IsValid(geometry));

-- 唯一约束
ALTER TABLE quality_daily_snapshots ADD CONSTRAINT uq_qds_api_date
    UNIQUE (api_key_id, snapshot_date);
```

### 2.2 存储策略

#### 2.2.1 BYTEA 大文件存储
```
┌─────────────────────────────────────────────────┐
│              文件上传流程                         │
├─────────────────────────────────────────────────┤
│ 1. 客户端上传文件                                │
│ 2. 计算 SHA-256 哈希                            │
│ 3. 检查文件大小限制 (100MB)                      │
│ 4. 检查哈希去重                                  │
│ 5. 存入 stored_files 表                         │
│ 6. 返回文件 ID                                  │
└─────────────────────────────────────────────────┘
```

#### 2.2.2 分片上传支持
```python
# 分片元数据
file_hash: str      # 文件 SHA-256 哈希
chunk_index: int    # 分片序号 (0 = 完整文件)
total_chunks: int   # 总分片数
```

#### 2.2.3 配置参数
```python
# app/config.py
max_single_file_size_mb: int = 100   # 单文件上限
file_chunk_size_mb: int = 10         # 分片大小
enable_file_dedup: bool = True       # 去重开关
```

### 2.3 空间数据管理

#### 2.3.1 几何类型路由
```python
# 几何类型 → 表名映射
GEOMETRY_TABLE_MAP = {
    "Point":            ("point_features",   "POINT"),
    "MultiPoint":       ("point_features",   "MULTIPOINT"),
    "LineString":       ("line_features",    "LINESTRING"),
    "MultiLineString":  ("line_features",    "MULTILINESTRING"),
    "Polygon":          ("polygon_features", "POLYGON"),
    "MultiPolygon":     ("polygon_features", "MULTIPOLYGON"),
}
```

#### 2.3.2 空几何支持
- geometry 字段改为 `nullable=True`
- CHECK 约束: `geometry IS NULL OR ST_IsValid(geometry)`
- 允许只存储属性数据的记录

---

## 3. 实现细节

### 3.1 ORM 模型变更

#### 3.1.1 StoredFile 模型
```python
class StoredFile(Base):
    __tablename__ = "stored_files"

    # 新增字段
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    file_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="文件 SHA-256 哈希"
    )
    chunk_index: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    total_chunks: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        onupdate=lambda: datetime.now(timezone.utc)
    )
```

#### 3.1.2 SpatialFeature 模型
```python
class _FeatureBase:
    # 新增字段
    stored_file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stored_files.id", ondelete="CASCADE"),
        nullable=True, index=True
    )
    api_key_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

class PointFeature(Base, _FeatureBase):
    geometry: Mapped[bytes | None] = mapped_column(
        Geometry('GEOMETRY', srid=4326), nullable=True  # 支持空几何
    )
    __table_args__ = (
        CheckConstraint(
            "geometry IS NULL OR ST_IsValid(geometry)",
            name="ck_point_features_valid"
        ),
    )
```

### 3.2 API 端点变更

#### 3.2.1 文件上传端点
```python
@router.post("/data/upload")
async def upload_file(...):
    # 1. 计算文件哈希
    sha256 = hashlib.sha256()
    while chunk := await file.read(1024 * 1024):
        sha256.update(chunk)
    file_hash = sha256.hexdigest()

    # 2. 文件大小限制
    if len(content) > single_file_limit:
        raise HTTPException(status_code=413, detail="文件超过限制")

    # 3. 哈希去重
    if settings.enable_file_dedup:
        existing = await db.execute(
            select(StoredFile).where(StoredFile.file_hash == file_hash)
        )
        if existing.scalar_one_or_none():
            return StoredFileUploadResponse(
                id=existing_file.id,
                message="文件已存在（哈希去重）"
            )

    # 4. 存入数据库
    archive_record = StoredFile(
        file_hash=file_hash,
        ...
    )
```

### 3.3 迁移策略

#### 3.3.1 旧迁移归档
```bash
# 归档旧迁移文件
mkdir -p alembic/versions/archived
mv alembic/versions/0001_*.py alembic/versions/archived/
mv alembic/versions/0002_*.py alembic/versions/archived/
mv alembic/versions/0003_*.py alembic/versions/archived/
```

#### 3.3.2 新基线迁移
```python
# alembic/versions/0001_baseline_v2_1.py
revision = "0001_baseline_v2_1"
down_revision = None  # 单基线起点

def upgrade():
    # 创建所有表结构
    # 包含外键约束、CHECK 约束、索引
    ...
```

---

## 4. 数据完整性

### 4.1 孤立数据检测
```sql
-- 检测孤立数据示例
SELECT t.id
FROM stored_files t
LEFT JOIN api_keys r ON t.api_key_id = r.id
WHERE t.api_key_id IS NOT NULL
  AND r.id IS NULL;
```

### 4.2 清理策略
```python
# scripts/cleanup_orphan_data.py
def cleanup_orphan_data(engine, check, orphan_ids):
    """将孤立记录的外键置为 NULL"""
    sql = text(f"""
        UPDATE {check['table']}
        SET {check['column']} = NULL
        WHERE id IN ({placeholders})
    """)
    with engine.begin() as conn:
        conn.execute(sql)
```

---

## 5. 性能优化

### 5.1 索引策略
```sql
-- 复合索引
CREATE INDEX ix_stored_files_api_created
    ON stored_files (api_key_id, created_at);

-- 空间索引
CREATE INDEX ix_point_features_geometry
    ON point_features USING gist (geometry);

-- 哈希索引 (用于去重查询)
CREATE INDEX ix_stored_files_file_hash
    ON stored_files (file_hash);
```

### 5.2 查询优化
- 使用 `selectin` 加载关联数据
- 避免 N+1 查询问题
- 合理使用 `expire_on_commit=False`

---

## 6. 安全考虑

### 6.1 数据完整性
- 外键约束防止孤立数据
- CHECK 约束确保几何有效性
- 唯一约束防止重复数据

### 6.2 输入验证
- 文件大小限制防止 DoS 攻击
- 文件类型验证防止恶意上传
- 文件哈希去重防止存储滥用

### 6.3 审计追踪
- `created_at` 记录创建时间
- `updated_at` 记录最后更新时间
- `api_key_id` 追溯操作来源

---

## 7. 部署方案

### 7.1 部署步骤
```bash
# 1. 清理孤立数据
python scripts/cleanup_orphan_data.py

# 2. 备份数据库
pg_dump vcs_db > backup_v2_0.sql

# 3. 执行迁移
alembic upgrade head

# 4. 验证迁移
python -c "from app.models import *; print('Models OK')"
```

### 7.2 回滚方案
```bash
# 1. 恢复旧迁移文件
mv alembic/versions/archived/* alembic/versions/
rm alembic/versions/0001_baseline_v2_1.py

# 2. 回滚数据库
alembic downgrade base

# 3. 恢复旧版代码
git checkout v2.0
```

---

## 8. 监控与告警

### 8.1 数据库监控
- 外键约束违反告警
- 孤立数据检测告警
- 几何无效数据告警

### 8.2 应用监控
- 文件上传失败率
- 文件去重率
- 大文件上传性能

---

## 9. 未来规划

### 9.1 短期优化 (v2.2)
- 实现真正的分片上传协议
- 添加文件存储后端 (S3/MinIO)
- 优化大文件内存使用

### 9.2 中期优化 (v3.0)
- 分区表支持
- 读写分离
- 数据归档策略

### 9.3 长期规划 (v4.0)
- 分布式文件存储
- 多租户支持
- 数据湖集成

---

## 10. 附录

### 10.1 配置参数表
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_single_file_size_mb` | int | 100 | 单文件上限 (MB) |
| `file_chunk_size_mb` | int | 10 | 分片大小 (MB) |
| `enable_file_dedup` | bool | True | 去重开关 |
| `max_upload_size_mb` | int | 500 | 总上传上限 (MB) |

### 10.2 外键约束清单
| 子表 | 子字段 | 父表 | 父字段 | ON DELETE |
|------|--------|------|--------|-----------|
| stored_files | api_key_id | api_keys | id | SET NULL |
| conversion_jobs | api_key_id | api_keys | id | SET NULL |
| conversion_jobs | batch_job_id | batch_jobs | id | SET NULL |
| quality_reports | job_id | conversion_jobs | id | SET NULL |
| point_features | stored_file_id | stored_files | id | CASCADE |
| ... | ... | ... | ... | ... |

### 10.3 迁移文件清单
| 文件 | 版本 | 说明 |
|------|------|------|
| 0001_baseline_v2_1.py | 0001 | 新基线迁移 |
| archived/0001_initial.py | - | 旧迁移 (已归档) |
| archived/0001_baseline.py | - | 旧迁移 (已归档) |
| archived/0002_*.py | - | 旧迁移 (已归档) |
| archived/0003_*.py | - | 旧迁移 (已归档) |
