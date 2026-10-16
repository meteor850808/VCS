# VCS v2.6 质检页重构测试手册

## 测试环境

- Python 3.13
- PostgreSQL 16 + PostGIS 3.4
- Redis 3+（RESP2 兼容）
- 浏览器: Chrome/Edge 最新版
- 服务端口: 45679（`空间质检` 项目，`restart.bat`）

## 前置条件

1. 服务已启动: `restart.bat`（内部执行 `uvicorn app.main:app --host 0.0.0.0 --port 45679`）
2. 数据库已迁移（`0004_add_key_group` + 历史手动 DDL，见 `docs/environment-notes.md`）
3. 已创建有效的 API Key（测试用: 通过 `X-Admin-Init-Key: VCS-admin-2026-q6k4H9nZxR3` 调用 `POST /api/v1/admin/apikeys` 创建）
4. 已导入至少一个 v2.2 数据标准（含层组/表/字段/字典）
5. 浏览器 localStorage 已设置正确的 API Key

> 注意：本手册所有断言值来自 v2.6 E2E 实测（2026-07-31），实际库状态可能随新增数据变化。

---

## 测试用例

### 1. QC 页面加载（#page-quality）

#### TC-1.1 文件栏加载
```
验证: 质检页文件列表正确加载并计数
方法: 进入「质量检查」页面
预期:
  - #qcFileList 显示已上传文件列表（含要素数/字段数统计）
  - #qcFileCnt 计数 badge 与文件列表一致
  - 实测库: 46 个文件，badge=46
```

#### TC-1.2 标准选择器加载
```
验证: 标准选择器按 optgroup 分组加载
方法: 点击 #qcStd2 下拉框
预期:
  - 选项按自定义/自动生成/测试等分组（optgroup）
  - 实测库: 3 个 optgroup（custom/auto/测试），16 个选项
  - 切换页面后重进，选中标准保持恢复
```

#### TC-1.3 检查配置渲染
```
验证: 3 组检查配置正确渲染
方法: 观察检查配置区
预期:
  - 属性组: 9 项（31010001~31010009）
  - 拓扑组: 13 项（t01~t13），其中 8 项（t03~t09、t11）渲染阈值输入框
  - 规则组: 渲染规则库列表
  - 每组标题栏含全选/生成按钮，可折叠
```

#### TC-1.4 布局与字号
```
验证: 紧凑布局计算样式
方法: DevTools getComputedStyle 检查
预期:
  - 检查项容器: display:flex; flex-wrap:wrap; gap:3px 6px
  - 检查项胶囊: padding:2px 7px
  - 表格/表头/表体 12px、执行按钮 11px、全选按钮 10px、胶囊标签 12px
```

#### TC-1.5 报告表渲染
```
验证: 报告表正确渲染并分页
方法: 观察报告表
预期:
  - 6 列: 文件/标准/属性/拓扑/评分/时间
  - page_size=50（接口上限），库中报告多于 50 时仅显示最新 50 条
  - 实测库: 72 报告，报告表显示 50 行
  - 含过滤控件（按文件/标准筛选）
```

---

### 2. 标准选择联动

#### TC-2.1 选中标准
```
验证: 选择标准后 UI 联动
方法: 在 #qcStd2 选择某标准
预期:
  - 顶部显示标准 tag（可关闭）
  - 无标准提示文案隐藏
  - 属性组全选按钮可用（未选标准时点击提示「属性检查由标准驱动」）
```

#### TC-2.2 取消标准
```
验证: 关闭标准 tag 恢复无标准状态
方法: 点击标准 tag 的关闭按钮
预期:
  - 标准 tag 消失
  - 无标准提示文案重新显示
  - 属性组检查项不受影响（已勾选项保留）
```

#### TC-2.3 标准结构树
```
验证: 标准结构树渲染层组嵌套
方法: 选中标准后展开标准结构树（#qcStdTree）
预期:
  - 按 layer_groups 嵌套显示图层分组
  - 每层可勾选/折叠（qcTogTable）
```

---

### 3. 检查项交互

#### TC-3.1 全选/取消切换
```
验证: 全选按钮批量切换
方法: 分别点击属性组、拓扑组的全选按钮
预期:
  - 全部 22 项被勾选（属性 9 + 拓扑 13）
  - 再次点击全部取消
  - 各组计数同步更新
```

#### TC-3.2 阈值输入
```
验证: 8 个阈值输入框生效
方法: 修改 t03 极大面阈值为 5（km²）、t04 极小面为 0.1（m²）
预期:
  - 输入框 id 为 qc-tol-t03 / qc-tol-t04 等
  - 执行质检后 topology_config 中对应值生效（见 TC-5.2 接口验证）
```

#### TC-3.3 前端校验（无标准无检查项）
```
验证: 非法提交被前端拦截
方法: 不选标准、不勾任何检查项，点击「执行质检」
预期:
  - toast 提示「未选择标准且未启用任何检查项」
  - 不发起接口请求
```

---

### 4. 执行质检

#### TC-4.1 无标准纯拓扑检查
```
验证: standard_id 为空时执行纯拓扑检查
方法: 不选标准，勾选拓扑组全部 13 项，点击「执行质检」
预期:
  - toast 显示「检查完成：0 个问题」
  - 报告表刷新，顶部出现新报告
  - 报告详情中属性维度为 0（attr_summary: total=0/failed=0/passed=0）
```

#### TC-4.2 带标准检查
```
验证: 选择标准后执行完整质检（属性 + 拓扑）
方法: 选择标准，勾选属性 9 项 + 拓扑 13 项，点击「执行质检」
预期:
  - toast 显示「检查完成：0 个问题」
  - 报告表刷新，新报告标准列显示所选标准名称
  - 报告详情包含五维评分卡与检查汇总表
```

#### TC-4.3 方案保存/加载/导出
```
验证: 检查配置方案可持久化
方法:
  1. 勾选部分检查项 + 修改阈值 → 保存方案
  2. 清空勾选 → 加载方案
  3. 导出方案
预期:
  - 保存/加载走 localStorage（qc-cfg-*）
  - 加载后勾选状态与阈值还原
  - 导出生成 JSON 文件
```

---

### 5. 后端接口（curl）

#### TC-5.1 无标准纯拓扑（HTTP 200）
```bash
# 前置: 取一个已入库文件的 id 作为 file_id
curl -X POST http://127.0.0.1:45679/api/v1/quality/check \
  -H "X-API-Key: vcs_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "<已入库文件id>",
    "topology_config": {"t01": {"enabled": true}, "t02": {"enabled": true}}
  }'
```
```
验证: 不传 standard_id 不返回 400
预期:
  - HTTP 200
  - attr_summary: total=0 / failed=0 / passed=0
  - 拓扑检查项执行并返回结果
```

#### TC-5.2 阈值生效
```
验证: 修改拓扑阈值后请求体中值生效
方法: 带 topology_config 发送 t03 max_area=5e9 等自定义值
预期:
  - 返回的拓扑检查配置反映自定义阈值
  - 报告详情 TopologyConfig 与请求一致
```

#### TC-5.3 带标准检查
```bash
curl -X POST http://127.0.0.1:45679/api/v1/quality/check \
  -H "X-API-Key: vcs_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "<已入库文件id>",
    "standard_id": "<标准id>",
    "topology_config": {"t01": {"enabled": true}}
  }'
```
```
验证: 带 standard_id 执行属性检查
预期:
  - HTTP 200
  - 属性检查项执行（无表定义的文件产生失败项）
  - 报告包含属性结果与五维评分
```

#### TC-5.4 报告查询
```bash
# 报告列表（page_size 上限 50）
curl -H "X-API-Key: vcs_xxx" "http://127.0.0.1:45679/api/v1/quality?page_size=50"
```
```
验证: 报告列表分页
预期:
  - 返回 {items, total, page, page_size}
  - page_size=50 时 items 最长 50
  - 实测库 total=72
```

---

### 6. 回归：超级管理员可见性

#### TC-6.1 Admin Key 数据可见
```
验证: 通过 ADMIN_INIT_KEY 鉴权的操作可见全部数据
方法: 使用 X-Admin-Init-Key 调用数据/报告列表
预期:
  - 可见所有 Key 创建的数据（scope_clause 放行）
  - 可见自身创建 + api_key_id IS NULL 的记录
  - 修复前: 管理员创建的数据被自身查询过滤（不可见）
```

---

### 7. 回归：JS 错误与 404

#### TC-7.1 控制台零错误
```
验证: 全页面无 JS 错误 / 无 404
方法: DevTools Console + Network 观察质检页完整操作
预期:
  - JS 错误 0 个
  - 404 0 个
  - 接口全部 2xx（认证失败场景除外）
```

---

## 已知限制

- 报告删除暂无 API 端点，清理走 SQL；`quality_rule_results` / `quality_violations` 对 `quality_reports` 为 `ON DELETE CASCADE`
- 前端报告列表使用 `GET /api/v1/quality?page_size=50`（上限 50），库中报告多于 50 时仅显示最新 50 条
- 环境差异（alembic 版本落后、`data_standards.sheet_count` 手动 `SET DEFAULT 0`）属开发库已知问题，详见 `docs/environment-notes.md`

---

## 测试记录

| 日期 | 版本 | 用例 | 结果 |
|------|------|------|------|
| 2026-07-31 | v2.6 | E2E（Playwright + Chrome）页面功能 10 项 | 全部通过 |
| 2026-07-31 | v2.6 | 布局计算样式 2 项 | 通过 |
| 2026-07-31 | v2.6 | 后端 curl 无标准/带标准/阈值 3 项 | 通过 |
| 2026-07-31 | v2.6 | 数据清理后 smoke（46 文件/14 标准/72 报告） | 通过 |

详细 E2E 断言与数据清理记录见 `docs/revision_v2_6.md`。
