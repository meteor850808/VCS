# 智能数据质量检查 — 优化方案与实施规划

## 1. 现状分析

### 当前质量检查系统

```
文件上传 → format_detection → attribute_processing → null_analysis 
→ critical_fields_check → constraint_violations → topology_check → score → report
```

**已实现的能力：**
- 空值统计 + 关键字段（YWBH/FEATURECODE）验证
- 字段约束违规检测（类型/值域/长度）
- 基础拓扑检查（重叠/间隙/自相交）
- HTML/JSON 报告生成
- 违规要素导出（GeoJSON/Excel）

**现存不足：**

| 问题 | 表现 | 影响 |
|------|------|------|
| 缺少跨文件对比 | 每次质检独立，无法横向对比 | 无法识别全库数据质量趋势 |
| 无自动化规则引擎 | 规则硬编码在代码中 | 用户无法自定义质量规则 |
| 报告存储分散 | HTML/JSON 在磁盘上，DB 仅存元数据 | 报告不可搜索、不可聚合分析 |
| 无字段画像 | 只统计空值，不做值分布/模式分析 | 无法发现异常值 |
| 前端体验弱 | 质检报告页面为简单列表 | 无法可视化浏览、筛选、排序 |
| 无定时/批量质检 | 每次需要手动上传 | 不适合长期质量监控 |

### 深度审查发现的 13 个 Bug 与设计缺陷

以下由代码审查 Agent 在 2026-06-18 全量扫描发现（影响程度从高到低）：

| 严重度 | 问题 | 文件 | 说明 |
|--------|------|------|------|
| 🔴 BUG | 前端拓扑配置格式与后端不匹配 | `index.html` / `quality_checker.py` | 前端发送 `{rules: [{name, enabled}]}`，后端期望 `{rule_name: {enabled}}`。**从前端发起的拓扑检查永远不生效** |
| 🔴 BUG | `CRITICAL_FIELDS` 导入但未使用 | `quality_checker.py:11` / `constants.py:28` | 常量定义了 7 个字段名含中文别名，但 `_check_critical_fields` 硬编码了 `["YWBH","FEATURECODE"]` |
| 🔴 BUG | 3 个数据库列在 ORM 存在但迁移缺失 | `quality_report.py:30-32` / 迁移文件 | `constraint_violations`、`topology_violations`、`data_standard_id` 在 Alembic 迁移中不存在 |
| 🟡 重复 | 质检端点 60+ 行代码重复 | `quality.py` / `data.py` | `POST /quality/check` 和 `POST /data/{id}/quality-check` 逻辑完全一致 |
| 🟡 死代码 | ValidationPipeline 从未被调用 | `validation_pipeline.py`（全文） | 完整的四层验证管道（L1-L4）已实现但没有任何 API 调用它 |
| 🟡 死代码 | QualityCheckConfig 从未被实例化 | `quality/models.py:33-39` | 配置类定义完整但从未被任何调用者使用 |
| 🟡 泄漏 | Excel 导出临时文件不清理 | `quality.py:246-253` | `tempfile.mkstemp` 创建的文件仅在响应后丢弃，磁盘持续增长 |
| 🟡 泄漏 | 质检上传目录不清理 | `quality.py:47` / `data.py:443-444` | 每次质检创建的临时目录永不删除 |
| 🟡 重复 | issues 和 constraint_violations 重复数据 | `quality_checker.py:305-351` / `quality_exporter.py` | 同一违规出现在两个字段中，导出 Excel 时重复计数 |
| 🟡 性能 | 数据标准详情 N+1 查询 | `data_standards.py:163-187` | 查询详情时先查 Sheet 再逐条查 Field |
| ⚪ 设计 | 无质检报告删除接口 | `quality.py` | 数据标准有 DELETE，质检报告没有 |
| ⚪ 设计 | `data_standard_id` 无外键约束 | `quality_report.py:32` | 报告可引用已删除的标准 |
| ⚪ 设计 | Boolean 类型推断死代码 | `quality_checker.py:231` | Python bool 在到达该检查前已被 int 分支拦截 |

### 与数据标准的关联

当前数据标准模型（DataStandard → Sheet → Field）定义了字段的名称、类型、长度、是否可空、允许值列表。智能质检应充分利用这些信息：

```
数据标准字段定义 → 质检规则生成 → 执行检查 → 对比偏差报告
```

---

## 2. 优秀方案调研总结

### 行业方案参考

| 方案 | 核心特点 | 可借鉴点 |
|------|----------|----------|
| **Great Expectations**（数据质量框架） | 声明式 Expectation + 自动化验证 + 数据文档 | Expectation 规则引擎模式、批量验证、数据文档生成 |
| **Apache Griffin**（企业数据质量） | 准确度/完整性/一致性/及时性/唯一性 五维模型 | 质量评分维度模型、趋势图表 |
| **Soda Core**（开源数据质量） | 可编程 Check + 指标收集 + 通知集成 | Check YAML 配置、指标聚合、CLI 工作流 |
| **Debezium + Kafka**（数据质量管道） | CDC 实时质量检测 | 增量质检管道 |

### 适用本项目的方案

本项目不需要完整的企业级方案，应采用 **轻量可扩展** 的架构：

```
数据标准 → 规则生成器 → 质检管道 → 评分引擎 → 报告聚合器 → 趋势分析
                         ↓
                 可视化前端（趋势图+明细表+导出）
```

---

## 3. 功能点分解

### Phase A：质检规则引擎（核心重构）

| 编号 | 功能点 | 说明 | 预估工时 |
|------|--------|------|----------|
| A1 | 规则定义模型 | 创建 QualityRule 数据模型（规则名称、类型、参数、严重级别、关联标准字段） | 2h |
| A2 | 规则自动生成 | 从数据标准（DataStandardField）自动生成质检规则 | 2h |
| A3 | 规则引擎 | 规则加载 → 匹配文件字段 → 执行检查 → 收集结果 | 3h |
| A4 | 规则存储 | 规则持久化到数据库（可查/可编辑/可开启关闭） | 1h |

规则类型设计：

```
RuleType:
  - NOT_NULL        # 字段不可为空
  - UNIQUE          # 字段值唯一
  - VALUE_IN_SET    # 值必须在允许列表中
  - TYPE_MATCH      # 字段类型匹配
  - LENGTH_LIMIT    # 字段长度限制
  - PATTERN_MATCH   # 正则表达式匹配
  - RANGE_CHECK     # 数值范围检查
  - CROSS_FIELD     # 跨字段逻辑校验
```

### Phase B：字段画像与异常检测（核心能力）

| 编号 | 功能点 | 说明 | 预估工时 |
|------|--------|------|----------|
| B1 | 值分布分析 | 字段值频率统计（TOP N + 低频值） | 2h |
| B2 | 异常值检测 | Z-Score / IQR 数值异常、罕见分类值 | 3h |
| B3 | 字段相关性 | 跨字段的 Null 值关联模式检测 | 2h |
| B4 | 重复记录检测 | 基于关键字段的完全/模糊重复检测 | 2h |

### Phase C：报告系统升级（用户体验）

| 编号 | 功能点 | 说明 | 预估工时 |
|------|--------|------|----------|
| C1 | 报告聚合模型 | 新建 quality_report_details 表，存储每条违规记录 | 2h |
| C2 | 多维评分 | 按维度评分（完整性/准确性/一致性/唯一性/及时性） | 2h |
| C3 | 报告对比 | 同一数据标准 + 不同文件的评分对比 | 2h |
| C4 | 趋势分析 | 同一文件的历史质检分数趋势（折线图） | 3h |
| C5 | 报告导出增强 | 带图表 PDF / 批量导出 | 2h |

### Phase D：前端质检工作台（用户交互）

| 编号 | 功能点 | 说明 | 预估工时 |
|------|--------|------|----------|
| D1 | 质检配置面板 | 选择数据标准、勾选启用的规则类型、配置拓扑参数 | 2h |
| D2 | 结果可视化 | 评分仪表盘（雷达图/环形图）+ 违规明细表（可排序/筛选/分页） | 4h |
| D3 | 违规详情钻取 | 点击违规项展开受影响要素列表（行级） | 3h |
| D4 | 趋势图表 | 历史质检评分趋势折线图 | 2h |
| D5 | 批量质检操作 | 上传时自动触发质检 / 对已入库文件批量质检 | 2h |

### Phase E：数据库建设（持久化与查询优化）

| 编号 | 功能点 | 说明 | 预估工时 |
|------|--------|------|----------|
| E1 | quality_rules 表 | 质检规则定义表 | 1h |
| E2 | quality_rule_results 表 | 规则执行结果表（每条规则一条记录） | 1h |
| E3 | quality_violations 表 | 违规明细表（每条违规一条记录，可追溯至具体要素） | 1h |
| E4 | quality_daily_snapshots 表 | 每日质量快照（趋势分析用） | 1h |
| E5 | 现有数据迁移 | 将旧版 quality_reports 中 issues 字段升级为关联表 | 2h |

### Phase F：数据库管理 & 运维

| 编号 | 功能点 | 说明 | 预估工时 |
|------|--------|------|----------|
| F1 | 数据保留策略 | 违规明细保留 90 天，快照保留 365 天（可配置） | 1h |
| F2 | 分区策略 | quality_violations 按月分区（pg_partman） | 2h |
| F3 | 清理任务 | Celery Beat 定时清理过期数据 | 1h |
| F4 | 监控指标 | 质检覆盖率（已质检文件/总文件）、平均评分趋势 | 1h |

---

## 4. 数据库建设方案

### 4.1 新建表结构

```sql
-- ═══════════════════════════════════════════════════════════════
-- 质检规则定义表
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE quality_rules (
    id              VARCHAR(36) PRIMARY KEY,
    api_key_id      VARCHAR(36) NOT NULL REFERENCES api_keys(id),
    standard_field_id VARCHAR(36) REFERENCES data_standard_fields(id) ON DELETE CASCADE,
    rule_type       VARCHAR(32) NOT NULL,   -- NOT_NULL / UNIQUE / VALUE_IN_SET / ...
    rule_name       VARCHAR(256) NOT NULL,
    severity        VARCHAR(16) NOT NULL DEFAULT 'warning', -- critical / warning / info
    params          JSONB,                  -- {allowed_values:[...], max_length:100, ...}
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_qrules_api_key ON quality_rules(api_key_id);
CREATE INDEX idx_qrules_field ON quality_rules(standard_field_id);

-- ═══════════════════════════════════════════════════════════════
-- 规则执行结果表（一次质检 = 一个 report + 多条 rule_results）
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE quality_rule_results (
    id              VARCHAR(36) PRIMARY KEY,
    report_id       VARCHAR(36) NOT NULL REFERENCES quality_reports(id) ON DELETE CASCADE,
    rule_id         VARCHAR(36) REFERENCES quality_rules(id) ON DELETE SET NULL,
    rule_type       VARCHAR(32) NOT NULL,
    field_name      VARCHAR(256) NOT NULL,
    passed          BOOLEAN NOT NULL,
    severity        VARCHAR(16) NOT NULL,
    total_checked   INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    detail          JSONB,                  -- {sample_values:[...], top_errors:[...]}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_qrr_report ON quality_rule_results(report_id);
CREATE INDEX idx_qrr_field ON quality_rule_results(field_name);

-- ═══════════════════════════════════════════════════════════════
-- 违规明细表（每条违规一条记录，可追溯到行）
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE quality_violations (
    id              VARCHAR(36) PRIMARY KEY,
    report_id       VARCHAR(36) NOT NULL REFERENCES quality_reports(id) ON DELETE CASCADE,
    rule_result_id  VARCHAR(36) REFERENCES quality_rule_results(id) ON DELETE CASCADE,
    field_name      VARCHAR(256) NOT NULL,
    issue_type      VARCHAR(32) NOT NULL,
    severity        VARCHAR(16) NOT NULL,
    feature_index   INTEGER,                -- 要素在文件中的行号（可为 NULL）
    expected        TEXT,                   -- 期望值
    actual          TEXT,                   -- 实际值
    message         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);
CREATE INDEX idx_qv_report ON quality_violations(report_id);
CREATE INDEX idx_qv_field ON quality_violations(field_name);

-- ═══════════════════════════════════════════════════════════════
-- 每日质量快照表（趋势分析用）
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE quality_daily_snapshots (
    id              VARCHAR(36) PRIMARY KEY,
    api_key_id      VARCHAR(36) NOT NULL REFERENCES api_keys(id),
    snapshot_date   DATE NOT NULL,
    total_files     INTEGER NOT NULL DEFAULT 0,
    checked_files   INTEGER NOT NULL DEFAULT 0,   -- 已有质检报告的文件
    completeness    NUMERIC(5,4),                 -- 完整性均值
    uniqueness      NUMERIC(5,4),                 -- 唯一性均值
    consistency     NUMERIC(5,4),                 -- 一致性均值
    violations_total INTEGER NOT NULL DEFAULT 0,  -- 违规总数
    score_overall   NUMERIC(5,4),                 -- 综合评分
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(api_key_id, snapshot_date)
);
CREATE INDEX idx_qds_date ON quality_daily_snapshots(snapshot_date);
```

### 4.2 表关系图

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────────────┐
│  stored_files │ ──→ │  quality_reports   │ ──→ │ quality_rule_results │
└──────────────┘     └───────────────────┘     └──────────────────────┘
                            │                            │
                            │                            │
                            ▼                            ▼
                     ┌──────────────┐          ┌──────────────────────┐
                     │ daily_snapshots│         │ quality_violations   │
                     └──────────────┘          │ (按月分区, 可溯源)    │
                                               └──────────────────────┘
                            ▲
                            │
                     ┌──────┴────────┐
                     │ data_standards │
                     │    (现有)      │
                     │  → sheets     │
                     │  → fields     │
                     └───────────────┘
                            │
                            ▼
                     ┌──────────────┐
                     │ quality_rules │
                     │ (从标准字段生成)│
                     └──────────────┘
```

---

## 5. 实施计划

### 实施路线图

```
Phase A: 规则引擎      ████████░░░░░░  第 1-3 天
Phase B: 字段画像      ░░░░████████░░  第 3-6 天  
Phase C: 报告升级      ░░░░░░░░██████  第 5-8 天
Phase D: 前端          ░░░░░░░░░░████  第 7-10 天
Phase E: 数据库建设    ██████████████  贯穿全部 Phase
Phase F: 运维管理      ░░░░░░░░░░░░██  第 9-10 天
```

### 详细任务分解

#### 第 1-2 天：规则引擎 + 数据库（A1-A4 + E1-E2）

| 任务 | 产出 |
|------|------|
| 创建 quality_rules ORM 模型 + Alembic 迁移 | `app/models/quality_rule.py` |
| 规则生成器：从 DataStandardField 自动生成规则 | `app/services/quality/rule_generator.py` |
| 规则引擎：加载规则 → 匹配字段 → 执行检查 | `app/services/quality/rule_engine.py` |
| 规则 CRUD API（创建/列表/更新/删除） | `app/api/v1/quality_rules.py` |

#### 第 3-5 天：字段画像 + 违规明细（B1-B4 + E3）

| 任务 | 产出 |
|------|------|
| 值分布分析器（频率统计 + TOP N + 低频值检测） | `app/services/quality/profile_analyzer.py` |
| 异常值检测（Z-Score for number, 罕见值 for string） | `app/services/quality/anomaly_detector.py` |
| 跨字段 Null 关联分析 | `app/services/quality/null_correlation.py` |
| 重复记录检测 | `app/services/quality/duplicate_detector.py` |
| quality_violations 模型 + 迁移 + 写入逻辑 | `app/models/quality_violation.py` |

#### 第 5-7 天：报告升级 + 评分模型（C1-C5）

| 任务 | 产出 |
|------|------|
| 五维评分模型（完整性/准确性/一致性/唯一性/及时性） | `app/services/quality/scoring.py` |
| 报告聚合 API（按标准/按文件/按时间聚合） | `app/api/v1/quality.py` 增强 |
| 报告对比 API（两个文件的评分对比） | `app/api/v1/quality.py` 新增 |
| 趋势数据 API（时间序列评分） | `app/api/v1/quality.py` 新增 |
| quality_daily_snapshots 模型 + 快照生成任务 | `app/workers/quality_snapshots.py` |

#### 第 7-9 天：前端质检工作台（D1-D5）

| 任务 | 产出 |
|------|------|
| 质检配置面板（标准选择 + 规则开关 + 参数配置） | `app/frontend/index.html` |
| 评分仪表盘（雷达图 + 环形图 + 评分卡片） | `app/frontend/index.html` |
| 违规明细表（可排序/筛选/分页） | `app/frontend/index.html` |
| 违规详情钻取弹窗 | `app/frontend/index.html` |
| 历史趋势图表 | `app/frontend/index.html` |
| 批量质检按钮 + 进度 | `app/frontend/index.html` |

#### 第 9-10 天：数据库管理 + 运维（E4-E5 + F1-F4）

| 任务 | 产出 |
|------|------|
| 数据保留策略实现 + 清理定时任务 | `app/workers/quality_snapshots.py` |
| quality_violations 按月分区 | Alembic 迁移 |
| 旧版数据迁移（issues JSON → 关联表） | `scripts/migrate_quality_reports.py` |
| 覆盖率监控指标 | `app/services/quality/monitor.py` |
| 数据质量标准管理增强（标准版本/变更历史） | `app/api/v1/data_standards.py` |

---

## 6. 技术实现要点

### 规则引擎核心流程

```
load_rules_for_standard(standard_id)
  → 从 quality_rules 表加载已启用的规则
  → 每个规则匹配文件字段（按字段名映射）
  → 执行检查器（NOT_NULL/UNIQUE/VALUE_IN_SET/...）
  → 收集 QualityIssue
  → 写入 quality_rule_results + quality_violations
  → 更新 quality_reports.completeness_score
```

### 评分模型

```
completeness = 1 - (null_fields / total_fields) × 0.4
accuracy    = 1 - (type_mismatches / total_checks) × 0.3
consistency = 1 - (violations / total_checks) × 0.2
uniqueness  = 1 - (duplicates / total_features) × 0.3
timeliness  = min(1, days_since_last_update / 30) × 0.2

score_overall = completeness × 0.25 
              + accuracy × 0.25 
              + consistency × 0.2 
              + uniqueness × 0.15 
              + timeliness × 0.15
```

### 前端技术选型

| 组件 | 库 | 说明 |
|------|-----|------|
| 雷达图/环形图 | Chart.js（CDN 引入） | 评分可视化 |
| 折线图 | Chart.js | 趋势分析 |
| 数据表格 | 原生 JS | 违规列表（排序/筛选/分页） |
| 详情弹窗 | 原生 JS | 违规明细钻取 |
| 文件下载 | 原生 JS | 报告导出 |

---

## 7. 交付物清单

| 类别 | 交付物 | 说明 |
|------|--------|------|
| 代码 | `app/models/quality_rule.py` | 规则 ORM |
| 代码 | `app/models/quality_violation.py` | 违规 ORM |
| 代码 | `app/models/quality_daily_snapshot.py` | 快照 ORM |
| 代码 | `app/services/quality/rule_generator.py` | 规则生成器 |
| 代码 | `app/services/quality/rule_engine.py` | 规则引擎 |
| 代码 | `app/services/quality/scoring.py` | 五维评分 |
| 代码 | `app/services/quality/profile_analyzer.py` | 字段画像 |
| 代码 | `app/services/quality/anomaly_detector.py` | 异常值检测 |
| 代码 | `app/services/quality/null_correlation.py` | Null 关联 |
| 代码 | `app/services/quality/duplicate_detector.py` | 重复检测 |
| 代码 | `app/services/quality/monitor.py` | 监控指标 |
| 代码 | `app/api/v1/quality_rules.py` | 规则管理 API |
| 代码 | `app/workers/quality_snapshots.py` | 快照生成任务 |
| 代码 | 增强 `app/api/v1/quality.py` | 报告对比/趋势/聚合 |
| 代码 | 增强 `app/frontend/index.html` | 质检工作台 |
| 迁移 | 4 个 Alembic 迁移文件 | 新表创建 + 分区 |
| 脚本 | `scripts/migrate_quality_reports.py` | 旧数据迁移 |
| 文档 | `VCS_质检工作台使用说明.md` | 用户文档 |

---

## 8. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 大文件字段画像性能差 | B1/B2 执行超时 | 中 | 采样分析（最多 10000 条记录） |
| 违规明细表数据量膨胀 | E3 磁盘暴涨 | 低 | 按月分区 + 90 天保留策略 |
| 规则配置复杂用户不用 | A4/D1 低使用率 | 中 | 从数据标准自动生成，零配置可用 |
| 前端图表库增加包体积 | D2 加载慢 | 低 | Chart.js CDN 按需加载 |
| 旧数据迁移失败 | E5 数据丢失 | 低 | 先备份后迁移，保留原字段 |
