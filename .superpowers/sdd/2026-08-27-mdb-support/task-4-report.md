## Task 4: 修改格式分类 — 完成报告

### 实现内容

1. **移动 MDB 分类**：将 `"MDB"` 从 `TABLE_FORMATS` 移至 `GEOMETRY_FORMATS`（`format_detection.py:84-86`）

2. **修改 `detect_data_type` 函数**（`format_detection.py:144-164`）：
   - 新增 `content: bytes = b""` 可选参数（向后兼容）
   - 支持传入格式名（如 `"MDB"`）或文件名（如 `"test.mdb"`）
   - MDB 格式返回 `"spatial"`（区别于其他几何格式的 `"geometry"`）

3. **创建测试文件** `tests/test_format_detection_mdb.py`（7 个测试用例）

### TDD 证据

**RED** — 7/7 测试失败（MDB 仍在 TABLE_FORMATS 中；函数签名不匹配）

**GREEN** — 7/7 测试通过，15/15 格式相关测试全部通过

### 修改文件

| 文件 | 变更 |
|------|------|
| `app/services/format_detection.py` | MDB 移至 GEOMETRY_FORMATS；detect_data_type 增加 content 参数和文件名解析 |
| `tests/test_format_detection_mdb.py` | 新建，7 个测试用例 |

### 测试结果

```
tests/test_format_detection_mdb.py::test_mdb_not_in_table_formats PASSED
tests/test_format_detection_mdb.py::test_mdb_in_geometry_formats PASSED
tests/test_format_detection_mdb.py::test_mdb_data_type_returns_spatial PASSED
tests/test_format_detection_mdb.py::test_mdb_data_type_by_extension PASSED
tests/test_format_detection_mdb.py::test_csv_still_table PASSED
tests/test_format_detection_mdb.py::test_xlsx_still_table PASSED
tests/test_format_detection_mdb.py::test_shp_still_geometry PASSED
```

完整格式测试套件（含既有测试）：15/15 通过。

### 自审发现

- `content` 参数在当前实现中未使用（MDB 判断仅依赖格式名），保留以备后续 magic bytes 验证
- `detect_format` 内部调用 `detect_data_type(fmt)` 时 MDB 返回 `"spatial"` 而非 `"geometry"`，与 FormatResult.data_type 字段兼容（该字段为自由字符串）
- 全量测试套件中 test_api.py 有 4 个预存失败（数据库连接/事件循环问题），与本次变更无关

### 提交

- **Commit:** `ccb638c` feat: classify MDB as spatial format instead of table
