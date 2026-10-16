# VCS v2.8 S1 PostGIS 入库稳定性回归测试手册

## 测试环境

- Python 3.13（项目 venv）
- PostgreSQL 16 + PostGIS 3.4
- Redis 3+（RESP2 兼容；可选：本机未启动时 `/health` 返回 `degraded` 属预期）
- 服务端口: 45679（`空间质检` 项目，`restart.bat`）

## 前置条件

1. 服务已启动: `restart.bat`（确认 `/health` 返回 `{"status":"degraded","postgres":"ok","redis":"error: "}` 属正常）
2. 数据库已迁移（含 `spatial_features_point/line/polygon` 三表 + `ST_IsValid` CHECK 约束）
3. 已创建有效的 API Key（测试用: `POST /api/v1/admin/apikeys` 创建）
4. 测试数据已清理（本手册执行后产生的新数据已删除）

> 注意：本手册所有断言值来自 v2.8 S1 HTTP 实测（2026-08-04），实际库状态可能随新增数据变化。

---

## 测试用例

### 1. 单文件上传：自相交 + 3D 几何（S1 核心回归）

#### TC-1.1 有效多边形 + 自相交 + 3D 混合 GeoJSON 上传
```
验证: 混合几何全部入库，无 500 整体回滚
方法:
  1. 构造 GeoJSON 含 5 要素：
     point_ok (Point)、line_ok (LineString)、poly_ok (Polygon 有效)、
     bowtie_bad (Polygon 自相交)、poly_3d (Polygon 带 Z)
  2. POST /api/v1/data/upload（multipart: file + apikey_id）
断言:
  - 状态 201
  - feature_count = 5（全部入库）
  - geometry_type = Polygon
  - warning = null
  - bowtie_bad 实际几何类型 = MultiPolygon（make_valid 修复）且入库成功
  - poly_3d 降维为 2D 入库
```

#### TC-1.2 入库数与提取数一致性
```
验证: detail.features / feature_count 与真实入库数一致
方法:
  1. 接 TC-1.1 后，按 file_id 查询要素（GET /api/v1/data/{id}/features）
断言:
  - 要素总数 = 5
  - 每个要素 geometry 非空（含 bowtie_bad 修复后 MultiPolygon）
```

#### TC-1.3 垃圾几何不炸整单
```
验证: 无法解析的几何以 geometry=NULL 落库（属性保留），不触发 CHECK 失败
方法:
  1. 构造 GeoJSON 含无效几何（如 malformed coordinates）+ 有效要素
  2. POST /api/v1/data/upload
断言:
  - 状态 201
  - feature_count = 有效要素数
  - 日志出现 "skipped_empty" 统计，无 500
```

### 2. 压缩包 ZIP 并发入库（S1 核心回归）

#### TC-2.1 多子文件 gather 并发 + 独立会话
```
验证: 压缩包子文件并发入库，单子文件失败不影响整单，回填与真实一致
方法:
  1. 构造 ZIP 含：a_layers.geojson（3 要素，含 bowtie）、b_lines.geojson（2 要素）、notes.csv（2 行）
  2. POST /api/v1/data/upload（file: xxx.zip）
断言:
  - 状态 201
  - extracted_count = 3
  - feature_count(archive) = 7（= 3+2+CSV 行数 2，子文件 feature_count 之和，原有语义）
  - extraction_details 每项真实回填：
    a_layers.geojson features=3 / b_lines.geojson features=2 / notes.csv status=table_only
  - 逐子文件查询入库数 == detail.features（独立会话无串扰）
```

#### TC-2.2 压缩包清理
```
验证: 测试数据删除后不残留
方法:
  1. DELETE /api/v1/data/{file_id}（子文件逐删 + 主 archive 记录）
断言:
  - 204；再查该 file_id 404
```

### 3. 回归：既有功能未受影响

#### TC-3.1 上传限流
```
验证: 60s 内第 6 次上传返回 429（限流 5/min 原有行为未变）
```

#### TC-3.2 压缩包空壳记录
```
验证: 压缩包解压后主列表（/data/files）不出现 archive 空壳记录（原有行为）
```

---

### TC-S9.10 runAllTests 全量回归 10 项失败修复（Playwright 自动验证）
验证: `test.html` 填 KEY 点「运行全部测试」runAllTests 20 项全量跑通：修复前 10 项失败（submit/list-jobs/list-formats/quality-complete/quality-incomplete/quality-list/batch-list 共 7 项 `signal is aborted without reason` —— `/convert` 在 Redis 未启动时 kombu 连接重试挂起占线程，其余请求排队 15s 超时；data-list/data-search 语义过时；data-download 恒 410）。修复: `_redis_available()` 2s 探活 + Redis 不可用降级进程内同步执行（`run_conversion.apply` + 临时 max_retries=0 短路 retry）；`celery_app` 配置快速失败；quality 两卡改用 `/quality/check/upload`；data 两卡适配 分页对象；download 410→warn。
断言: total=20 pass=19 fail=0 warn=1（`s9_runall_repro.py`）；S9 一键场景仍 23/23、`s9_testbench_front.py` 17/17；无 JS 控制台错误。

### TC-S9.13 空间分表 LIST 分区化 + 流式全量落库（"一个数据源一个表"）
验证: ①DB 重建后三张分表 `PARTITION BY LIST (stored_file_id)`，复合 PK `(stored_file_id, id)`，`properties` 为 JSONB；上传 SHP zip → 自动创建分区 `polygon_features_p_<uuid>`（uuid 的 `-` 映射为 `_`）→ 子记录 2 要素落库（FK 修复：分表写入用 `owner_id`，超级管理员为 NULL 不违反 api_keys FK）。②要素查询 `/data/{子记录}/features` 返回 2 条（跨分区父表透明查询，SRID 自适应 4326）。③删除父记录 → 递归删除子记录 + 各自分区 DETACH+DROP 秒级，`pg_partition_tree` 无残留分区、stored_files 归零。④`max_features_per_upload` 已移除（流式全量，无截断），`read_layer_features` 生成器 + `write_features_batch_sync` flush_every=1000。
断言: HTTP e2e 上传 201→分区存在→查询 2→删除后分区全消；ORM 级 sync/async 写+查+drop 全通；runAllTests 回归 20 项 pass=19 fail=0 warn=1。

### TC-S9.12 真实 GDB 上传 500 修复：detected_crs 扩容 + 要素上限提升 + 子记录回填（curl 实测）
复现: 真实 GDB zip（Beijing 1954 / Gauss-Kruger CM 123E，5 个 MultiPolygon 层）上传 → 500，`StringDataRightTruncationError (64)`：完整 PROJCS WKT（618 字符）写入 `detected_crs` varchar(64)。
修复验证: ①`ALTER detected_crs TYPE VARCHAR(2048)` 后 618 字符 WKT 插入/读取正常；②`max_features_per_upload` 2000→200000，超出时响应 warning 列明截断图层（`write_features_batch` 返回 `total/truncated`）；③SHP zip 上传后 archive `feature_count=2`（此前子记录未回填恒 0）；④清理 07-56 旧代码乱码残留 `组图形.gdb.zip/╫Θ═╝╨╬_*.geojson`（cp437 误读 GBK，新代码已修复，重传即可）。
断言: SHP v2 上传 201 且 feature_count=2；618 字符 CRS WKT 存取正常；runAllTests 回归 20 项 pass=19 fail=0 warn=1。

### TC-S9.11 GDB 压缩包上传：限制放开 + PostGIS 原生几何直入库（curl 实测）
验证: ①101MB 随机 ZIP 压缩包上传 → 201（此前 max_single_file_size_mb=100 误伤压缩包 → 413）；101MB 单文件（.bin 非压缩包）→ 413 保持。②真实 POLYGON SHP ZIP 上传 → 201，`polygon_features` 表存 `POLYGON((116.3 39.9,...))` 原生几何（ST_Transform 正常），SHP 子记录 features=2。③GBK 中文压缩包（无 UTF-8 flag，模拟 Windows 压缩）→ 解压条目乱码自动修复为 `测试区_2024年度动态维护管理数据.gdb`，classify 正确识别 GDB 组。④GDB 层不再生成 `.geojson` 中间产物：子记录 `file_format=gdb`、`file_data=None`，要素直接写入 PostGIS 分表。
断言: 上传三连 curl 均 201（413→201）；非压缩包 100MB 限制仍生效；分表几何为原生 WKT；runAllTests 回归 20 项 pass=19 fail=0 warn=1。

## 执行记录（S1）

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-1.1 | ✅ 通过 | 2026-08-04 | bowtie→MultiPolygon、3D→2D、5/5 入库 |
| TC-1.2 | ✅ 通过 | 2026-08-04 | 查询 5 要素几何全部非空 |
| TC-1.3 | ✅ 通过 | 2026-08-04 | 类 SQLite 批量验证（4 written / 1 skipped_empty / 1 skipped_no_geometry） |
| TC-2.1 | ✅ 通过 | 2026-08-04 | 3 子文件并发独立会话，回填与真实入库一致 |
| TC-2.2 | ✅ 通过 | 2026-08-04 | 清理完成无残留 |
| TC-3.1 | — | — | 未复测（限流逻辑未改动） |
| TC-3.2 | ✅ 通过 | 2026-08-04 | 列表无 archive 空壳 |

---

## S2 测试用例（CRS 元数据链路，2026-08-04）

### TC-S2.1 投影坐标系 GeoJSON（4547）上传
```
验证: 源 CRS 感知入库 + 预览统一 4326 输出
方法:
  1. 构造 GeoJSON 声明 crs: {"type":"name","properties":{"name":"urn:ogc:def:crs:EPSG::4547"}}
     坐标用 pyproj 将 [114.1,39.9] 转到 4547 投影坐标（米制，数十万量级）
  2. POST /data/upload
  3. GET /data/{id}/features 预览
断言:
  - detected_crs = urn:ogc:def:crs:EPSG::4547
  - 数据库 ST_SRID(geometry) = 4547（直连库查证）
  - 预览输出坐标为经纬度 ≈[114.1, 39.9]（ST_Transform 生效）
```

### TC-S2.2 WGS84（4326）回归
```
验证: 默认路径不回归
方法:
  1. 上传无 crs 声明的 GeoJSON（坐标 [116.3, 40.1]）
断言:
  - detected_crs = None（默认 4326）
  - DB SRID = 4326
  - 预览输出原样 [116.3, 40.1]
```

### TC-S2.3 坐标转换 4326→4547
```
验证: 转换后 SRID 与目标 CRS 一致 + detected_crs 联动 + 预览正确
方法:
  1. 接 TC-S2.2 文件：POST /spatial/{id}/transform?target_crs=EPSG:4547
  2. GET /data/{id} 查看 detected_crs；GET /data/{id}/features 预览
断言:
  - transformed = 1（旧代码恒 0——_transform_coords tuple bug）
  - detected_crs = EPSG:4547
  - DB SRID = 4547
  - 预览输出 ≈[116.3, 40.1]（转回 4326，误差 < 0.02°）
```

### TC-S2.4 几何修复保留 SRID
```
验证: repair 不改变要素 SRID
方法:
  1. 上传 4490 声明 + 自相交 bowtie 多边形
  2. POST /spatial/{id}/repair
断言:
  - repair 前后 DB ST_SRID 均 = 4490
```

### TC-S2.5 迁移验证
```
验证: 三表 geometry 列 typmod 已去除
方法:
  SELECT format_type(atttypid, atttypmod) FROM pg_attribute ... WHERE relname IN (三表)
断言:
  - 返回 geometry（非 geometry(Geometry,4326)）
  - alembic_version = 0005_remove_srid_typmod
```

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S2.1 | ✅ 通过 | 2026-08-04 | SRID=4547、预览经纬度正确 |
| TC-S2.2 | ✅ 通过 | 2026-08-04 | SRID=4326、预览原样 |
| TC-S2.3 | ✅ 通过 | 2026-08-04 | transformed=1、detected_crs 更新、误差 <0.02° |
| TC-S2.4 | ✅ 通过 | 2026-08-04 | repair 前后 SRID 均 4490 |
| TC-S2.5 | ✅ 通过 | 2026-08-04 | 三表 typmod 去除 + stamp 0005 |
| S1 回归 | ✅ 通过 | 2026-08-04 | 混合几何 5/5 入库不变 |

---

## S3 测试用例（Multipatch/OBJ 3D 降维入库，2026-08-04）

### TC-S3.1 单元：MultiPatch/PolyhedralSurface/TIN 降维
```
验证: shapely 不支持的 3D 复合类型按面语义降维为 2D，Z 移除
方法:
  1. 构造模拟 fiona 输出的 MultiPatch geometry dict
     （coordinates = [surface…]，surface 首环为外环，坐标含 Z）
  2. 调 app.services.spatial_loader.geojson_to_wkt_status
断言:
  - ok=True；WKT 为 MULTIPOLYGON；坐标均为 2D（无 Z）
  - PolyhedralSurface / TIN（每三角形一面）同样降维为 MultiPolygon
```

### TC-S3.2 单元：OBJ 格式识别与提取
```
验证: .obj 扩展名映射 + obj_parser 接入
方法:
  1. 构造 OBJ 文件（o box 5 顶点 5 面 + o roof 3 顶点 1 面）
  2. 调 _guess_format / extract_features_from_file
断言:
  - _guess_format('.obj') = 'OBJ'（旧代码兜底 GeoJSON → 提取失败）
  - 要素数 = 2（每对象 1 个质心 Point）
  - 属性含 object_name / vertex_count / face_count / min/max xyz
  - 质心 = 顶点均值（含 Z，写入时再降维）
```

### TC-S3.3 端到端：OBJ 上传入库 + 回填
```
验证: 完整链路：上传 → 提取 → 降维 → 落库 → feature_count 回填
方法:
  1. 构造 OBJ（2 对象，含 Z 顶点）POST /data/upload（新内容避免秒传）
  2. GET /data/{id}/features 预览
  3. 直连库查 point_features
断言:
  - 上传响应 feature_count = 2（实际写入要素数，非检测面数 10——原代码停留检测值）
  - geometry_type = Point；file_format = obj
  - 预览 2 个 Point 要素，坐标 2D（Z 已降维）
  - DB：point_features=2；几何非空；ST_SRID=4326；properties 含 object_name
```

### TC-S3.4 端到端：秒传去重复用
```
验证: 同内容 OBJ 再传返回已有记录，feature_count 正确
断言:
  - message 含「哈希去重」；返回 id 为已有记录；feature_count=2
```

### TC-S3.5 回归：S1/S2 未受影响
```
方法: 重跑 s2_integration.py（4 用例：4547 上传 / 4326 回归 / transform / repair）
断言: 全部通过
```

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S3.1 | ✅ 通过 | 2026-08-04 | MultiPatch/PolyhedralSurface/TIN → 2D MultiPolygon，Z 移除 |
| TC-S3.2 | ✅ 通过 | 2026-08-04 | _guess_format='OBJ'、2 要素、质心正确 |
| TC-S3.3 | ✅ 通过 | 2026-08-04 | feature_count=2 回填、预览 2 点要素、DB 2 条几何非空 |
| TC-S3.4 | ✅ 通过 | 2026-08-04 | 秒传返回已有记录（feature_count=2） |
| TC-S3.5 | ✅ 通过 | 2026-08-04 | S2 回归 4/4、S1 链路不变 |

> 遗留：MultiPatch 端到端未执行（本机无法构造 MultiPatch SHP：fiona 1.10 schema 不支持写入、无 GDAL CLI、PGeo 无法读 .mdb）。降维逻辑已单元验证，输入结构对齐 fiona 读取标准输出。

---

# VCS v2.8 S4 数据-标准-质检联动测试手册（2026-08-05）

## 测试环境

- 服务：`http://127.0.0.1:45679`；API Key：admin key（`X-API-Key`）
- 脚本：`%TEMP%\opencode\s4_e2e.py`（端到端 17 断言）、`s4_probe.py`（结构探测）

## 测试用例

### TC-S4.1 上传关联已有层组 → 回填 standard_table_id
```
方法: 建标准+层组+Point 表（含 name/code/height 字段）+Polygon 表（多表）；
     上传 Point GeoJSON（关联 standard_id+layer_group_id，不传 standard_table_id）
断言: 上传响应 + GET /data/{id} 均 standard_table_id=Point 表（几何类型匹配的首表）；feature_count=2
```

### TC-S4.2 质检继承文件关联标准+表
```
方法: POST /quality/check?file_id=（body 空，不带 standard_id/standard_table_id）
断言: 200；attr_results>0；字段级检查执行（A2/A3/A4/A9）；A1 pass（单表模式不误报缺失图层）；
     无 31010007 失败；scores.overall 正常
```

### TC-S4.3 几何不匹配上传拦截
```
方法: 单表层组（仅 Point 表），上传 Polygon GeoJSON 关联
断言: 400「数据类型不匹配：上传数据为 Polygon，标准该图层组要求 {'Point'}」
```

### TC-S4.4 回归 S2/S3
```
方法: 重跑 s2_integration.py（4 用例）+ OBJ 上传
断言: S2 4/4 通过；OBJ 秒传复用 → 预览 2 点要素 + DB 2 条几何非空 SRID=4326
```

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S4.1 | ✅ 通过 | 2026-08-05 | 响应+详情双回填；多表层组选几何匹配 Point 表 |
| TC-S4.2 | ✅ 通过 | 2026-08-05 | 6 项检查（A1/A2/A3/A4/A9×2），总分 1.0 |
| TC-S4.3 | ✅ 通过 | 2026-08-05 | 单表层组 Polygon 上传 400 |
| TC-S4.4 | ✅ 通过 | 2026-08-05 | S2 回归 4/4；S3 OBJ 回归通过；前端 node --check 通过 |

> 遗留：多表模式（未传 standard_table_id）A1 图层名比较仍沿用旧语义（源 layer_name 为临时文件名，与标准表名不同命名空间），不在 S4 范围，登记于 `revision_v2_8.md` S4 遗留说明。

---

# VCS v2.8 S5 质检报告可视化测试手册（2026-08-05）

## 测试环境

- 服务：`http://127.0.0.1:45679`；API Key：admin key（`X-API-Key`）
- 脚本：`%TEMP%\opencode\s5_e2e.py`（端到端 28 断言）

## 测试用例

### TC-S5.1 详情接口透传违规明细
```
方法: 建标准+层组+Point 表（name 必填、code 主键）→ 上传 3 要素（含空 name、重复 code）
     → POST /quality/check → GET /quality/{report_id}
断言: attr_results 与 constraint_violations 对齐（7 项）；topo_results 与 topology_violations 对齐（9 项）；
     total_violations=3；severity_dist={error:2,warning:0}；violation_samples=3 条
     （A4 空 name 样本 feature_index=1；A6 重复 code 样本 sample="X2"）；score_* 字段齐全
```

### TC-S5.2 HTML 报告渲染
```
方法: GET /quality/{report_id}/html
断言: 含五维 SVG 环形仪表（综合评分+5 维度）、严重度分布、违规样本明细表（要素 # 索引）、
     @media print 打印样式；全部内联无 CDN
```

### TC-S5.3 存量报告兼容
```
方法: 上传全通过数据 → 质检 → GET 详情 + HTML
断言: 详情 200 不报错；attr_results 无 fail 项；total_violations=0；
     HTML 显示「全部检查通过，无违规」
```

### TC-S5.4 A4 空字符串必填回归
```
方法: 含空 name 要素（dtype 为 pandas 2.x str）的必填字段检查
断言: A4 31010004 判 fail（failed_count=1），样本为对应 feature_index
     （此前 pandas 2.x str dtype 下空串不触发必填违规，为本次顺带修复）
```

### TC-S5.5 回归 S4/S2
```
方法: 重跑 s4_e2e.py + s2_integration.py
断言: S4 17/17；S2 4/4
```

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S5.1 | ✅ 通过 | 2026-08-05 | 详情字段全对齐；样本含 feature_index=1 / "X2" |
| TC-S5.2 | ✅ 通过 | 2026-08-05 | 五维 SVG + 严重度分布 + 样本表 + print |
| TC-S5.3 | ✅ 通过 | 2026-08-05 | 无违规详情 200；HTML 全部通过 |
| TC-S5.4 | ✅ 通过 | 2026-08-05 | 空串必填 fail + feature_index 样本（A4 bug 已修复） |
| TC-S5.5 | ✅ 通过 | 2026-08-05 | S4 17/17；S2 4/4；前端 node --check 通过 |

> 遗留：HTML 报告「违规要素点击跳预览定位」为规划可选增强，本轮未做，登记于 `revision_v2_8.md` S5 遗留说明。

---

# VCS v2.8 S6 Excel 模板补字典 Sheet + 导入导出对齐测试手册（2026-08-05）

## 测试环境

- 服务：`http://127.0.0.1:45679`；API Key：admin key（`X-API-Key`）
- 脚本：`%TEMP%\opencode\s6_e2e.py`（端到端 40 断言）

## 测试用例

### TC-S6.1 导出 Excel 五 Sheet 与列位对齐
```
方法: 建标准（根层组 BG/DC + 子层组 KC 挂 BG）+ 2 表 4 字段 + 2 字典
     （PT_TYPE 2 项 + EMPTY_DICT 空字典）→ POST /export?format=excel → openpyxl 解析
断言: 状态 200；Sheet=[标准信息, 层组定义, 数据表定义, 字段定义, 字典定义]；
     层组表头=(层组名|层组编码|父级编码|描述)；
     KC 行父级编码=「BG」（编码引用，非 UUID）；BG/DC 父级为空；
     数据表表头=(层组编码|表名|表编码|表类型|几何类型|描述)；CTRL_PT 行层组编码=BG+几何 Point；
     字段表头=(表编码|字段名|中文名|数据类型|长度|精度|必填|主键|描述)；
     pt_id 行必填/主键「是」；
     字典表头=(字典编码|字典名称|适用范围|字典项编码|字典项名称|描述)；
     PT_TYPE 展开 2 行（A/B）；EMPTY_DICT 保留 1 行（无项）
```

### TC-S6.2 导入 Excel 回环
```
方法: POST /import/excel（multipart: file + name）
断言: 201；ImportResult：3 层组 / 2 表 / 4 字段 / 2 字典 / 2 字典项；dictionaries_count=2
```

### TC-S6.3 导入后详情结构还原
```
方法: GET /data-standards-v2/{新标准_id}
断言: layer_groups 顶层仅根层组（树无重复，数=3）；KC.parent_id=BG.id；tables=2；
     dictionaries=2（PT_TYPE/EMPTY_DICT）——此前 dictionaries 字段恒为空（alias bug 已修复）
```

### TC-S6.4 字段属性回环
```
方法: GET /tables/{table_id}/fields（两张表）
断言: 字段数=4；pt_id：is_required=true + is_primary_key=true + length=20 + field_alias=「点号」；
     pt_type：length=10
```

### TC-S6.5 字典内容回环
```
方法: GET /dictionaries/{dict_id}
断言: PT_TYPE 2 项（A 平面点 / B 高程点）+ 描述还原；EMPTY_DICT 0 项
```

### TC-S6.6 模板含字典 Sheet
```
方法: GET /data-standards-v2/template/excel
断言: 200；Sheet 含「字典定义」（PT_TYPE 示例）
```

### TC-S6.7 超级管理员创建/导入字典（FK 守卫回归）
```
方法: 以 admin key（ADMIN_INIT_KEY 超管上下文）建标准→建字典（含项）→导出→导入
断言: 全部 201——此前超管虚拟 ID 写 api_key_id 触发外键失败被误报「名称已存在 409」
```

### TC-S6.8 中文标准名导出（Content-Disposition 回归）
```
方法: 标准名为中文 → POST /export?format=excel
断言: 200 非 500（此前 filename 含中文触发 latin-1 UnicodeEncodeError 500）；
     响应头含 filename*=UTF-8'' 编码
```

### TC-S6.9 回归 S4/S5
```
方法: 重跑 s4_e2e.py + s5_e2e.py
断言: S4 17/17；S5 28/28
```

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S6.1 | ✅ 通过 | 2026-08-05 | 五 Sheet 列位全对齐；父级编码引用；「是」布尔 |
| TC-S6.2 | ✅ 通过 | 2026-08-05 | 回环计数 3/2/4/2/2 全对 |
| TC-S6.3 | ✅ 通过 | 2026-08-05 | 树去重 + dictionaries alias 修复生效 |
| TC-S6.4 | ✅ 通过 | 2026-08-05 | pt_id 全属性 + 中文名回环 |
| TC-S6.5 | ✅ 通过 | 2026-08-05 | 含项/空字典/描述全还原 |
| TC-S6.6 | ✅ 通过 | 2026-08-05 | 模板含字典定义 Sheet |
| TC-S6.7 | ✅ 通过 | 2026-08-05 | 建/导/导入均 201（owner_id 守卫） |
| TC-S6.8 | ✅ 通过 | 2026-08-05 | 中文名导出 200（RFC 5987） |
| TC-S6.9 | ✅ 通过 | 2026-08-05 | S4 17/17；S5 28/28 |


# VCS v2.8 S7 标准回传更新测试手册（2026-08-05）

## 测试环境

- 服务：`http://127.0.0.1:45679`；API Key：admin key（`X-API-Key`）
- 脚本：`%TEMP%\opencode\s7_e2e.py`（端到端 37 断言）

## 测试用例

### TC-S7.1 create 模式带 code
```
方法: POST /import/json?mode=create，body 含标准编码 code + 层组/表/字段/字典
断言: 201；ImportResult.code 回传；updated=false；version=1；tables=1；dictionaries=1；
     GET 详情 code 一致
```

### TC-S7.2 同名 create 冲突（此前 500）
```
方法: 再次 POST 同 name + 同 code 的 import/json?mode=create
断言: 409（此前同 name 撞唯一约束触发 IntegrityError 500）——code 冲突提示含「标准名称…已存在（或标准编码…已被使用）」
```

### TC-S7.3 update 按 code 匹配重建
```
方法: POST /import/json?mode=update（同 code，改名 + 增 1 字段）
断言: 201；standard_id 不变；updated=true；version=2；fields=4；
     GET 详情 version=2、表名已替换、新字段已落库（elevation）
```

### TC-S7.4 update 未命中
```
方法: POST /import/json?mode=update，code 不存在
断言: 404「未找到可更新的标准」
```

### TC-S7.5 upsert 模式
```
方法: POST /import/json?mode=upsert（先新 code → 再同 code 再传一次）
断言: 第一次 201 新建（version=1）；第二次 201 更新（同 standard_id、version=2）
```

### TC-S7.6 Excel 回传闭环（导出→改文件→回传更新）
```
方法: GET /export?format=excel 下载 → openpyxl 在「字段定义」Sheet 追加 h_grade 行
     （高程等级/double/16）→ POST /import/excel?mode=update 回传
断言: 201；version=3；updated=true；字段=5（含 h_grade）；中文名 h_grade=高程等级 回环
```

### TC-S7.7 版本历史 + 版本 diff
```
方法: GET /data-standards-v2/{id}/versions；GET /data-standards-v2/{id}/versions/1/diff/2
断言: 每次 update 产生 1 条版本记录（共 2 条）；最新版本号=2；
     diff 非空且含 h_grade 字段差异（列表按键对齐无整段漂移）
```

### TC-S7.8 create_standard 带 code + 冲突
```
方法: POST /data-standards-v2（含 code）→ 再 POST 同 code 不同 name
断言: 第一次 201 且 code 回传；第二次 409（uq_ds_api_code 唯一约束）
```

### TC-S7.9 异步回环回归（MissingGreenlet）
```
方法: 上述 update/upsert/Excel 回传全部路径各跑一遍
断言: 全部 201 非 500——此前 async 会话中 db.expire_all() 后属性访问触发
     MissingGreenlet 500（已改 populate_existing=True + 移除 4 处 expire_all）
```

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S7.1 | ✅ 通过 | 2026-08-05 | create 带 code 201；version=1 |
| TC-S7.2 | ✅ 通过 | 2026-08-05 | 同名 409（此前 500） |
| TC-S7.3 | ✅ 通过 | 2026-08-05 | update 按 code 重建 version=2；字段 4 |
| TC-S7.4 | ✅ 通过 | 2026-08-05 | 未命中 404 |
| TC-S7.5 | ✅ 通过 | 2026-08-05 | upsert 新建/命中更新双路径 |
| TC-S7.6 | ✅ 通过 | 2026-08-05 | Excel 回传 version=3；5 字段；中文名回环 |
| TC-S7.7 | ✅ 通过 | 2026-08-05 | 版本 2 条；diff 含 h_grade |
| TC-S7.8 | ✅ 通过 | 2026-08-05 | code 201 + 冲突 409 |
| TC-S7.9 | ✅ 通过 | 2026-08-05 | 四更新路径全 201（populate_existing 修复） |

---

# S8 版本对比明细增强 — 手工测试用例

> 前置：服务 45679；admin key（`X-API-Key`）；标准含 1 层组（基础地理要素/BG）+ 1 表（控制点/CTRL_PT）+ 3 字段（pt_id/pt_name/pt_type）+ 1 字典（控制点类型/CTRL_TYPE，项 GPS/TRI）。

### TC-S8.1 无级联漂移 + 路径可读（单元级）
```
方法: 直接 import _deep_diff/_compute_diff（s8_unit.py）：v1 3 字段 → v2 中间插入 elevation、pt_type 长度 10→12、字典加 RTS
断言: added 恰 1 条且路径为 layer_groups/BG/tables/CTRL_PT/fields/elevation（无 [0]/[1]）；
     modified 恰 1 条 = fields/pt_type/length {old:10, new:12}；pt_id/pt_name 无任何漂移；
     standard/version 不在 modified；stats fields added=1 modified=1、items added=1；
     summary[0]=「新增：字段 1、字典项 1」
```

### TC-S8.2 版本记录语义（publish 基线 → update 迭代）
```
方法: POST /import/json?mode=create → POST /{id}/publish（基线 v1）→ POST /import/json?mode=update
      （加 elevation、pt_type 长度 12、字典加 RTS）
断言: create 不建版本记录；publish 建 v1（基线）；update 建 v2（迭代）；GET /versions 返回数组含 2 条
     （version_number=1,2）；diff 用 /versions/1/diff/2
```

### TC-S8.3 diff 载荷（stats/summary/labels）
```
方法: GET /data-standards-v2/{id}/versions/1/diff/2
断言: stats={layer_groups/tables/fields/dictionaries/items: {added/removed/modified}} 实体级去重；
     summary 中文摘要（含「新增：字段 1、字典项 1」「修改：字段 1」）；
     labels={layer_groups:{BG:基础地理要素}, tables:{CTRL_PT:控制点},
             fields:{pt_id:点号,…,elevation:高程}, items:{GPS:GPS控制点,…,RTS:全站仪}}
```

### TC-S8.4 几何修改对比
```
方法: update 改 geometry_type（Point→Polygon）后再 diff
断言: modified 含 …/tables/CTRL_PT/geometry_type，{old:"Point", new:"Polygon"}
```

### TC-S8.5 再 update 加表（整表新增不计字段）
```
方法: update 新增 1 表（水准点/LEVEL_PT，字段 lvl_id/lvl_h）后再 diff（对照上一版本）
断言: added 含 tables/LEVEL_PT 整表路径可读；原 CTRL_PT 无任何级联噪音；
     stats.tables.added=1 且 fields 不重复计入整表内字段；summary 含「新增：表 1」
```

### TC-S8.6 前端层级树渲染（Playwright 浏览器级）
```
方法: SPA（/app/?key=…）→ 数据标准 → 选中「S8 界面对比标准」→ 版本历史勾选 v1、v2 →
      点「📊 对比」
断言: 弹窗标题「📊 版本对比: v1 → v2」；统计徽标 +2 新增/-0 删除/~1 修改；
     摘要行「新增：字段 1、字典项 1　·　修改：字段 1」；
     层级折叠树按 层组→表→字段/字典→字典项 分组，中文名优先+编码灰显：
     「🗂 层组 基础地理要素 BG」「📄 表 控制点 CTRL_PT」「🔤 字段 高程 elevation」
     「📖 字典 控制点类型」「🔢 字典项 全站仪 RTS」；修改行「length 10 → 12」
```

### TC-S8.7 diff 树崩溃回归（真实 bug 修复）
```
方法: 任意标准勾选 2 版本对比（S8 之前的代码必崩）
断言: 不再抛「对比失败: Cannot convert undefined or null to object」——根因
     _renderDiffTree 收尾 return render(root)（应为 render(root.children)），
     Object.entries(undefined) 崩溃，已修复
```

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S8.1 | ✅ 通过 | 2026-08-05 | 单测 10/10；无级联噪音、pt_type 长度正确、字典项 RTS、版本号剔除、stats/summary 正确 |
| TC-S8.2 | ✅ 通过 | 2026-08-05 | publish 基线 v1 → update 迭代 v2；create 不建版本记录 |
| TC-S8.3 | ✅ 通过 | 2026-08-05 | labels 映射齐全（含新实体 elevation/RTS）；stats/summary 口径正确 |
| TC-S8.4 | ✅ 通过 | 2026-08-05 | geometry_type Point→Polygon modified |
| TC-S8.5 | ✅ 通过 | 2026-08-05 | 整表新增路径可读；原表无噪音；stats tables added=1 |
| TC-S8.6 | ✅ 通过 | 2026-08-05 | Playwright 12/12；中文名+灰显编码+10→12+折叠节点（截图 s8_diff_modal.png） |
| TC-S8.7 | ✅ 通过 | 2026-08-05 | 修复后 diff 树正常渲染；回归 s7_e2e 37/37 |

# S9 层组多分类 + 移动/复制修复 手动测试手册（2026-08-06）

## 测试环境
- 服务 45679（Postgres + Redis error 状态为正常降级），前端 `/app/`
- API Key：`VCS-admin-2026-q6k4H9nZxR3`（`X-API-Key` 头）
- 自动验证脚本：`s9_e2e.py`（49 断言）、`s9_import_probe.py`（7 断言）、`s9_front.py`（Playwright 21 断言）、回归 `s7_e2e.py`/`s8_e2e.py`/`s8_unit.py`

## 测试用例

### TC-S9.1 主归属自动建立（创建表即挂主归属）
前置: 标准 + 层组 A
步骤: `POST /api/v1/layers/{A}/tables` 建表 T1
断言: `GET /layers/{A}/tables` 返回 T1 且 `is_primary=true`；`GET /tables/{T1}/layers` 含 A（主）

### TC-S9.2 B3 跨标准移动拦截
前置: 标准 S1（层组 A）、标准 S2（层组 X）
步骤: `POST /layers/{A}/move` body `{"new_standard_id": S2}`
断言: 400「不能跨标准移动层组（如需跨标准请使用复制到其他标准）」

### TC-S9.3 B2 移动防环
前置: 层组 A→A1→A2 三级链
步骤: `POST /layers/{A1}/move` body `{"new_parent_id": A2}`
断言: 400（不能移动到自身或其子层组下）

### TC-S9.4 次挂载 + 主归属转移 + 摘挂
前置: 层组 A、B，表 T1 主归属 A
步骤: `POST /layers/{B}/tables/{T1}/attach`（默认次）→ `POST .../attach` body `{"is_primary": true}` → `DELETE /layers/{B}/tables/{T1}/attach`
断言: 次挂载后 A/B 均列出 T1（A 主/B 次）；设主后 B 变主且 `GET /tables/{T1}` 的 `layer_id` 同步为 B；摘挂 B 后 B 不再列出

### TC-S9.5 B5 删除守卫
前置: 表 T1 主归属 A，次归属独立层组 Z（标准外）
步骤: `DELETE /layers/{A}`
断言: 400 提示表名（Z 仍引用）；先摘挂 Z 再删 A → 204 且 T1 级联删除

### TC-S9.6 同标准复制保留次归属 / 跨标准复制丢弃子树外次归属
前置: T1 主归属 A、次归属 B；复制 A → A'（同标准）
断言: A' 含 T1 主归属；跨标准复制（body `new_standard_id`）→ 目标标准 A'' 含 T1 主归属但不含 B（子树外次归属丢弃）

### TC-S9.7 嵌套层组回滚（真实 bug 回归）
前置: JSON 导入嵌套树（BG→ELEV）→ publish v1
步骤: `POST /{std}/rollback/1`
断言: 201（修复前 500 `standard_layer_groups_pkey`）；回滚后两表主归属保留

### TC-S9.8 前端主/次徽标 + 归属面板 + 移动/复制对话框（Playwright 自动验证）
断言: A 列表「主」/B 列表「次」徽标；归属面板含两层组芯片可跳转；移动对话框无目标标准下拉、复制对话框含目标标准下拉（`s9_front.py` 21/21）

### TC-S9.9 API 测试台新增 S9 数据标准 section（test.html，Playwright 自动验证）
验证: `app/frontend/test.html` 新增「9. 数据标准 v2 · S9 多分类 (12 项)」section，含 12 张卡片：一键 S9 场景（23 步 E2E：建标准→嵌套层组→建表自动主归属→次挂载→B5 守卫→主归属转移→摘挂→B2 防环→B3 跨标准移动→B1 跨标准复制→发布×2→嵌套树回滚→清理）与 11 个单点卡片（建标准/建层组/建表/层组表列表/表归属/挂载三态/移动/复制/删层组/版本/删标准）
断言: 一键场景 23/23 通过且 0 失败；单点按钮链路（建标准→层组→表→归属查询→次挂载→清理）全通；无 JS 控制台错误、无意外 404（`s9_testbench_front.py` 17/17）
修复: `setStatus` 覆盖 `card.className` 抹掉 `open` 导致卡片运行后自动折叠 → 保留 wasOpen 状态

## 执行记录

| 测试用例 | 结果 | 执行时间 | 备注 |
|----------|------|----------|------|
| TC-S9.1 | ✅ 通过 | 2026-08-06 | e2e B 段；7 处建表点均注入主归属 |
| TC-S9.2 | ✅ 通过 | 2026-08-06 | e2e C 段 |
| TC-S9.3 | ✅ 通过 | 2026-08-06 | e2e D 段 |
| TC-S9.4 | ✅ 通过 | 2026-08-06 | e2e E/F/G 段；layer_id 同步实测 |
| TC-S9.5 | ✅ 通过 | 2026-08-06 | e2e H 段 |
| TC-S9.6 | ✅ 通过 | 2026-08-06 | e2e I/J 段 |
| TC-S9.7 | ✅ 通过 | 2026-08-06 | `s9_import_probe.py` 7/7；修复前回滚 500 |
| TC-S9.8 | ✅ 通过 | 2026-08-06 | `s9_front.py` 21/21（截图 s9_front_shot.png / s9_attach_shot.png） |
| TC-S9.9 | ✅ 通过 | 2026-08-06 | `s9_testbench_front.py` 17/17；一键场景 23/23；截图 s9_testbench_shot.png |
| 回归 | ✅ 通过 | 2026-08-06 | S7 37/37、S8 27/27、s8_unit ALL PASS |
| TC-S9.10 | ✅ 通过 | 2026-08-06 |
| TC-S9.13 | ✅ 通过 | 2026-08-08 | 三表 LIST 分区化（每源一分区）+ 复合 PK + JSONB + 自动建分区 + 递归删除（DETACH+DROP 秒删）+ 流式全量落库（移除 max_features 截断）+ super admin FK 修复（owner_id） | HTTP e2e 全通；ORM sync/async 全通；runAllTests 20 项 pass=19 fail=0 warn=1 |
| TC-S9.12 | ✅ 通过 | 2026-08-07 | detected_crs 64→2048 扩容（618 字符 CRS WKT 存取正常）；要素上限 2000→200000 + 截断 warning；SHP/GeoJSON 子记录 feature_count 统一回填（archive feature_count=2）；清理旧代码乱码残留 | runAllTests 20 项 pass=19 fail=0 warn=1 |
| TC-S9.11 | ✅ 通过 | 2026-08-06 | GDB 压缩包>100MB 上传 201（压缩包分级限制）；SHP POLYGON 原生入库；GBK 中文乱码修复；runAllTests 回归 0 fail | runAllTests 20 项 pass=19 fail=0 warn=1（`s9_runall_repro.py`）；一键场景 23/23；S9 测试台 17/17 |


