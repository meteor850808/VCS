# VCS v2.1 数据库架构升级测试手册

## 1. 测试概述

### 1.1 测试目标
验证 v2.1 数据库架构升级的正确性、兼容性和稳定性。

### 1.2 测试范围
- ORM 模型变更
- 数据库迁移
- API 端点兼容性
- 文件上传功能
- 空间数据处理
- 前端功能

### 1.3 测试环境
- **数据库**: PostgreSQL 15 + PostGIS 3.4
- **Python**: 3.11+
- **依赖**: 见 requirements.txt

---

## 2. 单元测试

### 2.1 ORM 模型导入测试

#### 测试用例 TC-001: 模型导入验证
```bash
# 执行命令
cd /path/to/vcs
python -c "from app.models import *; print('All models imported successfully')"

# 预期结果
# 输出: All models imported successfully
# 无 ImportError
```

#### 测试用例 TC-002: 模型关系验证
```bash
# 执行命令
python -c "
from app.models import *
from sqlalchemy import inspect

# 检查 StoredFile 模型
inspector = inspect(StoredFile)
fks = inspector.get_foreign_keys()
assert len(fks) > 0, 'StoredFile should have foreign keys'

# 检查 PointFeature 模型
inspector = inspect(PointFeature)
fks = inspector.get_foreign_keys()
assert len(fks) > 0, 'PointFeature should have foreign keys'

print('Model relationships verified')
"

# 预期结果
# 输出: Model relationships verified
```

### 2.2 数据库连接测试

#### 测试用例 TC-003: 数据库连接
```bash
# 执行命令
python -c "
import asyncio
from app.db.session import engine

async def test_connection():
    async with engine.connect() as conn:
        result = await conn.execute('SELECT 1')
        assert result.scalar() == 1
        print('Database connection OK')

asyncio.run(test_connection())
"

# 预期结果
# 输出: Database connection OK
```

### 2.3 配置参数测试

#### 测试用例 TC-004: 配置加载
```bash
# 执行命令
python -c "
from app.config import settings

# 检查新增配置项
assert hasattr(settings, 'max_single_file_size_mb')
assert hasattr(settings, 'file_chunk_size_mb')
assert hasattr(settings, 'enable_file_dedup')

# 检查默认值
assert settings.max_single_file_size_mb == 100
assert settings.file_chunk_size_mb == 10
assert settings.enable_file_dedup == True

print('Configuration loaded correctly')
"

# 预期结果
# 输出: Configuration loaded correctly
```

---

## 3. 集成测试

### 3.1 迁移测试

#### 测试用例 TC-005: 迁移执行
```bash
# 执行命令
cd /path/to/vcs
alembic upgrade head

# 预期结果
# 迁移成功执行，无错误
# 输出类似: INFO  [alembic] Running upgrade  -> 0001_baseline_v2_1
```

#### 测试用例 TC-006: 表结构验证
```bash
# 执行命令
python -c "
import asyncio
from app.db.session import engine
from sqlalchemy import text

async def verify_tables():
    async with engine.connect() as conn:
        # 检查所有表是否存在
        result = await conn.execute(text('''
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        '''))
        tables = [row[0] for row in result]

        expected_tables = [
            'api_keys', 'batch_jobs', 'conversion_jobs',
            'data_standard_fields', 'data_standard_sheets',
            'data_standards', 'dictionary_items', 'field_dict_links',
            'line_features', 'point_features', 'polygon_features',
            'quality_daily_snapshots', 'quality_reports',
            'quality_rule_results', 'quality_rules', 'quality_violations',
            'source_dictionaries', 'source_fields', 'source_tables',
            'stored_files', 'standard_versions'
        ]

        for table in expected_tables:
            assert table in tables, f'Table {table} not found'

        print(f'All {len(expected_tables)} tables verified')

asyncio.run(verify_tables())
"

# 预期结果
# 输出: All 21 tables verified
```

#### 测试用例 TC-007: 外键约束验证
```bash
# 执行命令
python -c "
import asyncio
from app.db.session import engine
from sqlalchemy import text

async def verify_foreign_keys():
    async with engine.connect() as conn:
        result = await conn.execute(text('''
            SELECT
                tc.table_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.table_name, kcu.column_name
        '''))

        fks = [(row[0], row[1], row[2], row[3]) for row in result]

        # 检查关键外键
        expected_fks = [
            ('stored_files', 'api_key_id', 'api_keys', 'id'),
            ('conversion_jobs', 'api_key_id', 'api_keys', 'id'),
            ('conversion_jobs', 'batch_job_id', 'batch_jobs', 'id'),
            ('quality_reports', 'job_id', 'conversion_jobs', 'id'),
            ('point_features', 'stored_file_id', 'stored_files', 'id'),
        ]

        for fk in expected_fks:
            assert fk in fks, f'Foreign key {fk} not found'

        print(f'All {len(expected_fks)} foreign keys verified')

asyncio.run(verify_foreign_keys())
"

# 预期结果
# 输出: All 5 foreign keys verified
```

### 3.2 文件上传测试

#### 测试用例 TC-008: 正常文件上传
```bash
# 执行命令
curl -X POST http://localhost:8000/api/v1/data/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@test.geojson" \
  -F "description=Test upload"

# 预期结果
# HTTP 201 Created
# 返回 JSON 包含 id, original_name, file_size, message
```

#### 测试用例 TC-009: 文件大小限制
```bash
# 执行命令
# 创建一个大于 100MB 的测试文件
dd if=/dev/zero of=large_file.bin bs=1M count=101

curl -X POST http://localhost:8000/api/v1/data/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@large_file.bin"

# 预期结果
# HTTP 413 Request Entity Too Large
# 返回错误信息: 文件超过限制
```

#### 测试用例 TC-010: 文件哈希去重
```bash
# 执行命令
# 第一次上传
curl -X POST http://localhost:8000/api/v1/data/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@test.geojson"

# 第二次上传相同文件
curl -X POST http://localhost:8000/api/v1/data/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@test.geojson"

# 预期结果
# 第二次返回: HTTP 201 Created
# 消息包含: "文件已存在（哈希去重）"
# 返回相同的文件 ID
```

### 3.3 空间数据测试

#### 测试用例 TC-011: 空几何支持
```bash
# 执行命令
python -c "
import asyncio
from app.db.session import AsyncSessionLocal
from app.models.spatial_feature import PointFeature

async def test_null_geometry():
    async with AsyncSessionLocal() as session:
        # 创建一个没有几何数据的记录
        feature = PointFeature(
            layer_name='test_layer',
            source_name='test.geojson',
            feature_index=0,
            properties={'name': 'test'},
            geometry=None  # 空几何
        )
        session.add(feature)
        await session.commit()

        # 验证记录已创建
        assert feature.id is not None
        print('Null geometry test passed')

asyncio.run(test_null_geometry())
"

# 预期结果
# 输出: Null geometry test passed
```

#### 测试用例 TC-012: 几何有效性检查
```bash
# 执行命令
python -c "
import asyncio
from app.db.session import AsyncSessionLocal
from app.models.spatial_feature import PointFeature
from geoalchemy2 import WKTElement

async def test_invalid_geometry():
    async with AsyncSessionLocal() as session:
        # 尝试插入无效几何
        try:
            feature = PointFeature(
                layer_name='test_layer',
                source_name='test.geojson',
                feature_index=0,
                geometry=WKTElement('POINT(0 0)', srid=4326)  # 有效几何
            )
            session.add(feature)
            await session.commit()
            print('Valid geometry test passed')
        except Exception as e:
            print(f'Error: {e}')

asyncio.run(test_invalid_geometry())
"

# 预期结果
# 输出: Valid geometry test passed
```

---

## 4. API 测试

### 4.1 兼容性测试

#### 测试用例 TC-013: 现有 API 端点
```bash
# 执行命令
# 测试所有主要端点
endpoints=(
    "GET /api/v1/data"
    "GET /api/v1/data/{file_id}"
    "POST /api/v1/convert/identify"
    "POST /api/v1/convert/run"
    "GET /api/v1/batch"
    "GET /api/v1/quality/reports"
)

for endpoint in "${endpoints[@]}"; do
    echo "Testing $endpoint"
    # 执行测试...
done

# 预期结果
# 所有端点返回预期响应
# 无 500 Internal Server Error
```

### 4.2 错误处理测试

#### 测试用例 TC-014: 无效 API Key
```bash
# 执行命令
curl -X GET http://localhost:8000/api/v1/data \
  -H "X-API-Key: invalid-key"

# 预期结果
# HTTP 401 Unauthorized
# 返回错误信息
```

#### 测试用例 TC-015: 无效文件类型
```bash
# 执行命令
echo "test" > test.txt
curl -X POST http://localhost:8000/api/v1/data/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@test.txt"

# 预期结果
# HTTP 400 Bad Request
# 返回错误信息: 不支持的文件类型
```

---

## 5. 性能测试

### 5.1 文件上传性能

#### 测试用例 TC-016: 大文件上传性能
```bash
# 执行命令
# 创建 50MB 测试文件
dd if=/dev/zero of=test_50mb.bin bs=1M count=50

# 测量上传时间
time curl -X POST http://localhost:8000/api/v1/data/upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@test_50mb.bin"

# 预期结果
# 上传时间 < 30 秒
# 内存使用稳定
```

### 5.2 数据库查询性能

#### 测试用例 TC-017: 空间查询性能
```bash
# 执行命令
python -c "
import asyncio
import time
from app.db.session import AsyncSessionLocal
from app.models.spatial_feature import PointFeature
from sqlalchemy import select

async def test_spatial_query():
    async with AsyncSessionLocal() as session:
        start = time.time()
        result = await session.execute(
            select(PointFeature).limit(1000)
        )
        features = result.scalars().all()
        elapsed = time.time() - start

        print(f'Query {len(features)} features in {elapsed:.3f}s')
        assert elapsed < 1.0, 'Query too slow'

asyncio.run(test_spatial_query())
"

# 预期结果
# 查询时间 < 1 秒
```

---

## 6. 前端测试

### 6.1 上传界面测试

#### 测试用例 TC-018: 上传界面功能
```bash
# 执行命令
# 打开浏览器访问 http://localhost:8000
# 测试以下功能:
# 1. 拖拽上传文件
# 2. 点击上传按钮
# 3. 查看上传进度
# 4. 查看上传结果

# 预期结果
# 界面正常显示
# 上传功能正常
# 结果显示正确
```

### 6.2 文件列表测试

#### 测试用例 TC-019: 文件列表显示
```bash
# 执行命令
# 在前端界面查看文件列表
# 验证以下字段:
# - 文件名
# - 文件大小
# - 上传时间
# - 文件描述

# 预期结果
# 所有字段正确显示
# 分页功能正常
```

---

## 7. 回归测试

### 7.1 核心功能测试

#### 测试用例 TC-020: 文件转换流程
```bash
# 执行命令
# 1. 上传 GeoJSON 文件
# 2. 执行格式转换
# 3. 下载转换结果
# 4. 验证转换结果

# 预期结果
# 转换成功完成
# 结果文件格式正确
# 数据完整性保持
```

### 7.2 批量处理测试

#### 测试用例 TC-021: 批量转换
```bash
# 执行命令
# 1. 创建批量任务
# 2. 上传多个文件
# 3. 执行批量转换
# 4. 监控任务进度
# 5. 下载所有结果

# 预期结果
# 所有文件转换成功
# 进度显示正确
# 结果完整
```

---

## 8. 清理脚本测试

### 8.1 孤立数据检测

#### 测试用例 TC-022: 孤立数据检测
```bash
# 执行命令
python scripts/cleanup_orphan_data.py --dry-run

# 预期结果
# 显示孤立数据统计
# 未执行实际清理
```

### 8.2 孤立数据清理

#### 测试用例 TC-023: 交互式清理
```bash
# 执行命令
python scripts/cleanup_orphan_data.py

# 预期结果
# 显示孤立数据
# 询问是否清理
# 用户确认后执行清理
```

---

## 9. 部署测试

### 9.1 Docker 部署

#### 测试用例 TC-024: Docker Compose 部署
```bash
# 执行命令
docker-compose down -v
docker-compose up -d

# 预期结果
# 所有服务正常启动
# 迁移自动执行
# API 可访问
```

### 9.2 数据库备份恢复

#### 测试用例 TC-025: 备份恢复
```bash
# 执行命令
# 备份
pg_dump vcs_db > backup.sql

# 恢复
psql vcs_db < backup.sql

# 预期结果
# 备份成功
# 恢复成功
# 数据完整
```

---

## 10. 测试报告模板

### 10.1 测试执行记录
| 测试用例 | 状态 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-001 | ✓ Pass | - | - |
| TC-002 | ✓ Pass | - | - |
| ... | ... | ... | ... |

### 10.2 问题跟踪
| 问题ID | 测试用例 | 描述 | 严重程度 | 状态 |
|--------|----------|------|----------|------|
| BUG-001 | TC-009 | 文件大小限制未生效 | High | Open |
| ... | ... | ... | ... | ... |

### 10.3 测试总结
- **总测试用例**: 25
- **通过**: 23
- **失败**: 2
- **通过率**: 92%

---

## 11. 注意事项

### 11.1 测试前准备
1. 确保数据库服务正常运行
2. 确保 Redis 服务正常运行
3. 确保 Python 依赖已安装
4. 准备测试数据文件

### 11.2 测试环境隔离
1. 使用独立的测试数据库
2. 不要在生产环境执行测试
3. 定期清理测试数据

### 11.3 测试数据管理
1. 使用标准化的测试数据
2. 不要使用真实业务数据
3. 定期更新测试数据

---

## 12. 附录

### 12.1 测试数据文件
- `test.geojson` - 有效 GeoJSON 文件
- `test.shp` - 有效 Shapefile 文件
- `large_file.bin` - 大文件测试
- `invalid_file.txt` - 无效文件类型

### 12.2 测试工具
- `curl` - HTTP 客户端
- `pytest` - Python 测试框架
- `pg_dump` - PostgreSQL 备份工具
- `docker-compose` - Docker 编排工具

### 12.3 相关文档
- [修订记录](revision_v2_1.md)
- [技术方案](technical_spec_v2_1.md)
- [API 文档](http://localhost:8000/docs)
