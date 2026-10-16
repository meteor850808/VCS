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