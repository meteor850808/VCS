# -*- coding: utf-8 -*-
"""导入「一厅八室」参照数据标准（5 份 JSON）到运行中的 VCS 服务。

用途：知识图谱纯视图（GET /{id}/graph）的真实验证数据 —— SHGT_SURVEY
是最大的一份（255 表 / 3288 字段 / 187 字典 / 2031 条目）。

用法：
  # 服务已在跑（端口 45681），key 在环境变量
  set VCS_API_KEY=vcs_xxx
  python scripts/import_reference_standards.py

  # 让脚本自己起服务、导完自动关
  python scripts/import_reference_standards.py --start-server --key vcs_xxx

行为：
  - mode=upsert 幂等 —— 重复跑安全（同 code 命中即全删重建）
  - 逐份打印导入结果（表/字段/字典计数 + warnings 数）
  - 任何一份失败退出码 1，不影响已导入的其余份
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = Path(r"C:\Users\Administrator\Desktop\AI项目\数据标准调整"
                   r"\一厅八室数据标准-转换产出\json")
IMPORT_PATH = "/api/v1/data-standards-v2/import/json?mode=upsert"


def _req(base: str, method: str, path: str, key: str,
         data: bytes | None = None, timeout: float = 60.0):
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, data=data, method=method)
    if key:
        req.add_header("X-API-Key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wait_health(base: str, tries: int = 60) -> bool:
    for _ in range(tries):
        try:
            _req(base, "GET", "/health", key="")
            return True
        except Exception:
            time.sleep(1.0)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:45681")
    ap.add_argument("--key", default=os.environ.get("VCS_API_KEY", ""))
    ap.add_argument("--dir", default=str(DEFAULT_DIR),
                    help="参照 JSON 目录（默认：一厅八室转换产出）")
    ap.add_argument("--start-server", action="store_true",
                    help="服务未运行时由脚本启动，导完自动关闭")
    args = ap.parse_args()

    if not args.key:
        print("!! 缺 API key：--key vcs_xxx 或环境变量 VCS_API_KEY")
        return 2

    files = sorted(Path(args.dir).glob("*.json"))
    if not files:
        print(f"!! 目录无 JSON: {args.dir}")
        return 2
    print(f"待导入 {len(files)} 份：{[f.name for f in files]}")

    server = None
    try:
        _req(args.base, "GET", "/health", key="")
        print(f"服务已运行: {args.base}")
    except Exception:
        if not args.start_server:
            print(f"!! 服务未运行（{args.base}）—— 加 --start-server 或先 restart.bat")
            return 2
        print("启动服务（uvicorn）...")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--port", "45681"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_health(args.base):
            print("!! 服务启动超时")
            server.terminate()
            return 2
        print("服务已就绪")

    rc = 0
    try:
        for f in files:
            payload = f.read_bytes()
            try:
                r = _req(args.base, "POST", IMPORT_PATH, args.key, payload,
                         timeout=300.0)
                warn = len(r.get("warnings") or [])
                print(f"  {f.stem:14s} -> 201  {r['name']}  "
                      f"层组标记={r.get('layer_groups_count')}  "
                      f"表={r.get('tables_count')}  字段={r.get('fields_count')}  "
                      f"字典={r.get('dictionaries_count')}  warnings={warn}")
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:300]
                print(f"  {f.stem:14s} -> {e.code}  {detail}")
                rc = 1
            except Exception as e:
                print(f"  {f.stem:14s} -> 异常 {e}")
                rc = 1
    finally:
        if server is not None:
            print("关闭脚本启动的服务...")
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:
                server.kill()

    print("完成" + ("（有失败）" if rc else "（全部成功）"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
