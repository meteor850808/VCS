# MDB 测试数据准备

## 需要的测试文件

| 文件 | 说明 | 用途 |
|------|------|------|
| `simple_points.mdb` | 包含一个点图层 | 基本上传流程验证 |
| `complex_layers.mdb` | 包含多个图层（点、线、面） | 多图层提取验证 |
| `empty_tables.mdb` | 只有表结构，没有数据 | 空数据边界测试 |
| `corrupt_file.mdb` | 损坏的 MDB 文件 | 错误处理测试 |

## 获取测试文件

### 方式 1: 从 ESRI Sample Data 下载
- ESRI Developer 网站提供 Personal Geodatabase 示例数据

### 方式 2: 使用 Access 创建
1. 创建新的 Access 数据库 (.mdb)
2. 添加空间表（使用 ESRI ArcCatalog 或 ODBC 连接）
3. 插入测试要素

### 方式 3: 转换现有 Shapefile
```bash
# 使用 ogr2ogr 将 Shapefile 转为 MDB
ogr2ogr -f "ESRI Personal Geodatabase" output.mdb input.shp
```

### 方式 4: 使用 pyodbc 程序化创建
```python
import pyodbc
import struct

# 创建一个简单的 MDB（需要 Microsoft Access Driver）
conn_str = r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=path/to/new.mdb;"
# 注意: pyodbc 无法直接创建 MDB，需要先用 Access 创建空文件
```

## 测试文件位置

```
tests/fixtures/mdb/
├── README.md              # 本文件
├── simple_points.mdb      # 简单点图层（待准备）
├── complex_layers.mdb     # 复杂多图层（待准备）
├── empty_tables.mdb       # 空表结构（待准备）
└── corrupt_file.mdb       # 损坏文件（待准备）
```

## 运行集成测试

### 使用环境变量指定 MDB 文件
```bash
# 设置测试 MDB 文件路径
export VCS_MDB_TEST_FILE=/path/to/your/test.mdb

# 运行集成测试
pytest tests/integration/test_mdb_integration.py -v -m integration
```

### 使用 fixtures 目录
```bash
# 将 MDB 文件放入 tests/fixtures/mdb/ 目录
# 然后运行测试（自动发现 .mdb 文件）
pytest tests/integration/test_mdb_integration.py -v -m integration
```

### 运行所有 MDB 相关测试
```bash
# 包括单元测试和集成测试
pytest tests/test_mdb_processor.py tests/integration/test_mdb_integration.py -v
```

## 测试覆盖

- **单元测试** (无需 MDB 文件): `tests/test_mdb_processor.py`
- **格式检测测试**: `tests/test_format_detection_mdb.py`
- **集成测试** (需要 MDB 文件): `tests/integration/test_mdb_integration.py`
- **上传端点测试**: `tests/test_upload_mdb.py`

## 注意事项

1. MDB 文件需要 Microsoft Access ODBC 驱动
2. Windows 环境下驱动自动可用
3. Linux 环境需要安装 mdbtools +odbc 驱动
4. 测试数据库 (vcs_test) 需要 PostGIS 扩展
