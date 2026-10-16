# E2E 测试（浏览器级）

针对真实后端 + 前端 (`app/frontend/index.html` + `app.js`) 的端到端回归。

## 文件

| 脚本 | 覆盖 |
|------|------|
| `dashboard_kanban.py` | 默认落地=汇总概览、任务/数据/图层/标准四维统计、Kanban 列头全选（全选/半选/取消） |
| `preview_attr.py` | 数据预览属性表：全字段横向滚动、客户端分页（50/100/200 + 上一页/下一页）、每页行数切换、点击记录定位（popup+行高亮）、再点取消/切换选中、>10000 要素截断提示、无控制台错误 |

## 运行前提

1. 后端已启动（`restart.bat` 或 `uvicorn app.main:app --port 45679`），前端已由 StaticFiles 挂载。
2. 安装 Playwright（需项目依赖之外的解释器，通常是系统 Python）：
   ```bash
   pip install playwright
   playwright install chromium
   ```
3. 提供管理员引导密钥（`ADMIN_INIT_KEY`）。

## 用法

```bash
# 环境变量或命令行参数二选一
python tests/e2e/dashboard_kanban.py \
    --base http://127.0.0.1:45679 \
    --admin-key VCS-admin-xxx \
    --screenshot e2e.png

python tests/e2e/preview_attr.py \
    --base http://127.0.0.1:45679 \
    --admin-key VCS-admin-xxx \
    --screenshot preview.png
```

等价环境变量：`VCS_E2E_BASE`、`VCS_E2E_ADMIN_KEY`。

脚本会自动：用 admin key 创建临时 API key → 造测试数据 → 驱动 UI 断言 →
清理数据并吊销 key。转换任务执行结果取决于环境 GDAL/Celery worker 是否可用，
脚本只统计卡片数量并按卡片实际所在列做列头全选，**不依赖 success/failed**。
预览页脚本的截断用例（>10000 要素）需要环境中存在此类文件，否则自动 SKIP。

退出码：`0` 全过，`1` 有失败。

## 调试

- `--no-cleanup`：保留任务与 key（手动检查现场）。
- 浏览器非 headless：将 `headless=True` 改为 `False` 观察。
