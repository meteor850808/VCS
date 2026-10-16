#!/usr/bin/env python3
"""E2E（浏览器级）——数据预览：属性表滚动 + 分页设置 + 点击定位。

针对真实（本地或 CI）后端 + 前端 index.html/app.js 运行，覆盖：
 1. 预览页属性表渲染：字段全量（横向滚动容器）+ 客户端分页（50/100/200）
 2. 翻页：上一页/下一页禁用态、页码/总数、行计数 pvAC、记录/总数 pvPI
 3. 每页行数切换后分页重置
 4. 点击属性行 → 地图定位（popup 弹出）+ 行进中高亮（pv-sel-row）
 5. 再点同一行 → 取消选中；点另一行 → 切换选中
 6. 截断提示（若存在 >10000 要素的文件）：pv-feat 显示 "N+"、pvPI 提示"仅加载"，否则 SKIP
 7. 无控制台错误

前提：
 - 后端已在 BASE 运行（FastAPI 45679）
 - 已安装 playwright（Python 解释器须含 playwright + chromium）：pip install playwright && playwright install chromium
 - 前端脚本已外置为 app/frontend/app.js（v2.15.1 起）

用法：
  python tests/e2e/preview_attr.py \
      --base http://127.0.0.1:45679 \
      --admin-key VCS-admin-xxx \
      [--screenshot out.png]

自动：用 admin key 创建临时 API key → 上传 130 个点的合成 GeoJSON →
驱动 UI 断言 → 清理文件并吊销 key。

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

FIELD_NAMES = ["fc1", "fc2", "fc3", "fc4"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default=os.environ.get("VCS_E2E_BASE", "http://127.0.0.1:45679"))
    p.add_argument("--admin-key", default=os.environ.get("VCS_E2E_ADMIN_KEY", ""))
    p.add_argument("--screenshot", default=None)
    p.add_argument("--feature-count", type=int, default=130, help="合成文件要素数（页大小 50 时按 3 页分布）")
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
        with urllib.request.urlopen(req, timeout=180) as r:
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


def gen_geojson(count, tag):
    feats = []
    for i in range(count):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + (i % 10) * 0.01, 39.0 + (i // 10) * 0.01]},
            "properties": {**{k: f"{k}-{tag}-{i}" for k in FIELD_NAMES}, "seq_id": i},
        })
    return json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False).encode("utf-8")


def main():
    global BASE, ADMIN_KEY
    args = parse_args()
    BASE = args.base.rstrip("/")
    ADMIN_KEY = args.admin_key or (lambda: (_ for _ in ()).throw(ValueError("缺 --admin-key / VCS_E2E_ADMIN_KEY")))()

    # ── 准备：创建临时 key + 上传合成文件 ────────────────────────
    tag = uuid.uuid4().hex[:6]
    st, created = admin("/api/v1/admin/apikeys", "POST", {"name": f"e2e_pv_{tag}", "scopes": ["data"]})
    assert st in (200, 201) and "id" in created, f"create key failed: {st} {created}"
    key_id, key = created["id"], created["api_key"]
    print(f"[setup] created key {key_id}")

    fname = f"e2e_pv_{tag}.geojson"
    st, body = http("/api/v1/data/upload", "POST",
                    {}, [("file", fname, "application/geo+json", gen_geojson(args.feature_count, tag))], key=key)
    assert st == 201 and "id" in body and body.get("feature_count") == args.feature_count, \
        f"upload failed: {st} {body}"
    file_id = body["id"]
    print(f"[setup] uploaded {fname} id={file_id} features={body.get('feature_count')}")

    # 探测是否存在 >10000 要素的文件（截断提示用例）—— 用 ADMIN_KEY 扫描（可跨 key 查询）
    big_fid = None
    st, lst = http("/api/v1/data?page_size=200", key=ADMIN_KEY)
    if st == 200:
        for f in (lst.get("items") or []):
            if f.get("is_archive"):
                continue
            st2, geo = http(f"/api/v1/data/{f['id']}/features?page_size=1", key=ADMIN_KEY)
            if st2 == 200 and (geo.get("metadata") or {}).get("total", 0) > 10000:
                big_fid = f["id"]
                break
    print(f"[setup] truncation file: {big_fid or 'NONE (SKIP 截断用例)'}")

    # 上传文件实际属性键数（上传管线会追加 id/layer_name/source_name/geometry_type）
    st, geo = http(f"/api/v1/data/{file_id}/features?page_size=1", key=key)
    assert st == 200 and geo.get("features"), f"features probe failed: {st} {geo}"
    n_props = len(set((geo["features"][0].get("properties") or {}).keys()))
    # 表头列数 = 属性键数 + # 列
    fields_n = n_props + 1
    print(f"[setup] property keys={n_props} expected thead={fields_n}")

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

            # 进入数据预览页
            page.click('a[data-page="preview"]')
            page.wait_for_timeout(1500)

            # [1] 属性表全字段渲染 + 行总数判断
            page.select_option("#pvFile", file_id)
            page.click("#pvLoadBtn")
            page.wait_for_selector("#pvAttr table", timeout=30000)
            page.wait_for_timeout(1500)
            th_n = page.locator("#pvAttr thead th").count()
            rows_n = page.locator("#pvAttr tbody tr").count()
            pvAC = page.text_content("#pvAC").strip()
            print(f"[1] fields: thead={th_n} (expect {fields_n}) rows@page1={rows_n} (expect 50) pvAC={pvAC}")
            ok &= th_n == fields_n and rows_n == 50 and pvAC == f"显示第 1-50 行 / 共 {args.feature_count} 行"

            # [2] 分页栏状态
            pi = page.text_content("#pvPI").strip()
            pn = page.text_content("#pvPN").strip()
            prev_dis = page.locator("#pvPrev").is_disabled()
            next_dis = page.locator("#pvNext").is_disabled()
            exp_pages = (args.feature_count + 49) // 50
            ok &= pi.startswith(f"共 {args.feature_count} 行") and pn == f"第 1 页 / 共 {exp_pages} 页" \
                 and prev_dis and not next_dis
            print(f"[2] paging: pi={pi} pn={pn} prev_disabled={prev_dis} next_disabled={next_dis}")

            # [3] 翻页
            page.click("#pvNext")
            page.wait_for_timeout(600)
            rows2 = page.locator("#pvAttr tbody tr").count()
            pn2 = page.text_content("#pvPN").strip()
            ok &= rows2 == 50 and pn2 == f"第 2 页 / 共 {exp_pages} 页" and not page.locator("#pvPrev").is_disabled()
            print(f"[3] page2: rows={rows2} pn={pn2} prev_enabled={not page.locator('#pvPrev').is_disabled()}")

            page.click("#pvNext")
            page.wait_for_timeout(600)
            rows3 = page.locator("#pvAttr tbody tr").count()
            last_page_rows = args.feature_count - (exp_pages - 1) * 50
            ok &= rows3 == last_page_rows and page.locator("#pvNext").is_disabled()
            print(f"[3] last page: rows={rows3} (expect {last_page_rows}) next_disabled={page.locator('#pvNext').is_disabled()}")

            # [4] 每页行数切换 → 分页重置
            page.select_option("#pvSizeSel", "100")
            page.wait_for_timeout(600)
            rows100 = page.locator("#pvAttr tbody tr").count()
            pn100 = page.text_content("#pvPN").strip()
            exp2 = (args.feature_count + 99) // 100
            ok &= rows100 == min(100, args.feature_count) and pn100 == f"第 1 页 / 共 {exp2} 页"
            print(f"[4] size=100: rows={rows100} pn={pn100}")
            page.select_option("#pvSizeSel", "50")
            page.wait_for_timeout(600)

            # [5] 点击行定位 + 行选中高亮
            page.click('#pvAttr tr[data-gi="5"]')
            page.wait_for_timeout(1200)
            sel_rows = page.locator("#pvAttr tr.pv-sel-row").count()
            popup = page.locator(".leaflet-popup").count()
            ok &= sel_rows == 1 and popup >= 1
            print(f"[5] click row gi=5: sel_rows={sel_rows} popup={popup}")

            # [6] 点另一行切换选中
            page.click('#pvAttr tr[data-gi="7"]')
            page.wait_for_timeout(800)
            sel_gi5 = page.locator('#pvAttr tr[data-gi="5"].pv-sel-row').count()
            sel_gi7 = page.locator('#pvAttr tr[data-gi="7"].pv-sel-row').count()
            ok &= sel_gi5 == 0 and sel_gi7 == 1
            print(f"[6] click row gi=7: gi5_selected={sel_gi5} gi7_selected={sel_gi7}")

            # [7] 再点同一行取消选中
            page.click('#pvAttr tr[data-gi="7"]')
            page.wait_for_timeout(400)
            sel_total = page.locator("#pvAttr tr.pv-sel-row").count()
            ok &= sel_total == 0
            print(f"[7] re-click same row: sel_rows={sel_total} (expect 0)")

            # [8] 截断提示（若存在 >10000 要素文件）—— 切到 ADMIN_KEY 重进预览页以列出全部文件
            if big_fid:
                page.fill("#apiKeyInput", ADMIN_KEY)
                page.click("#setKeyBtn")
                page.wait_for_timeout(800)
                page.click('a[data-page="dashboard"]')
                page.wait_for_timeout(600)
                page.click('a[data-page="preview"]')
                page.wait_for_timeout(1500)
                page.select_option("#pvFile", big_fid)
                page.click("#pvLoadBtn")
                page.wait_for_function("document.getElementById('pv-feat').textContent.includes('+')", timeout=60000)
                page.wait_for_timeout(1500)
                feat_txt = page.text_content("#pv-feat").strip()
                pi_big = page.text_content("#pvPI").strip()
                ok &= "仅加载前" in pi_big
                print(f"[8] truncation: pv-feat={feat_txt} pvPI={pi_big.replace(chr(0x26A0), '!')[:80]}")
            else:
                print("[8] truncation: SKIP（无 >10000 要素文件）")

            # [9] 无控制台错误
            print("[9] console errors:", errors if errors else "none")
            ok &= not errors
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
                print(f"[10] screenshot -> {args.screenshot}")
            browser.close()
    finally:
        # 清理：删除上传文件 + 吊销 key（FastAPI 裸 list body 需直接传数组）
        st, _ = http("/api/v1/data/batch-delete", "POST", [file_id], key=key)
        print("[cleanup] files delete:", st)
        st, _ = admin(f"/api/v1/admin/apikeys/{key_id}", "DELETE")
        print("[cleanup] key revoked:", st)

    print("\n=== E2E preview_attr:", "ALL OK" if ok else "FAILURES DETECTED", "===")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()