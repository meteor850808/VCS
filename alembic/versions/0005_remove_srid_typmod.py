"""Migration: remove SRID typmod from spatial feature geometry columns (S2).

v2.8 S2 变更：三张空间要素表的 geometry 列去除 srid=4326 typmod 约束，
使每行要素可按源 CRS 标注各自 SRID（源 4490/4547/北京54/西安80 等），
预览查询端统一 ST_Transform(geometry, 4326) 输出。

已有数据保持 4326 SRID 不变；去掉 typmod 后列变为 geometry(Geometry)。
"""
from alembic import op

revision = "0005_remove_srid_typmod"
down_revision = "0004_add_key_group"
branch_labels = None
depends_on = None

TABLES = ("point_features", "line_features", "polygon_features")


def upgrade():
    for tbl in TABLES:
        op.execute(f'ALTER TABLE {tbl} ALTER COLUMN geometry TYPE geometry;')


def downgrade():
    for tbl in TABLES:
        op.execute(
            f'ALTER TABLE {tbl} ALTER COLUMN geometry TYPE geometry(Geometry,4326);'
        )
