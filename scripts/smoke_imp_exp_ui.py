"""v2.22.2 冒烟：单框三段导入/导出弹窗的真实 DOM 行为（Playwright，无后端）。

内容契约测试只能断言字符串；本脚本用真浏览器验证 v2IeFmt 的实际副作用：
格式按钮高亮、accept/multiple 切换、JSON 文本框与 dropzone 互斥、
导出按钮 CSV 禁用、模板按钮文案跟随格式、三段始终同框可见。

运行：python scripts/smoke_imp_exp_ui.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "app" / "frontend" / "index.html"

FAILURES: list[str] = []
PASSED = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {extra}")
        FAILURES.append(label)


def vis(page, sel: str) -> bool:
    """可见性判定。

    不能用 offsetParent：position:fixed 元素的 offsetParent 恒为 null（CSSOM 规范），
    会把整个模态框误判为不可见。改用 计算样式 + 包围盒，position 无关。
    """
    return page.eval_on_selector(
        sel,
        """e => {
          if (!e) return false;
          const s = getComputedStyle(e);
          if (s.display === 'none' || s.visibility === 'hidden') return false;
          if (parseFloat(s.opacity) === 0) return false;
          const r = e.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        }""",
    )


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(INDEX.as_uri())
        page.wait_for_timeout(600)

        print("\n[1] 打开弹窗")
        page.evaluate("v2ShowImpExp()")
        page.wait_for_timeout(200)
        check("弹窗可见", vis(page, "#v2ImportModal"))

        print("\n[2] 三段同框并列（无互斥隐藏）")
        for sid in ("v2IeSecImport", "v2IeSecExport", "v2IeSecTemplate"):
            check(f"{sid} 可见", vis(page, f"#{sid}"))

        print("\n[3] 默认 Excel 格式")
        check("Excel 按钮高亮",
              page.eval_on_selector("#v2IeFmtExcel", "e => e.classList.contains('active')"))
        check("file accept=.xlsx",
              page.eval_on_selector("#v2IeFile", "e => e.accept") == ".xlsx")
        check("file single", not page.eval_on_selector("#v2IeFile", "e => e.multiple"))
        check("dropzone 可见", vis(page, "#v2IeDropZone"))
        check("JSON 框隐藏", not vis(page, "#v2IeJsonBox"))
        check("导出按钮=Excel",
              "Excel" in page.inner_text("#v2IeExportBtn") and
              not page.eval_on_selector("#v2IeExportBtn", "e => e.disabled"))
        check("模板按钮=Excel 模板",
              "Excel" in page.inner_text("#v2IeTplBtn") and
              "ZIP" not in page.inner_text("#v2IeTplBtn"))

        print("\n[4] 切到 JSON")
        page.evaluate("v2IeFmt('json')")
        page.wait_for_timeout(150)
        check("JSON 按钮高亮",
              page.eval_on_selector("#v2IeFmtJson", "e => e.classList.contains('active')"))
        check("Excel 按钮取消高亮",
              not page.eval_on_selector("#v2IeFmtExcel", "e => e.classList.contains('active')"))
        check("JSON 框可见", vis(page, "#v2IeJsonBox"))
        check("dropzone 隐藏", not vis(page, "#v2IeDropZone"))
        check("导出按钮=JSON", "JSON" in page.inner_text("#v2IeExportBtn"))
        check("模板按钮=JSON 模板", "JSON" in page.inner_text("#v2IeTplBtn"))

        print("\n[5] 切到 CSV（多选 + ZIP 导出）")
        page.evaluate("v2IeFmt('csv')")
        page.wait_for_timeout(150)
        check("CSV accept=.csv",
              page.eval_on_selector("#v2IeFile", "e => e.accept") == ".csv")
        check("file multiple", page.eval_on_selector("#v2IeFile", "e => e.multiple"))
        check("dropzone 可见", vis(page, "#v2IeDropZone"))
        check("导出按钮可用（v2.22.3 解封）",
              not page.eval_on_selector("#v2IeExportBtn", "e => e.disabled"))
        check("导出按钮文案含 ZIP", "ZIP" in page.inner_text("#v2IeExportBtn"))
        check("导出按钮 tooltip 明示归档含 manifest",
              "_manifest.json" in (page.eval_on_selector("#v2IeExportBtn", "e => e.title") or ""))
        check("模板按钮含 ZIP", "ZIP" in page.inner_text("#v2IeTplBtn"))
        check("CSV 提示文案为多选",
              "多选" in page.inner_text("#v2IeDropHint"))

        print("\n[6] 切回 Excel：accept 与 multiple 复位")
        page.evaluate("v2IeFmt('excel')")
        page.wait_for_timeout(150)
        check("accept 复位 .xlsx",
              page.eval_on_selector("#v2IeFile", "e => e.accept") == ".xlsx")
        check("multiple 复位", not page.eval_on_selector("#v2IeFile", "e => e.multiple"))
        check("导出按钮恢复可用",
              not page.eval_on_selector("#v2IeExportBtn", "e => e.disabled"))
        check("导出按钮文案为 Excel",
              "Excel" in page.inner_text("#v2IeExportBtn") and
              "ZIP" not in page.inner_text("#v2IeExportBtn"))
        check("三段仍全部可见", all(
            vis(page, f"#{s}") for s in
            ("v2IeSecImport", "v2IeSecExport", "v2IeSecTemplate")))

        print("\n[7] 无未捕获 JS 异常")
        check("无 pageerror", not errors, str(errors[:3]))

        browser.close()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"失败 {len(FAILURES)}/{PASSED + len(FAILURES)} 例: {FAILURES}")
        return 1
    print(f"全部通过（{PASSED}/{PASSED} 例）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
