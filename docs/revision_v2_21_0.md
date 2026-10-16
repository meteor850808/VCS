# v2.21.0 修订记录

**发布日期**: 2026-09-21 · **版本**: v2.21.0

## 背景

例行架构审查（2026-09-21）产出候选优化项，按价值排序在本批次实施。
核心主题：**报告违规样本 → 预览页定位联动（方案A）**：让报告 HTML 段 ↔ 预览页
（StoredPreview 坐标/要素索引）两条定位链路成为可契约守卫的联动通道。

## 变更清单

### ① 报告违规样本 → 预览定位联动（方案A）

| 文件 | 改动 |
|------|------|
| `app/frontend/app.js` | 新增 `locateReportSample(reportId, sampleIndex)` 与 `locatePvByFeatureIndex(pvId, featureIdx)` 两条定位链路（报告违规样本索引 ↔ StoredPreview 坐标/要素索引契约）；报告违规样本 → 预览页定位联动链路 |
| `tests/test_quality_checker_p6a.py` | 新增 `TestReportLocateContract` 契约守卫入库（报告 HTML 段 ↔ 预览定位契约守卫） |

### 契约守卫登记

- **BASELINE §4 契约守卫**: `TestReportLocateContract` 两条契约守卫
  入库（报告 JSON 段 ↔ 预览页 StoredPreview 定位契约），对应 §13 基准线覆盖批次。

## 版本号

`CONTEXT.md` / `BASELINE.md`（§15 版本追踪表）/ `app/main.py` / `deploy_linux.sh` /
`deploy_windows.bat` / `restart.bat` / `docs/revision_history.md` 全部升至 v2.21.0。
