#!/usr/bin/env python3
"""
【已执行完毕，仅作历史参考，勿再运行】

本脚本于 v2.25.0 一次性执行完毕（`app/` 下 34 处归属写入已全部改造），当前代码里
已不存在它要匹配的模式，再跑是 no-op。保留它只为记录当时怎么批量改的。

批量更新**读路径**查询模式：将 `api_key_id == api_key.id` 替换为 group-aware 调用。

变换规则：
1. WHERE 过滤:  Model.api_key_id == api_key.id  →  auth.scope_clause(Model)
                         (在 .where(...) 上下文中)
2. 权属检查:    sf.api_key_id != api_key.id      →  not auth.owns(sf)

**本脚本不处理写路径**——写路径的正确做法见 ADR-0006：归属列一律写
`AuthContext.owner_id`（超管为 `None`，普通 Key 为真实 ID）。**不要**写
`api_key.id` / `auth.id`：超管的 `id` 是虚拟常量
`00000000-0000-0000-0000-000000000001`，在 `api_keys` 表无对应行，写入任何带外键的
`api_key_id` 列都会 `ForeignKeyViolationError` → HTTP 500。该约束由
`tests/test_super_admin_ownership_p8.py` 静态守卫强制。

用法: python scripts/migrate_auth_patterns.py [--dry-run]
"""

import os
import re
import sys
import glob

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIRS = [
    os.path.join(PROJECT_DIR, "app"),
]


def find_python_files():
    """找出所有 app/ 目录下所有 .py 文件。"""
    files = set()
    for app_dir in APP_DIRS:
        pattern = os.path.join(app_dir, "**", "*.py")
        for f in glob.glob(pattern, recursive=True):
            if "site-packages" not in f and "__pycache__" not in f:
                files.add(os.path.normpath(f))
    return sorted(files)


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def transform_file(content, filename):
    """对单个文件执行变换，返回 (new_content, changed_count)。"""
    changes = 0
    new_content = content

    # ── Pattern 1: Model.api_key_id == api_key.id → auth.scope_clause(Model) ──
    # 在 .where(...) 上下文中，且不在赋值/比较运算符右侧
    # 匹配: .where(Model.api_key_id == api_key.id)
    #        .where(..., Model.api_key_id == api_key.id, ...)
    pat1 = re.compile(
        r'(\w+)\.api_key_id\s*==\s*api_key\.id'
    )

    def repl1(m):
        model = m.group(1)
        return f'auth.scope_clause({model})'

    new_content, n1 = pat1.subn(repl1, new_content)
    changes += n1

    # ── Pattern 2: Model.api_key_id != api_key.id → not auth.owns(...) ──
    # 在 .where() 条件或 if 语句中
    pat2 = re.compile(
        r'(\w+)\.api_key_id\s*!=\s*api_key\.id'
    )

    def repl2(m):
        obj = m.group(1)
        return f'not auth.owns({obj})'

    new_content, n2 = pat2.subn(repl2, new_content)
    changes += n2

    # ── Pattern 3（已废弃）：非 where 上下文里的 api_key.id 写入 ──────────────
    # 旧版本此处写「CREATE 操作保留 api_key.id，因为 auth.id 委托到它」。该说法与
    # ADR-0006 相反，是会重新引入 HTTP 500 的错误知识，已删除。
    # 正确做法：写路径用 auth.owner_id（超管 None、普通 Key 真实 ID）；
    # 禁止 api_key.id / auth.id。详见 docs/adr/ADR-0006-ownership-columns-owner-id.md。
    # 本脚本不自动改写路径，避免在没有外键上下文的情况下猜测。

    # ── Pattern 4: 函数参数中 api_key_id 用于过滤 ──
    # 对于 service 函数, 添加类型提示
    # 只处理 def func(..., api_key_id: str, ...) 的模式
    # 这些需要手动检查，跳过自动替换

    return new_content, changes


def main():
    dry_run = "--dry-run" in sys.argv

    total_changes = 0
    changed_files = []

    for filepath in find_python_files():
        filename = os.path.relpath(filepath, PROJECT_DIR)
        content = read_file(filepath)
        new_content, changes = transform_file(content, filename)

        if changes > 0:
            total_changes += changes
            changed_files.append((filename, changes))
            if not dry_run:
                write_file(filepath, new_content)
            print(f"  {'[DRY]' if dry_run else ' [OK]'} {filename}: {changes} changes")

    print(f"\n{'='*60}")
    print(f"Total: {len(changed_files)} files, {total_changes} changes")
    if dry_run:
        print("Run without --dry-run to apply changes")
    else:
        print("Changes applied. Review with: git diff")


if __name__ == "__main__":
    main()
