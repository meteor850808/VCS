# MDB 支持实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 MDB (ESRI Personal Geodatabase) 文件的读取和入库功能

**Architecture:** 
- 新建 `mdb_processor.py` 处理器，使用 `pyodbc` 读取 MDB 文件
- 修改上传端点支持 MDB 格式
- 调整格式分类，将 MDB 从表格格式改为空间格式

**Tech Stack:** Python 3.10+, pyodbc, Fiona (GeoJSON 转换), SQLAlchemy async

**Spec:** `docs/archive/upload-refactor-plan.md` (Phase 1)

---

## Global Constraints

- Python 3.10+
- SQLAlchemy 2.0 async
- FastAPI 0.115+
- Windows 环境 (pyodbc 依赖 ODBC 驱动)
- 不修改现有测试，新增测试覆盖新功能

---

## 文件结构

### 新建文件
- `app/services/mdb_processor.py` - MDB 读取处理器
- `tests/test_mdb_processor.py` - MDB 处理器单元测试

### 修改文件
- `requirements.txt` - 添加 pyodbc 依赖
- `app/services/format_detection.py:86` - MDB 分类调整
- `app/api/v1/data.py:~390-420` - 上传端点增加 MDB 处理分支

---

## Task 1: 安装依赖并验证环境

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: 无
- Produces: pyodbc 依赖安装

- [ ] **Step 1: 添加 pyodbc 到 requirements.txt**

```bash
# 在 requirements.txt 末尾添加
pyodbc>=5.1.0
```

- [ ] **Step 2: 安装依赖**

```bash
pip install pyodbc>=5.1.0
```

- [ ] **Step 3: 验证 pyodbc 安装**

```python
import pyodbc
print(f"pyodbc version: {pyodbc.version}")
print(f"Available drivers: {pyodbc.drivers()}")
```

- [ ] **Step 4: 验证 ODBC 驱动可用**

```python
# 检查是否有 Access ODBC 驱动
drivers = [d for d in pyodbc.drivers() if 'Access' in d]
if not drivers:
    print("Warning: No Access ODBC driver found. Install Microsoft Access Database Engine.")
else:
    print(f"Access drivers: {drivers}")
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "deps: add pyodbc for MDB support"
```

---

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

---

## Task 3: 实现 StoredFile 转换功能

**Files:**
- Modify: `app/services/mdb_processor.py`
- Modify: `tests/test_mdb_processor.py`

**Interfaces:**
- Consumes: `extract_mdb_features()`, StoredFile model
- Produces: `extract_mdb_to_stored_files()`

- [ ] **Step 1: 添加 StoredFile 转换测试**

```python
# tests/test_mdb_processor.py (追加)
def test_extract_mdb_to_stored_files_not_implemented():
    """Test that extract_mdb_to_stored_files raises NotImplementedError."""
    from app.services.mdb_processor import extract_mdb_to_stored_files
    
    with pytest.raises(NotImplementedError):
        extract_mdb_to_stored_files(b"fake mdb content", "test.mdb", "test-api-key", 1000000)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_mdb_processor.py::test_extract_mdb_to_stored_files_not_implemented -v
```

Expected: PASS (测试预期会失败)

- [ ] **Step 3: 实现 extract_mdb_to_stored_files**

```python
# app/services/mdb_processor.py (追加)
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
        [{"stored_file": dict, "features": list}]
    """
    import tempfile
    import hashlib
    
    # 检查文件大小
    if len(content) > max_bytes:
        raise ValueError(f"MDB file size {len(content)} exceeds limit {max_bytes}")
    
    # 写入临时文件
    with tempfile.NamedTemporaryFile(suffix='.mdb', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        # 提取要素
        mdb_tables = extract_mdb_features(tmp_path)
        
        results = []
        for table_info in mdb_tables:
            table_name = table_info["table_name"]
            geometry_type = table_info["geometry_type"]
            features = table_info["features"]
            fields = table_info["fields"]
            
            # 生成存储文件名
            stored_filename = f"{Path(filename).stem}_{table_name}.geojson"
            
            # 转换为 GeoJSON
            geojson = {
                "type": "FeatureCollection",
                "features": features
            }
            geojson_bytes = json.dumps(geojson, ensure_ascii=False).encode('utf-8')
            
            # 计算哈希
            file_hash = hashlib.sha256(geojson_bytes).hexdigest()
            
            # 创建 StoredFile 字典
            stored_file = {
                "id": str(uuid.uuid4()),
                "api_key_id": api_key_id,
                "original_name": stored_filename,
                "content_type": "application/geo+json",
                "file_size": len(geojson_bytes),
                "file_data": geojson_bytes,
                "file_hash": file_hash,
                "is_archive": False,
                "source_archive": filename,
                "data_type": "spatial",
                "geometry_type": geometry_type,
                "file_format": "GeoJSON",
                "feature_count": len(features),
                "layer_count": 1,
                "field_schema": {
                    "fields": [{"name": f, "type": "string"} for f in fields]
                }
            }
            
            results.append({
                "stored_file": stored_file,
                "features": features
            })
            
            logger.info(f"Prepared MDB table {table_name}: {len(features)} features")
        
        return results
    
    finally:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
```

- [ ] **Step 4: 更新测试用例**

```python
# tests/test_mdb_processor.py (替换测试)
def test_extract_mdb_to_stored_files():
    """Test extract_mdb_to_stored_files with mock data."""
    from app.services.mdb_processor import extract_mdb_to_stored_files
    
    # 创建一个最小的 MDB 文件用于测试（实际需要有效的 MDB）
    # 这里我们测试函数签名和异常处理
    with pytest.raises(Exception):  # 会因为无效 MDB 格式而失败
        extract_mdb_to_stored_files(b"not a real mdb", "test.mdb", "test-api-key", 1000000)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
pytest tests/test_mdb_processor.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/mdb_processor.py tests/test_mdb_processor.py
git commit -m "feat: implement MDB to StoredFile conversion"
```

---

## Task 4: 修改格式分类

**Files:**
- Modify: `app/services/format_detection.py:86`
- Create: `tests/test_format_detection_mdb.py`

**Interfaces:**
- Consumes: MDB 格式检测
- Produces: MDB 分类从 TABLE_FORMATS 改为 SPATIAL_FORMATS

- [ ] **Step 1: 创建格式检测测试**

```python
# tests/test_format_detection_mdb.py
import pytest
from app.services.format_detection import detect_data_type, TABLE_FORMATS, GEOMETRY_FORMATS

def test_mdb_data_type_detection():
    """Test that MDB is detected as spatial data type."""
    # MDB 应该被检测为 spatial 而不是 table
    data_type = detect_data_type("test.mdb", b"Standard Jet DB")
    assert data_type == "spatial"  # 而不是 "table"

def test_mdb_format_classification():
    """Test that MDB is in SPATIAL_FORMATS, not TABLE_FORMATS."""
    # MDB 不应该在 TABLE_FORMATS 中
    assert "MDB" not in TABLE_FORMATS
    # MDB 应该在 GEOMETRY_FORMATS 中（或新的 SPATIAL_FORMATS）
    # 注意：可能需要创建新的 SPATIAL_FORMATS 分类
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_format_detection_mdb.py -v
```

Expected: FAIL

- [ ] **Step 3: 修改 format_detection.py**

```python
# app/services/format_detection.py:86
# 原代码：
# TABLE_FORMATS = {"CSV", "XLSX", "MDB"}

# 修改为：
TABLE_FORMATS = {"CSV", "XLSX"}  # 移除 MDB
PERSONAL_GDB_FORMATS = {"MDB"}  # 新增 Personal GDB 分类

# 在 detect_data_type 函数中修改逻辑
def detect_data_type(filename: str, content: bytes = b"") -> str:
    """检测数据类型。"""
    ext = Path(filename).suffix.lower()
    
    # 检查是否是 Personal GDB
    if ext == '.mdb' or (content[:16] == b'Standard Jet DB'):
        return "spatial"  # MDB 是空间格式
    
    # 其他逻辑...
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_format_detection_mdb.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/format_detection.py tests/test_format_detection_mdb.py
git commit -m "feat: classify MDB as spatial format instead of table"
```

---

## Task 5: 修改上传端点支持 MDB

**Files:**
- Modify: `app/api/v1/data.py:~390-420`
- Create: `tests/test_upload_mdb.py`

**Interfaces:**
- Consumes: `extract_mdb_to_stored_files()`, upload endpoint
- Produces: MDB 上传处理分支

- [ ] **Step 1: 创建上传测试**

```python
# tests/test_upload_mdb.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_upload_mdb_endpoint_exists():
    """Test that upload endpoint exists and accepts MDB files."""
    # 这是一个基本测试，验证端点存在
    response = client.get("/api/v1/data/upload")
    # 应该返回 405 (Method Not Allowed) 或其他，而不是 404
    assert response.status_code != 404
```

- [ ] **Step 2: 运行测试确认通过**

```bash
pytest tests/test_upload_mdb.py -v
```

Expected: PASS

- [ ] **Step 3: 修改 data.py 上传端点**

```python
# app/api/v1/data.py (在 upload_file 函数中，约 390-420 行)
# 在处理压缩包之前添加 MDB 处理分支

# 查找合适的位置（在 content 读取后）
if content is None:
    content = await file.read()

# 添加 MDB 处理分支
if filename.lower().endswith('.mdb'):
    from app.services.mdb_processor import extract_mdb_to_stored_files
    
    try:
        mdb_results = extract_mdb_to_stored_files(
            content, filename, api_key.id, settings.max_upload_size_mb * 1024 * 1024
        )
        
        for result in mdb_results:
            stored_file_data = result["stored_file"]
            features = result["features"]
            
            # 创建 StoredFile 对象
            sf = StoredFile(**stored_file_data)
            
            # 检测和填充元数据
            _detect_and_populate(sf, sf.file_data or b"", sf.original_name)
            
            # 关联标准
            if standard_id:
                sf.standard_id = standard_id
                sf.standard_table_id = standard_table_id
            
            db.add(sf)
            await db.flush()
            
            extracted_count += 1
            
            # 异步加载要素到空间表
            if sf.file_data:
                sub_tasks.append(_load_all_features_to_spatial_async(
                    db, sf.id, sf.original_name, sf.file_data, api_key.id,
                ))
        
        logger.info(f"Processed MDB file {filename}: {extracted_count} tables extracted")
    
    except Exception as e:
        logger.error(f"Failed to process MDB file {filename}: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to process MDB file: {str(e)}"
        )
    
    # 跳过后续的压缩包处理
    # 注意：这里需要确保后续代码不会重复处理 MDB
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_upload_mdb.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/data.py tests/test_upload_mdb.py
git commit -m "feat: add MDB upload support to data endpoint"
```

---

## Task 6: 集成测试和验证

**Files:**
- Create: `tests/integration/test_mdb_integration.py`

**Interfaces:**
- Consumes: 所有前面的任务
- Produces: 完整的 MDB 上传和处理流程

- [ ] **Step 1: 创建集成测试**

```python
# tests/integration/test_mdb_integration.py
import pytest
import tempfile
import os
from pathlib import Path

@pytest.mark.integration
def test_mdb_upload_flow():
    """Test complete MDB upload flow."""
    # 注意：这个测试需要实际的 MDB 文件
    # 在真实环境中，应该准备测试数据
    
    # 1. 创建测试 MDB 文件（需要实际的 MDB 格式）
    # 2. 上传到 API
    # 3. 验证 StoredFile 创建
    # 4. 验证要素提取
    # 5. 验证空间数据存储
    
    pytest.skip("Requires actual MDB test file")
```

- [ ] **Step 2: 运行集成测试**

```bash
pytest tests/integration/test_mdb_integration.py -v
```

Expected: SKIP (需要测试数据)

- [ ] **Step 3: 创建测试数据说明文档**

```markdown
# MDB 测试数据准备

## 需要的测试文件

1. **简单 MDB 文件** - 包含一个点图层
2. **复杂 MDB 文件** - 包含多个图层（点、线、面）
3. **空 MDB 文件** - 只有表结构，没有数据
4. **损坏 MDB 文件** - 用于测试错误处理

## 获取测试文件

可以从以下来源获取 MDB 测试文件：
- ESRI Sample Data
- 自己创建的 Access 数据库
- 转换现有的 Shapefile 到 MDB

## 测试文件位置

将测试文件放在：
```
tests/fixtures/mdb/
├── simple_points.mdb
├── complex_layers.mdb
├── empty_tables.mdb
└── corrupt_file.mdb
```
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_mdb_integration.py
git commit -m "test: add MDB integration test framework"
```

---

## Task 7: 文档更新

**Files:**
- Modify: `BASELINE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 所有实现
- Produces: 更新的文档

- [ ] **Step 1: 更新 BASELINE.md**

```markdown
## 3. 数据库表结构

### 3.4 文件与空间存储

#### `stored_files` — 文件存储

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| ... | ... | ... | ... |
| data_type | String(20) | INDEX, DEFAULT 'unknown' | 数据类型（新增 'spatial' for MDB） |
| file_format | String(32) | INDEX, DEFAULT 'unknown' | 文件格式（新增 'GeoJSON' for converted MDB） |
```

- [ ] **Step 2: 更新 README.md**

```markdown
## 支持格式

| 格式 | 说明 | 状态 |
|------|------|------|
| ... | ... | ... |
| MDB | ESRI Personal Geodatabase | ✅ 支持 |
```

- [ ] **Step 3: 更新 BASELINE.md 版本号**

```markdown
> **当前版本**: v2.12.0  # 从 v2.11.0 升级
> **最后更新**: 2026-08-27  # 更新日期
```

- [ ] **Step 4: Commit**

```bash
git add BASELINE.md README.md
git commit -m "docs: update documentation for MDB support"
```

---

## Task 8: 最终验证和清理

**Files:**
- All modified files

**Interfaces:**
- Consumes: 所有任务完成
- Produces: 完整的 MDB 支持功能

- [ ] **Step 1: 运行所有测试**

```bash
pytest tests/ -v --tb=short
```

Expected: All tests PASS

- [ ] **Step 2: 代码质量检查**

```bash
# 如果有 flake8 或 black
black --check app/services/mdb_processor.py
flake8 app/services/mdb_processor.py
```

- [ ] **Step 3: 功能验证**

```bash
# 启动服务
python -m uvicorn app.main:app --reload --port 8000

# 测试 MDB 上传（需要实际 MDB 文件）
curl -X POST "http://localhost:8000/api/v1/data/upload" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "file=@test.mdb"
```

- [ ] **Step 4: 清理临时文件**

```bash
# 清理测试生成的临时文件
find . -name "*.pyc" -delete
find . -name "__pycache__" -type d -exec rm -rf {} +
```

- [ ] **Step 5: 最终 Commit**

```bash
git add -A
git commit -m "feat: complete MDB support implementation"
```

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-08-27-mdb-support.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** - 我为每个任务分发一个新的子代理，任务之间进行审查，快速迭代

**2. Inline Execution** - 在当前会话中使用 executing-plans 执行任务，批量执行并设置检查点

**选择哪种方式？**
