#!/usr/bin/env python3
"""把迁移 0014_scoring_profile 的结构变更直接应用到「脱管」数据库 — 幂等。

背景：线上库的 `alembic_version` 被人工 stamp 成仓库里不存在的 revision
（`0014_schema_drift_repair`），alembic 对它完全不可用（任何命令都报
"Can't locate revision"）。因此本脚本用幂等 DDL 直接落地 0014 的等价变更，
**不改动 `alembic_version`**；仓库侧的迁移链仍保持 0014 ← 0013，干净库走
`alembic upgrade head`。

变更内容（与 alembic/versions/0014_scoring_profile.py 等价）：
  1. quality_reports 增加 scoring_profile（varchar(8) NOT NULL DEFAULT 'v2'）
  2. 增加 score_evaluated（jsonb，可空）
  3. 增加 evaluation_errors（jsonb，可空）
  4. 五个 score_* 列 DROP NOT NULL（NULL = 该维度本轮未评价，不是 0 分）

用法:
    python scripts/apply_0014.py --dry-run   # 只读：报告当前结构与待执行动作
    python scripts/apply_0014.py             # 执行（可重复跑，已存在的跳过）

降级不提供：需要回滚时用 0014 的 downgrade（把 NULL 回填 0.0 再恢复 NOT NULL），
但那要求 alembic 能解析当前版本；脱管库请手工执行等价 SQL。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 中文 Windows 控制台默认 GBK，输出 ✓/✗ 会抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from app.db.session import SyncSessionLocal

SCORE_COLS = (
    "score_completeness",
    "score_accuracy",
    "score_consistency",
    "score_uniqueness",
    "score_topology",
)

# 顺序固定：先加列（有默认值，存量行自动填 v2），再放宽五列可空
DDL = [
    (
        "add scoring_profile",
        "ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS "
        "scoring_profile varchar(8) NOT NULL DEFAULT 'v2'",
    ),
    (
        "add score_evaluated",
        "ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS score_evaluated jsonb",
    ),
    (
        "add evaluation_errors",
        "ALTER TABLE quality_reports ADD COLUMN IF NOT EXISTS evaluation_errors jsonb",
    ),
] + [
    (f"relax {col}", f"ALTER TABLE quality_reports ALTER COLUMN {col} DROP NOT NULL")
    for col in SCORE_COLS
]

_STATE_SQL = text(
    """
    SELECT column_name, is_nullable, data_type, column_default
    FROM information_schema.columns
    WHERE table_name = 'quality_reports'
      AND column_name IN (
        'scoring_profile', 'score_evaluated', 'evaluation_errors',
        'score_completeness', 'score_accuracy', 'score_consistency',
        'score_uniqueness', 'score_topology'
      )
    ORDER BY column_name
    """
)


def show_state(db) -> dict:
    rows = db.execute(_STATE_SQL).fetchall()
    state = {}
    for name, nullable, dtype, default in rows:
        state[name] = (nullable, dtype, default)
        print(f"  {name:20s} nullable={nullable:3s} type={dtype:12s} default={default}")
    return state


def verify(db) -> list[str]:
    """返回未达标的项；空列表表示结构已与 0014 一致。"""
    state = show_state(db)
    problems = []
    for name in ("scoring_profile", "score_evaluated", "evaluation_errors"):
        if name not in state:
            problems.append(f"缺少列 {name}")
    for name in SCORE_COLS:
        if name not in state:
            problems.append(f"缺少列 {name}")
        elif state[name][0] != "YES":
            problems.append(f"{name} 仍为 NOT NULL")
    if "scoring_profile" in state and state["scoring_profile"][0] != "NO":
        problems.append("scoring_profile 应保持 NOT NULL")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="幂等应用 0014_scoring_profile 的结构变更")
    ap.add_argument("--dry-run", action="store_true", help="只读：报告当前结构与待执行动作")
    args = ap.parse_args()

    with SyncSessionLocal() as db:
        print("当前 quality_reports 相关列：")
        before = show_state(db)
        pending = [
            label for label, sql in DDL
            # 五列的 DROP NOT NULL 在已是可空时无需重跑；加列用 IF NOT EXISTS 自带幂等
            if not _already_done(label, before)
        ]
        if not pending:
            print("\n无需变更：结构已与 0014 一致。")
            return 0
        print("\n待执行动作：", "、".join(pending))
        if args.dry_run:
            print("（--dry-run，未执行任何 DDL）")
            return 0

        for label, sql in DDL:
            db.execute(text(sql))
            print(f"  ok  {label}")
        db.commit()

        print("\n执行后校验：")
        problems = verify(db)
        if problems:
            for p in problems:
                print(f"[FAIL] {p}")
            return 1
        print("[OK] 结构已与 0014_scoring_profile 一致")
    return 0


def _already_done(label: str, state: dict) -> bool:
    """按 label 判断该项是否已满足（用于 dry-run 与跳过提示）。"""
    if label.startswith("relax "):
        col = label.split(" ", 1)[1]
        return state.get(col, ("NO",))[0] == "YES"
    col = label.split(" ", 1)[1]
    return col in state


if __name__ == "__main__":
    raise SystemExit(main())
