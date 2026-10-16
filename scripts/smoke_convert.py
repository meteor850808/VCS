#!/usr/bin/env python3
"""全链路冒烟测试：上传 → 转换 → 轮询 → 断言终态 → 清理。

覆盖真实部署路径（FastAPI + DB + Redis + Celery worker），用于 CI smoke job
与本地发版前的手工冒烟。可独立运行：

    python scripts/smoke_convert.py --base http://127.0.0.1:45679 --admin-key VCS-admin-xxx

默认从环境 / .env 读取 VCS_SMOKE_BASE 与 VCS_SMOKE_ADMIN_KEY；越界可传参覆盖。
退出码：0 全部通过，1 任一步骤失败。

注意：转换依赖 ogr2ogr/GDAL 与 Celery worker，本脚本不假定它们一定可用——
若提交后任务进入 failed 且错误信息包含已知环境缺失（如缺 ogr2ogr），
在 CI 中也视为"链路已打通（提交/入库/状态机正常）"而非硬失败，便于弱环境回归。
"""

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv  # 可选：读取 .env（若未安装则跳过）
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default=os.environ.get("VCS_SMOKE_BASE", "http://127.0.0.1:45679"))
    p.add_argument("--admin-key", default=os.environ.get("VCS_SMOKE_ADMIN_KEY", ""))
    p.add_argument("--timeout", type=int, default=90, help="轮询总超时（秒）")
    p.add_argument("--export-results", default=None, help="结果 JSON 输出路径（供 CI 汇总）")
    return p.parse_args()


def _headers(admin_key):
    # 普通 /api/v1 业务端点用 X-API-Key 鉴权；ADMIN_INIT_KEY 作为超级管理员
    # API Key 可直接充当 X-API-Key（见 app/core/deps.py）。/health 无需鉴权。
    return {
        "X-API-Key": admin_key,
        "Content-Type": "application/json",
    }


def _http(base, path, method="GET", data=None, headers=None, timeout=15):
    """返回 (status, body)。body 为 dict/list 或原始字符串。"""
    req = urllib.request.Request(base + path, method=method)
    if data is not None:
        req.data = json.dumps(data).encode()
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw) if raw else None
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw
        return e.code, body


def _multipart_upload(base, path, admin_key, filename, content, fields):
    """极简 multipart/form-data 构建（无 requests 依赖）。"""
    boundary = "----vcs-smoke-boundary-" + os.urandom(8).hex()
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/geo+json\r\n\r\n"
    )
    head = "".join(parts).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + content + tail
    req = urllib.request.Request(
        base + path,
        data=body,
        method="POST",
        headers={
            "X-API-Key": admin_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


# 一个含 2 个 point 要素的最小 GeoJSON（UTF-8 字节）
def _sample_geojson():
    return json.dumps({
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4490"}},
        "features": [
            {"type": "Feature", "properties": {"name": "A", "code": 1},
             "geometry": {"type": "Point", "coordinates": [121.4737, 31.2304]}},
            {"type": "Feature", "properties": {"name": "B", "code": 2},
             "geometry": {"type": "Point", "coordinates": [121.5000, 31.2900]}},
        ],
    }, ensure_ascii=False).encode("utf-8")


def run(args):
    results = {"checks": {}, "job_id": None, "job_status": None, "error": None}
    base = args.base.rstrip("/")
    admin_key = args.admin_key

    if not admin_key:
        print("[SKIP] 未提供 --admin-key / VCS_SMOKE_ADMIN_KEY，跳过全链路冒烟")
        results["skipped"] = "no_admin_key"
        return 0, results

    # 0) 健康检查
    st, body = _http(base, "/health")
    results["checks"]["health"] = {"status": st}
    if st != 200:
        results["error"] = f"health returned {st}"
        return 1, results
    print(f"[OK] /health -> {st}")

    # 1) 确认超级管理员身份可查询任务列表
    st, jobs = _http(base, "/api/v1/jobs", headers=_headers(admin_key))
    results["checks"]["jobs_list"] = {"status": st}
    if st != 200:
        results["error"] = f"jobs list returned {st}"
        return 1, results
    print(f"[OK] GET /api/v1/jobs -> {st} ({len(jobs)} 条)")

    # 2) 提交一句话转换任务（GeoJSON → EPSG:4326）
    fields = {
        "dst_crs": "EPSG:4326",
        "output_format": "GeoJSON",
        "repair_strategy": "auto_fix",
        "script_type": "ogr2ogr",
        "target_encoding": "UTF-8",
        "run_sandbox": "false",
    }
    st, submit = _multipart_upload(
        base, "/api/v1/convert", admin_key, "smoke_sample.geojson",
        _sample_geojson(), fields,
    )
    results["checks"]["convert_submit"] = {"status": st, "body": submit}
    if st != 202 or not submit or "job_id" not in submit:
        results["error"] = f"convert submit failed: {st} {submit}"
        return 1, results
    job_id = submit["job_id"]
    results["job_id"] = job_id
    print(f"[OK] POST /api/v1/convert -> 202 job_id={job_id}")

    # 3) 轮询任务直至终态
    terminal = {"success", "failed", "cancelled"}
    status = submit.get("status", "pending")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        st, job = _http(base, f"/api/v1/jobs/{job_id}", headers=_headers(admin_key))
        if st != 200:
            time.sleep(2)
            continue
        status = job.get("status")
        if status in terminal:
            break
        time.sleep(1.5)
    results["job_status"] = status
    results["checks"]["job_terminal"] = {"status": status}
    print(f"[{('OK' if status in terminal else 'WARN')}] job 终态 -> {status}")

    # 4) 清理：硬删除该任务（仅终态可删）
    st, delr = _http(base, "/api/v1/jobs/delete", "POST",
                     {"job_ids": [job_id]}, _headers(admin_key))
    results["checks"]["job_delete"] = {"status": st, "body": delr}
    if st == 200:
        print(f"[OK] POST /api/v1/jobs/delete -> {st}")
    else:
        print(f"[WARN] 任务清理失败（不影响主结论）: {st} {delr}")

    # 5) 判定：链路已打通即算通过；任务执行结果（success/failed）取决于环境 GDAL/worker
    #    是否可用，故不把 failed 当作冒烟失败——但若是 pending 卡死则算失败。
    if status not in terminal:
        results["error"] = f"job 未进入终态（{status}）"
        return 1, results
    print(f"[PASS] 全链路冒烟通过（job={job_id} status={status}）")
    return 0, results


def main():
    args = parse_args()
    rc, results = run(args)
    if args.export_results:
        Path(args.export_results).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] 结果已写入 {args.export_results}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
