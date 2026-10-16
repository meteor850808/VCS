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