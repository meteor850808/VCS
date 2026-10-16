# VCS v2.1 数据库架构升级修订记录

## 版本信息
- **版本**: v2.1
- **日期**: 2026-07-15
- **类型**: 数据库架构重构
- **影响范围**: 全部 ORM 模型、数据库迁移、API 端点

---

## 修订目标

1. 消除双基线迁移问题，建立单基线迁移链
2. 添加外键约束确保数据完整性
3. 优化 BYTEA 大文件存储策略
4. 增强空间数据约束
5. 完善审计字段

---

## 详细变更

### 1. ORM 模型变更

#### 1.1 api_keys (无变更)
- 保持现有结构不变

#### 1.2 stored_files
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `api_key_id` | 新增 FK | `→ api_keys.id, ON DELETE SET NULL` |
| `file_hash` | 新增 | SHA-256 哈希，用于文件去重 |
| `chunk_index` | 新增 | 分片序号，0 表示完整文件 |
| `total_chunks` | 新增 | 总分片数 |
| `updated_at` | 新增 | 审计字段，自动更新 |

#### 1.3 conversion_jobs
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `api_key_id` | 新增 FK | `→ api_keys.id, ON DELETE SET NULL` |
| `batch_job_id` | 新增 FK | `→ batch_jobs.id, ON DELETE SET NULL` |
| `updated_at` | 新增 | 审计字段，自动更新 |

#### 1.4 batch_jobs
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `api_key_id` | 新增 FK | `→ api_keys.id, ON DELETE SET NULL` |
| `updated_at` | 新增 | 审计字段，自动更新 |

#### 1.5 quality_reports
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `job_id` | 新增 FK | `→ conversion_jobs.id, ON DELETE SET NULL` |
| `batch_job_id` | 新增 FK | `→ batch_jobs.id, ON DELETE SET NULL` |
| `api_key_id` | 新增 FK | `→ api_keys.id, ON DELETE SET NULL` |
| `data_standard_id` | 新增 FK | `→ data_standards.id, ON DELETE SET NULL` |
| `updated_at` | 新增 | 审计字段，自动更新 |

#### 1.6 data_standards / data_standard_sheets / data_standard_fields
- `api_key_id` 添加 FK 约束
- 所有表添加 `updated_at` 审计字段

#### 1.7 quality_rules / quality_rule_results / quality_violations
- 已有 FK 约束，保持不变
- quality_rules 已有 `updated_at`

#### 1.8 quality_daily_snapshot
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `api_key_id` | 新增 FK | `→ api_keys.id, ON DELETE CASCADE` |
| `updated_at` | 新增 | 审计字段，自动更新 |

#### 1.9 source_data (6 个子模型)
- 所有子模型添加 `api_key_id` FK 约束
- 所有子模型添加 `updated_at` 审计字段

#### 1.10 spatial_feature (point/line/polygon)
| 字段 | 变更类型 | 说明 |
|------|----------|------|
| `stored_file_id` | 新增 FK | `→ stored_files.id, ON DELETE CASCADE` |
| `api_key_id` | 新增 FK | `→ api_keys.id, ON DELETE SET NULL` |
| `geometry` | 修改 | `nullable=True` (支持空几何) |
| `updated_at` | 新增 | 审计字段，自动更新 |
| CHECK 约束 | 新增 | `ST_IsValid(geometry)` 几何有效性检查 |

### 2. 配置变更

#### 2.1 app/config.py
| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `max_single_file_size_mb` | 新增 | 100 | 单文件 BYTEA 存储上限 (MB) |
| `file_chunk_size_mb` | 新增 | 10 | 分片上传每片大小 (MB) |
| `enable_file_dedup` | 新增 | True | 启用文件哈希去重 |

### 3. API 变更

#### 3.1 POST /api/v1/data/upload
- 新增文件大小限制检查 (`max_single_file_size_mb`)
- 新增文件哈希去重逻辑
- 返回值增加去重提示信息

### 4. 迁移变更

#### 4.1 旧迁移归档
- 所有旧迁移文件移动至 `alembic/versions/archived/`
- 包括: `0001_initial.py`, `0001_baseline.py`, `0002_*.py`, `0003_*.py`

#### 4.2 新基线迁移
- 文件: `alembic/versions/0001_baseline_v2_1.py`
- 特点: 单文件包含所有表结构定义
- 包含: 外键约束、CHECK 约束、空间索引

---

## 向后兼容性

### 数据迁移
- 无需数据迁移，所有变更都是结构性的
- 孤立数据需要通过 `scripts/cleanup_orphan_data.py` 清理

### API 兼容性
- 所有现有 API 端点保持兼容
- 新增字段均为可选字段
- 上传接口增加限制但不影响现有客户端

### 部署兼容性
- docker-compose.yml 无需修改
- Dockerfile 无需修改
- 环境变量配置兼容

---

## 风险评估

### 高风险
- **孤立数据清理**: 如果存在孤立数据，添加外键约束会失败
  - 缓解措施: 提供清理脚本 `cleanup_orphan_data.py`

### 中风险
- **文件大小限制**: 新增的 100MB 限制可能影响大文件上传
  - 缓解措施: 可通过环境变量调整

### 低风险
- **几何约束**: ST_IsValid 检查可能拒绝无效几何数据
  - 缓解措施: 几何字段支持 NULL

---

## 回滚方案

### 完全回滚
1. 恢复旧迁移文件:
   ```bash
   mv alembic/versions/archived/* alembic/versions/
   rm alembic/versions/0001_baseline_v2_1.py
   ```

2. 重新应用旧迁移:
   ```bash
   alembic upgrade head
   ```

### 部分回滚 (保留数据)
1. 移除新增的外键约束
2. 保留所有表结构
3. 恢复旧版 ORM 模型

---

## 测试覆盖

### 单元测试
- ORM 模型导入测试
- 数据库连接测试
- 文件上传去重测试

### 集成测试
- 完整迁移流程测试
- API 端点兼容性测试
- 前端功能测试

### 性能测试
- 大文件上传性能
- 数据库查询性能
- 空间查询性能

---

## 相关文件

### 修改文件
- `app/models/stored_file.py`
- `app/models/conversion_job.py`
- `app/models/batch_job.py`
- `app/models/quality_report.py`
- `app/models/data_standard.py`
- `app/models/quality_daily_snapshot.py`
- `app/models/source_data.py`
- `app/models/spatial_feature.py`
- `app/config.py`
- `app/api/v1/data.py`

### 新增文件
- `alembic/versions/0001_baseline_v2_1.py`
- `scripts/cleanup_orphan_data.py`
- `docs/revision_v2_1.md`
- `docs/technical_spec_v2_1.md`
- `docs/test_manual_v2_1.md`

### 归档文件
- `alembic/versions/archived/0001_initial.py`
- `alembic/versions/archived/0001_baseline.py`
- `alembic/versions/archived/0002_add_batch_quality_progress.py`
- `alembic/versions/archived/0002_performance_indexes.py`
- `alembic/versions/archived/0003_quality_rules.py`

---

## 审批记录

| 角色 | 姓名 | 日期 | 备注 |
|------|------|------|------|
| 架构师 | - | 2026-07-15 | 初稿 |
| 开发负责人 | - | - | - |
| 测试负责人 | - | - | - |
| 产品负责人 | - | - | - |
