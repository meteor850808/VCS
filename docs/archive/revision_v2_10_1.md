# VCS 空间质检 v2.10.1 修订记录

> **日期**: 2026-08-19  
> **版本**: v2.10.1  
> **范围**: 质检检查器 api_key_id 过滤移除、标准质检概览端点

---

## 1. 问题背景

质检报告拓扑检查始终返回 0 条结果，所有规则 `total_checked=0`。

**根因**：要素表（polygon_features 等）的 `api_key_id` 为 NULL（上传时未写入），而存储文件的 `api_key_id` 为 admin 虚拟 ID `00000000-0000-0000-0000-000000000001`。查询条件 `model.api_key_id == api_key_id` 导致 NULL ≠ 虚拟 ID，要素查询返回 0 条。

---

## 2. 修复内容

### 2.1 拓扑检查器 (`app/services/topology_checker.py`)

- 移除 `run_topology_checks()` 中所有 13 处 `model.api_key_id == api_key_id` 查询条件
- 移除所有 raw SQL 中的 `AND a.api_key_id = :akid` / `AND b.api_key_id = :akid` 条件
- 移除所有 params 中的 `"akid": api_key_id`
- 保留函数签名 `api_key_id` 参数（默认空字符串）以保持向后兼容
- API 层（`quality.py`）已通过 `scope_clause` 验证文件访问权限，无需在要素查询层二次过滤

### 2.2 属性检查器 (`app/services/quality/attribute_checker.py`)

- `load_features_by_file()` 移除 `model.api_key_id == api_key_id` 条件
- 同样，API 层已通过 `scope_clause` 验证文件权限

### 2.3 质检规则生成器 v2 模型适配 (`app/services/quality/rule_generator.py` + `app/api/v1/quality_rules.py`)

- **问题**：`generate_rules_for_standard()` 使用旧 v1 模型 (`DataStandardField`/`DataStandardSheet`)，v1 表为空导致 0 规则生成 + 500 错误
- **修复**：
  - `rule_generator.py`: 重写使用 v2 `StandardField` 模型，新增字典关联 `VALUE_IN_SET` 规则 + 主键 `UNIQUE` 规则
  - `quality_rules.py`: `generate_rules` 端点改用 `StandardField.standard_id` 直接查询，`generate_rules_for_standard` 改为 `async`
  - 规则类型：NOT_NULL / TYPE_MATCH / LENGTH_LIMIT / UNIQUE / VALUE_IN_SET（字典关联）

### 2.4 标准质检概览端点 (`app/api/v1/data_standard_v2.py`)

- **新增** `GET /data-standards-v2/{id}/qc-summary`
- 返回标准下的表→字段→字典关联，以及可用字典（标准+全局）
- 用于前端质检配置区的"标准概览"卡片展示

---

## 3. 影响范围

| 文件 | 修改 |
|------|------|
| `app/services/topology_checker.py` | 移除 13 处 api_key_id 查询过滤 |
| `app/services/quality/attribute_checker.py` | 移除 1 处 api_key_id 查询过滤 |
| `app/services/quality/rule_generator.py` | 重写为 v2 StandardField + 字典关联规则 |
| `app/api/v1/quality_rules.py` | 生成端点适配 v2 + async 修复 |
| `app/api/v1/data_standard_v2.py` | 新增 qc-summary 端点 |
| `app/frontend/index.html` | 标准概览卡片、字段字典关联、质检按钮全选 |

---

## 4. 验证结果

### 4.1 质检规则生成 API

```
POST /api/v1/quality-rules/generate/61e9dbdf-ce26-41f5-a9fc-0a1d675df65a
→ 201 Created, 9 rules:

pt_id (控制点编号):
  NOT_NULL:       字段 控制点编号 不可为空
  TYPE_MATCH:     字段 控制点编号 应为 string 类型
  LENGTH_LIMIT:   字段 控制点编号 长度不超过 20
  UNIQUE:         字段 控制点编号 应唯一（主键）
  VALUE_IN_SET:   字段 控制点编号 值必须在字典 点类型 中

pt_name (控制点名称):
  NOT_NULL:       字段 控制点名称 不可为空
  TYPE_MATCH:     字段 控制点名称 应为 string 类型
  LENGTH_LIMIT:   字段 控制点名称 长度不超过 50
  VALUE_IN_SET:   字段 控制点名称 值必须在字典 点类型 中
```

### 4.2 质检拓扑检查（前次验证）

```
质检 API 测试（polygon file, topology rules）:
- T01 空几何: pass (0/0)
- T03 最大面积: pass (760 checked)
- T04 最小面积: pass (760 checked)
- T06 面重叠: pass (760 checked)
- T07 点重复: pass (0 checked)
- T08 线重叠: pass (0 checked)
- T12 环方向: pass (760 checked)
- T13 多部件: fail (19/760)
总计: 10 检查, 9 pass, 1 fail
```

质检概览 API:
```json
{
  "standard_id": "61e9dbdf-...",
  "tables": [{
    "table_name": "控制点",
    "linked_field_count": 2,
    "fields": [{"field_name": "pt_id", "linked": true}, ...]
  }],
  "available_dictionaries": {"standard": [...], "global": [...]},
  "stats": {"total_fields": 2, "linked_fields": 2, "total_dicts": 1}
}
```

---

## 5. 后续事项

- [ ] 质检规则需要根据实际图层名生成（当前标准定义图层名 `控制点1` 与实际文件图层名不匹配）
- [ ] 前端标准概览需要在浏览器中验证字典关联操作
- [ ] 考虑要素表 `api_key_id` 列的长期清理方案
