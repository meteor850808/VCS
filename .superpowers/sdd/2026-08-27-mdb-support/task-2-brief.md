## Task 2: 创建 MDB 处理器核心功能

**Files:**
- Create: `app/services/mdb_processor.py`
- Create: `tests/test_mdb_processor.py`

**Interfaces:**
- Consumes: pyodbc, json, tempfile, pathlib
- Produces: `extract_mdb_features()`, `extract_mdb_to_stored_files()`

- [ ] **Step 1: 创建测试文件框架**

```python
# tests/test_mdb_processor.py
import pytest
import tempfile
import os
from pathlib import Path

def test_mdb_processor_import():
    """Test that mdb_processor module can be imported."""
    from app.services.mdb_processor import extract_mdb_features, extract_mdb_to_stored_files
    assert callable(extract_mdb_features)
    assert callable(extract_mdb_to_stored_files)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_mdb_processor.py -v
```

Expected: FAIL with "ModuleNotFoundError: No module named 'app.services.mdb_processor'"

- [ ] **Step 3: 创建 mdb_processor.py 基础结构**

```python
# app/services/mdb_processor.py
"""MDB (ESRI Personal Geodatabase) 处理器。

读取 .mdb 文件中的空间表和属性表，提取为 GeoJSON features。
"""
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def extract_mdb_features(mdb_path: str) -> list[dict[str, Any]]:
    """从 MDB 文件中提取所有空间表的要素。
    
    Args:
        mdb_path: MDB 文件路径
        
    Returns:
        [{"table_name": str, "geometry_type": str, "features": [...], "fields": [...]}]
    """
    # TODO: 实现 MDB 读取逻辑
    raise NotImplementedError("MDB extraction not yet implemented")


def extract_mdb_to_stored_files(
    content: bytes, 
    filename: str, 
    api_key_id: str, 
    max_bytes: int
) -> list[dict[str, Any]]:
    """将 MDB 转换为 StoredFile 列表（每个空间表一个 StoredFile）。
    
    Args:
        content: MDB 文件二进制内容
        filename: 原始文件名
        api_key_id: API Key ID
        max_bytes: 最大文件大小限制
        
    Returns:
        [{"stored_file": StoredFile, "features": list}]
    """
    # TODO: 实现 MDB 到 StoredFile 转换
    raise NotImplementedError("MDB to StoredFile conversion not yet implemented")
```

- [ ] **Step 4: 运行测试确认失败**

```bash
pytest tests/test_mdb_processor.py -v
```

Expected: FAIL with "NotImplementedError"

- [ ] **Step 5: 实现 extract_mdb_features 核心逻辑**

```python
# app/services/mdb_processor.py (替换 extract_mdb_features)
def extract_mdb_features(mdb_path: str) -> list[dict[str, Any]]:
    """从 MDB 文件中提取所有空间表的要素。
    
    Args:
        mdb_path: MDB 文件路径
        
    Returns:
        [{"table_name": str, "geometry_type": str, "features": [...], "fields": [...]}]
    """
    import pyodbc
    
    results = []
    
    try:
        # 连接 MDB
        conn_str = f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={mdb_path};"
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        # 获取所有表名
        tables = [row.table_name for row in cursor.tables(tableType='TABLE')]
        logger.info(f"Found {len(tables)} tables in MDB: {tables}")
        
        for table_name in tables:
            # 跳过系统表
            if table_name.startswith('MSys'):
                continue
            
            try:
                # 获取表结构
                cursor.execute(f"SELECT * FROM [{table_name}]")
                columns = [desc[0] for desc in cursor.description]
                
                # 检查是否有 Shape 列（空间表标志）
                has_shape = any(col.lower() in ('shape', 'geom', 'geometry') for col in columns)
                
                if not has_shape:
                    logger.debug(f"Table {table_name} has no geometry column, skipping")
                    continue
                
                # 读取数据
                rows = cursor.fetchall()
                features = []
                
                for row in rows:
                    row_dict = dict(zip(columns, row))
                    
                    # 提取几何数据
                    shape_data = None
                    for col in ('Shape', 'shape', 'Geom', 'geom', 'Geometry', 'geometry'):
                        if col in row_dict:
                            shape_data = row_dict[col]
                            break
                    
                    if shape_data is None:
                        continue
                    
                    # 转换几何为 GeoJSON
                    geometry = _convert_shape_to_geojson(shape_data)
                    if geometry is None:
                        continue
                    
                    # 构建 properties（排除几何列）
                    properties = {k: v for k, v in row_dict.items() 
                                 if k.lower() not in ('shape', 'geom', 'geometry')}
                    
                    features.append({
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": properties
                    })
                
                if features:
                    # 确定几何类型
                    geometry_type = _detect_geometry_type(features)
                    
                    results.append({
                        "table_name": table_name,
                        "geometry_type": geometry_type,
                        "features": features,
                        "fields": columns
                    })
                    logger.info(f"Extracted {len(features)} features from table {table_name}")
            
            except Exception as e:
                logger.warning(f"Failed to extract table {table_name}: {e}")
                continue
        
        conn.close()
    
    except pyodbc.Error as e:
        logger.error(f"Failed to connect to MDB: {e}")
        raise
    
    return results


def _convert_shape_to_geojson(shape_data: Any) -> dict | None:
    """将 Shape 列数据转换为 GeoJSON geometry。
    
    Args:
        shape_data: Shape 列数据（WKB 或 WKT）
        
    Returns:
        GeoJSON geometry dict 或 None
    """
    try:
        # 如果是字节数据，可能是 WKB
        if isinstance(shape_data, bytes):
            from shapely import wkb
            geom = wkb.loads(shape_data)
            return json.loads(shapely.to_geojson(geom))
        
        # 如果是字符串，可能是 WKT
        elif isinstance(shape_data, str):
            from shapely import wkt
            geom = wkt.loads(shape_data)
            return json.loads(shapely.to_geojson(geom))
        
        # 如果是对象，尝试转换
        else:
            # 尝试作为 WKB 处理
            from shapely import wkb
            geom = wkb.loads(bytes(shape_data))
            return json.loads(shapely.to_geojson(geom))
    
    except Exception as e:
        logger.debug(f"Failed to convert shape data: {e}")
        return None


def _detect_geometry_type(features: list[dict]) -> str:
    """检测几何类型。
    
    Args:
        features: GeoJSON Feature 列表
        
    Returns:
        几何类型字符串
    """
    from collections import Counter
    
    type_counter = Counter()
    for feature in features:
        geom = feature.get("geometry", {})
        geom_type = geom.get("type", "Unknown")
        type_counter[geom_type] += 1
    
    if not type_counter:
        return "Unknown"
    
    # 返回最常见的类型
    return type_counter.most_common(1)[0][0]
```

- [ ] **Step 6: 运行测试确认通过**

```bash
pytest tests/test_mdb_processor.py -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/mdb_processor.py tests/test_mdb_processor.py
git commit -m "feat: add MDB processor core extraction logic"
```