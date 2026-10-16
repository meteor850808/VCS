# v2.19.2 修订记录

**发布日期**: 2026-09-17 · **版本**: v2.19.2

## 背景

v2.19.0/v2.19.1 的导出功能在自动化测试（pytest + TestClient + 全局 Python 3.12）里全绿，但在**真实部署环境（restart.bat → venv Python 3.13 + uvicorn）**做 HTTP 端到端实测时暴露两个问题：

1. `GET /data/{file_id}/download?format=mdb` 返回 **500**，且服务日志无任何 traceback（无日志可查）。
2. `geojson/shp/gpkg/csv` 及 400/410 分支全部通过——只有 mdb（唯一依赖 COM 的格式）挂掉。

## 根因

- 服务进程（restart.bat 使用的 `.venv`，Python **3.13.5**）中 `import win32com` 失败：
  `ModuleNotFoundError: No module named 'win32com'`。
- 开发/测试使用的全局 Python 3.12 恰好预装了 pywin32，掩盖了 **v2.19.0 依赖缺漏**（requirements.txt 只列了 pyodbc，未列 pywin32）。
- 导出管线的导出的 `export_mdb` 抛未捕获异常 → FastAPI 裸 500，错误 middleware 的 traceback 未落到 structlog/日志文件。

## 改动

| 文件 | 改动 |
|------|------|
| `requirements.txt` | 新增 `pywin32>=305; sys_platform == "win32"`（ADOX 建库依赖，仅 Windows 需要） |
| `app/services/export_service.py` | `_create_empty_mdb` 捕获 `ImportError`，缺 pywin32 时抛可操作 `RuntimeError`（"请先执行: pip install pywin32"） |
| `app/api/v1/data.py` | download 端点导出管线包 `try/except`：异常时 `logger.exception` 记录完整 traceback（含 file_id/fmt/srid），返回 `500 {"detail": "导出失败，请稍后重试或联系管理员"}` |
| 版本文件 | `CONTEXT.md` / `BASELINE.md` / `deploy_linux.sh` / `deploy_windows.bat` / `restart.bat` / `app/main.py` → v2.19.2 |

## 真实服务 HTTP 端到端验证（restart.bat 环境，super admin 鉴权，真实数据 8541e93a 3757 特征）

| format | 结果 | 说明 |
|--------|------|------|
| geojson | 200 | 3757 features，crs `urn:ogc:def:crs:EPSG::4326`，1.56 MB |
| shp | 200 | `application/zip`，混合几何 → 多 type 文件 zip |
| gpkg | 200 | `application/zip`（混合几何按类型分文件打包） |
| csv | 200 | `text/csv; charset=utf-8`，858 KB |
| mdb | 200 | `application/msaccess`，3.96 MB（13 图层拆 8 表） |
| ZIP 壳 07813390 | 400 | 提示展开后对内部文件导出 |
| GDB 无特征 debd733c | 410 | 原始数据未留存且无要素 |
| 无 format（file_data NULL） | 200 | 默认 geojson 导出 |

> 排障过程补充：先后排除了线程池并发（独立线程池 export_mdb 正常）、lifespan/TestClient（TestClient + 真实 lifespan 200），最终靠 download 端点 `logger.exception` 兜底拿到 traceback 定位到 venv 缺 pywin32。

## 测试

- `tests/test_data_download_export.py`：19 passed
- 全量回归：**1514 passed / 99.19%**（venv Python 3.13 下实测）