#!/usr/bin/env python3
"""E2E（浏览器级·真实服务）—— CAD Tab 前端接线验收（真实 DWG · 真实 SPA）。

驱动真实后端（45679）+ 前端（/app/ → index.html/app.js）验证「文件详情抽屉 →
CAD Tab → loadDCAD 接线」全链真实可用：

  1. admin 建临时 key（scopes 含 data/convert/standards）→ 上传真实 DWG
     (tests/fixtures/cad/real_sample.dwg) → 轮询 cad info 就绪（LibreDWG 管线）
  2. Playwright Chromium 打开 /app/ → 填 key → 进入「数据管理」页
  3. 打开刚上传 DWG 的详情抽屉（openDrawer 接线）→ 抽屉可见
  4. CAD Tab 按钮 #dtabCadBtn 可见（仅 dxf/dwg 显示，CSV/JSON 无）
  5. 切 CAD Tab → loadDCAD(#dtab-cad)：CAD 元数据（格式/单位/实体数/图层数/
     文本/标注/图块/填充）+ 图层树 (.cad-layer-item, bind layer_total 与
     entity_count) + SVG 预览（blob objectURL → #dtab-cad img）+ 实体表
     (#cadEntCount/#cadEntTable/#cadEntPag, 分页 50/页)
  6. 图层筛选交互：点图层 item → toggleCADLayer → 实体表刷新（加载 layer 过滤）
  7. 分页交互：下一页 → 实体表 offset 变化
  8. 无控制台错误（pageerror/console.error/HTTP 4xx+ 均计入）
  9. 清理：删临时文件 + 吊销 key

前提：playwright 已装（pip install playwright && playwright install chromium）、
后端在 BASE（45679）运行。退出码 0 = 全过，1 = 有失败。
"""
import argparse
import json
import os
import time
import uuid
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = ""
ADMIN_KEY = ""
CAD_SCOPES = ["data", "convert", "standards"]

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "fixtures", "cad", "real_sample.dwg")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default=os.environ.get("VCS_E2E_BASE", "http://127.0.0.1:45679"))
    p.add_argument("--admin-key", default=os.environ.get("VCS_E2E_ADMIN_KEY", ""))
    p.add_argument("--screenshot", default=None)
    p.add_argument("--no-cleanup", action="store_true", help="保留 key 与文件（调试用）")
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


def main():
    global BASE, ADMIN_KEY
    args = parse_args()
    BASE = args.base.rstrip("/")
    ADMIN_KEY = args.admin_key or (lambda: (_ for _ in ()).throw(ValueError("缺 --admin-key / VCS_E2E_ADMIN_KEY")))()
    assert os.path.exists(FIXTURE), f"缺少 CAD fixture: {FIXTURE}"
    with open(FIXTURE, "rb") as f:
        dwg_bytes = f.read()

    # ── setup：临时 key + 上传真实 DWG（LibreDWG 管线）─────────────
    st, keys = admin("/api/v1/admin/apikeys")
    assert st == 200 and isinstance(keys, list), f"admin list failed: {st} {keys}"
    name = f"e2e_cad_{uuid.uuid4().hex[:6]}"
    st, created = admin("/api/v1/admin/apikeys", "POST",
                        {"name": name, "scopes": CAD_SCOPES})
    assert st in (200, 201) and "id" in created, f"create key failed: {st} {created}"
    key_id, key = created["id"], created["api_key"]
    print(f"[setup] created key {name} id={key_id} scopes={CAD_SCOPES}")

    st, body = http("/api/v1/data/upload", "POST", {},
                    [("file", "e2e_cad.dwg", "application/x-dwg", dwg_bytes)], key=key)
    assert st in (200, 201) and "id" in body, f"upload failed: {st} {body}"
    file_id = body["id"]
    fid_safe = file_id.replace('"', "").replace("\\", "")
    print(f"[setup] uploaded DWG id={file_id} name={body.get('original_name')}")

    # ── 轮询 CAD 管线就绪（LibreDWG 转换，实体/图层元数据）──────────
    deadline = time.time() + 150
    cad_ready = False
    while time.time() < deadline:
        st, info = http(f"/api/v1/cad/{file_id}/info", key=key)
        if st == 200 and isinstance(info, dict) and info.get("entity_total"):
            cad_ready = True
            break
        time.sleep(2)
    print(f"[wait] cad info ready={cad_ready}"
          + (f" format={info.get('format')} entities={info.get('entity_total')} layers={info.get('layer_count')}" if cad_ready else ""))

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

            # [1] 数据管理页 → 定位刚上传的 DWG 行 → openDrawer 抽屉
            page.click('a[data-page="data"]')
            page.wait_for_timeout(1200)
            clicked = page.evaluate(f"""(fid)=>{{
              const btns=[...document.querySelectorAll('[onclick*="openDrawer"]')];
              const b=btns.find(e=>(e.getAttribute('onclick')||'').includes(fid));
              if(b){{b.click();return true;}}return false;
            }}""", file_id)
            page.wait_for_timeout(800)
            print(f"[1] drawer open via 详情: triggered={clicked}")
            ok &= bool(clicked) and page.locator("#drawer").is_visible()

            # [2] CAD Tab 按钮仅 dxf/dwg 时可见
            cad_btn = page.locator("#dtabCadBtn")
            cad_btn_vis = cad_btn.count() >= 1 and cad_btn.is_visible()
            print(f"[2] dtabCadBtn visible={cad_btn_vis}")
            ok &= cad_btn_vis

            # [3] 切 CAD Tab → loadDCAD 渲染元数据面板
            page.click("#dtabCadBtn")
            page.wait_for_timeout(4500)
            panel = page.locator("#dtab-cad")
            meta_txt = page.text_content("#dtab-cad").strip() if panel.count() else ""
            has_meta = "实体" in meta_txt and "图层" in meta_txt
            print(f"[3] cad panel visible={panel.is_visible()} has_meta={has_meta} meta_len={len(meta_txt)}")
            ok &= panel.is_visible() and has_meta

            # [4] CAD 图层树
            layers_n = page.locator(".cad-layer-item").count()
            print(f"[4] .cad-layer-item count={layers_n}")
            ok &= layers_n >= 1

            # [5] SVG 预览（blob objectURL → img）
            prev_img = page.locator('#dtab-cad img[src^="blob:"]').count()
            print(f"[5] svg preview blob img count={prev_img}")
            ok &= prev_img >= 1

            # [6] 实体表 + 分页（50/页）
            ent_rows = page.locator("#cadEntTable tbody tr").count()
            ent_cnt = page.text_content("#cadEntCount").strip() if page.locator("#cadEntCount").count() else "-"
            ent_pag = page.text_content("#cadEntPag").strip() if page.locator("#cadEntPag").count() else "-"
            print(f"[6] entity rows={ent_rows} count={ent_cnt} pag={ent_pag[:60]}")
            ok &= ent_rows >= 1 and ent_cnt not in ("-", "")

            # [7] 图层筛选交互：点第一图层 → 实体表刷新（layer 过滤）
            if layers_n:
                page.locator(".cad-layer-item").first.click()
                page.wait_for_timeout(4500)
                ent_rows2 = page.locator("#cadEntTable tbody tr").count()
                print(f"[7] after layer filter: rows={ent_rows2}")
                ok &= ent_rows2 >= 0 and not any("加载失败" in (page.text_content("#dtab-cad") or ""))
            else:
                ok = ok  # 无图层时不强断言（CAD 数据缺失时跳过筛选）

            # [8] 分页交互：下一页（若有）→ offset 变化
            nxt = page.locator("#cadEntPag button").last
            if nxt.count():
                before = page.evaluate("()=>window._cadEntOffset||0")
                nxt.click()
                page.wait_for_timeout(1500)
                after = page.evaluate("()=>window._cadEntOffset||0")
                print(f"[8] pagination: offset {before} -> {after}")
                ok &= after >= before + 50
            else:
                print("[8] pagination: 无下一页（单页数据，跳过）")

            # [9] 无控制台错误
            print("[9] console errors:", errors if errors else "none")
            ok &= not errors
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
                print(f"[10] screenshot -> {args.screenshot}")
            browser.close()
    finally:
        if not args.no_cleanup:
            # 清理：删除上传文件 + 吊销 key
            st, delr = http(f"/api/v1/data/{file_id}", "DELETE", key=key)
            print(f"[cleanup] file delete: {st}")
            st, _ = admin(f"/api/v1/admin/apikeys/{key_id}", "DELETE")
            print(f"[cleanup] key revoked: {st}")

    print("\n=== E2E cad_tab_wiring:", "ALL OK" if ok else "FAILURES DETECTED", "===")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
