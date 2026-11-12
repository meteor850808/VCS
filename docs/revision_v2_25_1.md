# v2.25.1 — 归属列语义落地与迁移链对齐模型

> 日期：2026-11-12
> 版本：v2.25.0 → v2.25.1
> 范围：ADR-0006 归属列统一 `owner_id`（超管归属落 NULL）、`app/` 静态守卫扩面、迁移
> `0015`–`0018` 把迁移链、生产库与 ORM 模型三方对齐。**无新增业务功能**。
> 相关 ADR：`docs/adr/ADR-0004-dual-line-repo-topology.md`（双线仓库拓扑）、
> `docs/adr/ADR-0005-cross-layout-porting-flow.md`（跨布局端口流程）、
> `docs/adr/ADR-0006-ownership-columns-owner-id.md`（归属列只写 `owner_id`）。

本版覆盖 `v2.25.0` 发版提交之后 `main` 上的全部内容（root 布局 18 个提交），并补上两处遗漏：
① 上一轮未同步的版本文档（`CONTEXT.md` / `BASELINE.md` §15 / `app/main.py` / 部署脚本 /
本文档）；② monorepo → root 端口时被跳过的一个纯文档提交 `95aa192`（只改
`docs/superpowers/plans/2026-11-10-quality-scoring-optimization.md` 的任务级状态注记，
因此 monorepo 侧该区间为 19 个提交、root 侧此前只有 18 个）。

## 1. 归属列：写入一律 `AuthContext.owner_id`

**问题**：超管用 `ADMIN_INIT_KEY` 鉴权时，`AuthContext` 内部构造一个**虚拟** `ApiKey`
（`id="00000000-0000-0000-0000-000000000001"`，`app/core/auth_context.py:147`）以复用普通
Key 的代码路径。该 ID 在 `api_keys` 表里没有对应行，写进任何带外键的 `api_key_id` 列都会
`ForeignKeyViolationError` → HTTP 500。CI 冒烟曾在 `POST /api/v1/convert` 上稳定复现
（`conversion_jobs_api_key_id_fkey`）。

**修复**：归属列写入改用 `AuthContext.owner_id`（超管返回 `None`，普通 Key 返回真实 ID），
读取改用 `scope_clause()` / `owns()`。具体落点：

- `app/api/v1/convert.py`、`batch.py`、`data.py`、`quality.py`、`quality_rules.py`、
  `source_data.py`、`spatial.py`、`data_standards.py`、`data_standard_v2/*`：共 34 处归属
  写入改为 `owner_id`。
- `quality.py` 的要素计数改用 `api_key.scope_clause(model)`：超管不过滤（`true()`），普通
  Key 按可达 ID 过滤，语义与列表查询一致。
- `load_standard_fields` 的归属校验改用 `owns()`，超管不再看不到自己的标准（**注意**：超管
  数据的归属是 NULL，任何按 `api_key_id == id` 的过滤都会漏掉它）。
- `app/services/quality/rule_generator.py` 的 `api_key_id` 形参改为 `str | None`，与调用方
  传入的 `owner_id` 对齐。
- 迁移 `0015_quality_rule_owner_nullable` 把 `quality_rules.api_key_id` 放宽为
  `nullable=True`——「超管规则」是真实存在的状态，旧 schema 无法表达。

**运行时实测**（ADR-0006 关键事实 5）：修复前 `POST /quality-rules`、
`POST /quality-rules/generate/{id}`、`POST /source-tables`、`POST /source-dictionaries`、
`POST /data-standards-v2`、`POST /data-standards-v2/{id}/publish` 六个端点全部 500；修复后
全部 201，且落库归属为 NULL。其余写入路径（上传、质检、几何修复等）由静态守卫覆盖，
**未逐一做运行时实测**。

## 2. 静态守卫：`app/` 全目录，禁 `api_key.id` 与 `auth.id`

`tests/test_super_admin_ownership_p8.py` 是本版新增的回归守卫，分两部分：

1. **写端点断言**：6 个超管写端点落库 `api_key_id IS NULL`（`TestQualityRuleRows` /
   `TestSourceDataRows` / `TestStandardRows`）。
2. **静态扫描**（`TestNoVirtualKeyIdWrites`）：
   - 扫描范围是 `app/` 下全部 `.py`（当前约 126 个文件），不再只扫 `app/api/v1/`；
   - 禁令牌为 `{api_key.id, auth.id}`——`AuthContext.id` 直接委托 `api_key.id`，只禁前者会
     漏掉等价违规；
   - 唯一豁免 `app/core/auth_context.py`（该文件职责就是产出 `id` / `owner_id`），由
     `test_exemption_surface_is_single_file` 钉住：新增第二个豁免必须同时改断言；
   - 扫描先用标准库 `tokenize` 屏蔽字符串与注释再做包含匹配，文档/注释里提到 `auth.id`
     不再误报；f-string 内的表达式（`f"{auth.id}"`）仍会被抓到；
   - `_check()` 断言实际扫描文件数 ≥ 100，避免路径算错时守卫静默空转。

**已知限制**（守卫是兜底，不做 AST 分析）：匹配是子串式，`oauth.id` 会被误判为违规；
`x = auth.id` 再 `api_key_id=x` 的变量中转仍可绕过；`Auth.id` 大小写变体、`auth .id`
空格变体、跨行属性链、`getattr(auth, "id")` 目前都不拦。

## 3. 迁移链追平：`0015` → `0018`

四条迁移的共同前提：2026-11-12 复核发现「只用迁移链从空库升级到 head」的库
（`vcs_mig_probe`）与生产库 `vcs_db` 都与 `Base.metadata` 有漂移，且**两库漂移集合不同**
（链上缺列/索引/类型，生产库缺注释/可空性/约束名）。因此每条迁移都遵循同一策略：
**先探测现状再动手**，不假设库长什么样。

| 迁移 | 作用 | 幂等性 |
|------|------|--------|
| `0015_quality_rule_owner_nullable` | `quality_rules.api_key_id` 放宽为可空 | `ALTER ... DROP NOT NULL` 天然幂等 |
| `0016_schema_sync_missing_cols_types` | 补 6 个缺失列（`stored_files.layer_name`、`quality_reports.score_overall`、`standard_versions.{version_number,snapshot_data,diff_data,is_rollback}`）；`point/line/polygon_features.properties` 与 `checked_layers` 对齐 `JSONB`；`stored_files.detected_crs` 放宽到 `VARCHAR(2048)` | 加列用 `IF NOT EXISTS`，类型变更先查 `information_schema` |
| `0017_ownership_fk_alignment` | 补 17 个外键（10 个 `api_key_id → api_keys.id`、7 个父子引用）；放宽 3 个归属列（`stored_files` / `source_tables` / `source_dictionaries` 的 `api_key_id`）；把孤立归属归一为 NULL | 判重看 `(conrelid, conkey) → (confrelid, confkey)` 而非约束名，链上已建好的库与自定义命名的库都安全跳过 |
| `0018_model_schema_alignment` | 62 列注释、8 列 `json → jsonb`、`quality_reports.score_overall` 收窄为 `numeric(5,4)`、37 列收紧为 NOT NULL、16 删 + 21 建索引、`key_groups.name` 收敛为唯一索引、`uq_qds_api_date` 约束改名、删 3 张要素父表的 `stored_file_id` 外键 | 全部按 `to_regclass` / 约束存在性 / 列现状探测 |

### 3.1 `0016` 的运维提示

`properties: JSON → JSONB` 会重写分区表数据。大表请在维护窗口执行。降级会把
`detected_crs` 收窄回 `VARCHAR(64)`，超长值将被截断。

### 3.2 `0017` 的降级是「有条件拒绝」

`downgrade` 按实际约束名删外键；若三个归属列中仍有 NULL（超管归属），**拒绝**恢复
NOT NULL 并提示先补归属——历史上的虚拟 ID 无法还原，迁移不伪造数据。

### 3.3 `0018` 的两道预检

- **`score_overall` 收窄前**先检查存量值是否越界（`numeric(5,4)` 只能存 ±9.9999），越界
  直接中止而不是静默截断；
- **37 列收紧 NOT NULL 前**逐列 `count(*) WHERE IS NULL`，有 NULL 就汇总报错中止——
  不猜默认值，不删数据。

`0018` 的 `downgrade` 是**尽力而为且不可完全还原**的：注释的原始值（多数为空）未被记录，
`jsonb → json` 与 `numeric → double precision` 会丢精度/顺序，被删的 `idx_*` 只能按列
定义重建。

### 3.4 要素父表外键为什么删

迁移链 `0001` 建三张要素表时带了 `stored_file_id → stored_files` 的 `ON DELETE CASCADE`；
模型刻意不建（子任务先写要素、父记录后提交，外键会违反这个时序）。删文件走
`feature_store.drop_partition` 显式清理分区。`0018` 删掉这 3 个外键，让库跟随模型。

## 4. 模型侧同步

- **8 列 `JSON → JSONB`**：`quality_reports.{attribute_summary,topology_summary,topology_config}`、
  `quality_rule_results.detail`、`quality_rules.params`、`source_fields.allowed_values`、
  `standard_versions.{snapshot_data,diff_data}`。
- **`asdecimal=False`**：`quality_report.score_overall` 与 `quality_daily_snapshots` 的
  `completeness` / `uniqueness` / `consistency` / `score_overall` 四列。这些列的注解是
  `float | None`，读回必须是 `float`——否则 `Decimal` 与 `float` 相乘会抛 `TypeError`。
  `asdecimal` 不参与 DDL 比对，改前改后 `alembic check` 均绿。
- **索引声明**：`stored_file_id`（三张要素父表）与 `standard_versions.standard_id` 补
  `index=True`；`QualityDailySnapshot.__table_args__` 显式声明
  `Index("idx_qds_date", "snapshot_date")`（历史库已有同名索引，不声明会被 autogenerate
  当成多余索引删掉）；去掉 `quality_daily_snapshots.api_key_id` 的 `index=True`——唯一约束
  `(api_key_id, snapshot_date)` 的前缀已覆盖该访问路径。
- **`alembic/env.py`**：新增 `_EXCLUDE_TABLES`（含 `GeoAlchemy2` 的 `spatial_ref_sys`、
  遗留表 `spatial_features`）与 `_PARTITION_PREFIXES`，让 `alembic check` 不再把运行时
  分区子表与已弃用表当成漂移。

### 4.1 `score_overall` 口径裁决

`score_overall` 的**真口径是 0–1**（与五个 `score_*` 维度分一致，由要素级派生比率加权
得到）。因此本版**不改列类型语义**，只保留 `Numeric(5,4)` 并把越界预检加上；出错的是测试
里的 mock 数据：`tests/test_data_crud_quality.py` 原先用 `95/90/85/80/70` 与
`overall=85.75`（按 0–100 构造），现改为 `0.95/0.90/0.85/0.80/0.70` 与
`overall=0.8575`，断言同步。

## 5. 验收

| 项 | 结果 | 口径 |
|---|---|---|
| 全量测试（root 布局） | **1886 passed / 9 skipped**，覆盖率 **99.90%**，EXIT=0 | `pytest tests/ -q --cov=app`，与 CI 同款命令 |
| 全量测试（monorepo 布局） | **1886 passed / 9 skipped** | cwd = `空间质检/` |
| `alembic check` · 生产库 `vcs_db` | `0018 (head)` + `No new upgrade operations detected.` | `compare_type=True`，不比较 `server_default` |
| `alembic check` · 链库 `vcs_mig_probe` | 同上 | 侧重建库只用迁移链 |
| 空库全链 | `0001 → 0018` EXIT=0；`check` 绿；`downgrade -1 → upgrade head` 后 `check` 仍绿 | 新建 `vcs_chain_verify`，先 `CREATE EXTENSION postgis` |
| 生产库数据 | 升级前后行数**零差异** | 逐表 `count(*)` 快照比对 |
| 生产写路径冒烟 | `SMOKE_OK checks=3 residue=0` | 事务内写入后回滚，无残留 |
| 迁移静态守卫 | `tests/test_migration_0018.py` 11 条全绿 | 迁移文件不能被测试 import（项目根 `alembic/` 遮蔽已安装包），用 `ast.literal_eval` 读常量 |

**空库全链必须先建 PostGIS 扩展**：迁移链自身不建扩展，`0001` 建 `geometry` 列时会报
`UndefinedObjectError: 类型 "geometry" 不存在`。

**生产库备份**（升级前）：`vcs_db_20261112_093108.dump`，1,894,051 B，SHA-256
`4BB29417B9B4C78CE052EDC37655F59948ADD72D9CFC81500A198CEA750F5480`；同目录另存
`.schema.sql` 与升级前后状态快照。

### 5.1 发版位点守卫（新增）

`tests/test_scoring_contract_v3.py` 原有 `test_context_version_bumped` 只断言 `CONTEXT.md`
里含字面量 `2.25.0`，因此 v2.25.0 漏同步 `BASELINE.md` §15 / `app/main.py` / 两个部署脚本时
测试仍全绿。本版改为：

- `test_context_version_bumped`：从 `app/main.py` 的 `version="X.Y.Z"` 读当前版本，再断言
  `CONTEXT.md` 含同一版本——不再写死字面量，发版时不用改测试；
- `test_release_positions_are_consistent`（新增）：断言 `BASELINE.md` §15 的
  `**app/main.py**: X.Y.Z` / `**CONTEXT.md**: vX.Y.Z`、`deploy_linux.sh` 的
  `VCS vX.Y.Z One-Click Deploy (Linux)`、`restart.bat` 的 `title VCS vX.Y.Z Restart`
  均与 `app/main.py` 同版本，且 `docs/revision_vX_Y_Z.md` 存在。

后果：漏同步任一位点时**测试直接失败**，不再依赖人工核对。


## 6. 部署与回滚

1. **必须先 `alembic upgrade head`**：本版把 `0015`–`0018` 全部纳入部署清单。未执行到
   `0015` 时，超管建规则仍会因 `quality_rules.api_key_id` 的 NOT NULL 报 400/500（不是外键
   错）。
2. **执行顺序不可乱**：`0017` 的孤立归属归一是 `0018` 收紧 NOT NULL 的前置条件之一；
   跳过 `0017` 直接把 37 列收紧 NOT NULL 会因存量 NULL 中止（迁移会中止，不会静默改数据）。
3. **`0018` 可重复执行**：注释、类型、索引、约束四类操作都先探测现状，重复跑是 no-op。
4. **回滚**：`0017` 在存在 NULL 归属时拒绝降级；`0018` 的降级会丢注释、丢 `jsonb` 键序、
   丢 `numeric` 精度，且不会恢复被删的 `idx_*` 原名。生产回滚应以备份恢复为主，
   `downgrade` 只作最后手段。
5. **脱管库**（`alembic_version` 被人工 stamp 成链上不存在的修订号）：先把版本号收敛到
   链上真实修订再 `upgrade`，不要直接 `upgrade` —— `0017` 的 docstring 记录了这次生产库
   的实际情况（曾被 stamp 成不存在的 `0014_schema_drift_repair`，schema 由链外 DDL 建成）。

## 7. 同批落地的其他修复

| 提交主题 | 说明 |
|---|---|
| 读路径带 `evaluation_errors` | 未评价报告不再被伪造成满分 |
| SQLAlchemy 2.1 兼容 | 删掉依赖 `Select.select` 的死代码 |
| 同步库 URL 显式 `psycopg2` | 消除 SQLAlchemy 2.1 默认驱动漂移 |
| 测试库 URL 随环境派生 | 凭据不再写死 `postgres:postgres` |
| CI 冒烟失败导出容器日志 | 脚本失败步骤自带输出，便于定位 |
| 导入冲突文案 | 不再依赖 PG 报错语言，补英文/结构化两条提取路径 |
| console-script 导入根 | 修两处环境依赖用例的跳过门 |

## 8. 发布链

| 环节 | 结果 |
|---|---|
| monorepo 内容提交 | `8e8862e`（分支 `feat/quality-scoring-v3`） |
| 端口到 root 布局 | `git format-patch --no-numbered --no-signature` → `git am -p2 --3way` → `edac977` |
| PR | #7 `feat/schema-alignment-0018`，CI 三检全绿（test / smoke / prod-config） |
| 合并方式 | **rebase-merge**，未 squash、未改写历史；`main = f9ea3f8`（父 `10bd0b6`，单提交保留） |
| blob 校验 | 9 个改动文件逐一比对 `8e8862e:空间质检/<path>` 与 `f9ea3f8:<path>`，0 处不一致 |
| 补端口提交 | `95aa192`（计划状态注记，docs-only）在本次一并 `am -p2` 进 root，消除两仓库该文件的偏差 |
| 布局差异 | `docs/README.md` 的索引更新**未端口**：该文件在 root 布局未跟踪（见 §9） |

本文件所在的提交是**版本文档同步**提交，同样经 PR → rebase-merge 进入 `main`，不重写
`f9ea3f8` 及更早的历史。

## 9. 已知遗留

- `docs/README.md`（docs 总索引）**只在 monorepo 布局跟踪**：root 布局从未端口该文件，也
  从未端口它引用的 `docs/docker/README.md`、`docs/deploy/README.md`（`ls-tree` 可核）。
  本次端口因此跳过 `docs/README.md` 的索引更新，**root 仓库的 docs 索引仍缺
  `revision_v2_25_0` / `revision_v2_25_1` 与 ADR-0004–0006 三条**。要收敛需单独端口这三个
  文件，并改其中的相对链接——root 布局下 `docker/`、`deploy/` 在仓库根，不在 `docs/` 下。
- `scripts/migrate_auth_patterns.py:81-82` 仍教用户写 `auth.id`；该脚本不在 `app/` 下，
  静态守卫覆盖不到。**未修**。
- 静态守卫的匹配精度问题（子串匹配误判 `oauth.id`、变量中转绕过、大小写/空格变体）见 §2。
- ADR-0006「负面/权衡」已记录：`quality_rules` 出现 NULL 归属行后，按 `api_key_id` 分组的
  统计需要显式处理 NULL 桶；含 `api_key_id` 的唯一约束在 PG 中 NULL 互不相等，超管可以
  创建同名标准。
