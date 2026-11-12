# Docker 相关文件索引

> 本目录仅做索引说明，实际 Docker 构建与编排文件均保留在项目根目录及 `docker/` 子目录中，不做移动，以保持现有脚本与引用路径稳定。
> 本文件位于 `docs/docker/`，故指向**仓库根**的文件需 `../../`，指向 **`docs/`** 的文件需 `../`。

## 根目录（顶层编排与镜像定义）

| 文件 | 说明 |
|---|---|
| `../../Dockerfile` | 多阶段构建（含 LibreDWG 源码构建阶段，用于 API/Worker/Beat/Flower 等镜像构建） |
| `../../docker-compose.yml` | 基线容器编排（开发/基础环境） |
| `../../docker-compose.prod.yml` | 生产覆盖层（Nginx 反代、自签 TLS、Docker secrets、资源限制、日志等） |
| `../../docker-compose.release.yml` | 发布编排（基于已发布镜像 `image:` 启动，内置 Nginx/TLS/secrets/端口收敛/migrate Job，支持 `REGISTRY/NAMESPACE/TAG` 三变量切换与离线 `save/load`） |
| `../../.env.prod.example` | 生产环境 `.env` 模板（v2.24.0） |

## `docker/` 目录资产

> 下表路径**相对仓库根的 `docker/` 目录**（即实际位置为 `docker/<名称>`）。

| 文件/目录 | 说明 |
|---|---|
| `entrypoint.sh` | 通用容器入口脚本：派生 `DATABASE_URL`、装载 Docker secrets、自动创建必要数据目录 |
| `nginx/` | Nginx 反代相关资产 |
| `nginx/Dockerfile` | Nginx 容器镜像构建定义 |
| `nginx/nginx.conf` | Nginx 主配置 |
| `nginx/conf.d/vcs.conf` | VCS 服务反向代理与 TLS 配置 |
| `scripts/` | Docker 构建/发布与证书/密钥辅助脚本 |
| `scripts/build_push.sh` | 镜像构建与推送脚本 |
| `scripts/gen_certs.sh` | 自签 TLS 证书生成脚本 |
| `scripts/gen_secrets.sh` | Docker secrets 随机密钥生成脚本 |
| `registry/` | 本地镜像仓库相关配置 |
| `registry/docker-compose.yml` | 本地 Docker Registry 编排 |
| `registry/gen_htpasswd.sh` | Registry 基础认证 htpasswd 生成脚本 |

## 相关文档

- [Docker 部署·使用·调试操作手册](../docker_ops_manual_v2_24_0.md) —— 架构、三路径镜像获取、配置、三种部署模式、日常运维、排障速查、数据备份恢复、升级回滚、安全、附录
- [v2.24.0 修订记录](../revision_v2_24_0.md) —— CI 双架构镜像构建与发布、Docker 生产加固相关变更
- [项目基准线](../../BASELINE.md) —— Docker 相关章节与发布编排说明

> **说明**：`.github/workflows/docker-publish.yml`（CI 双架构镜像构建与 manifest 合并发布）位于 `.github/workflows/`，不在 `docker/` 目录内，但属于 Docker 发布流水线，已在修订记录与 BASELINE 中有详细说明。
