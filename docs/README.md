# docs 目录索引

> 项目基准线与当前状态请以 `../BASELINE.md`、`../CONTEXT.md` 为准。
> 本文件仅做文档分类导航，不覆盖所有历史文件，历史文档统一归档于 `archive/`。
> **最后核对**：2026-11-13（与 `docs/` 实际文件逐项比对；结构由 `tests/test_docs_index_links.py` 守卫）。
> 本文件在 monorepo（`空间质检/docs/`）与 root 布局（`docs/`）中**内容相同、相对链接相同**，不需要端口时改写。

## 核心文档（根目录）

| 文件 | 说明 |
|---|---|
| `revision_v2_25_1.md` | v2.25.1 修订记录（归属列 `owner_id` 语义 + 迁移 0015–0018 对齐模型，补丁版；含 §10 遗留收敛、§11 文档体系整理） |
| `revision_v2_25_0.md` | v2.25.0 修订记录（五维评分口径 v3：要素级派生） |
| `revision_v2_24_0.md` | v2.24.0 修订记录（CI 双架构镜像构建发布、Docker 生产加固等） |
| `revision_v2_23_0.md` | v2.23.0 Windows 离线一键部署子系统（deploy/） |
| `revision_v2_22_3.md` | v2.22.3 CSV 无损归档往返 + 字段-字典绑定模板往返 |
| `revision_v2_22_2.md` | v2.22.2 导入/导出/模板单框三段重构 + mode 暴露 |
| `revision_v2_22_1.md` | v2.22.1 架构优化（孤儿模块清除 + pytest 基建现代化） |
| `revision_v2_22_0.md` | v2.22.0 data.py 收口批次（纯函数迁移 + except-swallow 治理） |
| `revision_v2_21_0.md` | v2.21.0 报告→预览定位联动（方案 A） |
| `revision_v2_20_0.md` | v2.20.0 架构收敛批次（C7/C2/C3/C1/C4 评估） |
| `revision_v2_19_6.md` | v2.19.6 压缩包自动清理修复 |
| `revision_v2_19_5.md` | v2.19.5 File Geodatabase 导出 |
| `revision_v2_19_4.md` | v2.19.4 MDB Jet4 格式修复 |
| `revision_v2_19_3.md` | v2.19.3 真实数据导出根因修复 |
| `revision_v2_19_2.md` | v2.19.2 依赖补全 |
| `revision_v2_19_1.md` | v2.19.1 混合几何分组 |
| `revision_v2_19_0.md` | v2.19.0 数据导出 |
| `revision_history.md` | 历史修订总览（每版追加一段，最新在最前） |
| `docker_ops_manual_v2_24_0.md` | Docker 部署·使用·调试操作手册（架构、三路径镜像获取、配置、三种部署模式、运维、排障、备份恢复、升级回滚、安全） |
| `qc_redesign_v5.html` | 最终质检页设计原型（v2.6 实现依据，仅保留最终版） |
| `review_v2.16.0.md` | v2.16.0 代码评审报告 |
| `stage_report_2026-09-01.md` | 阶段性汇报（2026-09-01 项目审查 + 清理日志 + 优化建议） |

## 架构决策记录（ADR）

| 文件 | 说明 |
|---|---|
| `adr/ADR-0001-route-split-needs-tests-first.md` | 路由拆分需先写测试 |
| `adr/ADR-0002-service-layer-transaction-boundary.md` | 服务层事务边界设计决策 |
| `adr/ADR-0003-knowledge-graph-pure-view.md` | 知识图谱采用纯视图方案（不引入图数据库） |
| `adr/ADR-0004-dual-line-repo-topology.md` | 双线仓库拓扑（monorepo 源 / root 布局发布） |
| `adr/ADR-0005-cross-layout-porting-flow.md` | 跨布局端口流程（format-patch → am -p2 → 全量测试 → PR） |
| `adr/ADR-0006-ownership-columns-owner-id.md` | 归属列只写 `AuthContext.owner_id`；超管归属落 NULL |

## 部署与运维（Deployment & Ops）

| 文件/目录 | 说明 |
|---|---|
| [`docker/README.md`](docker/README.md) | Docker 相关资产索引（Dockerfile、docker-compose*.yml、entrypoint、Nginx、脚本等） |
| [`deploy/README.md`](deploy/README.md) | 部署相关文件索引（Windows 离线一键部署子系统、Linux 部署脚本、一键重启等） |
| [`../deploy/README.md`](../deploy/README.md) | Windows 离线部署**运维手册**（交付/安装/排障/凭证），与上面的文件索引定位不同 |
| [`docker_ops_manual_v2_24_0.md`](docker_ops_manual_v2_24_0.md) | Docker 部署·使用·调试操作手册（最全面的容器化运维指引） |

## 工程执行材料（superpowers/）

`superpowers/plans/`（5 个）与 `superpowers/specs/`（4 个）为近期工程计划/设计文档，与当前实现直接相关的结论已收进对应 revision。

| 目录 | 内容 |
|---|---|
| `superpowers/plans/` | 2026-08-27 MDB 支持 · 2026-09-11 CAD 导出 · 2026-10-09 离线部署 · 2026-10-13 Docker · 2026-11-10 评分优化 |
| `superpowers/specs/` | 2026-09-11 CAD 导出 · 2026-10-09 离线部署 · 2026-10-13 Docker · 2026-11-10 评分优化 |

## 历史归档（archive/）

`archive/` 顶层 30 个文件 + `legacy_root/` 8 个 = **38**：早期 revision（12 个，均早于 v2.19.0）、`technical_spec_*`（4）、`test_manual_*`（8）、`plan_v2_8.md`、`proposal_*`（1）、`*-refactor-plan.md`（2）、`data-ingestion.md`、`environment-notes.md`，以及 `legacy_root/` 下 2026-09-01 由根目录归档的旧版中文文档与 HTML 汇报。

一般只需查阅最新 revision 或 `BASELINE.md` 即可获知当前状态。

> 如需查找某个具体变更的技术细节，优先阅读对应 `revision_vX_Y.md`。
