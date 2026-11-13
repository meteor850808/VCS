# VCS 空间质检项目 — Claude 工作流规则

## 工作流规则

### 1. 项目启动时读取基准线
每次会话开始时，必须读取 `BASELINE.md` 以了解项目当前状态、技术栈、目录结构和 API 端点。

### 2. 功能修改后同步文档
每次完成功能修改后，必须同步更新以下文档：

| 文档 | 更新时机 | 说明 |
|------|----------|------|
| `BASELINE.md` | 每次变更后 | 更新对应章节（API 端点 / 数据库表 / 服务模块 / §10 docs 体系 / §13 测试计数等），保持当前状态快照 |
| `docs/README.md` | 每次发版 / 新增文档 | docs 总索引：新 revision、新 ADR、新子目录都必须进索引（由 `tests/test_docs_index_links.py` 守卫） |
| `docs/adr/ADR-*.md` | 出现架构取舍时 | 一决策一文件；新增后同步进 `docs/README.md` 与 `BASELINE` §10 |
| `docs/revision_vX_Y_Z.md` | 每次发版 | 新建修订记录，不修改旧文件 |
| `docs/revision_history.md` | 每次发版 | 追加新段落，不修改旧段落 |
| `docs/technical_spec_vX_Y_Z.md` / `docs/test_manual_vX_Y_Z.md` | 重大功能时 | 直接写入 `docs/archive/`（主目录不保留技术方案/测试手册） |

### 3. BASELINE.md 更新清单
功能变更后，检查以下章节是否需要更新：
- [ ] 第 2 节: 目录结构（新增/删除文件）
- [ ] 第 3 节: 数据库表结构（新增/修改表）
- [ ] 第 4 节: API 端点清单（新增/修改/删除端点）
- [ ] 第 5 节: 服务模块清单（新增/修改服务）
- [ ] 第 6 节: 脚本清单（新增脚本）
- [ ] 第 10 节: docs/ 设计文档体系（新增文档/目录）
- [ ] 第 13 节: 测试（用例数与覆盖率）
- [ ] 第 15 节: 版本管理（更新版本号）
- [ ] 顶部: 最后更新日期

### 4. 版本发布清单
发版时同步 **6 个版本位点**（由 `tests/test_scoring_contract_v3.py::test_release_positions_are_consistent` 校验）：
1. `CONTEXT.md` — 版本号
2. `BASELINE.md` — 顶部版本号 + 最后更新日期 + §15「当前版本」
3. `deploy_linux.sh` — 标题版本号
4. `restart.bat` — 标题版本号
5. `app/main.py` — FastAPI `version` 字段
6. `docs/revision_vX_Y_Z.md` — 修订记录（必须新建）

> `deploy_windows.bat` **不是版本位点**：v2.23.0 起退役为薄封装（`call deploy\deploy_offline.bat`），无版本标题。
> 另需同步：`docs/revision_history.md` 追加一段、`docs/README.md` 索引更新。
