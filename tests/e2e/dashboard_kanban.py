#!/usr/bin/env python3
"""E2E（浏览器级）——任务看板汇总概览 + Kanban 列头全选。

针对真实（本地或 CI）后端 + 前端 index.html/app.js 运行，覆盖：
 1. 默认落地页 = 任务看板 → 汇总概览
 2. 任务统计（总数/环形图/格式柱状图）
 3. 数据统计卡（文件/大小/要素/已关联标准）
 4. 标准统计卡 + 分类/状态面板 + 字典 footer
 5. Kanban 列头批量全选（全选/半选 indeterminate/取消选择）
 6. 无控制台错误

前提：
 - 后端已在 BASE 运行（FastAPI 45679）
 - 已安装 playwright（Python 解释器须含 playwright + chromium）：pip install playwright && playwright install chromium
 - 前端脚本已外置为 app/frontend/app.js（v2.15.1 起）

用法：
  python tests/e2e/dashboard_kanban.py \
      --base http://127.0.0.1:45679 \
      --admin-key VCS-admin-xxx \
      [--screenshot out.png]

自动：用 admin key 创建临时 API key → 造 3 个 convert 任务 → 驱动 UI 断言 →
清理任务并吊销 key。任务转换结果取决于环境 GDAL/worker；本脚本只统计卡片
数量并按实际所在列做列头全选（不依赖 success/failed）。

退出码 0 全过，1 有失败。
"""
import argparse
import json
import os
import time
import uuid
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

ADMIN_KEY = ""
BASE = ""


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default=os.environ.get("VCS_E2E_BASE", "http://127.0.0.1:45679"))
    p.add_argument("--admin-key", default=os.environ.get("VCS_E2E_ADMIN_KEY", ""))
    p.add_argument("--screenshot", default=None)
    p.add_argument("--no-cleanup", action="store_true", help="保留任务与 key（调试用）")
    return p.parse_args()


def http(path, method="GET", fields=None, files=None, key=None):
    req = urllib.request.Request(BASE + path, method=method)
    if key:
        req.add_header("X-API-Key", key)
    if files:
        boundary = "----vcs" + uuid.uuid4().hex
        body = b""
        for k, v in (fields or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        for name, fname, ctype, data in files:
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fname}\"\r\n"
                     f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
        req.data = body
    elif fields is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(fields).encode()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def admin(path, method="GET", fields=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("X-Admin-Init-Key", ADMIN_KEY)
    if fields is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(fields).encode()
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


SAMPLE = json.dumps({
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon",
                     "coordinates": [[[116.3, 39.9], [116.4, 39.9], [116.4, 40.0], [116.3, 40.0], [116.3, 39.9]]]},
        "properties": {"NAME": "e2e", "CODE": "1"},
    }],
}, ensure_ascii=False).encode("utf-8")


def main():
    global BASE, ADMIN_KEY
    args = parse_args()
    BASE = args.base.rstrip("/")
    ADMIN_KEY = args.admin_key or (lambda: (_ for _ in ()).throw(ValueError("缺 --admin-key / VCS_E2E_ADMIN_KEY")))()

    # ── 准备：创建临时 key + 3 个任务 ─────────────────────────────
    st, keys = admin("/api/v1/admin/apikeys")
    assert st == 200, f"admin list failed: {st} {keys}"
    name = f"e2e_{uuid.uuid4().hex[:6]}"
    st, created = admin("/api/v1/admin/apikeys", "POST", {
        "name": name, "scopes": ["convert", "data", "standards"],
    })
    assert st in (200, 201) and "id" in created, f"create key failed: {st} {created}"
    key_id, key = created["id"], created["api_key"]
    print(f"[setup] created key {name} id={key_id}")

    jobs = []
    for i in range(3):
        st, body = http("/api/v1/convert", "POST",
                        {"dst_crs": "EPSG:4490", "output_format": "GeoJSON"},
                        [("file", f"e2e_{i}.geojson", "application/geo+json", SAMPLE)], key=key)
        assert st == 202, f"convert {i} failed: {st} {body}"
        jobs.append(body["job_id"])
    print("[setup] submitted jobs:", jobs)

    # 轮询至终态（仅用于确定卡片所在列，不要求 success）
    deadline = time.time() + 90
    statuses = {}
    while time.time() < deadline:
        st, lst = http("/api/v1/jobs?page_size=100", method="GET", key=key)
        if st == 200 and isinstance(lst, list):
            statuses = {j["id"]: j["status"] for j in lst if j["id"] in jobs}
            if all(s in ("success", "failed", "cancelled") for s in statuses.values()):
                break
        time.sleep(2)
    print("[setup] job terminal statuses:", statuses or "(卡死在轮询窗口)")

    ok = True
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1680, "height": 950})
            errors = []
            page.on("pageerror", lambda e: errors.append("PAGEERROR: " + str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("dialog", lambda d: d.accept())

            page.goto(BASE + "/app/")
            page.wait_for_load_state("networkidle")
            page.fill("#apiKeyInput", key)
            page.click("#setKeyBtn")
            page.wait_for_timeout(2500)

            # [1] 默认落地页 = dashboard（汇总概览）
            nav_active = page.get_attribute('a[data-page="dashboard"]', "class") or ""
            ok &= "active" in nav_active
            ov = page.locator("#dashOverview").is_visible()
            ok &= ov
            print(f"[1] default landing = overview: nav_active={bool('active' in nav_active)} overview_visible={ov}")

            # [2] 任务统计
            st_total = page.text_content("#st-total").strip() if page.locator("#st-total").count() else "-"
            donut = page.locator("#dashStatusDonut circle").count() > 0
            fmt = page.locator("#dashFormatBars .dash-bar-row").count() > 0
            ok &= (st_total == "3" and donut and fmt)
            print(f"[2] task stats: total={st_total} donut={donut} fmtbars={fmt}")

            # [3] 数据统计卡
            ds_files = page.text_content("#ds-files").strip().replace(",", "") if page.locator("#ds-files").count() else "-"
            data_panels_empty = (page.locator("#dashDataFmt .empty").count() +
                                 page.locator("#dashLayerFeats .empty").count())
            print(f"[3] data stats: ds-files={ds_files} empty-panels={data_panels_empty}")
            ok &= page.locator("#ds-files").count() >= 1

            # [4] 标准统计卡 + footer
            sd_total = page.text_content("#sd-total").strip() if page.locator("#sd-total").count() else "-"
            st_foot = page.text_content("#dashStdStatus")
            has_dict = bool(st_foot) and "字典" in st_foot
            print(f"[4] standard stats: sd-total={sd_total} dict-footer={has_dict}")
            ok &= (page.locator("#sd-total").count() >= 1 and has_dict)

            # [5] 任务管理 → 列头全选
            page.click('button[data-dtab="manage"]')
            page.wait_for_timeout(1200)
            cards = page.locator(".kb-card").count()
            selcols = page.locator(".kb-selcol").count()
            print(f"[5] manage: cards={cards} selcols={selcols}")
            ok &= cards >= 1 and selcols == 5

            col_key = page.evaluate("""() => {
                const cols = document.querySelectorAll('.kb-col');
                for (const c of cols) {
                    if (c.querySelector('.kb-card')) {
                        const s = c.querySelector('.kb-selcol');
                        return s ? (s.dataset.col || null) : null;
                    }
                }
                return null;
            }""")
            ok &= bool(col_key)
            if col_key:
                page.evaluate(f'document.querySelector(\'.kb-selcol[data-col="{col_key}"]\').click()')
                page.wait_for_timeout(500)
                checked = page.locator(".kb-check:checked").count()
                bar_vis = page.locator("#kbBatchBar").is_visible()
                cnt = page.text_content("#kbSelCount").strip()
                print(f"[5] col-select-all checked={checked} bar={bar_vis} count={cnt}")
                ok &= checked == cards and bar_vis and cnt == str(cards)
                # 半选
                page.evaluate("document.querySelector('.kb-check').click()")
                page.wait_for_timeout(400)
                indet = page.evaluate(f'document.querySelector(\'.kb-selcol[data-col="{col_key}"]\').indeterminate')
                print(f"[5] indeterminate after partial deselect: {indet}")
                ok &= bool(indet)
                # 取消选择
                page.evaluate("kbClearSel()")
                page.wait_for_timeout(400)
                bar_hidden = not page.locator("#kbBatchBar").is_visible()
                print(f"[5] batch bar hidden after 取消选择: {bar_hidden}")
                ok &= bar_hidden

            # [6] 无控制台错误
            print("[6] console errors:", errors if errors else "none")
            ok &= not errors
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
                print(f"[7] screenshot -> {args.screenshot}")
            browser.close()
    finally:
        if not args.no_cleanup:
            # 清理：先等仍在轮询窗口内落定的任务进入终态，再批量删除（避免 running 拒删竞态）
            deadline = time.time() + 30
            while time.time() < deadline:
                st, delr = http("/api/v1/jobs/delete", "POST", {"job_ids": jobs}, key=key)
                done = 0
                if st == 200 and isinstance(delr, dict):
                    results = delr.get("results") or []
                    done = sum(1 for r in results if r.get("success"))
                    still_ok = all(r.get("success") for r in results)
                    if still_ok:
                        break
                if st == 400 and isinstance(delr, str) and "运行中" in delr:
                    time.sleep(2)
                    continue
                break
            print("[cleanup] jobs delete:", st, delr)
            st, _ = admin(f"/api/v1/admin/apikeys/{key_id}", "DELETE")
            print("[cleanup] key revoked:", st)

    print("\n=== E2E dashboard_kanban:", "ALL OK" if ok else "FAILURES DETECTED", "===")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
