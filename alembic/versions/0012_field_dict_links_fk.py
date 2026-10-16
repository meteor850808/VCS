"""fix: field_dict_links.dict_id 外键改指 standard_dictionaries（v2.1 遗留）。

Revision ID: 0012_field_dict_links_fk
Create Date: 2026-09-10

背景（BASELINE §17「半迁移残留」）:
- 0001 基线创建 field_dict_links 时 dict_id FK → source_dictionaries.id（v2.1 旧字典）
- 0002 起字典体系迁移到 standard_dictionaries（新表 standard_field_dict_links 直接指
  standard_dictionaries）；模型 FieldDictLink.dict_id 亦已改指
  standard_dictionaries.id，但生产库的 FK 约束从未被迁移修正 → 升级后表级约束与
  应用模型不一致（dict_id 写入 standard_dictionaries 行时会因
  source_dictionaries 无对应行而违反 FK）。

本迁移:
- 实查 pg_constraint 拿到 dict_id 现存 FK 约束名（兼容 0001 默认命名或自定义命名）
- 若仍指向 source_dictionaries → drop 并重建指向 standard_dictionaries（ondelete CASCADE）
- 若已指向 standard_dictionaries（手动修正过的库）→ no-op 跳过，幂等
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_field_dict_links_fk"
down_revision = "0011_transform_results"
branch_labels = None
depends_on = None


def _dict_fk_info(con) -> tuple[str | None, str | None]:
    """返回 (约束名, 引用表)。dict_id 列现存 FOREIGN KEY 约束；无则 (None, None)。"""
    return con.execute(sa.text(
        """
        SELECT con.conname, conf.relname
        FROM pg_constraint con
        JOIN pg_class rel     ON rel.oid = con.conrelid
        JOIN pg_class conf    ON conf.oid = con.confrelid
        JOIN pg_attribute att ON att.attrelid = con.conrelid
                             AND att.attnum = ANY(con.conkey)
        WHERE rel.relname = 'field_dict_links'
          AND con.contype = 'f'
          AND att.attname = 'dict_id'
        """
    )).first()  # type: ignore[return-value]


def upgrade() -> None:
    con = op.get_bind()
    name, ref_table = _dict_fk_info(con)
    if name is None:
        # 表或约束不存在（新库按模型建表，或生产库重建过）→ 无需处理
        return
    if ref_table == "standard_dictionaries":
        # 已指向新字典表（手动修正 / 重建库）→ no-op
        return
    op.drop_constraint(name, "field_dict_links", type_="foreignkey")
    op.create_foreign_key(
        "field_dict_links_dict_id_fkey", "field_dict_links", "standard_dictionaries",
        ["dict_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    con = op.get_bind()
    name, ref_table = _dict_fk_info(con)
    if name is None or ref_table != "standard_dictionaries":
        # 约束缺失或已回退 → no-op
        return
    op.drop_constraint(name, "field_dict_links", type_="foreignkey")
    op.create_foreign_key(
        "field_dict_links_dict_id_fkey", "field_dict_links", "source_dictionaries",
        ["dict_id"], ["id"], ondelete="CASCADE",
    )