# VCS v2.8 修订记录 — S1 PostGIS 入库稳定性

## 版本信息
- **版本**: v2.8 (S1)
- **日期**: 2026-08-04
- **类型**: 数据入库链路稳定性修复（ERROR.txt 剩余项 S1）
- **影响范围**: 上传/切片合并/重新索引端点、要素分表写入、WKT 转换
- **关联文档**: `docs/plan_v2_8.md` 阶段 A · S1

---

## 修订目标

依据 `docs/plan_v2_8.md` 阶段 A S1，修复三个叠加的入库稳定性根因：

1. **asyncio.gather 共享同一 AsyncSession** — 压缩包多子文件并发展开时并发使用同一会话，存在并发安全与状态串扰风险
2. **单巨型事务 + ST_IsValid CHECK 整单回滚** — 所有子文件要素集中在主端点一个事务内最终统一提交，任一非法几何触发 CHECK 约束即整体失败
3. **geojson_to_wkt 吞异常** — 几何解析失败静默返回 `GEOMETRYCOLLECTION EMPTY`，类型与几何不匹配且用户无感知

---

## 详细变更

### 1. 几何转换：make_valid 兜底 + 状态化（`app/services/spatial_loader.py`）

#### 1.1 问题
- `geojson_to_wkt` 对解析成功但**无效**的几何（如自相交 bowtie 多边形）不做修复，WKT 写出后触发数据库 `ST_IsValid` CHECK 约束 → flush 失败 → 整个上传事务回滚
- 解析失败静默返回 EMPTY，无任何失败痕迹

#### 1.2 修复
- `geojson_to_wkt_status(geojson_geom) -> (wkt_str, ok)`：无效几何自动 `make_valid` 修复；无法解析/无法修复时返回 `("GEOMETRYCOLLECTION EMPTY", False)`
- `geojson_to_wkt` 保留原签名（兼容旧调用），内部委托新函数

| 输入 | 旧行为 | 新行为 |
|------|--------|--------|
| 有效多边形 | 正常写入 | 不变 |
| 自相交多边形 | 写无效 WKT → CHECK 失败整体回滚 | `make_valid` 修复（如 MultiPolygon）后写入 |
| 3D 坐标 | 降维 2D | 不变 |
| 无法解析的几何 | 静默 EMPTY | 返回 `ok=False`，调用方以 `geometry=NULL` 落库（属性保留） |

### 2. 要素写入：状态统计 + 分批 flush（`app/services/feature_store.py`）

#### 2.1 问题
- `write_feature` 只 `db.add()` 不 flush，数万条 pending 对象堆积到最终一次性 flush（内存峰值高、失败难定位）
- 返回值 bool 无实际语义

#### 2.2 修复
- `write_feature` 返回状态字符串：`written` / `skipped_empty`（几何无法解析，`geometry=NULL` 落库）/ `skipped_no_geometry`（无几何字段，未写入）
- 新增 `write_features_batch(db, stored_file_id, api_key_id, filename, features, max_features, flush_every=500)`：批量写入 + 每 500 条 `flush()`，返回三态统计 dict

### 3. 子任务独立会话（`app/api/v1/data.py`）

#### 3.1 问题
- `_load_extracted_features_async` / `_load_shp_group_features_async` / `_load_all_features_to_spatial_async` 全部接收主端点的 `db`，经 `asyncio.gather` 并发执行
- AsyncSession 不保证并发安全；所有子文件要素共用一个事务，单点失败整单回滚

#### 3.2 修复
- 三个子任务函数**移除 `db` 参数**，内部 `async with AsyncSessionLocal() as sub_db:` 创建独立会话，独立提交/回滚，单子文件失败不影响整单
- 统一返回统计 dict：`{stored_file_id, written, skipped_empty, skipped_no_geometry, geometry_type, error}`
- 新增 `_empty_feature_stats()` / `_collect_sub_task_results()`：gather 结果按 `stored_file_id` 收拢统计、提取错误列表
- 子任务不再直接改写 `StoredFile.feature_count`（消除跨会话缓存冲突），由主端点统一回填

### 4. 主端点聚合回填（`app/api/v1/data.py`）

#### 4.1 问题
- 子任务原在独立会话提交后，主会话 identity map 缓存的 `feature_count` 是旧检测值，聚合查询读到旧值

#### 4.2 修复（三个端点）
- `upload_file` / `merge_upload`：gather 后 `await db.flush()` + `db.expire_all()`，再执行原聚合逻辑；`extraction_details` 按 stats 回填**真实写入数**
- `reindex_file`：改用子任务返回的 `written` 统计，直接返回 `features_indexed`，并透出 `skipped_empty`
- 附带修复：`merge_upload` 非压缩包路径 `load_errors` 未定义（UnboundLocalError 隐患）——在压缩包分支前统一初始化

---

## 验收结果（端到端实测，Postgres）

### 单文件 GeoJSON（含自相交 + 3D 几何）
| 项 | 结果 |
|----|------|
| 上传状态 | 201 |
| feature_count | 5 / 5 全部入库 |
| bowtie 自相交 | 修复为 MultiPolygon 成功入库（旧代码 500） |
| 3D 面 | 降维 2D 入库 |
| 清理 | 204 |

### 压缩包 ZIP（2 GeoJSON + 1 CSV，gather 并发）
| 项 | 结果 |
|----|------|
| 上传状态 | 201 |
| 子文件 a_layers（3 要素含 bowtie） | detail=3，实际入库=3 ✓ |
| 子文件 b_lines（2 要素） | detail=2，实际入库=2 ✓ |
| CSV（2 行） | table_only，feature_count=2（行数，原有语义） |
| archive feature_count 汇总 | 7 = 3+2+2（子文件之和，原有语义） |
| extraction_details 回填 | 与真实入库数一致 ✓ |
| 清理 | 204 |

### 单元验证
- `geojson_to_wkt_status`：有效/自相交/3D/垃圾输入 5 用例全过
- `write_features_batch`（SQLite 内存库）：写入 4 / skipped_empty 1 / skipped_no_geometry 1，几何修复与 NULL 落库正确
- `_collect_sub_task_results`：dict/error 收拢正确
- 既有测试套件失败点（SQLite 无法编译 JSONB）为**预先存在**问题，stash 前后一致，与本次改动无关

---

## 遗留说明
- archive `feature_count` 聚合含 CSV 行数（3+2+2=7）为原有语义，S1 未改变；如需「仅空间要素」口径请另行立项
- `source_archive` 以文件名关联，同名压缩包多次上传会串聚合——原有设计缺陷，建议后续改用 archive id
- reindex 仅对 `file_data` 保留的历史数据可用（上传即清空，原有行为）

---

# VCS v2.8 修订记录 — S2 CRS 元数据链路

## 版本信息
- **版本**: v2.8 (S2)
- **日期**: 2026-08-04
- **类型**: 坐标系元数据链路打通（ERROR.txt 六-1 + 六-2 共同根因）
- **影响范围**: 空间要素三表、入库写入、预览查询、坐标转换/几何修复端点、CRS 解析
- **关联文档**: `docs/plan_v2_8.md` 阶段 A · S2

---

## 修订目标

1. **入库不感知源 CRS**：SRID 硬编码 4326，北京54/西安80/4547 投影图入库后预览偏移上百米
2. **转换写回错标 SRID**：`transform_features_by_file` 转换到目标 CRS 后仍写 `srid=4326`
3. **always_xy 行为分裂**：`transform_coords`（`crs_transform.py`）未加 `always_xy=True`，地理 CRS 轴序颠倒

---

## 详细变更

### 1. 数据模型：三表去除 SRID typmod（`app/models/spatial_feature.py` + `alembic/versions/0005_remove_srid_typmod.py`）
- `Geometry('GEOMETRY', srid=4326)` → `Geometry('GEOMETRY')`（点/线/面三表）
- 迁移 0005：`ALTER TABLE ... ALTER COLUMN geometry TYPE geometry;`（已对开发库执行，已 stamp）
- 效果：每行要素可按源 CRS 标注各自 SRID；已有 4326 数据不受影响

### 2. CRS 解析增强（`app/services/crs_transform.py`）
- 新增 `crs_to_srid(crs_str) -> int`：解析 detected_crs（.prj WKT 全文 / `EPSG:xxxx` / `urn:ogc:def:crs:EPSG::xxxx` / 中文别名 / 纯数字）→ SRID，失败回退 4326
- `transform_coords` 补 `always_xy=True`（修复地理 CRS 默认 lat,lon 轴序导致坐标颠倒）

### 3. 入库写源 SRID（`app/services/feature_store.py` + `app/api/v1/data.py`）
- `write_feature` / `write_features_batch` 新增 `srid: int = 4326` 参数，`WKTElement(wkt, srid=srid)`
- 三个子任务 `_load_*_async` 新增 `srid` 参数透传
- 全部 9 处调用点（upload/merge 的 GDB 组、SHP 组、矢量子文件、非压缩包、reindex）传 `srid=crs_to_srid(sf.detected_crs)`

### 4. 预览查询统一 4326 输出（`app/services/feature_store.py`）
- `query_features_by_file` 的 `ST_AsGeoJSON` 改为按行自适应：
  `CASE WHEN ST_SRID(geometry)=4326 THEN geometry ELSE ST_Transform(geometry, 4326) END`
- 修复：`func.case` 不支持 `else_` → 改用 `sqlalchemy.case`（500 根因）

### 5. 转换写回目标 SRID + detected_crs 联动（`app/services/feature_store.py` + `app/api/v1/spatial.py`）
- `transform_features_by_file` 新增 `target_srid` 参数，写回 `WKTElement(new_geom.wkt, srid=target_srid)`
- transform 端点 `source_crs` 缺省时读 `sf.detected_crs`；转换成功后 `sf.detected_crs = dst_epsg`（预览/后续转换基于新 SRID）
- **修复 `_transform_coords` 既有 bug**：shapely `mapping()` 对 Point 返回 tuple，`coords[0], coords[1] = ...` 抛 `TypeError` 被 except 吞掉 → 转换恒返回 `transformed=0`。重写为递归列表化版本 `_transform_xy`

### 6. 修复保留原 SRID（`app/services/feature_store.py`）
- `repair_features_by_file` 写回时读 `ST_SRID(geometry)` 保留原 SRID

### 7. 空间查询跨 SRID 安全（`app/services/spatial_query.py`）
- `bbox_query` / `knn_query` 的几何统一 `ST_Transform(geometry, :srid)` 再比较（混合 SRID 下不再报「Operation on mixed SRID」）

---

## 验收结果（端到端实测，Postgres）

| 用例 | 输入 | 断言 | 结果 |
|------|------|------|------|
| 1 | 4547 投影 GeoJSON（crs 声明） | DB SRID=4547；预览输出经纬度 ≈[114.1,39.9] | ✅ |
| 2 | 4326 GeoJSON（无 crs） | DB SRID=4326；预览原样 [116.3,40.1] | ✅ |
| 3 | transform 4326→4547 | transformed=1；detected_crs=EPSG:4547；SRID=4547；预览转回误差 <0.02° | ✅ |
| 4 | 4490 bowtie → repair | repair 后 SRID 仍 4490 | ✅ |
| 回归 | S1 混合几何上传 | 5/5 入库、bowtie 修复、3D 降维不变 | ✅ |

### 单元验证
- `crs_to_srid`：`urn:ogc:def:crs:EPSG::4547` → 4547（用例 1 隐含验证）；None → 4326
- 迁移幂等性：三表 `format_type` 由 `geometry(Geometry,4326)` → `geometry`

---

## 遗留说明
- 历史已入库数据（按 4326 误标但实际为投影坐标）无法自动修复，需 reindex 重导入后按新链路标注
- `transform_coords` 仅服务点坐标转换场景；批量要素转换统一走 `_transform_coords`（已修复）
- SHP 组的 `detected_crs` 依赖 `.prj` 文件随 ZIP 上传；无 `.prj` 时默认 4326

---

# VCS v2.8 修订记录 — S3 Multipatch/OBJ 3D 降维入库

## 版本信息
- **版本**: v2.8 (S3)
- **日期**: 2026-08-04
- **类型**: 3D 数据降维入库（ERROR.txt 四-3）
- **影响范围**: `spatial_loader.py`（降维/WKT/OBJ 提取/格式识别）、`feature_store.py`（3D 类型归一化）、`data.py`（非压缩包 feature_count 回填）
- **关联文档**: `docs/plan_v2_8.md` 阶段 A · S3

---

## 修订目标

1. **shapely 不支持 MultiPatch** → 几何恒 EMPTY，入库 0 要素
2. **`_guess_format` 缺 `.obj` 映射** → OBJ 被当 GeoJSON 解析，提取恒空
3. **3D 检测与 2D 存储断链** → `geometry_type` 无归一化，分表路由错乱

---

## 详细变更

### 1. 3D 复合类型降维（`app/services/spatial_loader.py` — `geojson_to_wkt_status`）
- 对 shapely 不支持的 3D 复合类型按面语义降维：
  | 类型 | 结构 | 降维结果 |
  |------|------|----------|
  | MultiPatch / PolyhedralSurface | 每 surface 首环为外环、其余为内环（≈MultiPolygon） | 2D MultiPolygon |
  | TIN | 每三角形一面 | 2D MultiPolygon（每三角形一 Polygon） |
- 降维在 `shape()` 解析**之前**完成（shapely 不认 MultiPatch），随后走既有 `_force_2d` / `make_valid` 链路
- 外环首环提取即「shell 外环 → Polygon」语义；Z 值经 `_force_2d` 丢弃（2D 存储为既定架构）

### 2. OBJ 提取链路（`app/services/spatial_loader.py` + `app/services/obj_parser.py`）
- `_guess_format` 补 `.obj → 'OBJ'` 映射（原兜底 GeoJSON 导致解析失败）
- `extract_features_from_file` 新增 OBJ 分支 → `_extract_from_obj`：调 `obj_parser.parse_obj_file` → `obj_to_geojson_features`
- 每 `o` 对象生成一个质心 Point 要素，属性含 `object_name` / `vertex_count` / `face_count` / `material` / 包围盒 min/max xyz；Z 在写入时由 `_force_2d` 降维

### 3. 3D 类型归一化（`app/services/feature_store.py` — `write_feature`）
- 新增 `_GEOM_TYPE_NORMALIZE`：`MultiPatch/PolyhedralSurface → MultiPolygon`、`TIN → Polygon`
- `geometry_type` 列与分表路由统一用归一化后的 2D 类型（MultiPatch 要素落 polygon_features 表，前端显示 Polygon）

### 4. 非压缩包 feature_count 回填（`app/api/v1/data.py` — `upload_file`）
- **问题**：非压缩包路径 `_load_all_features_to_spatial_async` 返回值被丢弃，`feature_count` 停留检测阶段估算值（OBJ 时为 face 数而非对象数）
- **修复**：捕获子任务 stats，回填 `feature_count = written`（与压缩包「子文件之和」口径统一为实际写入数）；`geometry_type` 同步回填；子任务 error 透出到 warning

---

## 验收结果

### 单元验证（`s3_unit.py`，全部通过）
| 用例 | 断言 | 结果 |
|------|------|------|
| MultiPatch（2 surface，含 Z） | → 2D MultiPolygon，Z 移除 | ✅ |
| TIN（2 三角形） | → 2D MultiPolygon | ✅ |
| PolyhedralSurface | → 2D MultiPolygon | ✅ |
| OBJ（box+roof 2 对象） | `_guess_format='OBJ'`；2 要素 Point；质心 (5,5,4) 正确 | ✅ |

### 端到端实测（Postgres，服务 45679）
| 用例 | 断言 | 结果 |
|------|------|------|
| OBJ 上传（2 对象，新内容绕过秒传） | feature_count=2（实际写入数，非检测 face 数 10）；geometry_type=Point；format=obj | ✅ |
| 预览 | 2 个 Point 要素，坐标 2D [60,50]/[245,45]（Z 已降维） | ✅ |
| DB 直查 | point_features=2；几何非空；SRID=4326；object_name 保留；vertex/face 统计属性 | ✅ |
| 秒传去重 | 同内容再传返回已有记录（feature_count=2 正确） | ✅ |
| 回归 S1/S2 | `s2_integration.py` 4 用例全过（4547 上传/4326 回归/transform/repair） | ✅ |

---

## 遗留说明
- **MultiPatch 端到端未做**：本机 fiona 1.10 schema 不支持写 MultiPatch（`UnsupportedGeometryTypeError`）、无 osgeo/GDAL CLI、PGeo 驱动无法读 `.mdb`。降维逻辑采用单元级验证，其输入结构对齐 fiona 读取 MultiPatch 的标准输出（`coordinates = [surface…]`，surface 首环为外环）；建议后续在具备真实 MultiPatch 样本的环境补一条端到端用例
- OBJ 质心为**对象级**（每 `o` 一点），非面级要素；如需面级入库（每个 face 一 Polygon）可后续扩展 `obj_to_geojson_features`
- 服务器启动环境说明：本机 `.venv` 的 `uvicorn.exe`/`python.exe` stub 与 `空间API服务\.venv` 完全相同（venvlauncher 解析到同一解释器），服务实以 `空间API服务` venv 的 site-packages 运行且依赖兼容；如需隔离需重建 venv（另行立项）

---

# VCS v2.8 修订记录 — S4 数据-标准-质检联动

## 版本信息
- **版本**: v2.8 (S4)
- **日期**: 2026-08-05
- **类型**: 数据-标准-质检联动打通（ERROR.txt 四-5）
- **影响范围**: `quality_checker.py`、`quality/attribute_checker.py`、`api/v1/quality.py`、`api/v1/data.py`、`schemas/storage.py`、`app/frontend/index.html`
- **关联文档**: `docs/plan_v2_8.md` 阶段 B · S4

---

## 修订目标

1. **`standard_table_id` 全程未打通** → 上传关联图层后，质检仍按标准全部表检查，图层级过滤失效
2. **源数据 `layer_name`（临时文件名）与标准表名不同命名空间** → A1「必要图层检查」恒误报缺失
3. **前端标准取手动下拉** → 不读文件 `standard_id`，切换文件不联动标准
4. **`feature_count=0` 一刀切 400** → Multipatch/OBJ 解析失败与真无要素无法区分，无恢复引导

---

## 详细变更

### 1. 质检链路透传 `standard_table_id`（`app/services/quality_checker.py` + `app/services/quality/attribute_checker.py`）
- `run_quality_check` 新增 `standard_table_id` 参数，透传 `run_attribute_checks`
- `run_attribute_checks` / `load_standard_tables` 新增 `standard_table_id` 过滤：指定时仅加载该标准表（层组递归遍历内按 `t.id` 过滤），为空时加载标准全部表（原有行为）
- `StandardTableDef` 增加 `table_id` 字段（供过滤/溯源）

### 2. 单表检查模式语义修正（`attribute_checker.py`）
- 当 `standard_table_id` 给定（单表模式）：
  - **A1 必要图层检查**：跳过「标准表名 vs 源图层名」比较（文件本身就是该图层的载体，两个命名空间本不相同），直接 pass 并提示已关联的标准表
  - **逐表检查**：`layer_feats` 改用文件全部要素（不再按 `layer_name` 匹配标准表名），A2 几何类型 / A3 属性结构 / A4 必填 / A5 值域 / A6 唯一 / A8 小数位 / A9 长度全部照常执行
- 多表模式（`standard_table_id` 为空）保持原有行为不变

### 3. 上传关联分支回填 `standard_table_id`（`app/api/v1/data.py` + `app/schemas/storage.py`）
- `upload_file` 的「关联已有图层组」分支：未显式传 `standard_table_id` 时按层组兜底——**优先选几何类型匹配的表**，无匹配取第一张（按 `table_name` 排序）
- 层组多表几何校验升级：存在任一表几何类型匹配即通过；无匹配才 400（原 `limit(1)` 取首表误判）
- `StoredFileUploadResponse` 补 `standard_table_id` 字段，三路上传响应（直接上传 / 秒传去重 / 切片合并）均回填

### 4. 质检前置校验增强（`app/api/v1/quality.py`）
- `feature_count=0` 时先查 spatial 三表实际要素数：实际有要素 → 放行（计数异常场景）；真无要素 → 400 + 引导 reindex / 重新上传
- 修复函数内 `from sqlalchemy import select` 位置导致 `UnboundLocalError`（import 提升到函数顶部）

### 5. 分页上限放宽
- `GET /data` `page_size` 上限 100 → 200；`GET /quality` 上限 50 → 200（前端列表/报告大分页）

### 6. 前端联动（`app/frontend/index.html`）
- `loadStoredSel` / `loadQCReports` / `loadStdsSel` 的 `page_size=50` → 200
- 文件切换自动回填关联标准（`applyFileStandard`：`qcPickFile` / `qcOnFileChange` 触发；仅当文件有 `standard_id` 且与当前选择不同）
- `doQcStored` 携带 `standard_table_id`（仅当请求标准与文件关联标准一致时，避免手动换标准后表错位）
- `feature_count=0` 错误提示区分引导

---

## 验收结果

### 端到端实测（Postgres，服务 45679）
| 用例 | 断言 | 结果 |
|------|------|------|
| 建标准+层组+Point 表+Polygon 表（多表） | 全 201 | ✅ |
| 上传 Point GeoJSON 关联层组（不传 table_id） | 响应+详情 `standard_table_id` 均回填 Point 表（几何匹配）；feature_count=2 | ✅ |
| 质检（不带标准/表） | 200；继承文件关联标准+表；6 项字段级检查执行；A1 pass（不误报缺失图层）；无 31010007 失败；总分 1.0 | ✅ |
| 几何不匹配（单表层组仅 Point，传 Polygon） | 400「数据类型不匹配」 | ✅ |
| 回归 S2 | `s2_integration.py` 4 用例全过 | ✅ |
| 回归 S3 | OBJ 上传秒传复用 → 预览 2 点 + DB 2 条几何非空 SRID=4326 | ✅ |
| 前端 JS | `node --check` 通过 | ✅ |

---

## 遗留说明
- **多表模式（未传 `standard_table_id`）A1 图层名匹配**：仍按「源 `layer_name` vs 标准表名」比较。上传文件 `layer_name` 为临时文件名，与标准表名不同命名空间，全表模式仍可能报缺失图层——这是 S4 范围外的既有行为；建议后续将 A1 语义改为「文件要素已按表关联归属」或在上传时记录可映射的图层名
- **单表模式取文件全部要素**：当文件实际为多图层混合数据、仅关联一张标准表时，字段检查会跨图层聚合；当前语义以「一个文件 = 一个图层（表）」为前提，与上传侧「每文件选一张表」的模型一致
- 质检报告生成路径（`quality.py` 生成 `QualityReport` 处）已带 `standard_table_id`，报告详情可溯源到表

---

# VCS v2.8 修订记录 — S5 质检报告可视化

## 版本信息
- **版本**: v2.8 (S5)
- **日期**: 2026-08-05
- **类型**: 质检报告可视化（ERROR.txt 五-3 + 五-4）
- **影响范围**: `schemas/quality.py`、`api/v1/quality.py`、`services/quality/attribute_checker.py`、`app/frontend/index.html`
- **关联文档**: `docs/plan_v2_8.md` 阶段 B · S5

---

## 修订目标

1. **违规明细未透传到详情接口**：`run_quality_check` 已产出 `total_violations`/`violation_samples`/`scores`，但 `QualityReportDetail` 无对应字段，`GET /quality/{id}` 仅返回原始落库列
2. **HTML 报告信息密度低**：无维度仪表图、无严重度统计、无违规样本，无法直接作为交付报告
3. **前端详情面板缺可视化**：仅"综合评分 + 属性/拓扑两表"，五个维度无直观对比

---

## 详细变更

### 1. 详情 schema 透传（`app/schemas/quality.py`）
- `QualityReportDetail` 新增：
  - `attr_results: list[CheckItemResult]` / `topo_results: list[TopoCheckItemResult]`（全量检查项，含 pass）
  - `total_violations: int = 0` / `violation_samples: list[ViolationSample]` / `severity_dist: dict[str, int] = {}`
  - 全部带默认值、保留 `from_attributes=True`，存量报告兼容
- `CheckItemResult.samples` 放宽为 `list`（原 `list[dict]` 与属性检查的 int/值样本不兼容）；`ViolationSample.sample` 放宽为 `Any`（int=要素索引 / 值字符串）

### 2. 详情接口手工构建（`app/api/v1/quality.py::get_quality_report`）
- `GET /quality/{id}` 不再直接返回 ORM 对象，改为手工构建 `QualityReportDetail`：
  - `attr_results`/`topo_results` 从 `constraint_violations`/`topology_violations`（JSONB）重建
  - `violation_samples`：attr 取 `samples[:5]`（int→`feature_index`，其它→`sample` 值）；topo 取 `feature_indices[:5]`；合并前 100 条
  - `total_violations`：fail 项 `failed_count` 求和
  - `severity_dist`：fail 项按 `severity` 计数（error/warning）

### 3. HTML 报告模板化重写（`app/api/v1/quality.py::get_quality_report_html`）
- 五维 **SVG 环形仪表**（内联、零 CDN）：综合评分大环 + 完整性/准确性/一致性/唯一性/拓扑 5 个维度环，≥85 绿 / ≥60 黄 / 红
- **严重度分布**：错误/警告计数 + 占比条（全通过时显示"全部检查通过"）
- **违规样本明细表**：规则/图层/字段/样本（要素索引红标、值黄标）
- 保留属性检查 / 拓扑检查表 + 摘要计数，新增 `@media print` 打印样式（可导出 PDF）

### 4. 前端详情面板增强（`app/frontend/index.html::toggleQcDetail`）
- 新增 `.dp-dims` 五维评分卡片行（边框/数值按阈值着色）
- 新增 `.dp-sev` 严重度分布条（错误/警告/违规总数）
- 新增违规样本明细表（跨列、`.qc-idx`/`.qc-val` 着色标签），`GET /quality/{id}` 直接消费新字段

### 5. 【顺带修复】A4 必填检查空字符串漏判（`app/services/quality/attribute_checker.py`）
- **根因**：pandas 2.x 字符串列 dtype 推断为 `str`（非 `object`），原 `if col.dtype == object` 分支不执行 → 空字符串不计数 → 必填违规恒 pass
- **修复**：统一用 `empty_mask = col.isna() | (col.astype(str).str.strip() == "")` 统计缺失
- **样本修复**：df 行序与 `layer_feats` 一致，`df[empty_mask].index` 映射回原始 `feature_index`（此前 `sample_rows["feature_index"]` 列不存在，样本永远为空）

---

## 验收结果

### 端到端实测（Postgres，服务 45679）
| 用例 | 断言 | 结果 |
|------|------|------|
| 建标准+层组+Point 表（name 必填 + code 主键）→ 上传 3 要素（含空 name、重复 code） | 全部 201 | ✅ |
| 质检（继承标准+表） | 200；报告 id 返回 | ✅ |
| 详情字段对齐 | `attr_results`=7 与 `constraint_violations` 对齐；`topo_results`=9 对齐 | ✅ |
| 违规统计 | 违规 2 项（A4 必填 + A6 唯一）；`total_violations`=3；`severity_dist`={error:2,warning:0} | ✅ |
| 违规样本 | 3 条；A4 样本 `feature_index=1`（空 name 要素）；A6 样本 `sample="X2"` | ✅ |
| HTML 报告 | 含五维 SVG、严重度分布、违规样本明细、`@media print`、`要素 #` 索引 | ✅ |
| 存量兼容（全通过数据） | 详情 200；`attr_results` 无 fail 项；`total_violations`=0；HTML 显示"全部检查通过" | ✅ |
| 回归 S4 | `s4_e2e.py` 17/17 | ✅ |
| 回归 S2 | `s2_integration.py` 4 用例全过 | ✅ |
| 前端 JS | `node --check` 通过 | ✅ |

### 验证脚本
- `s5_e2e.py`：28 断言全过（`%TEMP%\opencode\s5_e2e.py`）

---

## 遗留说明
- **HTML 报告"违规要素点击跳预览定位"**：原规划可选增强，本轮未做（需前端预览地图与报告联动定位 feature_index），留待后续
- **`severity_dist` 仅统计 fail 项**：info/skip 不计数；维度卡片用分数本身反映 pass 项质量

---

# VCS v2.8 修订记录 — S6 Excel 模板补字典 Sheet + 导入导出对齐

## 版本信息
- **版本**: v2.8 (S6)
- **日期**: 2026-08-05
- **类型**: 标准模块 Excel 回环修复（ERROR.txt 二-1/7）
- **影响范围**: `api/v1/data_standard_v2.py`、`services/data_standard_parser.py`、`schemas/data_standard_v2.py`、`core/auth_context.py`
- **关联文档**: `docs/plan_v2_8.md` 阶段 C · S6

---

## 修订目标

1. **导出→导入回环破损**：`export_standard` Excel 列位与模板/解析器不一致——层组第 3 列导出 `parent_id`(UUID) 而解析器按「父级编码」读；数据表列错位；字段第 1 列导出表名而解析器读表编码
2. **无字典 Sheet**：模板与导出均缺字典定义，字典无法经 Excel 回环
3. **字段中文名（field_alias）未参与回环**：模板/导出有「中文名」列但解析器不读、导入不写

---

## 详细变更

### 1. 导出对齐（`app/api/v1/data_standard_v2.py::export_standard`）
- Excel 分支重写为五 Sheet，列位与模板/解析器完全一致：
  - `标准信息`（字段名/值）｜`层组定义`（层组名|层组编码|父级编码|描述）｜`数据表定义`（层组编码|表名|表编码|表类型|几何类型|描述）｜`字段定义`（表编码|字段名|中文名|数据类型|长度|精度|必填|主键|描述）｜`字典定义`（字典编码|字典名称|适用范围|字典项编码|字典项名称|描述）
- 层组「父级编码」改导出 `group_code`（`group_code_by_id` 索引 parent_id→编码），不再导出 UUID
- 表/字段第 1 列改导出 `table_code`；字段补 `field_alias`（中文名）；必填/主键导出「是」布尔
- 字典 Sheet：每字典至少一行（空字典保留行头），有字典项时展开为多行，解析器按 `dict_code` 聚合

### 2. 模板补字典 Sheet（`app/api/v1/data_standard_v2.py::download_excel_template`）
- 新增 Sheet 5「字典定义」示例（PT_TYPE 点类型 A 平面点 / B 高程点）

### 3. 解析器（`app/services/data_standard_parser.py::parse_standard_excel_v2`）
- `V2FieldDef` 新增 `field_alias`；字段 Sheet 解析读「中文名」列（下标 2）
- `V2DictDef` 新增 `description` 字段；新增「5. 字典定义/字典」Sheet 解析（每字典一行、字典项展开多行，按 dict_code 聚合；scope/描述容错；识别标志补「字典定义」）

### 4. 字典导入（`app/api/v1/data_standard_v2.py::_import_from_v2`）
- 按 dict_code 聚合创建 `StandardDictionary` + `DictionaryItem`；scope 校验 standard/global；`ImportResult` 补 `dictionaries_count`

### 5. JSON 导出/详情树去重（`data_standard_v2.py` + `schemas/data_standard_v2.py`）
- **根因**：`DataStandard.layer_groups` 关系返回全部层组（含子层组），子层组同时出现在顶层与父 `children` 中 → 详情树与 JSON 导出重复
- 修复：`DataStandardDetail` 加 `model_validator` 顶层只保留根层组；JSON 导出 `layer_groups` 过滤 `parent_id is None`

### 6. 【顺带修复】三处真实 bug
- **① 超级管理员建字典恒 409**：`AuthContext.create_super_admin` 使用虚拟 ApiKey ID `00000000-0000-0000-0000-000000000001`（不存在于 `api_keys` 表），`create_dictionary`/JSON 导入/Excel 导入写 `api_key_id` 触发外键失败，被 IntegrityError 兜底误报为「字典名称已存在」。修复：`AuthContext` 新增 `owner_id` 属性（超管返回 None，可安全写入外键列），三处字典插入改用之；IntegrityError 兜底补真实异常日志（不再掩盖根因）
- **② `DataStandardDetail.dictionaries` 恒为空**：schema 字段名与模型属性 `standard_dictionaries` 不匹配（`from_attributes` 下读不到）→ `validation_alias="standard_dictionaries"` 映射
- **③ 中文标准名导出 500**：`Content-Disposition: filename={std.name}.xlsx` 含中文触发 latin-1 `UnicodeEncodeError` → 新增 `_content_disposition`（RFC 5987 `filename*=UTF-8''` + ASCII fallback），JSON/Excel 两分支共用

---

## 验收结果

### 端到端实测（Postgres，服务 45679，`s6_e2e.py` 40/40）
| 用例 | 断言 | 结果 |
|------|------|------|
| 建标准+根层组 BG/DC+子层组 KC（挂 BG）+2 表 4 字段+2 字典（PT_TYPE 2 项 + EMPTY_DICT 空） | 全 201 | ✅ |
| 导出 Excel | 200；五 Sheet 齐全；列位与模板对齐 | ✅ |
| 层组父级编码 | KC 父级=「BG」编码（非 UUID）；根层组父级为空 | ✅ |
| 数据表/字段列位 | CTRL_PT 行层组编码=BG+几何 Point；pt_id 行表编码=CTRL_PT+必填/主键「是」 | ✅ |
| 字典 Sheet | PT_TYPE 展开 2 行（A/B）；EMPTY_DICT 保留 1 行（无项） | ✅ |
| 导入 Excel 回环 | 201；ImportResult 3 层组/2 表/4 字段/2 字典/2 字典项；`dictionaries_count=2` | ✅ |
| 详情结构 | 层组数=3（树去重生效）、表数=2、字典数=2；KC.parent=BG.id | ✅ |
| 字段属性回环 | pt_id 必填+主键+长度 20+中文名「点号」；pt_type 长度 10 | ✅ |
| 字典回环 | PT_TYPE 2 项（A 平面点/B 高程点）、EMPTY_DICT 0 项、描述还原 | ✅ |
| 模板 | 含「字典定义」Sheet | ✅ |
| 回归 S4 | `s4_e2e.py` 17/17 | ✅ |
| 回归 S5 | `s5_e2e.py` 28/28 | ✅ |

### 验证脚本
- `s6_e2e.py`：40 断言全过（`%TEMP%\opencode\s6_e2e.py`）

---

## 遗留说明
- **`list_global_dictionaries` 不显示标准级字典**：全局列表查询 `scope=='global' OR scope_clause`，标准级字典仍按标准详情/标准字典列表访问（既有语义，S6 未改）
- **CSV 导入仍为表级语义**：不创建字典（模板本身无字典概念），如需 CSV 字典回环另行立项
- **超级管理员 `owner_id=None` 的字典归属**：不可按 key 过滤追溯（超管本身可访问所有数据，语义一致）；普通 Key 创建的字典仍记录真实 `api_key_id`


# VCS v2.8 修订记录 — S7 标准回传更新

## 版本信息
- **版本**: v2.8 (S7)
- **日期**: 2026-08-05
- **类型**: 标准模块导入三模式回传（ERROR.txt 二-3）
- **影响范围**: `api/v1/data_standard_v2.py`、`models/data_standard.py`、`schemas/data_standard_v2.py`、`services/data_standard_parser.py`、`alembic/versions/0006_add_standard_code.py`
- **关联文档**: `docs/plan_v2_8.md` 阶段 C · S7

---

## 修订目标

1. **三格式导入永远新建**：JSON/Excel/CSV 导入无更新语义，同 name 重导撞唯一约束 500
2. **无 `code` 标识**：标准模型缺业务编码字段，无法稳定按编码定位「同一标准」
3. **更新无版本联动**：重导不产生版本快照，无回传审计

---

## 详细变更

### 1. 模型 + 迁移（`app/models/data_standard.py` + `0006_add_standard_code.py`）
- `DataStandard` 新增 `code` 列（可空，`max_length=64`）
- 新增唯一约束 `uq_ds_api_code`（`api_key_id, code`）：同一 Key 下编码唯一，跨 Key 可重复
- 迁移 `0006_add_standard_code`（down_revision `0005_remove_srid_typmod`）已应用并 DB 验证（code 列 + 约束存在）

### 2. 导入三模式（`app/api/v1/data_standard_v2.py`）
- 三个导入端点（JSON/Excel/CSV）统一支持 `mode=create|update|upsert`（`VALID_IMPORT_MODES`）
- `_resolve_import_target(db, api_key, mode, code, name)`：create 恒返回 None；update/upsert 按 code（优先）→ name 匹配目标，遵循 `api_key.scope_clause`（超管可匹配任意标准）；update 未命中抛 404
- 命中更新：改标准元信息（code/name/description/category/file_name）→ `_delete_standard_children` 整体删子 → 按来源重建（`_create_json_children`/`_create_v2_children`/`_create_flat_children`/`_create_csv_children`）→ `flush` → 版本快照+1
- create/upsert 新建路径 `IntegrityError` 兜底 → 409（「名称已存在或编码已被使用」）

### 3. 版本快照联动（`_import_version_snapshot`）
- 复用发布链路：`_build_standard_snapshot`（populate_existing）→ `_get_previous_version` → `_compute_diff` → 写 `StandardVersion`（`api_key_id=api_key.owner_id`，超管 None 安全）→ `target.version += 1`
- `ImportResult` 新增 `mode` / `updated` / `version` 字段，前端可判断「新建 or 更新到 vN」

### 4. 【根因修复】async 下 `db.expire_all()` 触发 MissingGreenlet 500
- 现象：update 模式回传恒 500，日志 `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called`，栈帧 `attributes.__get__ → _load_expired → load_on_ident`（`target.id` 读取即触发）
- 根因：`_delete_standard_children` 用 bulk delete 后，同会话 identity map 仍驻留旧子记录；此前用 `db.expire_all()` 清缓存，但 async 会话中 `expire_all` 后属性访问触发同步刷新 IO，不在 greenlet 上下文 → MissingGreenlet
- 修复：`_build_standard_snapshot` 查询加 `.execution_options(populate_existing=True)`（查询结果覆盖 identity map 中的陈旧对象），并删除全部 4 处 `db.expire_all()`（JSON 更新 / Excel 扁平更新 / Excel v2 更新 / CSV 更新路径）

### 5. 导出/模板/解析器补「标准编码」
- JSON/Excel 导出与导入均携带 `code`；JSON 模板与 Excel 模板「标准信息」Sheet 补 `STD_CODE` 行
- parser `V2StandardParseResult` 新增 `code`，读取「标准编码」键

### 6. CRUD 与 schema
- `create_standard`/`update_standard` 支持 `code`，撞唯一约束 → 409（同导入路径一致）
- `DataStandardCreate/Update/Brief/Detail` 全部补 `code`

---

## 验收结果

### 端到端实测（Postgres，服务 45679，`s7_e2e.py` 37/37）
| 用例 | 断言 | 结果 |
|------|------|------|
| A. create 带 code | 201；code 回传；updated=False；version=1；tables=1；dictionaries=1 | ✅ |
| B. 同名 create 冲突 | 409（此前 500） | ✅ |
| C. update 按 code 匹配 | 201；同 standard_id；updated=True；version=2；fields=4；表名已替换；新字段已落库 | ✅ |
| D. update 未命中 | 404 | ✅ |
| E. upsert | 未命中→新建 201；命中→更新 version=2 | ✅ |
| F. Excel 回传 | 导出 200；改字段定义加 h_grade→mode=update 回传 201；version=3；字段=5；中文名 h_grade=高程等级 回环 | ✅ |
| G. 版本历史 | 2 条记录（每次 update 一条）；最新版本号=2；diff 含 h_grade（/versions/1/diff/2） | ✅ |
| H. create_standard 带 code | POST code 201；code 冲突 409 | ✅ |
| 清理 | 全部 DELETE 204 | ✅ |

### 验证脚本
- `s7_e2e.py`：37 断言全过（`%TEMP%\opencode\s7_e2e.py`）

---

## 遗留说明
- **update 回传为「整体重建」语义**：删子→重建（非增量合并），版本 diff 体现整份快照差异；增量合并如需留待后续立项
- **code 唯一性按 (api_key_id, code)**：跨 Key 标准编码可重复，匹配时遵循 scope_clause（各 Key 只见自己数据范围）
- **超管虚拟 Key 的 data_standards 写入**：DB 探针确认 `data_standards.api_key_id` 无外键（仅 `standard_dictionaries.api_key_id` 有外键，S6 已用 `owner_id` 处理）


# VCS v2.8 修订记录 — S8 版本对比明细增强

## 版本信息
- **版本**: v2.8 (S8)
- **日期**: 2026-08-05
- **类型**: 版本对比增强（三-1：diff 漂移噪音 + 路径可读性 + 统计口径 + 前端层级树）
- **影响范围**: `app/api/v1/data_standard_v2.py`、`app/schemas/data_standard_v2.py`、`app/frontend/index.html`
- **关联文档**: `docs/plan_v2_8.md` 阶段 C · S8

---

## 修订目标

1. **diff 级联漂移噪音**：`_deep_diff` 按列表下标对比，中间插入元素导致后续全部元素整段 modified 漂移（S7 验收时已见：加 1 字段 → 5+ 条噪音）
2. **路径不可读**：旧下标路径 `fields[0]/length` 无法定位实体，前端无法按层组→表→字段分组展示
3. **统计口径失真**：叶子路径计数把「1 个字段修改 3 个属性」算成 3 条
4. **前端 diff 表格不可用**：旧表格渲染无法表达层级；且 `_renderDiffTree` 存在必然崩溃的 bug

---

## 详细变更

### 1. `_deep_diff` 自然键对齐（`app/api/v1/data_standard_v2.py`）
- 列表分支改为按自然键配对（`_LIST_KEY_CHAIN`）：
  - `layer_groups` / `children` → `group_code`（回退 `id`）
  - `tables` → `table_code`（回退 `id`）
  - `fields` → `field_name`（回退 `id`）
  - `dictionaries` → `dict_name`（回退 `id`）
  - `items` → `item_code`
- 键相同配对递归对比；键只在单侧 → added/removed；不再按下标对齐
- 路径改为自然键：`layer_groups/BG/tables/CTRL_PT/fields/elevation`
- `_IGNORED_KEYS = {"id", "ordering"}`：重建时 UUID 重生成、ordering 由位置推导，均非内容变更
- `_compute_diff` 剔 `standard/version` 噪音（版本号递增是版本记录本身而非内容变更）

### 2. 实体级统计 + 中文摘要 + 中文名映射
- `_diff_entity_stats(diff)`：取每条路径最内层实体（`_innermost_entity`）去重计数 → `{类型: {added/removed/modified: 数}}`（字段改 3 属性只算 1 条修改）
- `_diff_summary(stats)`：生成中文摘要行，如「新增：字段 1、字典项 1　·　修改：字段 1」
- `_collect_diff_labels(old, new)`：收集实体自然键 → 中文名（层组 `group_code→group_name`、表 `table_code→table_name`、字段 `field_name→field_alias`、字典 `dict_name→dict_name`、字典项 `item_code→item_name`），前后快照合并（保证仅出现在新快照的实体也有名）
- `_build_standard_snapshot` 字段 dict 补 `field_alias`（此前缺失导致中文名无源可依；两版同值不产生 diff 噪音，别名变更会正常显示为修改）
- `VersionDiff` schema 新增 `summary` / `stats` / `labels`（默认空，向后兼容）

### 3. 前端层级折叠树（`app/frontend/index.html`）
- `v2ShowDiff`：实体级统计徽标（+N 新增 / -N 删除 / ~N 修改）+ 变色摘要行（新增绿/修改黄/删除红）+ 三个分组区块（➕ 新增 / ➖ 删除 / ✏️ 修改）
- `_renderDiffTree(entries, color, labels)`：按路径段建树——实体类型段（`_DT_ICON`/`_DT_LABEL` 图标+中文类型）与名称段组成折叠节点（`<details>`，≤6 子节点默认展开），属性段为子节点，路径终点挂值；实体节点显示**中文名优先 + 编码灰显**（如 `🔤 字段 高程 elevation`）；修改节点属性行显示 `旧 → 新`（如 `length` `10 → 12`）；删旧表格渲染
- 【**顺带修复真实 bug**】`_renderDiffTree` 收尾 `return render(root)` 传错容器：`render` 的入参是节点对象（其 key 为实体名，value 为带 `children` 的节点），传 `root` 自身会让首层迭代 `n = root.children` → `render(n.children)` = `render(undefined)` → `Object.entries(undefined)` 抛 `Cannot convert undefined or null to object`（**任何 diff 必然崩溃**，旧表格渲染路径未调用该函数故 E2E 未暴露）；改为 `return render(root.children)`

---

## 验收结果

### 单元测试（`s8_unit.py` 10/10，直接 import 后端函数）
- 中间插入 elevation 字段：仅 1 条 added，`pt_id/pt_name` 无任何级联漂移；`pt_type/length` 恰好 1 条 modified `{old:10, new:12}`；字典项 RTS added；`standard/version` 噪音剔除；stats 字段 added=1/modified=1、items added=1；summary「新增：字段 1、字典项 1」

### 端到端实测（Postgres，服务 45679，`s8_e2e.py` 27/27）
| 用例 | 断言 | 结果 |
|------|------|------|
| A. publish 基线 → update 插字段/改长度/加字典项/改几何 | 201；版本记录=2（v1 基线、v2 迭代） | ✅ |
| B. diff v1→v2 路径 | 无下标路径（无 `[0]`/`[1]`）；无级联噪音；路径可读 `layer_groups/BG/tables/CTRL_PT/fields/elevation` | ✅ |
| C. 几何修改 | `geometry_type` modified 正确 | ✅ |
| D. 噪音剔除 | `standard/version` 不在 modified；无 `id`/`ordering` 噪音 | ✅ |
| E. stats/summary | stats 字段 added=1 modified=1；summary 含「新增：字段 1、字典项 1」 | ✅ |
| F. 再 update 加表 | 整表新增路径可读；原表无级联噪音；stats tables added=1；整表新增不计字段 | ✅ |
| 清理 | 全部 DELETE 204 | ✅ |

### 前端浏览器级验证（Playwright + Chromium，`s8_ui.js` 12/12）
- 版本列表勾选 v1/v2 → 对比 → 弹窗标题「📊 版本对比: v1 → v2」
- 层级树按 层组→表→字段/字典→字典项 分组折叠渲染
- 中文名 + 灰显编码：`🗂 层组 基础地理要素 BG`、`📄 表 控制点 CTRL_PT`、`🔤 字段 高程 elevation`、`📖 字典 控制点类型`、`🔢 字典项 全站仪 RTS`
- 修改显示旧→新：`length` `10 → 12`；统计徽标 +2 新增 / ~1 修改；摘要行「新增：字段 1、字典项 1　·　修改：字段 1」
- 快照：`%TEMP%\opencode\s8_diff_modal.png`

### 回归
- S7 `s7_e2e.py` 37/37 全过（diff 语义变更未破坏 S7 断言）
- `s8_unit.py` 单测 10/10

### 验证脚本
- `s8_unit.py`（单元）、`s8_e2e.py`（端到端 27 断言）、`s8_ui.js`（Playwright UI 12 断言）、`s7_e2e.py`（回归 37 断言）

---

## 遗留说明
- **旧版本快照无 `field_alias`**：本修订前发布的版本快照字段 dict 缺该键，与新快照 diff 时字段级会显示 `field_alias` added（开发库无存量生产数据，重建标准即可；如遇历史版本对比噪音可忽略或重建基线版本）
- **统计口径为「最内层实体去重」**：整表新增只计 1 条表（字段含于表内）；如需「表+字段全量展开计数」可另配口径
- **labels 仅在 diff 端点返回**：列表/详情接口不含该映射，前端树渲染依赖 diff 载荷；如需其他场景复用可抽公共函数

# VCS v2.8 修订记录 — S9 层组多分类 + 移动/复制修复（方案 A：表多分类）

## 版本信息
- **版本**: v2.8 (S9)
- **日期**: 2026-08-06
- **类型**: 标准模型增强——表归属多分类（M2M 关联表 + 唯一主归属）、层组移动/复制/删除规则修复（B1/B2/B3/B5）、嵌套树回滚修复
- **影响范围**: `app/api/v1/data_standard_v2.py`、`app/models/standard_layer_group_table_link.py`（新增）、`app/models/standard_layer_group.py`、`app/models/standard_table.py`、`app/schemas/data_standard_v2.py`、`alembic/versions/0007_layer_group_table_links.py`（新增）、`app/frontend/index.html`
- **关联文档**: `docs/plan_v2_8.md` 阶段 C · S9

---

## 修订目标

1. **表归属单一（S2-S8 遗留）**：表只能挂在单一层组，无法表达「同一张表同时出现在基础地理要素与专题层」的行业需求 → 采用**方案 A（表多分类）**：新增 `standard_layer_group_tables` 关联表，一表可挂多个层组，其中**唯一主归属**为 `standard_tables.layer_id` 真源
2. **B2 移动防环缺失**：把层组移动到自己的子孙节点下可形成环
3. **B3 跨标准移动未拦截**：前端/API 均可跨标准移动层组（语义错误，应为「复制到其他标准」）
4. **B1 跨标准复制不可用**：`copy_layer` 只能复制到自身标准下
5. **B5 删除无引用检查**：删除层组时其主归属表若被其他层组次归属引用，会留下脏引用
6. **嵌套层组回滚 500（顺带修复真实 bug）**：回滚嵌套树时 `standard_layer_groups_pkey` 唯一冲突（见下）

---

## 详细变更

### 1. 数据模型（新增 `app/models/standard_layer_group_table_link.py` + 迁移 0007）
- `StandardLayerGroupTableLink`：`layer_id`/`table_id`/`is_primary`/`ordering`，联合唯一约束 `(layer_id, table_id)`，两外键均 `ondelete="CASCADE"`
- `StandardLayerGroup.table_links`（relationship，`viewonly=True`，可排序）、`table_count`（property：主归属+次归属去重计数）；`StandardTable.table_links` 同理
- 迁移 0007 建表 + 索引 + 唯一约束 + **回填**：把现有每张表在其主层组下生成 `is_primary=True` 的关联行（存量数据无损升级）

### 2. 主归属唯一真源与全创建点注入（`app/api/v1/data_standard_v2.py`）
- 语义：主归属 = `standard_tables.layer_id` + 关联表 `is_primary=True` 行（两者互相同步）
- `_primary_table_link(table)` 辅助：建表时自动挂主归属关联行
- **全部 7 处建表点注入**：`create_table`、`_copy_layer_recursive`、`_restore_from_snapshot`、Excel 扁平导入、v2 children 导入、CSV 导入、JSON 导入
- `list_tables`（`GET /layers/{layer_id}/tables`）重写：经关联表 join 返回**本层组全部表（含次归属）**，`(link.ordering, table.ordering)` 排序，返回 `is_primary`；`list_layers` 层组树深载用 `_layer_tree_options(depth=MAX_LAYER_DEPTH)`（修复 3 层树 `ResponseValidationError: MissingGreenlet` 潜在 bug）

### 3. 移动/复制/删除规则修复
- **B2 防环**（`move_layer`）：沿目标父级祖先链上溯，命中自身 → 400「不能移动到自身或其子层组下」；深度校验含子树高度
- **B3 跨标准拦截**（`move_layer`）：目标标准 ≠ 当前标准 → 400「不能跨标准移动层组（如需跨标准请使用复制到其他标准）」
- **B1 跨标准复制**（`copy_layer`）：body 新增 `new_standard_id`；校验目标标准归属与目标父层组属于目标标准；深度校验含子树高度；`_copy_layer_recursive` 重写为 async 显式查询版（修复懒加载 `MissingGreenlet`）：同标准复制保留子树外次归属、跨标准复制丢弃无法映射的次归属
- **B5 删除守卫**（`delete_layer`）：`_find_shared_primary_tables` 查子树内主归属表是否被子树外层组次归属引用 → 400 并提示表名

### 4. 表归属 API（3 个新端点）
- `POST /layers/{layer_id}/tables/{table_id}/attach`：次挂载；`is_primary=True` 时旧主降级 + `table.layer_id` 同步转移；已存在的次归属**原位升级**（不 409）
- `DELETE /layers/{layer_id}/tables/{table_id}/attach`：摘挂；主归属摘挂 → 400「主归属层组不能摘挂」
- `GET /tables/{table_id}/layers`：表归属列表（`TableLayerLink{layer_group, is_primary, ordering}`，主在前 + ordering 排序）

### 5. 快照/回滚语义（含真实 bug 修复）
- 快照只保留主归属（次归属为「展示/查询型」关联，不进版本快照，回滚后以主归属重建——设计取舍，S9 e2e 实测）
- `_restore_from_snapshot` 回滚时对每张表重建 `is_primary=True` 关联行
- **【修复真实 bug】`_build_standard_snapshot` 嵌套层组重复输出**：`DataStandard.layer_groups` 关系返回标准**全部**层组行（含子层组），顶层数组同时输出「嵌套在父 children 里的子层组」与「带 parent_id 的扁平子层组」→ 快照中同一层组出现两次 → 回滚恢复时对同一 id 双插 → `standard_layer_groups_pkey` 唯一冲突（500）。修复：顶层仅输出 `parent_id is None` 的根层组，子层组经 `children` 递归。此前 S7/S8 回滚测试均为扁平层组故未暴露，嵌套导入标准首次回滚即触发

### 6. 前端（`app/frontend/index.html`）
- 表列表项主/次归属徽标（主=绿色「主」、次=橙色「次」）；表行新增 ⛓ 归属管理入口
- 移动对话框**移除目标标准下拉**（仅同标准，含提示文案）；复制对话框**新增目标标准下拉**（跨标准复制）+ 目标父层组按所选标准动态加载
- 表归属面板：选中表后字段区上方显示「🏷 归属层组」芯片（主绿色/次灰，点击跳转该层组）
- 归属管理面板（⛓）：当前归属列表（主归属不可摘挂、次归属可摘挂）+ 挂载下拉（已归属层组剔除）+「设为目标层组的主归属」勾选

---

## 验收结果

### 单元测试
- `s8_unit.py` 全量回归 ALL PASS（diff 语义未受快照根层组过滤影响）

### 端到端实测（Postgres，服务 45679）
- **`s9_e2e.py` 49/49**：A 建标准/层组树/表 → B 主归属自动建 → C B3 拦截 → D B2 防环 → E 次挂载/409 语义 → F 主归属转移 + layer_id 同步 → G 主归属摘挂 400 → H B5 守卫 → I 同标准复制保留次归属 → J 跨标准复制丢弃子树外次归属 → K 发布/回滚（次归属不进快照）→ L 清理
- **`s9_import_probe.py` 7/7**：JSON 导入嵌套树（BG→ELEV）两表均有唯一主归属 ✅；发布 v1 → 回滚 → 201 ✅（修复前 500）；回滚后主归属保留 ✅
- **回归**：S7 `s7_e2e.py` 37/37、S8 `s8_e2e.py` 27/27、`s8_unit.py` ALL PASS

### 前端浏览器级验证（Playwright + Chromium，`s9_front.py` 21/21）
- 建标准/层组 A/B/表 T1 → 主徽标显示；挂载 T1 到 B → A 列表「主」/B 列表「次」徽标正确
- 归属面板显示两个层组（房屋建筑★主 + 道路设施次），点击可跳转
- 移动对话框无目标标准下拉 + 含「仅限当前标准内」提示；复制对话框含目标标准 + 目标父层组下拉
- `node --check` JS 语法通过；控制台无 JS 错误、无意外 404
- 快照：`%TEMP%\opencode\s9_front_shot.png`、`s9_attach_shot.png`

### 验证脚本
- `s9_e2e.py`（端到端 49 断言）、`s9_import_probe.py`（导入+回滚 7 断言）、`s9_front.py`（Playwright UI 21 断言）、`s8_unit.py`/`s8_e2e.py`/`s7_e2e.py`（回归）

---

## 遗留说明
- **次归属不进版本快照**：回滚后次归属会丢失（按设计取舍）；如需「次归属也随版本走」，可在 `_build_standard_snapshot`/`_restore_from_snapshot` 增补关联快照
- **旧版本快照含重复层组**：S9 修复前发布的嵌套树快照（如经旧代码发布过）回滚时仍可能撞唯一冲突；开发库无存量，如遇可重建基线版本
- **移动/复制仅校验标准归属**：`copy_layer` 的 `new_parent_id` 若指向目标标准下不存在的层组会 400（安全拒绝）；不校验「父层组层级 ≥ MAX_LAYER_DEPTH」之外的业务约束，如需可扩展




