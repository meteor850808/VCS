# VCS v2.9 数据字典展示拆分回归测试手册

## 测试环境

- Python 3.13（项目 venv）
- PostgreSQL 16 + PostGIS 3.4
- 服务端口: 8000（`空间质检` 项目）
- 浏览器: Chromium + playwright-core（headless）

## 前置条件

1. 服务已启动（uvicorn app.main:app），`/health` 正常
2. 已创建有效的 API Key（测试用 `ADMIN_INIT_KEY` 可作为超级管理员 X-API-Key）
3. 测试数据已清理（本手册执行后产生的新数据已删除）

> 注意：本手册所有断言值来自 v2.9 HTTP / 浏览器实测（2026-08-12），实际库状态可能随新增数据变化。

---

## 测试用例

### A. 后端 API 维度拆分（集成测试，脚本 `test_dict_split.py`）

#### TC-A1 标准端点创建标准字典
```
验证: 标准维度字典创建，scope 恒为 standard
方法:
  1. POST /api/v1/data-standards-v2 创建标准（name/category）
  2. POST /api/v1/data-standards-v2/{stdId}/dictionaries
     body: { dict_name, dict_code, scope: 'standard' }
断言:
  - 标准创建 201
  - 字典创建 201
  - 响应 scope = 'standard'
```

#### TC-A2 标准端点拒绝全局 scope
```
验证: 标准端点不允许创建 global 字典（引导走全局端点）
方法:
  1. POST /api/v1/data-standards-v2/{stdId}/dictionaries
     body: { dict_name, scope: 'global' }
断言:
  - 状态 400
  - 错误提示引导使用 POST /dictionaries
```

#### TC-A3 全局端点创建全局字典
```
验证: 全局字典 standard_id 恒为 NULL，scope 强制 global
方法:
  1. POST /api/v1/dictionaries
     body: { dict_name, dict_code, scope: 'global' }
断言:
  - 状态 201
  - 响应 scope = 'global'
  - 响应 standard_id = null
```

#### TC-A4 全局字典列表纯全局
```
验证: GET /dictionaries 只返回全局维度（不混入标准字典）
方法:
  1. 已存在标准字典 + 全局字典各 1
  2. GET /api/v1/dictionaries
断言:
  - 只含 scope='global' 且 standard_id 为 NULL 的字典
  - 不含任何标准字典
```

#### TC-A5 标准字典列表纯标准
```
验证: GET /{stdId}/dictionaries 只返回标准维度
方法:
  1. GET /api/v1/data-standards-v2/{stdId}/dictionaries
断言:
  - 只含 scope='standard' 的字典
  - 不含全局字典
```

#### TC-A6 share 转全局清归属
```
验证: 分享后字典脱离标准归属（不变量保持）
方法:
  1. PUT /api/v1/dictionaries/{id}/share
断言:
  - 状态 200
  - scope = 'global'
  - standard_id = null
```

#### TC-A7 更新 scope 一致性
```
验证: 转 global 清 standard_id；转 standard 无归属则 400
方法:
  1. PUT /api/v1/dictionaries/{id} { scope: 'global' }（原标准字典）
  2. 新建全局字典 → PUT { scope: 'standard' }
断言:
  - ① standard_id 清空为 null
  - ② 状态 400
```

#### TC-A8 全局/标准维度无重叠
```
验证: 两个维度互不串扰
方法:
  1. 分别创建全局字典 G1、标准字典 S1
  2. GET /dictionaries 与 GET /{stdId}/dictionaries 交叉核对
断言:
  - 全局列表只含 G1
  - 标准列表只含 S1
  - 两个列表元素无交集
```

### B. 前端浏览器级验证（Playwright + Chromium）

#### TC-B1 默认激活全局字典 tab
```
验证: 进入数据标准页默认展示全局字典维度
方法:
  1. 打开 /app/ → 点击导航「📋 数据标准」
断言:
  - #v2DictTabGlobal 具有 active class
  - #v2DictList 显示全局字典列表（或「暂无字典」）
```

#### TC-B2 全局字典独立加载
```
验证: 全局字典不依赖选中标准/表/字段
方法:
  1. 不选中任何标准（或选中标准后切到全局 tab）
断言:
  - 全局 tab 正常发起 GET /api/v1/dictionaries
  - 列表正常渲染
```

#### TC-B3 未选标准时标准字典提示
```
验证: 标准维度空态引导
方法:
  1. 不选中任何标准
  2. 点击「📋 标准字典」tab
断言:
  - tab 切换到 standard
  - 列表显示「请先在左侧选择数据标准」
```

#### TC-B4 UI 创建全局字典
```
验证: 全局 tab 创建流程（dialog: 名称 → 编码）
方法:
  1. 全局 tab 点击 ➕
  2. 输入名称「测试全局字典G」、编码「GLB001」
断言:
  - 列表出现「测试全局字典G」
  - badge 显示「共享」、编码 GLB001
```

#### TC-B5 选中标准后创建标准字典
```
验证: 标准 tab 创建流程归属当前标准
方法:
  1. API 建标准（或左侧创建）→ 点击选中
  2. 切到「📋 标准字典」tab → 点 ➕
  3. 输入名称「UI标准字典S」、编码「STD001」
断言:
  - 列表出现「UI标准字典S」
  - badge 显示「标准」、编码 STD001
```

#### TC-B6 维度隔离（核心回归）
```
验证: 两个 tab 内容互不串扰
方法:
  1. 已存在全局字典 G + 标准字典 S
  2. 切到「🌐 全局字典」→ 记录列表
  3. 切到「📋 标准字典」→ 记录列表
断言:
  - 全局 tab 只显示 G（不含 S）
  - 标准 tab 只显示 S（不含 G）
```

#### TC-B7 JS 错误检查
```
验证: 页面无控制台错误、无意外 404
方法:
  1. 全程监听 console error / pageerror / 网络失败
断言:
  - 0 个 JS 错误
  - 无意外 404
```

---

## 执行记录（2026-08-12 实测）

| 用例 | 断言 | 结果 |
|------|------|------|
| TC-A1 | 201 + scope=standard | ✅ |
| TC-A2 | 400 引导 | ✅ |
| TC-A3 | 201 + scope=global + standard_id=null | ✅ |
| TC-A4 | 纯全局 | ✅ |
| TC-A5 | 纯标准 | ✅ |
| TC-A6 | share 清 standard_id | ✅ |
| TC-A7 | 转 global 清归属 / 转 standard 无归属 400 | ✅ |
| TC-A8 | 维度无重叠 | ✅ |
| TC-B1 | 默认全局 tab 激活 | ✅ |
| TC-B2 | 全局独立加载 | ✅ |
| TC-B3 | 「请先在左侧选择数据标准」 | ✅ |
| TC-B4 | 全局字典创建 + 「共享」badge | ✅ |
| TC-B5 | 标准字典创建 + 「标准」badge | ✅ |
| TC-B6 | 维度隔离 | ✅ |
| TC-B7 | 0 JS 错误 | ✅ |

### B. scope_clause 修复回归（`auth_context.py`）

#### TC-B1 超级管理员 source-dictionaries 查询
```
验证: scope_clause() 返回 SQLAlchemy true() 后，| 运算符不再报错
前置: 超级管理员 API Key 已配置
方法:
  1. GET /api/v1/source-dictionaries
     Header: X-API-Key: <super_admin_key>
断言:
  - 状态码: 200
  - 响应体为列表
  - 无 TypeError / 500 错误
```

#### TC-B2 scope_clause 返回类型验证
```
验证: scope_clause() 对超级管理员返回 SQLAlchemy True_ 而非 Python bool
方法:
  1. from sqlalchemy import true
  2. from app.core.auth_context import AuthContext
  3. ctx = await AuthContext.create_super_admin(db)
  4. clause = ctx.scope_clause(SourceDictionary)
  5. isinstance(clause, type(true()))
断言:
  - clause 类型为 sqlalchemy.sql.elements.True_
  - clause 可与 BinaryExpression 使用 | 运算符
```

#### TC-B3 scope_clause 逗号分隔 AND 场景回归
```
验证: | 运算符修复后，逗号分隔 AND 场景不受影响
方法:
  1. GET /api/v1/data/{file_id}
     Header: X-API-Key: <super_admin_key>
  2. GET /api/v1/quality/{report_id}
     Header: X-API-Key: <super_admin_key>
  3. GET /api/v1/batch/{batch_id}
     Header: X-API-Key: <super_admin_key>
断言:
  - 各端点保持原状态码（200 或 404，不应出现 500）
```

### 验证脚本
- `test_dict_split.py`（后端集成 8 断言，`%TEMP%\opencode\`）
- `smoke_dict.js`（前端冒烟，Playwright）
- `e2e_dict.js`（前端端到端，Playwright，含清理）
- `tmp_scope_eval.py`（scope_clause 影响评估，临时脚本）

---

## 数据清理记录

- 测试标准、标准字典、全局字典全部删除（含级联 dictionary_items / standard_field_dict_links）
- 遗留开发库清洗：32 个测试标准及全部关联数据（86 sheets 字段 / 7 sheets / 6 字典 / 9 版本 / 54 标准字段 / 33 标准表 / 39 层组 / 1 孤儿质检规则）已删；外围 SET NULL 保留 1 源表 / 5 存储文件 / 109 质检报告
