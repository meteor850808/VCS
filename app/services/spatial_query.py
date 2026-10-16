"""空间查询服务 — BBOX / KNN / 空间关系谓词。

依赖 PostgreSQL + PostGIS 空间索引（GIST）。

用法:
    from app.services.spatial_query import SpatialQueryService

    svc = SpatialQueryService(db_session)
    results = await svc.bbox_query(xmin, ymin, xmax, ymax, layer="roads")
    nearest = await svc.knn_query(lon, lat, k=10, layer="poi")
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class SpatialQueryResult:
    feature_id: str
    properties: dict[str, Any]
    geom_wkt: str
    distance: float | None = None


class SpatialQueryService:
    """PostGIS 空间查询服务。"""

    def __init__(self, session: AsyncSession):
        self.db = session

    async def bbox_query(
        self,
        xmin: float, ymin: float, xmax: float, ymax: float,
        layer: str = "point_features",
        srid: int = 4326,
        limit: int = 200,
    ) -> list[SpatialQueryResult]:
        """包围盒查询 — 返回与 BBOX 相交的所有要素（跨三表）。"""
        tables = ["point_features", "line_features", "polygon_features"]
        if layer in tables:
            tables = [layer]
        union_parts = []
        for tbl in tables:
            union_parts.append(f"""
                SELECT id AS feature_id, properties, ST_AsText(geometry) AS geom_wkt
                FROM {tbl}
                WHERE ST_Transform(geometry, :srid) && ST_MakeEnvelope(:xmin, :ymin, :xmax, :ymax, :srid)
                  AND ST_Intersects(ST_Transform(geometry, :srid), ST_MakeEnvelope(:xmin, :ymin, :xmax, :ymax, :srid))
            """)
        query = text("SELECT * FROM (" + " UNION ALL ".join(union_parts) + f") AS sub LIMIT {limit}")
        result = await self.db.execute(query, {
            "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
            "srid": srid, "limit": limit,
        })
        return [
            SpatialQueryResult(
                feature_id=row.feature_id,
                properties=row.properties or {},
                geom_wkt=row.geom_wkt,
            )
            for row in result
        ]

    async def knn_query(
        self,
        lon: float, lat: float,
        k: int = 10,
        layer: str = "point_features",
        srid: int = 4326,
    ) -> list[SpatialQueryResult]:
        """K 近邻查询 — 返回距离点最近的 K 个要素（跨三表）。"""
        tables = ["point_features", "line_features", "polygon_features"]
        if layer in tables:
            tables = [layer]
        query = text("""
            SELECT feature_id, properties, geom_wkt, distance FROM (""" +
            " UNION ALL ".join(f"""
                SELECT id AS feature_id, properties,
                       ST_AsText(geometry) AS geom_wkt,
                       ST_Distance(ST_Transform(geometry, :srid), ST_SetSRID(ST_MakePoint(:lon, :lat), :srid)) AS distance
                FROM {tbl}
            """ for tbl in tables) + """
            ) AS sub
            ORDER BY distance
            LIMIT :k
        """)
        result = await self.db.execute(query, {
            "lon": lon, "lat": lat, "srid": srid, "k": k,
        })
        return [
            SpatialQueryResult(
                feature_id=row.feature_id,
                properties=row.properties or {},
                geom_wkt=row.geom_wkt,
                distance=float(row.distance) if row.distance else None,
            )
            for row in result
        ]

    async def spatial_relation(
        self,
        source_id: str,
        relation: str = "intersects",
        layer: str = "point_features",
        srid: int = 4326,
    ) -> list[SpatialQueryResult]:
        """空间关系查询 — 查找与指定要素存在特定关系的要素（跨三表）。

        relation: intersects, contains, within, touches, crosses, overlaps
        """
        valid_relations = {"intersects", "contains", "within", "touches", "crosses", "overlaps"}
        if relation not in valid_relations:
            raise ValueError(f"不支持的空间关系: {relation}，有效值: {valid_relations}")

        tables = ["point_features", "line_features", "polygon_features"]
        if layer in tables:
            tables = [layer]
        st_func = f"ST_{relation.capitalize()}"
        unions = []
        for tbl in tables:
            unions.append(f"""
                SELECT s.id AS feature_id, s.properties, ST_AsText(s.geometry) AS geom_wkt
                FROM {tbl} t
                JOIN {tbl} s ON {st_func}(t.geometry, s.geometry)
                WHERE t.id = :source_id AND s.id != :source_id
            """)
        query = text("(" + ") UNION (".join(unions) + ") LIMIT 200")
        result = await self.db.execute(query, {"source_id": source_id})
        return [
            SpatialQueryResult(
                feature_id=row.feature_id,
                properties=row.properties or {},
                geom_wkt=row.geom_wkt,
            )
            for row in result
        ]

    async def spatial_stats(
        self,
        layer: str = "all",
        srid: int = 4326,
    ) -> dict[str, Any]:
        """空间统计 — 要素数、面积和、重心等聚合信息（跨三表）。"""
        tables = ["point_features", "line_features", "polygon_features"]
        if layer in tables:
            tables = [layer]

        feature_count = 0
        total_area = 0.0
        total_vertices = 0

        for tbl in tables:
            query = text(f"""
                SELECT
                    COUNT(*) AS feature_count,
                    COALESCE(SUM(ST_NPoints(geometry)), 0) AS total_vertices
                FROM {tbl}
                WHERE geometry IS NOT NULL
            """)
            result = await self.db.execute(query)
            row = result.fetchone()
            feature_count += int(row.feature_count) if row and row.feature_count else 0
            total_vertices += int(row.total_vertices) if row and row.total_vertices else 0

            # 面积只在 polygon 表有意义
            if "polygon" in tbl:
                area_q = text(f"""
                    SELECT COALESCE(SUM(ST_Area(geometry)), 0) FROM {tbl} WHERE geometry IS NOT NULL
                """)
                area_r = await self.db.execute(area_q)
                total_area += float(area_r.scalar() or 0.0)

        return {
            "feature_count": feature_count,
            "total_area": round(total_area, 3),
            "total_vertices": total_vertices,
        }
