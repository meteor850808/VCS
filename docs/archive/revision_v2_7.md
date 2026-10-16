# VCS v2.7 使用问题批量修复 + 一键重启修复修订记录

## 版本信息
- **版本**: v2.7
- **日期**: 2026-08-04
- **类型**: ERROR.txt 使用问题批量修复（8 模块 30+ 项清单）+ restart.bat 卡住修复
- **影响范围**: 管理 API、质检服务、数据标准 v2、文件下载、WebSocket、健康检查、前端 SPA、自检页、一键重启脚本

---

## 修订目标

1. **按 ERROR.txt 清单逐项修复**: 依据 `ERROR.txt`（2026-08-03 生成，8 模块 30+ 项使用问题清单）逐项排查并修复全部问题
2. **修复 restart.bat 卡住**: 解决一键重启「每次跑都要手动介入、窗口关不掉」问题
3. **端到端回归验证**: 所有修复经接口实测 + 语法校验，测试数据已清理

---

## 详细变更

### 1. 管理 API：密钥启用 / 重新生成（`app/api/v1/admin.py`）

#### 1.1 问题
- 已吊销 Key 只能永久删除，无法重新启用
- Key 明文只在创建时展示一次，遗失后无法重新获取（只能新建）

#### 1.2 修复
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/admin/apikeys/{key_id}/activate` | PUT | 重新启用 Key（`is_active=True`） |
| `/api/v1/admin/apikeys/{key_id}/regenerate` | POST | 重新生成 Key（重算 SHA-256 哈希，旧钥立即失效；新明文仅本次响应展示一次） |

- 前端密钥表格（`app/frontend/index.html`）：
  - 已吊销 Key 显示 ▶️ 启用 / 🔁 重新生成按钮
  - 激活中 Key 显示 🗑️ 吊销 / 🔁 重新生成按钮
  - 已激活 Key 隐藏永久删除按钮（防止误操作）
  - 事件委托补 `.key-activate-btn` / `.key-regen-btn` 处理器

### 2. 前端通用修复（`app/frontend/index.html`）

#### 2.1 `esc()` 单引号转义（全前端 onclick 断裂通用根因）
- `esc()` 补 `'` → `&#39;` 转义
- **根因**：`onclick="v2EditDictItem('${item.key}')"` 等内联事件中，字符串含单引号时属性断裂 → 按钮无效/报错
- 修复后所有内联 onclick 调用修复生效

#### 2.2 fetch 封装（`_t()`）整体重写
- 统一 30s AbortController 超时（此前无超时，挂起请求永久卡死 UI）
- GET 非 JSON 响应兜底（返回文本而非抛错）
- `dlAuth` 下载支持 RFC 5987 `filename*=UTF-8''` 优先解析
  - **根因**：旧正则 `filename\*?=(?:UTF-8'')?` 会误把 `filename*=` 整段当文件名 → 中文文件名下载（DOC/DOCX）挂起失败

#### 2.3 adminInitKey 持久化（`admKey()`）
- 改 localStorage 读写（key: `vcs_admin_key`）
- 读取时非空自动回填 DOM；为空时兜底读取 DOM
- **修复**：刷新页面后管理员密钥不丢失

#### 2.4 qcTopoState 默认值初始化
- 新增 `TOPO_RULES.forEach(r => { qcTopoState[r.id] = r.on !== false; })`
- **修复**：t09/t10/t11（设计 `on:false`）误显示勾选

#### 2.5 全局拖拽拦截
- `window` dragover/drop preventDefault
- **修复**：拖文件到页面其他区域时浏览器直接打开文件（拖拽上传偶发失效根因）

#### 2.6 数据管理过滤压缩包空壳记录
- `loadFiles` 加 `raw.filter(f => !f.is_archive)`
- **需求**：压缩包解压后不保留原压缩包记录

#### 2.7 字典栏目增删改
- 字典列表：✏️ 重命名（`v2RenameDict`，PUT）/ 🗑️ 删除（`v2DelDict`，DELETE + confirm）
- 条目表格：✏️ 编辑（`v2EditDictItem`，PUT items 批量更新）/ 🗑️ 删除
- 新增 `_v2CurDictId` 全局记录当前字典

#### 2.8 层组父级下拉
- `v2AddLayer` 由 prompt 手输 UUID 改弹层 `<select id="v2AddLayerParent">` 下拉
- 新增 `_flatLayers` 递归展开带缩进

#### 2.9 showGroupModal 竞态
- 层组选项加载完成前禁用保存按钮
- **修复**：`loadGroupModalOptions` 未返回时点击保存导致无效提交

### 3. 质检空要素校验（`app/api/v1/quality.py` + `app/services/quality_checker.py`）

- **入口 400**：`POST /quality/check` 前置 `feature_count <= 0` → 400「该文件无图形要素」
- **Phase 0.5 守卫**：`quality_checker.py` 查 `StoredFile.feature_count`，为 0 直接返回 0 分空结果（不再走到要素遍历）
- **`_calculate_score_legacy` 修正**：`total == 0` 时评分由 `1.0` 改为 `0.0`（无要素不得分）
- **报告修正**：`total_features` 由 DB 值填充（原错用 `check_result["attr_summary"]["total"]`）

### 4. 数据标准 v2（`app/api/v1/data_standard_v2.py` + `app/services/data_standard_parser.py`）

#### 4.1 字典创建校验
- 预查重 + `IntegrityError` 捕获 → 409「字典名称已存在」（含 body 中文提示）
- `scope` 枚举校验（None / standard / global）→ 400

#### 4.2 Excel 4-sheet 模板导入（v2 解析器）
- 新增 `parse_standard_excel_v2()`：标准信息 / 层组定义 / 数据表定义 / 字段定义 4-sheet 解析
- 兼容导出命名（层组/数据表/字段），返回 `recognized` 标志
- `import_standard_excel` 优先 v2 解析（`_import_from_v2` 完整建层组树/表/几何类型/字段），失败回退旧扁平解析
- 层组编码未匹配的字段归入「未分组」兜底
- `.xls` 明确报错提示另存 `.xlsx`（openpyxl 不支持旧格式）

### 5. 健康检查限时（`app/main.py`）

- `/health` 的 PG/Redis ping 各加 `asyncio.wait_for` 2s 超时
- **根因**：PG 不可达时默认 60s 超时拖垮健康检查/自检监控
- 超时返回 `degraded` 状态而非挂起

### 6. WebSocket 连接存活检测（`app/api/v1/ws.py`）

- Redis 不可用分支由 `while True: await asyncio.sleep(600)` 改 `while websocket.client_state == WebSocketState.CONNECTED: await asyncio.sleep(1)`
- 新增 `from starlette.websockets import WebSocketState`
- **修复**：Redis 挂了连接也永远不断开（资源泄漏 + 前端进度永远转圈）

### 7. 下载 RFC 5987 文件名（`app/api/v1/data.py`）

- `download_file` 响应头改 `attachment; filename*=UTF-8''{quote(original_name)}`
- **根因**：原 `filename="{original_name}"` 中文/latin-1 编码失败

### 8. 自检页超时（`app/frontend/test.html`）

- `apiCall` 加 15s AbortController 超时
- `runAllTests` 串行 await，任一挂起卡死整个自检 → 超时自动跳过

### 9. restart.bat 卡住修复（根目录 + `scripts/restart.bat` 双版本同步）

#### 9.1 根因（三层）
1. **主因**：`start "VCS-API" /b uvicorn ...` 让 uvicorn 共享当前控制台窗口 → bat 结束后窗口永远关不掉
2. **次因**：结尾 `pause` 强制等待按键
3. `curl` 无 `-m` 超时（探测失败无限挂起）

#### 9.2 修复
- Step 6 改 `powershell Start-Process -FilePath '...uvicorn.exe' -ArgumentList ... -WindowStyle Hidden -RedirectStandardOutput ...server_out.log -RedirectStandardError ...server_err.log`
- 成功路径 `exit /b 0` 自动关窗（失败保留 `pause` 便于查看错误）
- `curl -s -m 5` 探测 + `ping -n 9 127.0.0.1 >nul` 延迟
- 服务日志分储 `server_out.log` / `server_err.log`

---

## 文件变更清单

### 修改文件
| 文件 | 变更 |
|------|------|
| `app/api/v1/admin.py` | 新增 activate / regenerate 端点 |
| `app/api/v1/quality.py` | 空要素前置 400 校验 + total_features 修正 |
| `app/services/quality_checker.py` | Phase 0.5 空要素守卫 + `_calculate_score_legacy` 0 分修正 |
| `app/api/v1/data_standard_v2.py` | 字典 409/scope 400；Excel 导入 v2 优先；`_import_from_v2`；.xls 提示 |
| `app/services/data_standard_parser.py` | 新增 `parse_standard_excel_v2` |
| `app/api/v1/data.py` | 下载响应头 RFC 5987 |
| `app/api/v1/ws.py` | Redis 不可用时连接存活检测 |
| `app/main.py` | /health PG/Redis ping 2s 超时 |
| `app/frontend/index.html` | esc 单引号转义 / fetch 30s 超时 / admKey 持久化 / qcTopoState 初始化 / 拖拽拦截 / 压缩包过滤 / 密钥按钮 / 字典增删改 / 层组父级下拉 / showGroupModal 竞态 |
| `app/frontend/test.html` | apiCall 15s 超时 |
| `restart.bat` / `scripts/restart.bat` | Start-Process 隐藏窗口 + 自动关窗 + curl 超时 |

### 新增文件
| 文件 | 说明 |
|------|------|
| `docs/revision_v2_7.md` | 本文档 |
| `docs/test_manual_v2_7.md` | v2.7 回归验证手册 |

---

## E2E 验证（2026-08-04，HTTP 实测 + 语法校验）

### 管理 API
| 断言 | 结果 |
|------|------|
| 创建 Key → 重新生成 → 新钥可用 | ✅ |
| 吊销 → 401 阻断（用 `/api/v1/jobs` 鉴权端点验证） | ✅ |
| 激活 → 恢复可用 | ✅ |
| 永久删除（hard）清理 | ✅ |
| 注意：`/api/v1/capabilities` 是公开端点不鉴权，鉴权验证须用 `/api/v1/jobs` 等 | ✅ |

### 数据标准 v2
| 断言 | 结果 |
|------|------|
| 字典重复名 → 409（含中文 body 提示） | ✅ |
| 非法 scope → 400 | ✅ |
| Excel 4-sheet 模板端到端导入「E2E标准」→ 1 层组（道路/ROAD）/ 1 表（道路中心线/ROAD_LN/spatial/LineString）/ 1 字段（id/string/✅req/✅pk）全部入库 | ✅ |
| 旧扁平结构导入 recognized=False 正确回退 | ✅ |

### 质检
| 断言 | 结果 |
|------|------|
| 空要素文件 → 400「该文件无图形要素」（库中 49 文件有 35 个零要素） | ✅ |
| 正常文件 → 200 生成报告 | ✅ |

### 语法校验
- `python -m py_compile`：8 个 Python 文件全部 OK
- node `new Function`：index.html / test.html 内联 script 全部 OK

### 数据清理
- 测试 Key（api-regen-test / api-revoke-test2/3/4 / dict-test-key）hard 删除
- 测试标准（E2E标准 / dict-test-std）删除
- `e2e_std.xlsx` 写入临时目录（不入库）

---

## 遗留项（已记录，未在本轮实施）

- 版本对比仅展示数量无明细（ERROR.txt 清单项，需前端 diff 展示增强）
- 坐标转换预览未开发完
- 任务看板未开发
- 数据标准「回传更新」端点（导入只能新建）
- 层组多分类（当前单父树模型限制）
- Excel 导入模板缺字典 sheet
- Multipatch 解析完善
