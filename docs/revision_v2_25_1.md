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

> **2026-11-13 注**：上一段描述的是本版发布时的实现，已被 §11 的 AST 改造**取代**——
> 误报与四类漏拦均已归零；`Auth.id` 经实测判定为**不该拦**（见 §11.2）。此处保留原文
> 作为发布时状态的记录，不回改。

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
| 布局差异 | `docs/README.md` 的索引更新本次**未随该 PR 端口**（该文件在 root 布局未跟踪），已由 §10 的后续提交收敛 |

本文件所在的提交是**版本文档同步**提交，同样经 PR → rebase-merge 进入 `main`，不重写
`f9ea3f8` 及更早的历史。

## 9. 已知遗留

- ~~静态守卫的匹配精度问题（子串匹配误判 `oauth.id`、变量中转绕过、大小写/空格变体）见 §2。~~
  **已收敛**（2026-11-13）：守卫改为 AST 扫描，误报与漏拦归零，见 §11。
- ADR-0006「负面/权衡」已记录：`quality_rules` 出现 NULL 归属行后，按 `api_key_id` 分组的
  统计需要显式处理 NULL 桶；含 `api_key_id` 的唯一约束在 PG 中 NULL 互不相等，超管可以
  创建同名标准。

## 10. 遗留收敛（v2.25.1 后续提交）

本版发布时记录的两条遗留已在本节所述的后续提交中处理，**版本号不变**（纯文档与注释，
无功能变更，不重走 6 个版本位点）。

### 10.1 docs 索引三文件端口到 root

`docs/README.md`、`docs/docker/README.md`、`docs/deploy/README.md` 由 `808ee40`（2026-10-19）
引入，root 的 `origin/main` 从未跟踪过它们。本次把三个文件按**原路径**端口到 root 的
`docs/` 下，两仓库结构自此一致。

同时修掉两个文件的**既有路径 bug**：这两份子索引原本是按「住在 `docker/`、`deploy/` 目录里」
写的，被放进 `docs/` 下后没有改链接层级。

| 文件 | 原写法（错） | 现写法 |
|---|---|---|
| `docs/docker/README.md` | `` `../Dockerfile` ``、`` `../docker-compose*.yml` ``、`` `../.env.prod.example` `` | `` `../../Dockerfile` `` 等（指向仓库根） |
| `docs/deploy/README.md` | `` `../deploy_windows.bat` ``、`` `../deploy_linux.sh` ``、`` `../restart.bat` ``、`` `../restart.sh` `` | `` `../../...` `` |
| `docs/deploy/README.md` | 自指 `README.md`（实解析到 `docs/deploy/README.md`） | `../../deploy/README.md` |

`docker/`、`deploy/` 两个目录内的文件表改用「路径**相对仓库根的 `docker/` / `deploy/` 目录**」
的表头说明，避免在表格里写一串 `../../`。

**关键点**：修好后的相对路径在 monorepo（`空间质检/docs/...`）与 root（`docs/...`）两种布局下
**完全相同**——相对链接只取决于文件之间的关系，与仓库根位置无关。所以同一份文件可以原样
用在两个布局，不需要端口时改写。

`docs/deploy/README.md` 顶部另加一行指向 [`../../deploy/README.md`](../../deploy/README.md)：
root 已有的 24 KB 运维手册（交付/安装/排障/凭证）与这份 3.7 KB 文件索引定位不同，两者并存
且互相指引。

### 10.2 新增索引守卫 `tests/test_docs_index_links.py`

| 用例 | 断言 |
|---|---|
| `test_index_files_exist` | 三个索引文件都存在 |
| `test_docs_index_links_resolve` | 所有 markdown 相对链接目标存在（跳过 `http(s)://`、`mailto:`、`#锚点`） |
| `test_docs_index_backticked_paths_resolve` | 反引号里的 `../` / `../../` 路径目标存在——**本次真正的 bug 就属于这一类，链接型守卫抓不到** |
| `test_docs_index_covers_all_adrs` | `docs/adr/ADR-*.md` 每个文件都在 `docs/README.md` 里出现 |
| `test_docs_index_lists_current_version_revision` | 当前版本（从 `app/main.py` 读）的发版说明被索引 |

反向验证：把 `docs/deploy/README.md` 的 `` `../../restart.sh` `` 临时改回 `` `../restart.sh` ``，
测试立即报 `docs\deploy\README.md -> ../restart.sh`，确认守卫不是空转。

### 10.3 `scripts/migrate_auth_patterns.py` 的完成声明

该脚本是 v2.25.0 的一次性批量改造工具，使命已完成——现在跑 `--dry-run` 输出
`Total: 0 files, 0 changes`。本次三处修正：

1. 文件头加 **「已执行完毕，仅作历史参考，勿再运行」**，并写明它只处理**读路径**、不处理写路径。
2. 删掉 `APP_DIRS` 里早已不存在的 `PROJECT_DIR/空间API服务/app`（死路径）。
3. 把 Pattern 3 的注释（原文：「CREATE 操作保留 `api_key.id`，因为 `auth.id` 委托到它」）
   改为明确的反面指引：写路径必须用 `auth.owner_id`，禁止 `api_key.id` / `auth.id`，
   否则超管虚拟 ID 会触发外键失败（HTTP 500），并指向 ADR-0006 与静态守卫
   `tests/test_super_admin_ownership_p8.py`。

原文那句注释是本版修掉的坑的**反向知识**——照它做会重新引入 500，因此不只是措辞问题。

### 10.4 验收

| 项 | 结果 |
|---|---|
| 全量测试（monorepo 布局） | **1891 passed / 9 skipped**，覆盖率 **99.90%**，EXIT=0 |
| 全量测试（root 布局） | **1891 passed / 9 skipped**（端口后复跑） |
| 新增守卫反向验证 | 把 `` `../../restart.sh` `` 改回 `` `../restart.sh` `` → 测试报 `docs\deploy\README.md -> ../restart.sh`；恢复后绿 |
| `scripts/migrate_auth_patterns.py` | `py_compile` 通过；`--dry-run` 输出 `Total: 0 files, 0 changes` |
| 版本位点 | **不变**（仍 `2.25.1` / `v2.25.1`）：本轮是纯文档与注释，无功能变更，不重走 6 位点 |


## 11. 文档体系整理 + 静态守卫精度（2026-11-13，版本号不变）

本节是 v2.25.1 之后的第三批收敛（§10 为第二批）：**纯文档与测试实现，无运行时行为变更**，
版本位点不动（仍 `2.25.1` / `v2.25.1`，不重走 6 个位点，不新建 `revision_v2_25_2.md`）。

触发点很具体：`BASELINE.md` §10 的目录树停在 v2.22.3 而标注是「2026-09-29 复核」，
`docs/README.md` 把 7 个 v2.19.x 压成一行 `v2.19_6 ~ v2.19_0`，于是「索引/基准线与磁盘
不符」这件事没有任何机制能发现——顺带把 §2 自陈的静态守卫精度遗留一并收掉。

### 11.1 收敛的 8 处文档不一致

| # | 位置 | 收敛前的事实 | 处理 |
|---|---|---|---|
| 1 | `BASELINE.md:21` | 仍有 `> **当前版本**: v2.24.0 · …` 整段，与顶部（L4）和 §15（`2.25.1`）矛盾 | 改为「历史累计串（截至 v2.24.0，**已停用**）」，指向 §15 |
| 2 | `BASELINE.md` §2 目录树 | `docs/` 注释写「revision_history + adr/ADR-0001 + review_v2.16.0 + archive/」，漏 5 类资产 | 补齐 `README` 索引 / `adr/ADR-0001~0006` / `docker/` / `deploy/` / `superpowers/` |
| 3 | `BASELINE.md` §10 | 主目录树停 v2.22.3，漏 `revision_v2_23_0`/`24_0`/`25_0`/`25_1`、`README.md`、`adr/`、`docker/`、`deploy/`、`superpowers/`；标「2026-09-29 复核」 | 整棵树重写为「2026-11-13 复核」版，archive 标注「顶层 30 + legacy_root 8 = 38」 |
| 4 | `BASELINE.md` §15 发版清单第 4 项 | 要求改 `deploy_windows.bat` 标题版本号，而 §15「当前版本」已声明它退役、无版本标题 | 标 `[x] ~~…~~` 且写明「不是版本位点」 |
| 5 | `CLAUDE.md` §4 | 同样把 `deploy_windows.bat` 算作版本位点 | 明确「6 个版本位点」并点名该文件退役，引用 `test_release_positions_are_consistent` |
| 6 | `CLAUDE.md` §2 / §3 | 同步清单缺 `docs/README.md`、`docs/adr/`、`revision_history.md`、§10、§13；`technical_spec_*`/`test_manual_*` 实际全在 `archive/` | 重写两张表，技术方案/测试手册改为「直接写 `docs/archive/`」 |
| 7 | `docs/README.md` | 17 个 revision 压缩成一行；漏 `qc_redesign_v5.html`；无 archive 计数；无「两布局同一份」说明 | 全文重写：17 个 revision 逐条 + ADR 6 条 + docker/deploy 索引 + `superpowers/` plans 5 & specs 4 + archive 38 |
| 8 | `docs/revision_history.md` | v2.25.1 段仍写「**遗留**：`scripts/migrate_auth_patterns.py:81-82` 仍教写 `auth.id`」——§10.3 已修 | 追加两条收敛条目（**不改旧段**，遵守「只追加」规则），头部「最后更新」改 2026-11-13 |

顺带校正 3 处**计数**（都能用 `ls` 复核）：

- 主目录 revision 文档实为 **17 个**：`v2.19.0`–`v2.19.6`（7）+ `v2.20.0`/`v2.21.0`/`v2.22.0`/`v2.22.1`/`v2.22.2`/`v2.22.3`/`v2.23.0`/`v2.24.0`/`v2.25.0`/`v2.25.1`（10）。
- §10 里两条旧注记的计数与磁盘不符：「v2.22.3 续」称 **16 个**、「v2.22.2 复核修正」称 **15 个**；
  按当时应有文件复核应为 **13 个**。已在注记里写明更正，不回改原句。
- `archive/` **顶层 30 个** + `legacy_root/` 8 个 = **38**；其中 `proposal_*.md` 实为 **1 个**（原记 2 个）。

### 11.2 静态守卫：子串匹配 → AST

`tests/test_super_admin_ownership_p8.py::TestNoVirtualKeyIdWrites` 的扫描主体重写：

| 组件 | 作用 |
|---|---|
| `_dotted_name(node)` | 把属性链还原成 `a.b.c`；基不是 `ast.Name`（调用/下标/字面量）就返回 `''` |
| `_violations(tree)` | 找 `ast.Attribute(attr=="id")` 且基名 ∈ `{auth, api_key}`；另找 `getattr(<base>, "id")` |
| `_render(rel, lineno, raw)` | 用原始源码行渲染违规行（AST 的 `lineno` 在跨行链上指向**基名那一行**，如实反映） |
| `_scan` | `ast.parse` 成功走 AST；`SyntaxError` 时退回 `_mask_strings_and_comments` + `_FORBIDDEN_SUBSTRINGS` 子串兜底（宁可误报，不可漏报） |

一次归零的四类漏拦 + 一类误报：

| 形态 | 改造前 | 改造后 |
|---|---|---|
| `oauth.id` / `my_auth.id` | **误报**（子串包含） | 不报（`Name` 相等比较） |
| `auth . id`（空格） | 漏 | 报 |
| 跨行 `(\n auth\n .id\n)` | 漏 | 报（`Attribute.lineno` 指向 `auth` 行） |
| `self.auth.id` 嵌套链 | 漏 | 报（基名取属性链最后一段） |
| `getattr(auth, "id")` | 漏 | 报（单独识别 `ast.Call`） |
| `x = auth.id` 变量中转 | 漏（原描述） | 报——**读点本身即违规**，与它后面流向哪里无关 |

**基名比较大小写敏感**，这是本节的第二个裁决，也是首版实现的 bug：首版为「顺手覆盖
`Auth.id`」写了 `.lower()` 匹配，结果在真实 `app/` 上误报 **4 处 `ApiKey.id`**：

```
app/api/v1/admin.py:105  result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
app/api/v1/admin.py:166  result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
app/api/v1/admin.py:236  select(ApiKey.group_id, func.count(ApiKey.id).label("cnt"))
app/core/deps.py:59      select(ApiKey).where(ApiKey.id == key_id, ApiKey.is_active == True)
```

这 4 处都是 **ORM 列表达式**（`select(...).where(...)`、`func.count(...)`），合法且必要。
因此禁令牌只保留 `{auth, api_key}` 且比较**不转小写**；`apikey` 变体一并删掉。

**`Auth.id` 判为「不该拦」而非「补上」**：`app/` 下不存在 `Auth` 标识符（`AuthContext` 是另一个名字，
SQL 里的 `:auth` 是字符串参数），而 `Auth` 作为**类**引用时不可能等于「当前请求的虚拟 ID」——
它压根不是实例属性。故本条**推翻 §2 原文**「`Auth.id` 大小写变体……目前都不拦」的措辞：
不是遗留，是误判。

新增 6 条单元用例（都直接驱动 `_scan`，红绿双向）：

| 用例 | 断言 |
|---|---|
| `test_oauth_id_is_not_an_offender` | `oauth.id` / `my_auth.id` / `token.api_key_id` → 0 条 |
| `test_orm_column_and_class_refs_are_not_offenders` | `select(ApiKey).where(ApiKey.id == …)` / `Auth.id` → 0 条（钉住 4 处真实误报） |
| `test_space_variant_is_caught` | `auth . id` / `api_key .id` → 2 条 |
| `test_cross_line_and_nested_chains_are_caught` | 跨行 `(\n auth\n .id\n)` + `self.auth.id` → 2 条 |
| `test_getattr_variant_is_caught` | `getattr(auth, "id")` 报、`getattr(oauth, "id")` 不报 |
| `test_unparseable_file_falls_back_to_substring_scan` | 语法错误文件仍报 `X = auth.id`，且计入扫描数 |

### 11.3 新增 2 条 docs 结构守卫

| 用例 | 断言 |
|---|---|
| `test_docs_index_lists_every_top_level_file` | `docs/` 顶层每个 `.md` / `.html`（除 `README.md`）都必须出现在 `docs/README.md` 文本里；豁免集合 `_INDEX_EXEMPT` 当前为空 |
| `test_baseline_docs_section_tracks_current_structure` | 解析 §10 的**目录树代码块**，条目集合必须覆盖 `README.md`、`adr/`、`docker/README.md`、`deploy/README.md`、`superpowers/`、`archive/` 与当前版本 revision（版本号从 `app/main.py` 派生，发版不用改测试） |

**这里有一次自我纠错，值得留档**：`test_baseline_docs_section_tracks_current_structure` 首版
写的是「`adr/` 等字符串出现在 §10 全文即可」。反向验证时把树里的 `├── adr/` 整行删掉，
测试**仍然通过**——因为下面「2026-11-13 复核」的注记段落里也写了 `adr/`。守卫等于同义反复，
比没有守卫更危险（它会让人以为已经守住）。改成 `_docs_tree_entries()` 解析代码块、只认
去掉树形符号与行尾注释后的条目名，同一变异立即变红。

### 11.4 反向验证

文档守卫（每条都是「变异 → 红 → 还原 → 绿」，还原后逐字节比对工作区与备份）：

| 变异 | 期望报错 | 实际 |
|---|---|---|
| `docs/README.md` 删掉 `review_v2.16.0.md` 条目 | `未索引这些顶层文件: ['review_v2.16.0.md']` | 红 ✅ |
| `BASELINE.md` §10 树里删 `├── adr/`（叙述段落仍含 `adr/`） | `目录树未覆盖: ['ADR 目录']` | 红 ✅（首版此处为绿，见 §11.3） |
| §10 树里删 `├── README.md` | `目录树未覆盖: ['docs 总索引']` | 红 ✅ |
| §10 树里删 `├── revision_v2_25_1.md` | `目录树未覆盖: ['当前版本 revision']` | 红 ✅ |
| `docs/README.md` 的 `../deploy/README.md` 改成 `../deploy/NOPE.md` | `docs\README.md -> ../deploy/NOPE.md` | 红 ✅ |

静态守卫端到端（证明它对真实 `app/` 不是空转）：在 `app/` 下临时放 `_guard_probe_tmp.py`，
内容为跨行 `(\n auth\n .id\n)`：

```
注入后 → FAILED，报 app/_guard_probe_tmp.py:4: auth
删除后 → 1 passed，残留文件 False
```

### 11.5 验收

| 项 | 结果 |
|---|---|
| 定向测试 | `tests/test_docs_index_links.py` + `tests/test_super_admin_ownership_p8.py` → **26 passed**（7 + 19） |
| 全量（monorepo 布局） | **1899 passed / 9 skipped**，覆盖率 **99.90%**，EXIT=0（发布时 1886，§10 后 1891，本轮 +8：6 条 AST 用例 + 2 条 docs 结构守卫） |
| 反向验证 | 5 条文档变异全部变红并逐字节还原；真实 `app/` 注入 1 条违规变红、删除后绿 |
| 版本位点 | **不变**（仍 `2.25.1` / `v2.25.1`）：无运行时行为变更 |
| 明确未做 | 未把主目录旧 revision 移入 `archive/`——改动归档策略属破坏性整理，留给单独立项决定 |
