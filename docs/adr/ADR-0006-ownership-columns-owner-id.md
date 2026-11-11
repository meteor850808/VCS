# ADR-0006 — 归属列只写 `AuthContext.owner_id`；超管归属落 NULL，质检规则列放宽可空

- **状态**: 已接受（2026-11-11）
- **决策者**: VCS 空间质检维护者
- **上下文**: 超级管理员用 `ADMIN_INIT_KEY` 鉴权时，`AuthContext` 内部构造一个**虚拟**
  `ApiKey`（`id="00000000-0000-0000-0000-000000000001"`）来复用普通 Key 的代码路径。
  虚拟 ID 在 `api_keys` 表里没有对应行，把它写进任何带外键的 `api_key_id` 列都会
  `ForeignKeyViolationError` → HTTP 500。CI 冒烟曾在 `POST /api/v1/convert` 上稳定复现
  （`conversion_jobs_api_key_id_fkey`）。只修一个端点不够：`app/api/v1` 下还有 34 处
  同形态写入，是同一颗地雷。需要一条能长期自洽的归属语义。

## 关键事实（取证，2026-11-11）

1. **虚拟 ID 是固定常量**：`app/core/auth_context.py:147` 写死
   `00000000-0000-0000-0000-000000000001`，`is_super_admin=True`。
2. **`owner_id` 早已存在**：`AuthContext.owner_id`（同文件 :70）语义是
   「可安全写入 `api_key_id` 外键列的值」——超管返回 `None`，普通 Key 返回真实 ID。
   只是当时只在少数上传路径被采用，其余端点仍写 `api_key.id`。
3. **除一列外都可空**：`stored_files`、`data_standards`、`standard_tables`、
   `standard_dictionaries`、`standard_versions`、`spatial_*`、`batch_jobs`、
   `conversion_jobs`、`quality_reports`、`transform_results` 的 `api_key_id` 均
   `nullable=True` 且 `ondelete="SET NULL"`；唯一例外是 `quality_rules.api_key_id`
   （`nullable=False`，无 ondelete）。
4. **两个 NOT NULL 列中只有一个有写路径**：`quality_daily_snapshots.api_key_id`
   同为 NOT NULL，但全仓无任何插入代码（仅 DDL 脚本），当前不构成超管风险。
5. **同一颗地雷在 6 个端点实测可复现**：新建测试在修复前对
   `POST /quality-rules`、`POST /quality-rules/generate/{id}`、`POST /source-tables`、
   `POST /source-dictionaries`、`POST /data-standards-v2`、
   `POST /data-standards-v2/{id}/publish` 全部拿到 500；修复后全部 201 且落库归属为 NULL。
6. **读侧也有两处按 `api_key.id` 过滤**：`quality.py` 的要素计数与
   `load_standard_fields` 的归属校验。前者若保留 `model.api_key_id == api_key.id`，
   超管查自己（NULL 归属）的文件会得到 0 个要素；后者会让超管看不到自己的标准。

## 决策

**归属列写入一律用 `AuthContext.owner_id`；读取一律用 `scope_clause()` / `owns()`。
`api_key.id` 不再出现在 `app/api/v1/` 的归属读写中，并由静态扫描测试守住。**

> 注（2026-11-11）：静态守卫的**范围与禁令牌已扩展**——扫描 `app/` 全目录、同时禁
> `auth.id`、唯一豁免 `app/core/auth_context.py`。原文保留不改，详见文末「补充」。

配套：
1. `quality_rules.api_key_id` 放宽为 `nullable=True`（模型 + 迁移
   `0015_quality_rule_owner_nullable`），使「超管规则」这个真实存在的状态可被表达；
   降级必须先 `DELETE FROM quality_rules WHERE api_key_id IS NULL` 才能恢复 NOT NULL
   （旧 schema 无法表达这些行）。
2. `rule_generator` 的 `api_key_id` 形参改为 `str | None`，与调用方传入的 `owner_id` 对齐。
3. 新增 `tests/test_super_admin_ownership_p8.py`：6 个写端点断言落 NULL +
   对 `app/api/v1/**/*.py` 的静态扫描（出现 `api_key.id` 即失败），
   防止新代码把虚拟 ID 写回来。
   （2026-11-11 注：范围已扩至 `app/**/*.py`，禁令牌增加了 `auth.id`，见文末补充。）
4. `quality.py` 要素计数改 `api_key.scope_clause(model)`：超管 `true()`
   不过滤、普通 Key 按可达 ID 过滤，语义与列表查询一致。

## 替代方案与权衡

| 方案 | 说明 | 结论 |
|------|------|------|
| A. **统一 `owner_id`（超管 → NULL）+ 规则列放宽可空** | 与既有 nullable/SET NULL 列语义一致；超管数据天然「无主」 | ✅ 采纳 |
| B. 超管写操作直接返回 400（拒绝创建） | 无需迁移；但超管本来就是运营/排障入口，禁写会让「用初始化密钥救火」失效 | ❌ 否决 |
| C. 给超管建一行真实 `api_keys` 记录（系统 Key） | 归属可追溯、无非空问题 | ❌ 否决：需种子数据/迁移，且 `ADMIN_INIT_KEY` 是配置项不是 DB 记录，二者仍会漂移 |
| D. 只在写入前把虚拟 ID 映射成「某一个真实 Key ID」 | 无需迁移 | ❌ 否决：把超管的操作记到别人名下，审计语义错误 |
| E. 各端点原地 try/except 吞掉外键错误 | 掩盖问题，数据静默丢失 | ❌ 否决 |

## 后果

- **正面**：已在运行时实测转换（CI smoke `POST /api/v1/convert`）与 6 个写端点
  （`POST /quality-rules`、`POST /quality-rules/generate/{id}`、`POST /source-tables`、
  `POST /source-dictionaries`、`POST /data-standards-v2`、
  `POST /data-standards-v2/{id}/publish`）不再因虚拟 Key 触发外键错误；其余写入路径
  （上传、质检、几何修复等）由 `app/api/v1` 静态扫描守卫覆盖，**未逐一做运行时实测**；
  归属语义统一为「NULL = 系统/超管操作」，与 `ondelete="SET NULL"` 天然兼容。
- **负面/权衡**：`quality_rules` 出现 NULL 归属行后，按 `api_key_id` 分组的统计需要
  显式处理 NULL 桶；`uq_ds_api_name` / `uq_ds_api_code` 这类含 `api_key_id` 的唯一约束
  在 PG 中 NULL 互不相等，超管可以创建同名标准（普通用户不能）——这是可接受的边角差异，
  但已知并留档。
- **监控**：静态扫描测试是回归守卫；`alembic upgrade head` 未执行到 0015 时，
  超管建规则仍会因 NOT NULL 报错（不是外键错），部署清单必须包含该迁移。

## 触发重评估条件

1. 出现「超管操作必须可审计到具体自然人」的合规要求 → 回到 C 案（系统/操作者 Key）。
2. `quality_rules` 的 NULL 归属导致统计口径混乱（如日报按 Key 汇总漏算超管规则）
   → 需引入显式「系统归属」维度而非 NULL。
3. 新增的 `api_key_id` 外键列默认 NOT NULL 且需要超管写入 → 新列必须同时放宽可空，
   否则本 ADR 的「一律 owner_id」约定会被迫破例。

## 补充（2026-11-11）：静态守卫扩面至 `app/` 并覆盖 `auth.id`

上文「决策」段的表述已被后续实现取代。ADR 是历史记录，故不改写原文，在此补充：

- **扫描范围**：`app/api/v1/` → `app/` 全目录，扫描 `app/` 下全部 `.py`（当前约 126 个）。
  实现见 `tests/test_super_admin_ownership_p8.py::TestNoVirtualKeyIdWrites._scan`。
- **禁令牌**：`{api_key.id, auth.id}`。原因：`AuthContext.id` 直接委托 `api_key.id`
  （`app/core/auth_context.py`），超管下同样返回虚拟 ID `00000000-...-0001`；
  只禁 `api_key.id` 会漏掉写 `auth.id` 的等价违规。
- **唯一豁免**：`app/core/auth_context.py`（该文件职责就是产出 `id` / `owner_id`）。
  由 `test_exemption_surface_is_single_file` 钉住——新增第二个豁免必须同时改断言，
  从而在 review 中显形。
- **精确度**：扫描先用标准库 `tokenize` 屏蔽字符串与注释，再做包含匹配。文档/注释里
  提到 `auth.id` 不再误报；f-string 内的表达式（如 `f"{auth.id}"`）仍会被抓到。
- **非空校验**：`_check()` 断言实际扫描文件数 ≥ 100，避免路径算错时守卫静默空转。
- **已知限制**：匹配仍是子串式（`oauth.id` 会被误判为违规）；变量中转
  （`x = auth.id` 再 `api_key_id=x`）仍可绕过。守卫是兜底，不做 AST 分析。
