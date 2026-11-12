# docs 目录索引

> 项目基准线与当前状态请以 `../BASELINE.md`、`../CONTEXT.md` 为准。  
> 本文件仅做文档分类导航，不覆盖所有历史文件，历史文档统一归档于 `archive/`。

## 核心文档（根目录）

| 文件 | 说明 |
|---|---|
| `revision_v2_25_1.md` | v2.25.1 修订记录（归属列 `owner_id` 语义 + 迁移 0015–0018 对齐模型，补丁版） |
| `revision_v2_25_0.md` | v2.25.0 修订记录（五维评分口径 v3：要素级派生） |
| `revision_v2_24_0.md` | v2.24.0 修订记录（CI 双架构镜像构建发布、Docker 生产加固等） |
| `revision_v2_23_0.md` | v2.23.0 Windows 离线一键部署子系统（deploy/） |
| `revision_v2_22_3.md` | v2.22.3 CSV 无损归档往返 + 字段-字典绑定模板往返 |
| `revision_v2_22_2.md` | v2.22.2 导入/导出/模板单框三段重构 + mode 暴露 |
| `revision_v2_22_1.md` | v2.22.1 架构优化（孤儿模块清除 + pytest 基建现代化） |
| `revision_v2_22_0.md` | v2.22.0 data.py 收口批次（纯函数迁移 + except-swallow 治理） |
| `revision_v2_21_0.md` | v2.21.0 报告→预览定位联动（方案 A） |
| `revision_v2_20_0.md` | v2.20.0 架构收敛批次（C7/C2/C3/C1/C4 评估） |
| `revision_v2_19_6.md` ~ `revision_v2_19_0.md` | 数据导出与混合几何分组、依赖补全、真实数据导出根因修复、MDB/FGDB 导出等 |
| `revision_history.md` | 历史修订总览（通用汇总） |
| `docker_ops_manual_v2_24_0.md` | Docker 部署·使用·调试操作手册（架构、三路径镜像获取、配置、三种部署模式、运维、排障、备份恢复、升级回滚、安全） |
| `review_v2.16.0.md` | v2.16.0 代码评审报告 |
| `stage_report_2026-09-01.md` | 阶段性汇报（2026-09-01） |

## 架构决策记录（ADR）

| 文件 | 说明 |
|---|---|
| `adr/ADR-0001-route-split-needs-tests-first.md` | 路由拆分需先写测试 |
| `adr/ADR-0002-service-layer-transaction-boundary.md` | 服务层事务边界设计决策 |
| `adr/ADR-0003-knowledge-graph-pure-view.md` | 知识图谱采用纯视图方案（不引入图数据库） |
| `adr/ADR-0004-dual-line-repo-topology.md` | 双线仓库拓扑（monorepo 源 / root 布局发布） |
| `adr/ADR-0005-cross-layout-porting-flow.md` | 跨布局端口流程（format-patch → am -p2 → 全量测试 → PR） |
| `adr/ADR-0006-ownership-columns-owner-id.md` | 归属列只写 `AuthContext.owner_id`；超管归属落 NULL |

## 历史归档（archive/）

`archive/` 包含更早期的 revision/spec/test_manual 等文档，以及 `legacy_root/` 下的早期中文文档、plans/refactors 等历史材料。一般只需查阅最新 revision 或 `BASELINE.md` 即可获知当前状态。

## 部署与运维（Deployment & Ops）

| 文件/目录 | 说明 |
|---|---|
| [`docker/README.md`](docker/README.md) | Docker 相关资产索引（Dockerfile、docker-compose*.yml、entrypoint、Nginx、脚本等） |
| [`deploy/README.md`](deploy/README.md) | 部署相关文件索引（Windows 离线一键部署子系统、Linux 部署脚本、一键重启等） |
| [`docker_ops_manual_v2_24_0.md`](docker_ops_manual_v2_24_0.md) | Docker 部署·使用·调试操作手册（最全面的容器化运维指引） |

## 工程执行材料（superpowers/）

`superpowers/plans/` 与 `superpowers/specs/` 为近期工程计划/设计文档（与当前实现直接相关的已在 revision 中体现）。

> 如需查找某个具体变更的技术细节，优先阅读对应 `revision_vX_Y.md`。