# v2.19.3 修订记录

**发布日期**: 2026-09-17 · **版本**: v2.19.3

## 背景

v2.19.2 全量回归 + 单条 200 通过后，**用户真实文件**（`图纸.gdb.zip/图纸_道路图纸`，detected_crs_srid=918073，GDB 图层中文属性列）在真实服务下载时 MDB/SHP 仍返回「导出失败，请稍后重试或联系管理员」。server_err.log 的 `logger.exception` traceback 暴露三连根因：

1. **MDB 长文本参数绑定**：`pyodbc.Error: (HYC00, ... 所选函数未实现 (106) (SQLBindParameter))` — Microsoft Access ODBC 驱动的 `SQLBindParameter` 对 **>255 字节**参数不实现。
2. **SHP 中文/超长列名**：`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe5 in position 61` — GDB 中文字段名（如「地块名称(项目名称)」）直写 Shapefile dbf，GDAL 报错且错误消息含 GBK 字节，被 fiona 的 `_env.logging_error_handler` 按 UTF-8 解码时崩溃，掩盖真实 GDAL 错误（此前 8541e93a DWG 文件 ASCII 列名全通过，故未被发现）。
3. **MDB 大整数溢出**（修 shp 后浮现）：`pyodbc.DataError: (22018, ... 标准表达式数据类型不匹配)` — 地籍编号 `1100230546425258` 超出 **Access INTEGER 32-bit 有符号上限**（2^31−1），写入 INTEGER 列报类型不匹配。

## 改动

| 文件 | 改动 |
|------|------|
| `app/services/export_service.py` | ① `_access_literal(v, force_quote)`：MDB INSERT 全部改**内联 SQL 字面量**（单引号 `''` 转义），不再走参数绑定（解决 HYC00/106）；MEMO/TEXT 列 `force_quote=True`，大整数以文本字面量写入文本列保整。② `_merge_access_type`：属性列类型**全列扫描合并**（数值族 → DOUBLE；出现 str → MEMO），替代原「首值定类型」。③ `_scalar_sql_type`：int 超出 32-bit 范围 → MEMO 文本列（保整不丢精度）。④ `_shapefile_field_map` + `_build_shapefile_schema`：Shapefile 属性列名映射为 **ASCII ≤10 字符去重**（保留 ASCII 片段，纯中文/冲突回退 `F{n}`）；`_fiona_records` 加 `name_map` 参数。shp 分支专用，**gpkg/csv/mdb 保留中文列名**不受影响。⑤ shp/gpkg 两处 `TemporaryDirectory(ignore_cleanup_errors=True)`：修复导出后 `.dbf` 文件句柄（fiona/GDAL 延迟释放）导致的 `PermissionError: WinError 32` |
| `tests/test_data_download_export.py` | 新增 6 例：`TestShpAsciiFields`（中文列名映射 ASCII、冲突去重、全中文回退 F{n}、导出 schema/记录列名全 ASCII）+ `TestExportAccessLiteral`（大整数 → MEMO、真实 Access 回读 `1100230546425258` 字符串保整） |

## 真实数据回归（venv Python 3.13 直连 + 真实服务 HTTP）

| 文件 | 特征 | mdb | shp | gpkg | csv | geojson |
|------|------|-----|-----|------|-----|---------|
| `7e9d1302`（图纸_道路图纸 GDB） | 185（MultiPolygon，37 列中文属性） | 200 / 2.5 MB | 200 / 145 KB（ASCII 列名） | 200 | 200 / 494 KB | 200 / 697 KB |
| `d9f3b0a7`（同图层 9 特征） | 9（含大整数地籍编号） | 200 / 299 KB | 200 | 200 | 200 | 200 |

> HTTP 验证：`X-API-Key: admin_init_key`（super admin 鉴权），`/api/v1/data/{id}/download?format=...` 两文件 × 5 格式全部 **200**（mdb 6.3s / 1.7s）。

## 测试

- `tests/test_data_download_export.py`：**30 passed**（19 → 30）
- 全量回归：**1525 passed / 99.20%**（venv Python 3.13 下实测）