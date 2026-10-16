# v2.19.0 修订记录

**发布日期**：2026-09-16

## 变更摘要

### 数据「下载」→「导出」（Feature Export v2.19.0）

**核心变更**：`/data/{file_id}/download` 端点支持格式选择，从原始字节下载升级为多格式特征导出，完整覆盖主流 GIS 工具链。

#### 支持格式

| 格式 | 说明 | 适用场景 |
|------|------|----------|
| geojson | GeoJSON FeatureCollection + CRS 声明 | QGIS/在线地图 |
| shp | Shapefile (zip 打包，.prj WKT + .dbf UTF-8) | ArcGIS/通用 |
| gpkg | GeoPackage (内嵌 CRS，单文件 SQLite) | QGIS/ArcGIS 3.x+ |
| mdb | Access 数据库（ADOX 建库 + pyodbc 写入，按图层拆表，几何 GeoJSON+WKT 双 MEMO 列） | Access/Excel |
| csv | UTF-8-BOM CSV（WKT 几何 + 属性并集列） | Excel/Python |

#### 向后兼容

- `format` 参数省略且 `file_data` 非空 → 返回原始字节（保持 v2.18.x 行为）
- ZIP 壳（`is_archive=True`）→ 400「压缩包无独立特征数据，请展开后对内部文件导出」
- 无原始字节且无特征 → 410

#### MDB 导出技术栈（本项目首次引入）

- **ADOX** (win32com + ACE OLEDB 12.0)：创建空 .mdb 文件（pyodbc 无法直接建库）
- **pyodbc** + Microsoft Access Driver (*.mdb, *.accdb)：写入表数据
- 表命名：`lyr_` 前缀 + 清洗后 layer_name，空则 `features`
- 列名：`f_` 前缀 + 清洗后属性名，规避保留字
- 几何：GeoJSON 文本（MEMO）+ WKT（MEMO）双列存，无 Shape Geometry 依赖
- Access `TEXT(n)` 上限 255 字符不可靠，长文本一律用 MEMO

#### GDB 不可行说明

fiona 缺失 FileGDB 写驱动（仅 `OpenFileGDB` 只读驱动），写入 FileGDB 需 Esri File Geodatabase SDK（闭源，本项目不引入）。已告知用户。

---

## 涉及文件

| 文件 | 变更说明 |
|------|----------|
| `app/api/v1/data.py` | download 端点重写：format 参数 + 特征聚合导出分支 + CRS WKT 解析 + _resolve_export_wkt 辅助函数 + 线程池执行导出 |
| `app/services/export_service.py` | 新增 export_mdb / export_csv / _safe_table_name / _safe_column_name / _scalar_sql_type / _create_empty_mdb；v2.19 标注 |
| `app/services/feature_store.py` | 新增 load_file_features_geojson（从三张分区表聚合 GeoJSON Feature，不转换坐标系） |
| `app/frontend/app.js` | dlFile 支持 fmt 参数 + dlFmtWrap 复用格式下拉生成；三处下载按钮（卡片/表格/详情抽屉）改为「⬇+格式 select」；ZIP 壳禁用导出提示；toast 文案「下载」→「导出」 |
| `BASELINE.md` | v2.19.0 版本号 + API 端点说明 + export_service 服务条目 + 测试表 + 版本管理 + 全量 passed 计数 |
| `CONTEXT.md` | 版本号 v2.19.0 |
| `deploy_linux.sh` / `deploy_windows.bat` / `restart.bat` | 版本号 v2.19.0 |
| `app/main.py` | FastAPI version 字段 2.19.0 |
| `tests/test_data_download_export.py` | **新增** 16 例（单元 3 + 集成 13） |

## 测试

- **全量 1511 passed**（覆盖 99.16%，无失败，无 skip）
- `test_data_download_export.py` 16 例全部通过，覆盖：
  - export_mdb 单元（layer 拆表断言、字段类型、pyodbc 回读）
  - export_csv 单元（UTF-8 BOM 头、属性并集、WKT）
  - 端点集成：geojson/shp/gpkg/csv/mdb 格式导出 + content-type/content-disposition 断言
  - 向后兼容：有 file_data + 无 format → 原始字节
  - 异常分支：ZIP 壳 400、无特征 410、非法格式 400、404
  - 自定义 SRID PROJCS → shp .prj 写入
  - detected_crs 非 PROJCS → .prj 不存在
  - pyproj fallback（srid<910000 + 无 srtext 行）→ 异常兜底

## 部署注意

- Windows 本机需安装 **Microsoft Access Database Engine**（ACE OLEDB 12.0/16.0）+ **pyodbc**（≥5.1.0）
- Linux 服务器无 Access Driver → MDB 格式不可用（后端返回 410），需预装或移除 MDB 格式选项
- `requirements.txt` 已包含 `pyodbc>=5.1.0`，Linux 部署前确认已 `pip install`
