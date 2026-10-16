# VCS v2.4 数据上传模块重建测试手册

## 测试环境

- Python 3.13
- PostgreSQL 16 + PostGIS 3.4
- Redis 3+（RESP2 兼容）
- 浏览器: Chrome/Edge 最新版
- 服务端口: 8000（`空间质检` 项目）

## 前置条件

1. 服务已启动: `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`
2. 数据库已迁移（含 stored_files 新字段 + quality_reports 补列）
3. 已创建有效的 API Key（测试用: `vcs_BWTaUCkn4M8obMyYv2uci_he4KLwyMycNdveu9sTkE4`）
4. 已导入至少一个 v2.2 数据标准（含层组/表/字段/字典）
5. 浏览器 localStorage 已设置正确的 API Key

---

## 测试用例

### 1. 格式检测（format_detection）

#### TC-1.1 GeoJSON 格式检测
```
验证: GeoJSON 文件正确检测格式、几何类型、CRS
方法: 上传 Polygon 类型的 GeoJSON 文件
预期: file_format=GeoJSON, geometry_type=Polygon, data_type=geometry
```

#### TC-1.2 Shapefile 格式检测
```
验证: SHP 文件正确检测几何类型
方法: 上传 Point 类型的 Shapefile (.shp + .dbf + .shx)
预期: file_format=SHP, geometry_type=Point, data_type=geometry
```

#### TC-1.3 OBJ 3D 检测
```
验证: OBJ 文件固定检测为 3d 类型
方法: 上传 .obj 文件
预期: file_format=OBJ, geometry_type=3d, data_type=geometry
```

#### TC-1.4 CSV 表格检测
```
验证: CSV 文件检测为 table 类型
方法: 上传 .csv 文件
预期: file_format=CSV, geometry_type=table, data_type=table
```

#### TC-1.5 BOM 处理
```
验证: 带 BOM 的 UTF-8 文件正确解析
方法: 上传带 BOM 头的 GeoJSON 文件
预期: 格式检测成功，不报 JSON 解析错误
```

#### TC-1.6 压缩包检测
```
验证: ZIP 压缩包正确检测为 archive 类型
方法: 上传 .zip 文件
预期: data_type=archive, is_archive=true
```

---

### 2. 上传 API（data/upload）

#### TC-2.1 基础上传（不关联标准）
```
验证: 文件上传成功，不关联任何标准
方法: POST /data/upload，不传 standard_id
预期:
  - 返回 201，包含 id, original_name, file_size
  - standard_id=null, data_standard_name=null
  - geometry_type 正确填充
```

#### TC-2.2 关联已有标准上传
```
验证: 上传时关联已有数据标准
方法: POST /data/upload，传入 standard_id
预期:
  - 返回 201
  - stored_files.standard_id = 传入的 standard_id
  - data_standard_name = 标准名称
```

#### TC-2.3 新增标准上传（auto_create_standard=true）
```
验证: 上传时自动创建新标准
方法: POST /data/upload，auto_create_standard=true，standard_name="测试标准"
预期:
  - 返回 201
  - data_standards 表新增一条记录
  - standard_layers / standard_tables / standard_fields 表新增对应记录
  - stored_files.standard_id 指向新标准
```

#### TC-2.4 在已有标准下新增图层
```
验证: 在已有标准下新增图层组
方法: POST /data/upload，standard_id=已有标准，auto_create_standard=true
预期:
  - 不新建 DataStandard
  - 在已有标准下新增层组 + 标准表 + 字段
```

#### TC-2.5 文件哈希去重
```
验证: 重复上传相同文件返回已有记录
方法: 上传同一文件两次
预期:
  - 第二次返回 message 含"哈希去重"
  - 返回已有文件的 id
```

#### TC-2.6 文件大小限制
```
验证: 超大文件被拒绝
方法: 上传超过 max_data_store_size_mb 的文件
预期: 返回 413 错误
```

---

### 3. 大文件切片上传

#### TC-3.1 切片上传 + 合并
```
验证: 大文件切片上传并合并
方法:
  1. POST /data/upload/chunk (chunk 0)
  2. POST /data/upload/chunk (chunk 1)
  3. POST /data/upload/merge
预期:
  - 合并后 stored_files 记录完整
  - file_size = 所有切片之和
  - 临时切片目录已清理
```

#### TC-3.2 取消上传
```
验证: 取消上传清理临时切片
方法:
  1. POST /data/upload/chunk (chunk 0)
  2. DELETE /data/upload/chunk
预期: 临时切片目录已清理
```

---

### 4. 数据类型筛选

#### TC-4.1 统计 API（by_geometry_type）
```
验证: 统计接口返回几何子类型分布
方法: GET /data/stats
预期: 响应包含 by_geometry_type 字段，值为各类型计数
```

#### TC-4.2 按 geometry_type 过滤
```
验证: 列表接口支持 geometry_type 过滤
方法: GET /data?geometry_type=Point
预期: 仅返回 geometry_type=Point 的文件
```

#### TC-4.3 复合格式过滤
```
验证: geometry_type 支持复合格式 "geometry:Point"
方法: GET /data?geometry_type=geometry:Point
预期: 仅返回 data_type=geometry 且 geometry_type=Point 的文件
```

#### TC-4.4 前端筛选
```
验证: 数据管理页筛选下拉正确过滤
方法:
  1. 打开数据管理页
  2. 点击统计卡片或筛选下拉
  3. 选择具体类型
预期: 文件列表仅显示对应类型的文件
```

---

### 5. 标准管理

#### TC-5.1 标准列表加载
```
验证: 标准列表正确加载并显示
方法: 打开标准管理页
预期:
  - 显示所有标准（按 API Key 隔离）
  - 每项显示名称、分类、状态、版本、描述
  - 每项右侧显示 ✏️ 编辑和 🗑️ 删除按钮
```

#### TC-5.2 编辑标准
```
验证: 编辑弹窗正确保存
方法:
  1. 点击标准项的 ✏️ 按钮
  2. 修改名称、描述、分类、状态
  3. 点击"保存"
预期:
  - 弹窗关闭
  - 列表刷新，显示更新后的信息
  - PUT /data-standards-v2/{id} 返回 200
```

#### TC-5.3 删除标准
```
验证: 删除标准并刷新列表
方法:
  1. 点击标准项的 🗑️ 按钮
  2. 确认删除
预期:
  - 确认对话框出现
  - 确认后标准从列表移除
  - DELETE /data-standards-v2/{id} 返回 204
```

#### TC-5.4 编辑弹窗取消
```
验证: 取消编辑不保存
方法:
  1. 点击 ✏️ 打开弹窗
  2. 修改内容
  3. 点击"取消"或点击背景
预期: 弹窗关闭，标准信息不变
```

#### TC-5.5 标准详情查看
```
验证: 点击标准项加载右侧详情
方法: 点击标准项（非按钮区域）
预期:
  - 右侧加载层组树、标准表、字段、字典
  - 标准项高亮显示
```

---

### 6. 上传配置面板

#### TC-6.1 三模式切换
```
验证: 三个 Radio 选项互斥切换
方法:
  1. 选择"不关联标准" → 子面板隐藏
  2. 选择"关联已有标准" → 显示标准/图层下拉
  3. 选择"新增标准" → 显示标准集/图层名/数据类型
预期: 每次切换仅显示对应子面板
```

#### TC-6.2 关联标准 → 图层层联
```
验证: 选择标准后自动加载图层列表
方法: 在"关联已有标准"模式下选择一个标准
预期:
  - 图层组下拉加载该标准下的层组
  - 选择图层后显示字段结构表
```

#### TC-6.3 新增标准 → 数据类型选择
```
验证: 数据类型选择正确传递
方法: 在"新增标准"模式下选择"📍点"
预期:
  - geometry_type=point 传递到后端
  - 后端 stored_files.geometry_type=Point
```

---

### 7. 字段结构（field_schema）

#### TC-7.1 GeoJSON 字段结构提取
```
验证: GeoJSON 文件提取正确的字段结构
方法: 上传含 name(string)、id(integer) 字段的 GeoJSON
预期: field_schema 包含正确的字段名、类型、采样值
```

#### TC-7.2 SHP 字段结构提取
```
验证: Shapefile 提取 dbf 字段结构
方法: 上传 .shp 文件
预期: field_schema 包含 .dbf 中的所有字段
```

---

## 测试数据

### 推荐测试文件
| 文件 | 格式 | 预期 geometry_type | 用途 |
|------|------|-------------------|------|
| test.geojson | GeoJSON | Polygon | 基础上传测试 |
| points.shp | SHP | Point | SHP 检测测试 |
| model.obj | OBJ | 3d | 3D 检测测试 |
| data.csv | CSV | table | 表格检测测试 |
| archive.zip | ZIP | archive | 压缩包测试 |
| bom.geojson | GeoJSON | Polygon | BOM 处理测试 |

### 测试 API Key
```
vcs_BWTaUCkn4M8obMyYv2uci_he4KLwyMycNdveu9sTkE4  (test-admin-key, 28 个标准)
vcs_6A7VLEc-h4rqwBylo2WeZU_d_-wkEglD8GPAcUSUPrs  (dev-key-2026-07-22, 0 标准)
```

---

## 已知问题

1. **端口冲突**: 确保仅运行 `空间质检` 项目（端口 8000），不要同时运行 `空间API服务`（端口 45678）
2. **API Key 吊销**: 浏览器 localStorage 中的 Key 可能已吊销，需检查 `is_active` 状态
3. **quality_reports 列缺失**: 已通过迁移 `0003c` 修复，但需确认迁移已执行
