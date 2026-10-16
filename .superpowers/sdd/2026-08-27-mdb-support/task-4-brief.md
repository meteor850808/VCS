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