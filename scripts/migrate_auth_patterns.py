#!/usr/bin/env python3
"""
批量更新查询模式：将 api_key_id == api_key.id 替换为 group-aware 调用。

变换规则：
1. WHERE 过滤:  Model.api_key_id == api_key.id  →  auth.scope_clause(Model)
                         (在 .where(...) 上下文中)
2. 权属检查:    sf.api_key_id != api_key.id      →  not auth.owns(sf)
3. 参数重命名:  函数定义中 api_key_id: str 作为过滤参数 → 添加说明或改为 auth 上下文

用法: python scripts/migrate_auth_patterns.py [--dry-run]
"""

import os
import re
import sys
import glob

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 需要同时处理两个 app/ 目录
APP_DIRS = [
    os.path.join(PROJECT_DIR, "app"),
    os.path.join(PROJECT_DIR, "空间API服务", "app"),
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

    # ── Pattern 3: == api_key.id 在非 where 上下文中 → use auth.id ──
    # 这通常是 CREATE 操作，我们保留它们（auth.id 委托到 api_key.id）

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
