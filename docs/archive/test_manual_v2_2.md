# VCS v2.2 数据标准功能测试手册

## 测试环境

- Python 3.11+
- PostgreSQL 15 + PostGIS 3.4
- Redis 3+（RESP2 兼容）
- 浏览器: Chrome/Edge 最新版

## 前置条件

1. 数据库已迁移到 v2.2:
   ```bash
   alembic upgrade head
   python scripts/migrate_data_standard_v2_2.py
   ```

2. 已创建有效的 API Key

---

## 测试用例

### 1. 数据标准 CRUD

#### TC-1.1 创建标准
```
POST /api/v1/data-standards-v2
Headers: X-API-Key: <key>
Body: {"name": "测试标准", "category": "custom", "description": "测试用标准"}
Expected: 201, 返回标准 ID
```

#### TC-1.2 查询标准列表
```
GET /api/v1/data-standards-v2?page_size=10
Headers: X-API-Key: <key>
Expected: 200, 返回分页列表
```

#### TC-1.3 获取标准详情
```
GET /api/v1/data-standards-v2/{id}
Headers: X-API-Key: <key>
Expected: 200, 包含层组/表/字段/字典信息
```

#### TC-1.4 更新标准
```
PUT /api/v1/data-standards-v2/{id}
Headers: X-API-Key: <key>
Body: {"name": "更新后的标准名称"}
Expected: 200
```

#### TC-1.5 删除标准
```
DELETE /api/v1/data-standards-v2/{id}
Headers: X-API-Key: <key>
Expected: 204
```

---

### 2. 层组管理

#### TC-2.1 创建顶层组
```
POST /api/v1/data-standards-v2/{standard_id}/layers
Headers: X-API-Key: <key>
Body: {"group_name": "基础地理要素", "group_code": "BG"}
Expected: 201
```

#### TC-2.2 创建子层组
```
POST /api/v1/data-standards-v2/{standard_id}/layers
Headers: X-API-Key: <key>
Body: {"group_name": "控制点", "group_code": "CTRL", "parent_id": "<layer_id>"}
Expected: 201
```

#### TC-2.3 获取层组树
```
GET /api/v1/data-standards-v2/{standard_id}/layers
Headers: X-API-Key: <key>
Expected: 200, 返回嵌套结构
```

#### TC-2.4 嵌套深度限制
```
尝试创建第 6 层子层组
Expected: 400, 错误信息 "层组嵌套深度不能超过 5 层"
```

#### TC-2.5 移动层组
```
POST /api/v1/layers/{layer_id}/move
Headers: X-API-Key: <key>
Body: {"new_parent_id": "<target_parent_id>"}
Expected: 200
```

#### TC-2.6 复制层组
```
POST /api/v1/layers/{layer_id}/copy
Headers: X-API-Key: <key>
Body: {"new_name": "控制点副本"}
Expected: 201
```

---

### 3. 数据表管理

#### TC-3.1 创建空间表
```
POST /api/v1/layers/{layer_id}/tables
Headers: X-API-Key: <key>
Body: {"table_name": "控制点", "table_code": "CTRL_PT", "table_type": "spatial", "geometry_type": "Point"}
Expected: 201
```

#### TC-3.2 创建属性表
```
POST /api/v1/layers/{layer_id}/tables
Headers: X-API-Key: <key>
Body: {"table_name": "属性表", "table_type": "attribute"}
Expected: 201
```

#### TC-3.3 获取表详情（含字段）
```
GET /api/v1/tables/{table_id}
Headers: X-API-Key: <key>
Expected: 200, 包含 fields 数组
```

#### TC-3.4 删除表
```
DELETE /api/v1/tables/{table_id}
Headers: X-API-Key: <key>
Expected: 204
```

---

### 4. 字段管理

#### TC-4.1 创建字段
```
POST /api/v1/tables/{table_id}/fields
Headers: X-API-Key: <key>
Body: {"field_name": "pt_id", "field_alias": "点号", "data_type": "string", "length": 20, "is_primary_key": true}
Expected: 201
```

#### TC-4.2 字段排序
```
POST /api/v1/tables/{table_id}/fields/reorder
Headers: X-API-Key: <key>
Body: {"field_ids": ["<id1>", "<id2>", "<id3>"]}
Expected: 200
```

#### TC-4.3 删除字段
```
DELETE /api/v1/fields/{field_id}
Headers: X-API-Key: <key>
Expected: 204
```

---

### 5. 数据字典

#### TC-5.1 创建标准字典
```
POST /api/v1/data-standards-v2/{standard_id}/dictionaries
Headers: X-API-Key: <key>
Body: {"dict_name": "控制点类型", "dict_code": "CTRL_TYPE", "scope": "standard"}
Expected: 201
```

#### TC-5.2 创建全局字典
```
POST /api/v1/data-standards-v2/{standard_id}/dictionaries
Headers: X-API-Key: <key>
Body: {"dict_name": "行政区划", "dict_code": "ADMIN_AREA", "scope": "global"}
Expected: 201
```

#### TC-5.3 查询全局字典
```
GET /api/v1/dictionaries
Headers: X-API-Key: <key>
Expected: 200, 包含所有 scope=global 的字典
```

#### TC-5.4 添加字典条目
```
POST /api/v1/dictionaries/{dict_id}/items
Headers: X-API-Key: <key>
Body: {"item_code": "GPS", "item_name": "GPS控制点"}
Expected: 201
```

#### TC-5.5 设为全局共享
```
POST /api/v1/dictionaries/{dict_id}/share
Headers: X-API-Key: <key>
Body: {"is_global": true}
Expected: 200
```

---

### 6. 字段-字典关联

#### TC-6.1 创建关联
```
POST /api/v1/fields/{field_id}/dict-links
Headers: X-API-Key: <key>
Body: {"dict_id": "<dict_id>", "value_mode": "strict"}
Expected: 201
```

#### TC-6.2 查询字段关联
```
GET /api/v1/fields/{field_id}/dict-links
Headers: X-API-Key: <key>
Expected: 200, 返回关联列表
```

---

### 7. 版本管理

#### TC-7.1 发布版本
```
POST /api/v1/data-standards-v2/{standard_id}/publish
Headers: X-API-Key: <key>
Body: {"snapshot_summary": "发布初始版本"}
Expected: 201, 返回版本号
```

#### TC-7.2 查询版本历史
```
GET /api/v1/data-standards-v2/{standard_id}/versions
Headers: X-API-Key: <key>
Expected: 200, 按版本号降序
```

#### TC-7.3 版本回滚
```
POST /api/v1/data-standards-v2/{standard_id}/rollback/1
Headers: X-API-Key: <key>
Expected: 200
```

---

### 8. 多格式导入

#### TC-8.1 JSON 导入
```
POST /api/v1/data-standards-v2/import/json
Headers: X-API-Key: <key>
Body: {"name": "导入标准", "layer_groups": [...], "dictionaries": [...]}
Expected: 201, 返回导入结果
```

#### TC-8.2 Excel 导入
```
POST /api/v1/data-standards-v2/import/excel
Headers: X-API-Key: <key>
Body: FormData(file=<.xlsx>, name="导入标准")
Expected: 201
```

#### TC-8.3 CSV 导入
```
POST /api/v1/data-standards-v2/import/csv
Headers: X-API-Key: <key>
Body: FormData(files=<.csv files>, name="导入标准")
Expected: 201
```

#### TC-8.4 模板下载
```
GET /api/v1/data-standards-v2/template/json → JSON 模板
GET /api/v1/data-standards-v2/template/excel → Excel 模板
GET /api/v1/data-standards-v2/template/csv → ZIP 压缩包
Expected: 200, 正确文件格式
```

#### TC-8.5 导出标准
```
POST /api/v1/data-standards-v2/{id}/export?format=json
Headers: X-API-Key: <key>
Expected: 200, JSON 格式导出
```

---

### 9. 前端测试

#### TC-9.1 标准列表加载
1. 打开前端页面
2. 点击 "数据标准" 导航
3. 验证标准列表正确显示

#### TC-9.2 标准选择
1. 点击列表中的标准
2. 验证右侧层组树、字典、版本正确加载

#### TC-9.3 层组操作
1. 点击 "➕" 新建层组
2. 验证层组出现在树中
3. 点击层组，验证右侧显示数据表
4. 点击 "🗑" 删除层组

#### TC-9.4 表和字段
1. 选择层组，点击 "➕ 新建表"
2. 选择表，验证字段列表加载
3. 点击 "➕ 新增字段"
4. 验证字段出现在列表中

#### TC-9.5 导入功能
1. 点击 "⬆ 导入"
2. 切换 JSON / Excel / CSV 标签
3. 填写数据/选择文件
4. 点击导入按钮
5. 验证导入成功提示

#### TC-9.6 版本发布
1. 选择标准
2. 点击 "📦 发布版本"
3. 输入版本说明
4. 验证版本历史更新

---

## 回归测试

### 已有功能验证

| 功能 | 端点 | 预期 |
|------|------|------|
| 文件上传 | POST /api/v1/data/upload | 201 |
| 任务创建 | POST /api/v1/convert | 201 |
| 任务状态 | GET /api/v1/convert/{id}/status | 200 |
| 数据管理 | GET /api/v1/data | 200 |
| 质检报告 | GET /api/v1/quality/reports | 200 |
| API 密钥 | GET /api/v1/admin/apikeys | 200 |

---

## 已知问题

1. 字段编辑功能（v2EditField）前端仅显示 toast 提示，待后续实现完整编辑表单
2. 层组移动/复制功能 API 已实现，前端暂无操作入口
3. 字段-字典关联的前端操作入口待补充
