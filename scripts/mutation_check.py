"""v2.22.3 CSV 归档关键路径变异测试。

逐个注入一个语义变异，跑相关测试，断言「测试真的抓得住」。
每个变异跑完立即还原 —— 这是历史教训（§七 编码事故）要求的纪律。

用法: python scripts/mutation_check.py
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "api" / "v1" / "data_standard_v2" / "import_export.py"
BACKUP = TARGET.with_suffix(".py.mutationbak")

# (编号, 变异描述, 原文, 替换文, 相关测试选择器)
MUTATIONS = [
    ("M1", "必需角色判断反转：tables/fields 缺席不报 400",
     '_CSV_REQUIRED_ROLES = ("tables", "fields")',
     '_CSV_REQUIRED_ROLES = ()',
     "test_imp_exp_dict_roundtrip"),

    ("M2", "「名称永不覆盖」被去掉：归档名称直接改写导入名称",
     "    return form_name, description, category",
     "    return meta.get('name') or form_name, description, category",
     "test_imp_exp_dict_roundtrip"),

    ("M3", "空字典又被过滤掉（回退到 Excel 侧 if d.items 的旧行为）",
     "    for dictionary in dictionaries:\n        dict_rows.append([",
     "    for dictionary in (dictionaries if all(d.items for d in dictionaries) else [d for d in dictionaries if d.items]):\n        dict_rows.append([",
     "test_imp_exp_dict_roundtrip"),

    ("M4", "重复编码检测失效：diagnostics 永远为空",
     "    diagnostics: list[str] = []\n    for label, dupes in (",
     "    diagnostics: list[str] = []\n    for label, dupes in (\n        # mutation: disabled\n        ('x', [])  # noqa",
     "test_imp_exp_dict_roundtrip"),

    ("M5", "未知字典编码不告警（静默跳过绑定）",
     "                warnings.append(f\"字段 {field.field_name} 的字典编码 {code} 未在字典中声明，已忽略\")\n                continue",
     "                continue",
     "test_imp_exp_dict_roundtrip"),

    ("M6", "standard 角色的 description 不消费（回到本轮修复前）",
     '        "description": _csv_cell(rows[0], 1),',
     '        "description": "",',
     "test_imp_exp_dict_roundtrip"),

    ("M7", "BOM 被移除（中文用户在 Excel 里双击会乱码）",
     "    return _CSV_BOM + out.getvalue().encode(\"utf-8\")",
     "    return out.getvalue().encode(\"utf-8\")",
     "test_imp_exp_dict_roundtrip"),

    ("M8", "必需角色缺席只告警不报错（静默建空标准）",
     "        if role in _CSV_REQUIRED_ROLES:\n            # 必需角色既没声明也没文件 → 归档不是有效的 v1 归档，不能静默建空标准\n            raise HTTPException(400, f\"CSV 归档缺少必需角色 {role}（manifest.roles 未声明）\")\n        return None",
     "        if role in _CSV_REQUIRED_ROLES:\n            warnings.append(f'缺必需角色 {role}')\n        return None",
     "test_imp_exp_dict_roundtrip"),

    ("M9", "standard 角色回到本轮修掉的错位 bug（首格是表头文字 + 3 个数据值）",
     '        "standard": [_CSV_HEADERS["standard"],\n                     [std.name or "", std.description or "", std.category or ""]],',
     '        "standard": [[_CSV_HEADERS["standard"][0], std.name or "", std.description or "",\n                      std.category or ""]],',
     "test_imp_exp_dict_roundtrip"),

    ("M10", "legacy 降级失效：有 manifest 也走 legacy（外部作者的旧 ZIP 会静默丢结构）",
     "    if manifest is not None:\n        name, description, category = _merge_standard_meta(",
     "    if False:\n        name, description, category = _merge_standard_meta(",
     "test_imp_exp_dict_roundtrip"),

    ("M11", "重复层组编码不再跳过（撞 DB 唯一约束 500）",
     "            if code and code in seen_codes:",
     "            if False:",
     "test_imp_exp_dict_roundtrip"),
]

TESTS = [
    "tests/test_imp_exp_dict_roundtrip.py",
    "tests/test_csv_upload_edge_cases.py",
    "tests/test_import_export_routes.py",
]


def run_tests(selector=None) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", *TESTS, "-q", "--no-cov", "-x"]
    if selector:
        cmd += ["-k", selector]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0, proc.stdout[-400:]


def main():
    original = TARGET.read_text(encoding="utf-8")
    BACKUP.write_text(original, encoding="utf-8")

    print("=" * 74)
    print("baseline: all tests must pass before mutation")
    print("=" * 74)
    ok, tail = run_tests()
    print("PASS" if ok else "FAIL")
    if not ok:
        print(tail)
        BACKUP.unlink(missing_ok=True)
        return 1

    caught, missed, broken = [], [], []
    for tag, desc, old, new, selector in MUTATIONS:
        if original.count(old) != 1:
            print(f"{tag} SKIP  锚点不唯一/不存在（count={original.count(old)}）— 变异未注入")
            broken.append(f"{tag} 锚点失效")
            continue
        TARGET.write_text(original.replace(old, new, 1), encoding="utf-8", newline="")
        ok, tail = run_tests(selector)
        detail = ""
        for line in tail.splitlines():
            if " failed" in line or " passed" in line:
                detail = line.strip()
        if ok:
            missed.append(f"{tag} {desc}")
            print(f"{tag} MISSED  {desc}")
        else:
            caught.append(f"{tag} {desc}")
            print(f"{tag} caught  {desc}  [{detail}]")
        TARGET.write_text(original, encoding="utf-8", newline="")

    # 还原后必须回到绿色
    ok, tail = run_tests()
    print()
    print("restored baseline: " + ("PASS" if ok else "FAIL -- file not restored cleanly!"))
    if not ok:
        print(tail)
    BACKUP.unlink(missing_ok=True)

    print()
    print(f"caught {len(caught)} / missed {len(missed)} / injection-failed {len(broken)}")
    if missed:
        print("\nsurviving mutations (tests do NOT guard these paths):")
        for m in missed:
            print(f"  - {m}")
    if broken:
        print("\nfailed injections:")
        for b in broken:
            print(f"  - {b}")
    return 0 if (ok and not missed and not broken) else 1


if __name__ == "__main__":
    sys.exit(main())
