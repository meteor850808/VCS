# ADR-0002 — 服务层事务边界保持在 API 层（Unit-of-Work），服务内不自行 commit

- **状态**: 已接受（2026-09-28）
- **决策者**: VCS 空间质检维护者
- **上下文**: 架构评审发现 `app/services/`（28 文件 + quality/ 6 文件）存在 **0 处 `db.commit()`**，而 `app/api/v1/`（13 文件）有 **58 处 commit / 39 处 db.add**。services 层的 5 处 `db.add`（`feature_store.py` ×2、`structure_analyzer.py` ×3）均无同层 commit。初看像"事务边界缺失"或"静默丢失写入"，但属架构性观察，需评估而非盲改。

## 关键事实（测量，2026-09-28）

- 层间操作分布：

| 层 | 文件 | commit() | db.add() | db.query/execute |
|----|------|----------|----------|------------------|
| app/services/ | 28 | **0** | 5 | 47 |
| app/services/quality/ | 6 | **0** | 0 | 2 |
| app/api/v1/ | 13 | **58** | 39 | 98 |

- services 层 5 处 `db.add` 归属：`feature_store.py` L168（`db.add(sf)`）、L279（`db.add(StoredFileLayer(...))`）；`structure_analyzer.py` L67/L82/L104（layer_group / std_table / std_field）。
- **风险扫描**（按端点函数切块，含 `db.add` 且不含 `db.commit` 的端点数）：**0**。所有 add 的 API 端点均有同块 commit，无"add 后异常缺 commit"的静默丢弃分支。

## 决策

**保持事务边界在 API/适配器层（Unit-of-Work 模式），服务层不引入 `db.commit()`。同期项目文档补充事务边界说明。**

理由：
1. **组合事务需求**：导入流程由多服务协作（`structure_analyzer` 建标准结构 + `feature_store` 存文件/层），需在 API 端点内一次性原子提交。若 service 自行 commit，会破坏组合语义并制造部分提交。
2. **注入 session 契约**：services 不持有独立 session/事务工厂（无 `db.begin`），一律使用被调用方注入的 `db`（FastAPI `Depends(get_db)` 会话）。在注入会话内 commit 属于越权且难以追踪。
3. **已实证无风险**：0 个"add 无 commit"端点，表明 commit 纪律在 API 层执行良好，无需把事务下沉。
4. 事务归属 API 层是 FastAPI + SQLAlchemy 生态的标准惯例（请求作用域 session + 端点提交）。

## 替代方案与权衡

| 方案 | 说明 | 结论 |
|------|------|------|
| A. 在 service 内补 commit | 解决不了组合事务，制造部分提交风险；与注入 session 契约冲突 | ❌ 否决 |
| B. 引入 service 级独立 session/事务工厂 | 大重构，破坏现有注入模式，收益为零（已实证无 bug） | ❌ 否决 |
| C. **保持现状 + 文档化边界** | 现状自洽（0 风险端点）；补齐事务边界说明让未来维护者理解 | ✅ 采纳 |
| D. 把 services 的 db.add/query 上移到 API 层 | 反向移动，会让 API 层臃肿，违背"服务承载业务操作"的分层意图 | ❌ 否决 |

## 后果

- **正面**：未来服务层新增写操作（`db.add`/`db.delete`）时，维护者明确"由调用方 API 端点 commit"的契约；新增端点时按"同块必须有 commit"自检。
- **负面/权衡**：服务层在读上下文中被复用（如批量查询服务）时，事务边界仍在 API 层，服务间无法自行开启子事务（当前无此需求）。
- **监控**：后续若服务层新增写操作，纳入端点级扫描（`db.add` 无 `db.commit` 即告警）作为回归守卫。

## 触发重评估条件

- 出现需要"跨 API 端点的独立事务"场景（如同一请求内多个服务各自原子提交但互不依赖）。
- 服务层新增独立 session 工厂或出现对会话生命周期的显式管理需求。