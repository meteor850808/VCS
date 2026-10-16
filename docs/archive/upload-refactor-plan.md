# 上传模块重构方案 — MDB/GDB/ZIP/SHP

## 一、当前问题诊断

### 问题 1: MDB 无实际读取能力
**现状**: `format_detection.py` 能识别 MDB 格式（Jet DB header），但：
- `format_detection.py:82` 将 MDB 归类为 `TABLE_FORMATS`（纯表格），`data_type="table"`
- 没有任何 MDB 读取器 — `pyodbc` / `mdbtools` / `jpype` 均未安装
- 上传 MDB 后只存储二进制，`feature_count=0`，`geometry_type="table"`
- MDB 本质是 ESRI Personal Geodatabase，内含空间数据（Point/Line/Polygon）

**影响**: 用户上传 .mdb 文件后无法提取任何要素，完全无用

### 问题 2: GDB 提取流程缺陷
**现状**:
- `gdb_processor.py` 从 ZIP 中检测 `.gdbtable` → 用 Fiona 逐 layer 读取 → 转 GeoJSON bytes
- 转换后创建 `StoredFile` 但**不设置 `field_schema`**（GDB layer 的字段信息丢失）
- `data.py:394` 在 GDB 提取后调用 `_detect_and_populate` 可以补回 schema，但...
- `_detect_and_populate` 对 GeoJSON bytes 写临时文件再用 Fiona 检测 → 可能因 bytes→文件→ Fiona 的转换丢失 GDB 特有的字段类型信息
- GDB 中的子目录结构（多个 FeatureDataset → 多个 FeatureClass）被拍平

**影响**: GDB 提取后字段信息不完整，标准生成质量差

### 问题 3: ZIP 内 SHP 分组逻辑脆弱
**现状**:
- `data.py:414-421` 用 `stem|parent_dir` 作为分组 key
- 问题场景：
  - ZIP 内 `data/city.shp` + `data/city.dbf` + `data/city.shx` → 分组正确 ✅
  - ZIP 内 `city.shp` + `city.dbf`（不同目录）→ 被当成两个组 ❌
  - ZIP 内多层嵌套 `a/b/c/city.shp` → 分组正确但路径显示异常 ⚠️
  - ZIP 内 `.shp` 无伴生文件 → 作为主文件存储但 Fiona 读取失败 ❌
- `_load_shp_group_features_async` 写临时目录后 Fiona 读取，但临时目录命名用 stem，可能冲突

**影响**: 复杂 ZIP 结构的 SHP 数据提取失败或路径混乱

### 问题 4: ZIP 双重处理
**现状**:
- `data.py:391` 先调 `extract_gdb_to_stored_files(content)` — 检测 GDB
- `data.py:407` 再调 `extract_archive(content)` — 解压所有文件
- 同一份 ZIP 内容被解压两次（一次检测 GDB，一次提取所有文件）
- GDB 文件被处理后，`extract_archive` 仍会解压出 GDB 内部文件（`.gdbtable` 等），这些无用文件也被存储

**影响**: 大 ZIP 处理时间翻倍，GDB 相关垃圾文件占用存储

### 问题 5: 特征加载不可靠
**现状**:
- GDB 提取的 GeoJSON → `_load_all_features_to_spatial_async` → 可能静默失败
- SHP 组 → `_load_shp_group_features_async` → 依赖临时目录写入
- 非 SHP 非 GDB 文件 → `_load_extracted_features_async` → 只处理 `_is_vector_file` 的文件
- CSV/XLSX 文件在 ZIP 中不会被加载到 spatial 表（正确行为，但需明确）
- `feature_count` 在异步加载完成后更新，但如果异步任务失败，count 不回滚

**影响**: `feature_count` 与实际 spatial_features 表中的记录数可能不一致

### 问题 6: 标准关联粒度问题
**现状**:
- 上传 ZIP 时 `standard_id` 应用到**所有**提取出的文件
- 但 ZIP 内可能包含不同几何类型的文件（点 + 线 + 面）
- `auto_create_standard` 只创建一个 layer_group + 一个 table
- 多个不同类型的文件被关联到同一个标准表

**影响**: 标准与数据类型不匹配，质检无法正确执行

### 问题 7: 临时目录泄漏
**现状**:
- `gdb_processor.py` 的 `finally: shutil.rmtree` 在正常情况下有效
- 但如果进程被 kill 或 OOM，临时目录残留
- `shp_group_features_async` 同理
- 没有启动时清理或定期清理机制

**影响**: 磁盘空间逐渐耗尽

---

## 二、重构方案

### Phase 1: MDB 读取能力（核心阻塞）

#### 1.1 安装 MDB 读取依赖
```bash
pip install pyodbc    # Windows 上通过 OLE DB/ODBC 读取 MDB
# 备选: pip install mdbtools (纯 Python MDB 解析器，跨平台)
```

**决策点**: 
- `pyodbc` + Windows Access ODBC Driver → 仅限 Windows，但性能最好
- `mdbtools` (Python binding) → 跨平台，纯 Python，无需安装 Access
- 建议: 优先 `pyodbc`（当前环境为 Windows），fallback 到 `mdbtools`

#### 1.2 新建 `app/services/mdb_processor.py`
```python
"""MDB (ESRI Personal Geodatabase) 处理器。

读取 .mdb 文件中的空间表和属性表，提取为 GeoJSON features。
"""
import json
import uuid
import tempfile
import os

def extract_mdb_features(mdb_path: str) -> list[dict]:
    """从 MDB 文件中提取所有空间表的要素。
    
    Returns:
        [{"table_name": str, "geometry_type": str, "features": [...], "fields": [...]}]
    """
    # 1. 连接 MDB（通过 pyodbc 或 mdbtools）
    # 2. 枚举所有表（mdb_tables）
    # 3. 识别空间表（含 Shape 列或 MSysGeoColumns）
    # 4. 读取每个表的字段定义
    # 5. 读取每个表的数据行
    # 6. 从 Shape 列提取几何（WKB/WKT → GeoJSON）
    # 7. 返回结构化结果

def extract_mdb_to_stored_files(
    content: bytes, filename: str, api_key_id: str, max_bytes: int
) -> list[StoredFile]:
    """将 MDB 转换为 StoredFile 列表（每个空间表一个 StoredFile）。"""
    # 写入临时文件 → extract_mdb_features → 转 GeoJSON → 创建 StoredFile
```

#### 1.3 修改 `data.py` 的上传端点
```python
# 在 upload_file 中增加 MDB 处理分支
if filename.lower().endswith('.mdb'):
    from app.services.mdb_processor import extract_mdb_to_stored_files
    mdb_files = extract_mdb_to_stored_files(content, filename, api_key.id, max_bytes)
    for sf in mdb_files:
        _detect_and_populate(sf, sf.file_data or b"", sf.original_name)
        if standard_id:
            sf.standard_id = standard_id
            sf.standard_table_id = standard_table_id
            sf.data_standard_name = resolved_standard_name
        db.add(sf)
        extracted_count += 1
        if sf.file_data:
            sub_tasks.append(_load_all_features_to_spatial_async(
                db, sf.id, sf.original_name, sf.file_data, api_key.id,
            ))
```

#### 1.4 修改 `format_detection.py`
- MDB 不再归类为 `TABLE_FORMATS`，改为 `SPATIAL_FORMATS`（因为它包含空间数据）
- 或新增 `PERSONAL_GDB_FORMATS = {"MDB"}` 分类

---

### Phase 2: ZIP 处理重构

#### 2.1 统一解压入口，消除双重处理
**当前**: GDB 检测 + 通用解压 = 两次解压
**重构**: 一次解压，统一分类

```python
# data.py 中的新流程
ex_result = extract_archive(content, filename, max_bytes, max_bytes * 2)

# 统一分类
gdb_files = []      # GDB layer 文件
shp_groups = {}     # SHP 组件分组
vector_files = []   # 其他矢量文件
table_files = []    # CSV/XLSX
companion_files = [] # SHP 伴生文件（.dbf/.shx/.prj 等）

for ef in ex_result.files:
    classify_file(ef, gdb_files, shp_groups, vector_files, table_files, companion_files)

# 先处理 GDB（需要写临时目录）
for gdb_layer in gdb_files:
    process_gdb_layer(gdb_layer)

# 处理 SHP 组
for stem, components in shp_groups.items():
    process_shp_group(stem, components)

# 处理其他矢量文件
for vf in vector_files:
    process_vector_file(vf)

# 处理表格文件
for tf in table_files:
    process_table_file(tf)
```

#### 2.2 GDB 检测从解压阶段移到分类阶段
**当前**: `contains_gdb(content)` 在解压前检测 → 需要扫描 ZIP 内容
**重构**: 解压后遍历文件列表，检测 `.gdb` 目录结构

```python
def classify_files(files: list[ExtractedFile]) -> dict:
    """将解压出的文件分类到不同处理组。"""
    # 检测 GDB 目录结构
    gdb_dirs = set()
    for ef in files:
        if '.gdb/' in ef.original_name:
            gdb_dir = ef.original_name.split('.gdb/')[0] + '.gdb'
            gdb_dirs.add(gdb_dir)
    
    # 如果有 GDB 目录，标记其中的文件为 GDB
    # 否则按扩展名分类
```

#### 2.3 SHP 分组逻辑加固
```python
def group_shp_components(files: list[ExtractedFile]) -> dict[str, list]:
    """将 SHP 组件文件按 stem 分组。
    
    分组规则：
    1. 提取所有 .shp 文件
    2. 对每个 .shp，查找同目录下的伴生文件（.dbf/.shx/.prj/.cpg）
    3. 如果伴生文件不在同目录，按 stem 名称匹配
    4. 无伴生文件的 .shp 单独处理（标记警告）
    """
    groups = {}
    shp_files = [f for f in files if f.original_name.lower().endswith('.shp')]
    
    for shp in shp_files:
        stem = Path(shp.original_name).stem.lower()
        parent = str(Path(shp.original_name).parent)
        key = f"{stem}|{parent}"
        
        # 查找伴生文件
        companions = {}
        companion_exts = {'.dbf', '.shx', '.prj', '.cpg', '.sbn', '.sbx'}
        for ef in files:
            ext = Path(ef.original_name).suffix.lower()
            if ext in companion_exts:
                ef_stem = Path(ef.original_name).stem.lower()
                ef_parent = str(Path(ef.original_name).parent)
                if ef_stem == stem and ef_parent == parent:
                    companions[ext] = ef
        
        groups[key] = {'shp': shp, 'companions': companions}
    
    return groups
```

#### 2.4 ZIP 中的 GDB 内部文件过滤
解压后如果检测到 GDB 目录结构，过滤掉 GDB 内部文件（`.gdbtable`/`.gdbindexes`/`.spx` 等），避免重复处理。

---

### Phase 3: 特征加载可靠性

#### 3.1 统一特征加载结果跟踪
```python
# 每个异步任务返回加载结果
async def _load_features_tracked(
    db, stored_file_id, filename, content, api_key_id
) -> dict:
    """加载特征并返回结果统计。"""
    try:
        features = await asyncio.to_thread(extract_features_from_bytes, content, filename)
        loaded = 0
        for feat in features[:settings.max_features_per_upload]:
            await write_feature(db, stored_file_id, api_key_id, Path(filename).name, feat)
            loaded += 1
        return {"loaded": loaded, "total": len(features), "success": True}
    except Exception as e:
        return {"loaded": 0, "total": 0, "success": False, "error": str(e)}
```

#### 3.2 feature_count 一致性保证
- 在 `await asyncio.gather(*sub_tasks)` 后，从 `spatial_features` 表重新统计实际 count
- 更新每个 `StoredFile.feature_count` 为实际值
- 如果 count=0 且文件是矢量格式，标记 warning

#### 3.3 临时目录安全清理
```python
import atexit
import signal

TEMP_DIRS: set[str] = set()

def register_temp_dir(path: str):
    TEMP_DIRS.add(path)

def cleanup_temp_dirs():
    for d in TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    TEMP_DIRS.clear()

atexit.register(cleanup_temp_dirs)
signal.signal(signal.SIGTERM, lambda s,f: (cleanup_temp_dirs(), exit(1)))
```

---

### Phase 4: 标准关联优化

#### 4.1 每个提取文件独立关联标准
- ZIP 中的每个文件可以关联不同的标准
- 前端需要支持"按文件选择标准"（当前是全局选择）
- 简化方案：保持全局选择，但验证每个文件的 geometry_type 与标准匹配

#### 4.2 auto_create_standard 改进
- 对 ZIP 中的每个文件，如果 geometry_type 不同，创建不同的 layer_group
- 同一 geometry_type 的文件合并到同一个 layer_group

---

### Phase 5: 前端体验优化

#### 5.1 上传结果详细反馈
```json
{
  "id": "...",
  "original_name": "data.zip",
  "extracted_count": 5,
  "extraction_details": [
    {"name": "city.shp", "type": "SHP", "features": 1200, "status": "ok"},
    {"name": "roads.shp", "type": "SHP", "features": 3500, "status": "ok"},
    {"name": "data.xlsx", "type": "XLSX", "features": 0, "status": "table_only"},
    {"name": "readme.txt", "type": "TXT", "features": 0, "status": "skipped"}
  ]
}
```

#### 5.2 上传进度增强
- 当前只有简单的进度条（20% → 60% → 100%）
- 改进：WebSocket 或 SSE 实时推送解压/提取/入库各阶段进度

---

## 三、实施优先级

| 阶段 | 任务 | 复杂度 | 阻塞 |
|------|------|--------|------|
| P0 | MDB 读取能力 | 高 | 是（当前完全不可用） |
| P1 | ZIP 统一解压消除双重处理 | 中 | 否（但浪费性能） |
| P1 | SHP 分组逻辑加固 | 中 | 否（但影响复杂 ZIP） |
| P2 | 特征加载结果跟踪 | 低 | 否（影响数据一致性） |
| P2 | feature_count 一致性 | 低 | 否 |
| P2 | 临时目录安全清理 | 低 | 否 |
| P3 | 标准关联优化 | 中 | 否 |
| P3 | 前端反馈增强 | 低 | 否 |

---

## 四、文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `app/services/mdb_processor.py` | **新建** | MDB 读取处理器 |
| `app/services/archive_extractor.py` | 重构 | 统一解压，增加 GDB 过滤 |
| `app/services/gdb_processor.py` | 重构 | 改为从已解压文件处理，不再自行解压 |
| `app/services/spatial_loader.py` | 增强 | 增加 MDB 格式支持 |
| `app/services/format_detection.py` | 修改 | MDB 分类调整 |
| `app/api/v1/data.py` | 重构 | 统一上传流程，消除双重解压 |
| `app/frontend/index.html` | 增强 | 上传结果详细反馈 |
| `requirements.txt` | 新增 | `pyodbc` 或 `mdbtools` |
