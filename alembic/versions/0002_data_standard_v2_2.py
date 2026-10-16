"""data_standard v2.2 — 新增标准层级模型。

Revision ID: 0002_data_standard_v2_2
Create Date: 2026-07-15 (fixed 2026-07-16)

变更内容：
- 新增 standard_layer_groups (支持嵌套层组)
- 新增 standard_tables (标准数据表)
- 新增 standard_fields (标准字段，合并原 DataStandardField + SourceField)
- 新增 standard_dictionaries (标准字典，支持 scope)
- 新增 dictionary_items (字典条目)
- 新增 standard_field_dict_links (字段-字典关联)
- 更新 data_standards 表 (添加 category/status/version + 唯一约束)
- 更新 quality_rules 外键 (standard_field_id 指向新表)

修复记录 (2026-07-16):
- new_dictionary_items → dictionary_items (与 ORM 模型表名一致)
- dictionary_items 补充 created_at/updated_at 列 (ORM 模型需要)
- quality_rules FK: 使用 IF EXISTS 安全删除
- 约束命名统一 (与 ORM __table_args__ 一致)
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0002_data_standard_v2_2"
down_revision = "0001_baseline_v2_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. 更新 data_standards 表 ──
    op.add_column("data_standards", sa.Column("category", sa.String(64), nullable=False, server_default="custom"))
    op.add_column("data_standards", sa.Column("status", sa.String(32), nullable=False, server_default="draft"))
    op.add_column("data_standards", sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")))
    # 注：data_standards.updated_at 已于 0001 建表时创建，此处不再重复 ADD
    #（原 38 行 add_column updated_at 在全新库上会 DuplicateColumnError，
    #  2026-09-10 空库验证时发现的迁移链缺陷，已移除）
    # file_name 改为可选
    op.alter_column("data_standards", "file_name", existing_type=sa.String(512), nullable=True)
    # 添加唯一约束
    op.create_unique_constraint("uq_ds_api_name", "data_standards", ["api_key_id", "name"])

    # ── 2. standard_layer_groups ──
    op.create_table(
        "standard_layer_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("standard_layer_groups.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("group_name", sa.String(256), nullable=False),
        sa.Column("group_code", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("standard_id", "parent_id", "group_name", name="uq_slg_std_parent_name"),
        sa.UniqueConstraint("standard_id", "parent_id", "group_code", name="uq_slg_std_parent_code"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id != id", name="ck_slg_no_self_ref"),
    )

    # ── 3. standard_tables ──
    op.create_table(
        "standard_tables",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("layer_id", sa.String(36), sa.ForeignKey("standard_layer_groups.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("table_name", sa.String(256), nullable=False),
        sa.Column("table_code", sa.String(64), nullable=True),
        sa.Column("table_type", sa.String(32), nullable=False, server_default="spatial"),
        sa.Column("geometry_type", sa.String(32), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("layer_id", "table_name", name="uq_st_layer_name"),
        sa.UniqueConstraint("layer_id", "table_code", name="uq_st_layer_code"),
    )

    # ── 4. standard_fields ──
    op.create_table(
        "standard_fields",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("table_id", sa.String(36), sa.ForeignKey("standard_tables.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("field_name", sa.String(256), nullable=False),
        sa.Column("field_alias", sa.String(256), nullable=True),
        sa.Column("data_type", sa.String(32), nullable=False, server_default="string"),
        sa.Column("length", sa.Integer, nullable=True),
        sa.Column("precision", sa.Integer, nullable=True),
        sa.Column("nullable", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_primary_key", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("default_value", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("table_id", "field_name", name="uq_sf_table_field"),
    )

    # ── 5. standard_dictionaries ──
    op.create_table(
        "standard_dictionaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("standard_id", sa.String(36), sa.ForeignKey("data_standards.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("api_key_id", sa.String(36), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("dict_name", sa.String(256), nullable=False),
        sa.Column("dict_code", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("scope", sa.String(32), nullable=False, server_default="standard"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("standard_id", "dict_name", name="uq_sd_standard_name"),
    )

    # ── 6. dictionary_items —— 0001 已建全字段，仅迁移 dict_id 外键到新字典体系 ──
    #（原 create_table 与 0001 重名，全新库必然 DuplicateTableError；
    #  2026-09-10 空库验证发现的链缺陷。表结构两版一致，正解决：drop 旧 FK
    #  → 重建指向 standard_dictionaries → 补 (dict_id, item_code) 唯一约束。）
    conn = op.get_bind()
    di_fk = conn.execute(sa.text(
        """
        SELECT con.conname, conf.relname
        FROM pg_constraint con
        JOIN pg_class rel  ON rel.oid = con.conrelid
        JOIN pg_class conf ON conf.oid = con.confrelid
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
        WHERE rel.relname = 'dictionary_items' AND con.contype = 'f' AND att.attname = 'dict_id'
        """
    )).first()
    if di_fk is None or di_fk[1] != "standard_dictionaries":
        fk_name = di_fk[0] if di_fk else "dictionary_items_dict_id_fkey"
        op.drop_constraint(fk_name, "dictionary_items", type_="foreignkey")
        op.create_foreign_key(
            "dictionary_items_dict_id_fkey", "dictionary_items", "standard_dictionaries",
            ["dict_id"], ["id"], ondelete="CASCADE",
        )
    op.create_unique_constraint("uq_di_dict_code", "dictionary_items", ["dict_id", "item_code"])

    # ── 7. standard_field_dict_links ──
    op.create_table(
        "standard_field_dict_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("field_id", sa.String(36), sa.ForeignKey("standard_fields.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("dict_id", sa.String(36), sa.ForeignKey("standard_dictionaries.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("filter_condition", sa.String(256), nullable=True),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("ordering", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("field_id", "dict_id", name="uq_sfdl_field_dict"),
    )

    # ── 8. 更新 quality_rules 外键 ──
    # PostgreSQL 约束名不可预测，用 DO block 安全删除旧 FK（如果存在）
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'quality_rules'::regclass
                  AND contype = 'f'
                  AND pg_get_constraintdef(oid) LIKE '%data_standard_fields%'
            ) THEN
                ALTER TABLE quality_rules
                    DROP CONSTRAINT quality_rules_standard_field_id_fkey;
            END IF;
        END$$;
    """)
    op.create_foreign_key(
        "quality_rules_standard_field_id_fkey",
        "quality_rules", "standard_fields",
        ["standard_field_id"], ["id"],
        ondelete="CASCADE"
    )


def downgrade() -> None:
    # ── 恢复 quality_rules 外键 ──
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'quality_rules'::regclass
                  AND contype = 'f'
                  AND conname = 'quality_rules_standard_field_id_fkey'
            ) THEN
                ALTER TABLE quality_rules
                    DROP CONSTRAINT quality_rules_standard_field_id_fkey;
            END IF;
        END$$;
    """)

    # ── 删除新表 (按外键依赖逆序) ──
    op.drop_table("standard_field_dict_links")
    op.drop_table("standard_dictionaries")
    op.drop_table("standard_fields")
    op.drop_table("standard_tables")
    op.drop_table("standard_layer_groups")
    # dictionary_items 属 0001，不回退表；仅回退字典外键与唯一约束
    conn = op.get_bind()
    di_fk = conn.execute(sa.text(
        """
        SELECT con.conname, conf.relname
        FROM pg_constraint con
        JOIN pg_class rel  ON rel.oid = con.conrelid
        JOIN pg_class conf ON conf.oid = con.confrelid
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
        WHERE rel.relname = 'dictionary_items' AND con.contype = 'f' AND att.attname = 'dict_id'
        """
    )).first()
    op.drop_constraint("uq_di_dict_code", "dictionary_items", type_="unique")
    if di_fk is not None and di_fk[1] == "standard_dictionaries":
        op.drop_constraint(di_fk[0], "dictionary_items", type_="foreignkey")
        op.create_foreign_key(
            "dictionary_items_dict_id_fkey", "dictionary_items", "source_dictionaries",
            ["dict_id"], ["id"], ondelete="CASCADE",
        )

    # ── 恢复 data_standards ──
    op.drop_constraint("uq_ds_api_name", "data_standards", type_="unique")
    op.drop_column("data_standards", "category")
    op.drop_column("data_standards", "status")
    op.drop_column("data_standards", "version")
    # 注：data_standards.updated_at 属 0001，此处不回退（配合 upgrade 修复）
    op.alter_column("data_standards", "file_name", existing_type=sa.String(512), nullable=False)
