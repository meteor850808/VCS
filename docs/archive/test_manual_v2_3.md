# VCS v2.3 数据质检模块测试手册

## 测试环境

- Python 3.13
- PostgreSQL 16 + PostGIS 3.4
- Redis 3+（RESP2 兼容）
- pandas 3.0
- shapely 2.0+
- 浏览器: Chrome/Edge 最新版

## 前置条件

1. 服务已启动: `uvicorn app.main:app --host 127.0.0.1 --port 45678 --reload`
2. 数据库已迁移（含 stored_files / quality_reports 新字段）
3. 已创建有效的 API Key
4. 已导入至少一个 v2.2 数据标准（含层组/表/字段/字典）
5. 已上传至少一个矢量文件并入库

---

## 测试用例

### 1. 属性检查引擎（attribute_checker）

#### TC-1.1 标准加载
```
验证: load_standard_tables() 返回完整的标准定义
方法: 使用已导入的 v2.2 标准 ID 调用
预期: 返回 StandardTableDef 列表，每个含 table_name, geometry_type, fields[]
每个 field 含 dict_item_codes（字典值域）
```

#### TC-1.2 必要图层检查（A1）
```
验证: 标准定义的图层在数据中存在
方法: 对包含标准中所有图层的文件执行质检
预期: rule_code=31010001, status=pass, failed_count=0
```

#### TC-1.3 图层缺失（A1 fail）
```
验证: 标准定义的图层在数据中缺失
方法: 对缺少某个标准图层的文件执行质检
预期: rule_code=31010001, status=fail, failed_count>0, samples 含缺失图层名
```

#### TC-1.4 字段必填项检查（A4）
```
验证: is_required=true 的字段是否为空值
方法: 对含空必填字段的数据执行质检
预期: rule_code=31010004, status=fail, message 含字段名和空值数量
```

#### TC-1.5 值域控制（A5）
```
验证: 值是否在字典允许范围内
方法: 对含越界值的数据执行质检（如"用地类型"字段值为"ZZZ"）
预期: rule_code=31010005, status=fail, samples 含违规值
```

#### TC-1.6 值域通过（A5 pass）
```
验证: 所有值均在字典允许范围内
方法: 对标准数据执行质检
预期: rule_code=31010005, status=pass
```

#### TC-1.7 主键唯一性（A6）
```
验证: 主键字段值唯一
方法: 对含重复主键的数据执行质检
预期: rule_code=31010006, status=fail, message 含重复数量
```

#### TC-1.8 字段长度限制（A9）
```
验证: 字符串长度不超过标准定义
方法: 对含超长字符串的数据执行质检
预期: rule_code=31010009, status=fail, samples 含超长值
```

---

### 2. 拓扑检查引擎（topology_checker）

#### TC-2.1 空几何检查（T01）
```
验证: 检测几何为空的要素
方法: 对含空几何要素的数据执行质检
预期: rule_code=T01, status=fail, failed_count>0, feature_indices 非空
```

#### TC-2.2 极大面检查（T03）
```
验证: 检测面积超过 max_area 的面要素
方法: 设置较小的 max_area 值（如 0.001）
预期: rule_code=T03, status=fail
```

#### TC-2.3 极小面检查（T04）
```
验证: 检测面积小于 min_area 的面要素
方法: 设置较大的 min_area 值（如 100）
预期: rule_code=T04, status=fail
```

#### TC-2.4 微短线检查（T05）
```
验证: 检测长度小于 min_length 的线要素
方法: 设置较大的 min_length 值
预期: rule_code=T05, status=fail
```

#### TC-2.5 面重叠检查（T06）
```
验证: 检测重叠的面要素
方法: 对含重叠面的数据执行质检
预期: rule_code=T06, status=fail, pairs 含重叠要素对
```

#### TC-2.6 点重叠检查（T07）
```
验证: 检测重合的点要素
方法: 对含重合点的数据执行质检
预期: rule_code=T07, status=fail
```

#### TC-2.7 拓扑全部通过
```
验证: 无拓扑问题
方法: 对拓扑正确的数据执行质检
预期: 所有 T* 规则 status=pass
```

#### TC-2.8 规则可选关闭
```
验证: 通过 enabled_rules 选择性关闭规则
方法: topology_config.enabled_rules 只含 ["empty_geometry"]
预期: 仅 T01 执行，其他 T* 不在结果中
```

---

### 3. 五维评分

#### TC-3.1 完全合格数据
```
验证: 五维评分全部为 1.0
方法: 对完全符合标准且无拓扑问题的数据执行质检
预期: scores.completeness=1.0, accuracy=1.0, consistency=1.0, uniqueness=1.0, topology=1.0, overall=1.0
```

#### TC-3.2 必填项缺失影响完整性
```
验证: 必填项缺失降低 completeness 分数
方法: 对含缺失必填字段的数据执行质检
预期: scores.completeness < 1.0, overall < 1.0
```

#### TC-3.3 拓扑问题影响拓扑分
```
验证: 拓扑违规降低 topology 分数
方法: 对含面重叠的数据执行质检
预期: scores.topology < 1.0
```

#### TC-3.4 无标准时拓扑独立评分
```
验证: 仅拓扑检查时 topology 分数独立计算
方法: 不传 standard_id，仅配置拓扑规则
预期: scores.topology 正常计算，其他维度为 1.0
```

---

### 4. API 端点

#### TC-4.1 POST /quality/check（v2 接口）
```
POST /api/v1/quality/check?file_id=<id>
Headers: X-API-Key: <key>
Content-Type: application/json
Body: {"standard_id": "<std_id>"}

预期: 200, 返回 QualityCheckResponse（含 scores, attr_results, topo_results）
```

#### TC-4.2 缺少 standard_id
```
POST /api/v1/quality/check?file_id=<id>
Headers: X-API-Key: <key>
Body: {}

预期: 400, detail="请提供 standard_id"
```

#### TC-4.3 文件不存在
```
POST /api/v1/quality/check?file_id=nonexistent
Headers: X-API-Key: <key>

预期: 404, detail="文件不存在或无权限访问"
```

#### TC-4.4 POST /quality/check/upload
```
POST /api/v1/quality/check/upload
Headers: X-API-Key: <key>
Content-Type: multipart/form-data
Body: file=<.geojson>, data_standard_id=<std_id>

预期: 200, 返回 QualityReportDetail（含五维评分字段）
```

#### TC-4.5 POST /data/{file_id}/quality-check
```
POST /api/v1/data/<file_id>/quality-check
Headers: X-API-Key: <key>
Content-Type: multipart/form-data
Body: data_standard_id=<std_id>, standard_table_id=<tbl_id>

预期: 200, 返回 QualityReportDetail
```

#### TC-4.6 GET /quality（报告列表）
```
GET /api/v1/quality?page_size=10
Headers: X-API-Key: <key>

预期: 200, 返回 QualityReportBrief 列表
```

#### TC-4.7 GET /quality/{report_id}（报告详情）
```
GET /api/v1/quality/<report_id>
Headers: X-API-Key: <key>

预期: 200, 返回含 attribute_summary, topology_summary, score_* 字段
```

#### TC-4.8 GET /quality/{report_id}/export
```
GET /api/v1/quality/<report_id>/export?format=geojson
Headers: X-API-Key: <key>

预期: 200, Content-Type: application/geo+json
```

#### TC-4.9 GET /data-standards-v2/{id}/tables
```
GET /api/v1/data-standards-v2/<std_id>/tables
Headers: X-API-Key: <key>

预期: 200, 返回该标准下所有表的扁平列表
```

---

### 5. 标准管理 Bug 修复验证

#### TC-5.1 版本 diff 正常
```
前提: 标准至少发布过 2 个版本
方法: POST /data-standards-v2/{id}/diff/1/2

预期: 200, 返回对比结果（非 500 错误）
```

#### TC-5.2 版本回滚正常
```
前提: 标准至少发布过 1 个版本
方法: POST /data-standards-v2/{id}/rollback/1

预期: 200, 创建回滚版本
```

---

### 6. 前端测试

#### TC-6.1 质检配置面板加载
```
1. 打开前端页面，点击 "数据检查" 导航
2. 验证: 标准下拉加载 v2.2 标准列表
3. 验证: 13 项拓扑规则复选框正确显示
4. 验证: 已入库文件下拉加载文件列表
```

#### TC-6.2 标准→标准表级联
```
1. 选择一个数据标准
2. 验证: 标准表下拉自动加载该标准下的所有表
3. 切换到另一个标准
4. 验证: 标准表下拉更新为新标准的表
```

#### TC-6.3 上传文件质检
```
1. 拖拽上传 .geojson 文件到质检区
2. 选择关联标准
3. 点击 "对已入库文件质检"
4. 验证: 状态显示 "质检中..." → "检查完成"
5. 验证: 报告列表出现新报告
```

#### TC-6.4 质检结果展示
```
1. 点击报告列表中的报告
2. 验证: 展开详情显示五维评分卡片（5 个彩色百分比）
3. 验证: 属性检查汇总表显示规则码/名称/状态/失败数
4. 验证: 拓扑检查汇总表显示规则码/名称/状态
5. 验证: 下载按钮可用（HTML/JSON/GeoJSON/Excel）
```

#### TC-6.5 文件列表行内质检
```
1. 在数据管理页面，点击文件行的 "质检" 按钮
2. 验证: 行内显示 "质检中..." → 分数百分比
3. 验证: 分数使用五维综合分（非旧单分）
```

---

## 回归测试

### 版本管理功能
| 功能 | 端点 | 预期 |
|------|------|------|
| 版本发布 | POST /data-standards-v2/{id}/publish | 201 |
| 版本列表 | GET /data-standards-v2/{id}/versions | 200 |
| 版本对比 | POST /data-standards-v2/{id}/diff/{v1}/{v2} | 200 |
| 版本回滚 | POST /data-standards-v2/{id}/rollback/{v} | 200 |

### 数据标准 CRUD
| 功能 | 端点 | 预期 |
|------|------|------|
| 标准列表 | GET /data-standards-v2 | 200 |
| 标准详情 | GET /data-standards-v2/{id} | 200 |
| 标准表列表 | GET /data-standards-v2/{id}/tables | 200 |

### 文件管理
| 功能 | 端点 | 预期 |
|------|------|------|
| 文件上传 | POST /data/upload | 201 |
| 文件列表 | GET /data | 200 |
| 文件详情 | GET /data/{id} | 200 |
| 空间要素 | GET /data/{id}/features | 200 |

---

## 已知限制

1. **层名映射**: 上传文件的 `layer_name`（文件名）与标准表 `table_name` 匹配基于模糊匹配，极端情况可能误匹配
2. **大数据量**: 属性检查加载全部特征到内存 DataFrame，超过 100 万要素时内存压力较大
3. **异步拓扑**: PostGIS SQL 查询是异步的，但 shapely 计算在线程池中同步执行
4. **旧报告兼容**: v2.1 旧格式报告在前端展示时使用降级展示（仅显示旧字段）
5. **拓扑配置快照**: 报告存储了拓扑配置快照，但前端暂不支持从历史报告修改配置后重新执行
