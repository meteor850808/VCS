# v2.19.5 修订记录

**发布日期**: 2026-09-17 · **版本**: v2.19.5

## 背景

用户对 MDB 导出的反馈链：v2.19.4 修复「Access 打不开」（Jet4 格式）后，
用户仍反馈 **ArcGIS Pro 无法打开 MDB，且个人地理数据库（.mdb）中只有表单
数据、看不到图形**。调研结论（对用户机器是决定性约束）：

1. **ArcGIS Pro 从 2015 年发布起就不支持个人地理数据库（.mdb）**——连读取
   都没有，官方只提供「转换」工具（Convert Personal Geodatabase）把 .mdb
   转成 File GDB 等格式。因此「升级成个人地理数据库」对 Pro 用户没有意义：
   Pro 打不开，此前用户反馈的根因即在此。
2. **跳过 arcpy 写个人地理数据库：无任何开源实现**——Esri 官方只有
   `arcpy.management.CreatePersonalGDB`；开源侧 GDAL PGeo 驱动、
   GitHub pgdb_recovery 均**只读**；PGDB 体系（GDB_Items GUID、SHAPE 二进制、
   几何索引）手工构造风险极高且无先例。
3. **可行的替代：File Geodatabase（.gdb）**——ArcGIS Pro 直接原生格式
   （「添加数据 → 文件夹 → *.gdb」即可显示图形）；GDAL 3.6+ 的 OpenFileGDB
   驱动支持**创建/写入**，本机 fiona 1.10.1 / GDAL 3.9.1 实测可行，纯 Python
   无 arcpy 依赖。

方案 A（生成 .gdb 文件地理数据库）经用户确认后实施。

## 实现

| 文件 | 改动 |
|------|------|
| `app/services/export_service.py` | 新增 `_build_gdb_schema`（int→int64 大整数升级）、`export_gdb`（OpenFileGDB 写 .gdb 目录打包 zip）、`_gdb_origin`/`_walk_coords`（负坐标数据的网格原点自动计算）；`fiona.Env(CHECK_WITH_INVERT_PROJ=False)` 规避投影多边形域校验误报 |
| `app/api/v1/data.py` | `_EXPORT_FORMATS` 增 `gdb`；下载端点 dispatch `format=gdb` → `export_gdb`，返回 `{base}_gdb.zip`（application/zip） |
| `tests/test_data_download_export.py` | 新增 `TestExportGdb`（7 单项：分层 Feature Class / 同层混合几何拆分 / 无 layer 回退 / CRS 往返 / 大整数保整 / zip 结构 / 负坐标回归）+ 端点 `format=gdb` 用例，共 +8 |

## 关键实测结论（踩坑实录）

- **追加语义**：OpenFileGDB 对已存在数据集用 `"w"` 打开是**追加**（各 Feature Class
  共存），`"a"` 模式反而报 `NULL pointer error`（与 v2.19.1 GPKG 追加问题同族）。
- **几何类型读回**：写入 `LineString`/`Polygon` 读回为 `MultiLineString`/`MultiPolygon`
  （OGR/OpenFileGDB 存储行为），ArcGIS 显示不受影响。
- **大整数**：fiona `int` 映射 Integer32，地籍编号 `1100230546425258` 超 32-bit；
  `_build_gdb_schema` 升级 `int64`，且必须传 layer 创建选项
  `TARGET_ARCGIS_VERSION=ARCGIS_PRO_3_2_OR_LATER`，否则 GDAL 降级 Float64 丢精度。
- **投影域校验误报**：EPSG:4527（CGCS2000 3°带）下写 Polygon 报
  `Point outside of projection domain`（Point 正常、Polygon/Line 全挂）——OpenFileGDB
  写几何默认 `CHECK_WITH_INVERT_PROJ` 逆向投影校验；以 `fiona.Env(CHECK_WITH_INVERT_PROJ=False)`
  包住写入解决（与 gdalwarp #4934 同思路，仅收敛校验，不影响 ArcGIS 打开）。
- **负坐标编码失败**：AutoCAD 图纸坐标（如 DWG 导出特征 x/y 可为负，-10665~13548）
  写 OpenFileGDB 报 `Cannot encode Y value`——GDB 坐标以「相对网格原点的正整数偏移」
  编码（`int((coord-origin)*scale)`，要求 coord ≥ origin），而默认 XORIGIN/YORIGIN 为
  0/负偏置（地理 CRS 默认 -400），负坐标即越界中断。ArcGIS 建库时自动把原点放到数据
  范围外；OpenFileGDB 创建选项 `XORIGIN/YORIGIN` 即对应此网格原点——新增 `_gdb_origin()`：
  取每组要素坐标最小值向下取整再 -1，作为 Featrue Class 创建选项显式传入
  （`_walk_coords` 递归遍历任意嵌套 GeoJSON 坐标）。GDB 网格量化带来 1e-12 级浮点残差
  （默认 1e-9 弧度/1e-4 米精度），ArcGIS 打开无感知，属正常。
- **CRS**：手写 Esri 风格 WKT（Datum "China_2000"）无法被 pyproj 回认 EPSG，且坐标易触发
  域校验；改用 pyproj 生成标准 WKT（与生产 `spatial_ref_sys.srtext` 一致）。
- **`layer_name` 语义**：与 mdb 一致，`layer_name` 是表/Feature Class 维度列，从属性中剔除
  不冗余。

## 验证

- 单元/端点：`tests/test_data_download_export.py` **39 passed**（含 TestExportGdb 7 +
  `format=gdb` 端点 1）。
- 真实文件（服务 HTTP，重启为 v2.19.5 后）：
  - `7e9d1302`（185 特征，srid 918073 自定义投影，PROJCS 完整保留）
  - `d9f3b0a7`（9 特征，含大整数地籍编号——18 个大整数与 geojson 原始值逐一比对 identical）
  - `8541e93a`（3757 特征 DWG，混合几何 → 21 个 Feature Class，负坐标图层正常）
  - `GET /data/{id}/download?format=gdb` → 200，zip 内 *.gdb 目录结构完整。
- 全量回归：**1534 passed**（venv Python 3.13.5）。

## 兼容性说明

- File Geodatabase 是 ArcGIS 全系原生格式：ArcGIS Pro / ArcMap / ArcGIS Enterprise
  均直接支持；QGIS（GDAL）同样可读。
- 交付方式：zip 下载（.gdb 为目录），解压后拖入 ArcGIS Pro 即显示图形。
- MDB 导出保留不变（Access 场景仍可用）；`format=gdb` 与既有五种格式并存。