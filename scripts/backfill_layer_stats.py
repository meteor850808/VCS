#!/usr/bin/env python3
"""回填「图层 × 几何类型」统计（v2.10.5）— 幂等，可重复执行。

从三张分区要素表实时聚合：
  1. 全量重建 stored_file_layers（每文件每图层一行，点/线/面三维计数）
  2. 回写 stored_files.point_count / line_count / polygon_count 冗余列

用法:
    python scripts/backfill_layer_stats.py            # 执行回填
    python scripts/backfill_layer_stats.py --dry-run  # 仅打印统计，不写库

背景：迁移 0010 新建的统计结构只覆盖新上传数据；历史入库文件的
图层统计需本脚本一次性补齐。此后增量由上传/合并/reindex 链路的
aggregate_layer_stats 自动维护。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.session import SyncSessionLocal


# 三表 UNION 后按 (stored_file_id, layer_name) 聚合；geometry_type 按"出现的类型种数"判定
_AGG_SQL = text("""
    WITH p AS (
        SELECT stored_file_id, layer_name, api_key_id,
               COUNT(*) AS cnt, 0 AS line_c, 0 AS poly_c
        FROM point_features GROUP BY stored_file_id, layer_name, api_key_id
    ),
    l AS (
        SELECT stored_file_id, layer_name, api_key_id,
               0 AS cnt, COUNT(*) AS line_c, 0 AS poly_c
        FROM line_features GROUP BY stored_file_id, layer_name, api_key_id
    ),
    g AS (
        SELECT stored_file_id, layer_name, api_key_id,
               0 AS cnt, 0 AS line_c, COUNT(*) AS poly_c
        FROM polygon_features GROUP BY stored_file_id, layer_name, api_key_id
    ),
    u AS (
        SELECT stored_file_id, layer_name, api_key_id, cnt, line_c, poly_c FROM p
        UNION ALL
        SELECT stored_file_id, layer_name, api_key_id, cnt, line_c, poly_c FROM l
        UNION ALL
        SELECT stored_file_id, layer_name, api_key_id, cnt, line_c, poly_c FROM g
    )
    SELECT stored_file_id,
           COALESCE(MAX(api_key_id), '')          AS api_key_id,
           layer_name,
           SUM(cnt)::int                           AS point_count,
           SUM(line_c)::int                        AS line_count,
           SUM(poly_c)::int                        AS polygon_count,
           (SUM(cnt) + SUM(line_c) + SUM(poly_c))::int AS feature_count,
           CASE
               WHEN (CASE WHEN SUM(cnt) > 0 THEN 1 ELSE 0 END
                  + CASE WHEN SUM(line_c) > 0 THEN 1 ELSE 0 END
                  + CASE WHEN SUM(poly_c) > 0 THEN 1 ELSE 0 END) > 1 THEN 'mixed'
               WHEN SUM(cnt) > 0     THEN 'Point'
               WHEN SUM(line_c) > 0  THEN 'LineString'
               ELSE 'Polygon'
           END                                     AS geometry_type
    FROM u
    WHERE layer_name IS NOT NULL AND layer_name <> ''
    GROUP BY stored_file_id, layer_name
""")

_INSERT_LAYERS = text("""
    INSERT INTO stored_file_layers
        (id, stored_file_id, api_key_id, layer_name, geometry_type,
         point_count, line_count, polygon_count, feature_count)
    VALUES (:id, :stored_file_id, :api_key_id, :layer_name, :geometry_type,
            :point_count, :line_count, :polygon_count, :feature_count)
""")

_UPDATE_FILE_COUNTERS = text("""
    UPDATE stored_files sf SET
        point_count   = COALESCE(a.point_count, 0),
        line_count    = COALESCE(a.line_count, 0),
        polygon_count = COALESCE(a.polygon_count, 0)
    FROM (
        SELECT stored_file_id,
               SUM(point_count)   AS point_count,
               SUM(line_count)    AS line_count,
               SUM(polygon_count) AS polygon_count
        FROM stored_file_layers
        GROUP BY stored_file_id
    ) a
    WHERE sf.id = a.stored_file_id
""")

_ZERO_EMPTY_FILES = text("""
    UPDATE stored_files
    SET point_count = 0, line_count = 0, polygon_count = 0
    WHERE id NOT IN (SELECT DISTINCT stored_file_id FROM stored_file_layers)
      AND (point_count <> 0 OR line_count <> 0 OR polygon_count <> 0)
""")


def main() -> int:
    parser = argparse.ArgumentParser(description="回填图层 × 类型统计（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印统计，不写库")
    args = parser.parse_args()

    import uuid

    with SyncSessionLocal() as session:
        rows = session.execute(_AGG_SQL).mappings().all()
        total_features = sum(r["feature_count"] for r in rows)
        print(f"聚合完成: {len(rows)} 个图层行 / {total_features} 条要素"
              f"（{len({r['stored_file_id'] for r in rows})} 个文件）")

        if args.dry_run:
            for r in rows[:20]:
                print(f"  {r['layer_name']:<30} {r['geometry_type']:<12}"
                      f"P{r['point_count']} L{r['line_count']} G{r['polygon_count']}")
            if len(rows) > 20:
                print(f"  ... 其余 {len(rows) - 20} 行省略")
            return 0

        session.execute(text("DELETE FROM stored_file_layers"))
        for r in rows:
            session.execute(_INSERT_LAYERS, {
                "id": str(uuid.uuid4()),
                "stored_file_id": r["stored_file_id"],
                "api_key_id": r["api_key_id"],
                "layer_name": r["layer_name"],
                "geometry_type": r["geometry_type"],
                "point_count": r["point_count"],
                "line_count": r["line_count"],
                "polygon_count": r["polygon_count"],
                "feature_count": r["feature_count"],
            })
        n_files = session.execute(_UPDATE_FILE_COUNTERS).rowcount
        n_zero = session.execute(_ZERO_EMPTY_FILES).rowcount
        session.commit()
        print(f"已写入 stored_file_layers {len(rows)} 行；"
              f"更新 stored_files 计数 {n_files} 个文件（另归零 {n_zero} 个空文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
