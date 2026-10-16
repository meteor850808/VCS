# VCS v2.7 使用问题修复回归测试手册

## 测试环境

- Python 3.13
- PostgreSQL 16 + PostGIS 3.4
- Redis 3+（RESP2 兼容；可选：本机未启动时 `/health` 返回 `degraded` 属预期）
- 浏览器: Chrome/Edge 最新版
- 服务端口: 45679（`空间质检` 项目，`restart.bat`）

## 前置条件

1. 服务已启动: `restart.bat`（v2.7 版本：Start-Process 隐藏窗口，成功自动关窗）
2. 数据库已迁移（`0004_add_key_group` + 历史手动 DDL，见 `docs/environment-notes.md`）
3. 已创建有效的 API Key（测试用: 通过 `X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3` 调用 `POST /api/v1/admin/apikeys` 创建）
4. 浏览器 localStorage 已设置正确的 API Key

> 注意：本手册所有断言值来自 v2.7 HTTP 实测（2026-08-04），实际库状态可能随新增数据变化。

---

## 测试用例

### 1. 密钥生命周期（v2.7 新增）

#### TC-1.1 密钥重新生成
```
验证: 重新生成后新钥可用、旧钥立即失效
方法:
  POST /api/v1/admin/apikeys                → 创建 Key（记录 key）
  POST /api/v1/admin/apikeys/{id}/regenerate → 响应含新明文 key
预期:
  - 新 key 调用 GET /api/v1/jobs → 200
  - 旧 key 调用 GET /api/v1/jobs → 401
```

#### TC-1.2 吊销 → 激活
```
验证: 已吊销 Key 可重新启用
方法:
  DELETE /api/v1/admin/apikeys/{id}          → 吊销（软删除）
  GET /api/v1/jobs (用该 key)                → 401
  PUT /api/v1/admin/apikeys/{id}/activate    → 重新启用
  GET /api/v1/jobs (用该 key)                → 200
注意: /api/v1/capabilities 是公开端点不鉴权，鉴权验证必须用 /api/v1/jobs 等
```

#### TC-1.3 前端密钥表格按钮
```
验证: 密钥列表操作按钮按状态渲染
方法: 打开管理页 → 密钥列表
预期:
  - 已吊销 Key: ▶️启用 + 🔁重新生成（无永久删除）
  - 激活中 Key: 🗑️吊销 + 🔁重新生成
  - 重新生成后弹窗展示新明文一次
```

### 2. 字典校验（v2.7）

#### TC-2.1 重名 409
```
验证: 同名字典创建返回 409 中文提示
方法: 两次 POST 相同 name 的字典
预期: 第二次返回 HTTP 409，body 含「字典名称已存在」
```

#### TC-2.2 非法 scope 400
```
验证: 非 None/standard/global 的 scope 被拒绝
方法: POST 字典 scope="invalid"
预期: HTTP 400
```

### 3. Excel 4-sheet 模板导入（v2.7）

#### TC-3.1 标准导入
```
验证: 4-sheet 模板（标准信息/层组定义/数据表定义/字段定义）端到端导入
方法:
  1. 下载模板: GET /api/v1/data-standards-v2/template/excel
  2. 按 4-sheet 填写（如: 标准「E2E标准」/ 层组「道路」ROAD / 表「道路中心线」ROAD_LN spatial LineString / 字段「id」string required pk）
  3. POST /api/v1/data-standards-v2/import/excel (multipart)
预期:
  - 响应 recognized=true
  - 标准详情含 1 层组（道路/ROAD）/ 1 表（道路中心线/ROAD_LN/spatial/LineString）/ 1 字段（id/string/✅req/✅pk）
```

#### TC-3.2 旧扁平结构回退
```
验证: 旧单-sheet 扁平结构仍可导入（recognized=false 回退旧解析）
方法: 导入旧版扁平结构 Excel
预期: 成功导入，响应 recognized=false
```

#### TC-3.3 .xls 提示
```
验证: 旧格式 .xls 明确报错
方法: 导入 .xls 文件
预期: 报错提示另存为 .xlsx（openpyxl 不支持 .xls）
```

### 4. 空要素校验（v2.7）

#### TC-4.1 空要素文件 400
```
验证: 无图形要素文件执行质检被拒绝
方法: 上传零要素文件 → POST /api/v1/quality/check
预期: HTTP 400「该文件无图形要素」
```

#### TC-4.2 正常文件 200
```
验证: 正常文件质检不受影响
方法: 对含要素文件执行质检
预期: HTTP 200，报告 total_features 与库中 feature_count 一致
```

### 5. 前端通用修复（v2.7）

#### TC-5.1 esc 单引号转义
```
验证: 名称含单引号的条目操作按钮有效
方法: 创建含单引号的字典/字段名 → 点击其编辑/删除按钮
预期: 按钮正常触发（修复前 onclick 属性断裂无反应）
```

#### TC-5.2 fetch 超时
```
验证: 挂起请求 30s 自动超时不卡 UI
方法: DevTools Network 模拟 hang → 操作上传/查询
预期: 30s 内提示超时错误，页面可继续操作
```

#### TC-5.3 admKey 持久化
```
验证: 管理员密钥刷新页面不丢失
方法: 管理页输入 ADMIN_INIT_KEY → F5 刷新
预期: 输入框回填（localStorage vcs_admin_key），列表仍可加载
```

#### TC-5.4 qcTopoState 默认值
```
验证: t09/t10/t11 默认不勾选
方法: 进入质检页 → 检查配置拓扑组
预期: t09 面裂隙/t10 线连通/t11 不规则面 默认未勾选（on:false），其余已勾选
```

#### TC-5.5 拖拽拦截
```
验证: 拖文件到非上传区不打开文件
方法: 从文件管理器拖任意文件到页面空白区松开
预期: 浏览器不导航到该文件（无新页面/新 tab）
```

#### TC-5.6 压缩包空壳过滤
```
验证: 解压后列表不显示原压缩包记录
方法: 上传 ZIP/TAR → 解压完成 → 打开数据管理页
预期: 列表仅显示解压后的文件，无 is_archive 空壳记录
```

#### TC-5.7 字典增删改
```
验证: 字典列表/条目维护操作可用
方法: 字典列表点 ✏️重命名（PUT）/ 🗑️删除（confirm）；条目表点 ✏️编辑（PUT items）/ 🗑️删除
预期: 操作生效并刷新
```

#### TC-5.8 层组父级下拉
```
验证: 添加层组父级从下拉选择
方法: 标准 → 层组 → 添加层组
预期: 父级为 `<select>` 下拉（含递归缩进的候选层组），非手输 UUID
```

### 6. 后端健壮性（v2.7）

#### TC-6.1 /health 限时
```
验证: PG/Redis 不可达时 2s 内返回 degraded
方法: 停掉 PG（或 Redis）→ GET /health
预期: ≤2s 返回 {"status":"degraded",...}，不挂起
```

#### TC-6.2 下载中文文件名
```
验证: 中文名文件下载成功
方法: 上传中文名文件（如「测试.docx」）→ GET /api/v1/data/{id}/download
预期: 下载成功，响应头含 filename*=UTF-8''..., 保存为原名
```

#### TC-6.3 自检页超时
```
验证: 自检单请求超时不卡死整页
方法: 打开 http://localhost:45679/app/test.html → 运行全部测试
预期: 15s 超时项自动跳过，测试继续执行至完成
```

### 7. 一键重启（v2.7）

#### TC-7.1 restart.bat 自动关窗
```
验证: 重启成功自动关闭窗口
方法: 双击 restart.bat
预期:
  - 启动输出 SUCCESS，窗口自动关闭（无需按键）
  - uvicorn 为独立进程（不共享控制台）
  - server_err.log 含完整启动日志
```

#### TC-7.2 失败保留 pause
```
验证: 启动失败时保留窗口显示错误
方法: 停掉 PG → 运行 restart.bat
预期: 显示错误信息并 pause 等待按键
```

---

## 验证结果摘要（2026-08-04 实测）

| 用例 | 结果 |
|------|------|
| TC-1.1 重新生成（新钥可用/旧钥 401） | ✅ |
| TC-1.2 吊销→401→激活→恢复 | ✅ |
| TC-2.1 字典重名 409 | ✅ |
| TC-2.2 非法 scope 400 | ✅ |
| TC-3.1 Excel 4-sheet 端到端导入 | ✅ |
| TC-3.2 旧扁平结构回退 | ✅ |
| TC-4.1 空要素 400 | ✅ |
| TC-4.2 正常文件 200 | ✅ |
| 语法校验（py_compile 8 文件 + node 内联 script） | ✅ |
| TC-7.1 restart.bat 自动关窗 | ✅ |

## 数据清理记录

- 测试 Key（api-regen-test / api-revoke-test2/3/4 / dict-test-key）已 hard 删除
- 测试标准（E2E标准 / dict-test-std）已删除
- `e2e_std.xlsx` 测试文件写入临时目录（不入库）
