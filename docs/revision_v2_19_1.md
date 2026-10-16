# v2.19.1 修订记录

**发布日期**：2026-09-16

## 变更摘要

### 混合几何类型导出修复（真实数据暴露）

**背景**：v2.19.0 用纯 Point 测试数据验证，但真实 DWG/GDB 数据（如「道路图纸202015516731.dwg」3757 特征）混合 Point + LineString + MultiLineString + Polygon。Shapefile/GeoPackage **单层只允许单一几何类型**，fiona 写入时 `Record's geometry type does not match collection schema` 直接报错。

**修复**：

| 格式 | 处理 |
|------|------|
| shp | 按几何类型分组，每组一个独立 `.shp`（文件名 `base_<Type>.shp`）；单一类型数据文件名保持 `base.shp` 不变（向后兼容） |
| gpkg | fiona 本机 GDAL 无法对同一 gpkg append 第二层（`a` 模式 NULL pointer，fiona 1.10.1 / Windows wheel 已知兼容问题，无配置 workaround）；改为每类型写独立 `.gpkg`（`base_<Type>.gpkg`）并打包 zip。端点 `gpkg` 分支按特征几何类型数动态返回 `application/zip` 或单文件 gpkg |

geojson / csv / mdb 不受单一几何类型限制，无需改动（mdb 按图层拆表天然兼容混合几何）。

## 真实数据端到端验证（8541e93a 道路图纸，3757 特征）

```
[aggregate] 3757 features loaded, 13 distinct layers
[shp] zip entries: roads_..._Point/LineString/MultiLineString/Polygon (.shp/.shx/.dbf/.cpg ×4)
       Point 组读回 3264 行，属性 Text/Layer/Linetype/PaperSpace/SubClasses
[gpkg] mixed -> zip 4 个 .gpkg，total_rows=3757
[csv] 3758 行（含表头），UTF-8 BOM，属性并集列
[mdb] 13 图层拆 8 表：'0'=3147, js_close=9, js_jzd=156, kc_close=9, kc_dw=42,
       kc_gh=18, kc_jt=304, kc_swl_fw=3；样例行 Point / MEMO / WKT "POINT (13548.91 -10665.31)"
[mdb] file bytes=3964928 (3.9MB)
```

## 涉及文件

| 文件 | 说明 |
|------|------|
| `app/services/export_service.py` | `_group_by_geometry` 新增；`export_shapefile_zip`/`export_gpkg` 混合类型分组（shp 分文件、gpkg 混合打包 zip、单类型保持原样） |
| `app/api/v1/data.py` | gpkg 分支按 `_group_by_geometry(features)` 动态返回 zip（`{base}_gpkg.zip`）或单文件 |
| `tests/test_data_download_export.py` | 新增 `TestMixedGeometryExport` 3 例：混合 shp zip 拆分、混合 gpkg zip 拆分 + 回读、单类型保持旧文件名 |
| `BASELINE.md`/`CONTEXT.md`/`deploy_*`/`restart.bat`/`app/main.py` | 版本号 v2.19.1 |

## 测试

- **全量 1514 passed**（覆盖率 99.21%）
- 新增 3 例混合几何；既有 1511 例无回归