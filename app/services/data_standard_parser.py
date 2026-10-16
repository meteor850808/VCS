"""Excel 数据标准解析 — 读取 .xlsx 中的字段定义，支持中英文表头。"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from openpyxl import load_workbook


@dataclass
class FieldDefinition:
    field_name: str
    field_type: str = "string"
    length: int | None = None
    nullable: bool = True
    allowed_values: list[str] | None = None
    description: str | None = None
    ordering: int = 0


@dataclass
class SheetDefinition:
    sheet_name: str
    fields: list[FieldDefinition] = field(default_factory=list)
    ordering: int = 0


@dataclass
class StandardParseResult:
    sheets: list[SheetDefinition] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# 中英文表头映射
_HEADER_MAP = {
    "字段名": "field_name", "field_name": "field_name", "name": "field_name",
    "类型": "field_type", "field_type": "field_type", "type": "field_type",
    "长度": "length", "length": "length", "max_length": "length",
    "是否可空": "nullable", "nullable": "nullable", "可空": "nullable", "允许空": "nullable",
    "允许值": "allowed_values", "allowed_values": "allowed_values",
    "枚举": "allowed_values", "enum": "allowed_values", "values": "allowed_values",
    "描述": "description", "description": "description", "desc": "description",
}

_VALID_TYPES = {"string", "integer", "float", "date", "geometry"}


def parse_standard_excel(file_path: str) -> StandardParseResult:
    """解析 Excel 标准文件，返回 SheetDefinition 列表。"""
    result = StandardParseResult()
    wb = load_workbook(file_path, read_only=True, data_only=True)

    for sheet_idx, ws in enumerate(wb.worksheets):
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # 第 1 行 = 表头
        header_row = rows[0]
        col_map = _parse_header(header_row, result)

        if not col_map:
            result.warnings.append(f"工作表 '{ws.title}' 表头无法识别，跳过")
            continue

        sheet_def = SheetDefinition(sheet_name=ws.title, ordering=sheet_idx)
        field_order = 0

        for row in rows[1:]:
            if all(cell is None or (isinstance(cell, str) and cell.strip() == "") for cell in row):
                continue  # 跳过空行

            field_name = _get_cell(row, col_map, "field_name")
            if not field_name or str(field_name).strip() == "":
                continue

            field_type = str(_get_cell(row, col_map, "field_type") or "string").strip().lower()
            if field_type not in _VALID_TYPES:
                result.warnings.append(f"字段 '{field_name}' 类型 '{field_type}' 不可识别，使用默认 string")
                field_type = "string"

            raw_length = _get_cell(row, col_map, "length")
            length = int(raw_length) if raw_length is not None and str(raw_length).strip() else None

            raw_nullable = _get_cell(row, col_map, "nullable")
            nullable = _parse_nullable(raw_nullable)

            raw_values = _get_cell(row, col_map, "allowed_values")
            allowed_values = _parse_allowed_values(raw_values)

            description = str(_get_cell(row, col_map, "description") or "").strip() or None

            fd = FieldDefinition(
                field_name=str(field_name).strip(),
                field_type=field_type,
                length=length,
                nullable=nullable,
                allowed_values=allowed_values,
                description=description,
                ordering=field_order,
            )
            sheet_def.fields.append(fd)
            field_order += 1

        if sheet_def.fields:
            result.sheets.append(sheet_def)

    wb.close()
    return result


def _parse_header(header_row: list, result: StandardParseResult) -> dict[str, int]:
    """将表头行解析为 {内部字段名: 列索引} 映射。"""
    col_map = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        key = str(cell).strip()
        mapped = _HEADER_MAP.get(key.lower()) or _HEADER_MAP.get(key)
        if mapped:
            col_map[mapped] = idx
        else:
            result.warnings.append(f"忽略未识别的表头列: '{key}'")
    if "field_name" not in col_map:
        return {}
    return col_map


def _get_cell(row: list, col_map: dict, key: str):
    idx = col_map.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _parse_nullable(val) -> bool:
    if val is None:
        return True
    s = str(val).strip().lower()
    return s not in ("否", "n", "no", "false", "0", "不可空", "必填")


def _parse_allowed_values(val) -> list[str] | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Only strip spaces after splitting (preserve internal spaces in values)
    parts = [v.strip() for v in s.replace("，", ",").replace("、", ",").split(",") if v.strip()]
    return parts if parts else None


# ══════════════════════════════════════════════════════════════════════════════
# v2 四 Sheet 模板解析（标准信息 / 层组定义 / 数据表定义 / 字段定义）
# 与 data_standard_v2.py 的模板下载（/template）和导出（/{id}/export）结构对齐
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class V2GroupDef:
    group_name: str
    group_code: str = ""
    parent_code: str = ""
    description: str | None = None


@dataclass
class V2TableDef:
    table_name: str
    table_code: str = ""
    layer_code: str = ""
    table_type: str = "attribute"
    geometry_type: str | None = None
    description: str | None = None


@dataclass
class V2FieldDef:
    table_code: str
    field_name: str
    field_alias: str | None = None
    data_type: str = "string"
    length: int | None = None
    precision: int | None = None
    nullable: bool = True
    is_required: bool = False
    is_primary_key: bool = False
    default_value: str | None = None
    description: str | None = None
    ordering: int = 0
    dict_codes: list[str] = field(default_factory=list)
    """绑定的字典编码列表（v2.22.3 新增）。

    来自「字段定义」Sheet 第 10 列「字典编码」，逗号/中文逗号/分号/空白分隔，
    顺序即书写顺序 —— 首个编码落库时记为 is_primary=True（主字典）。
    匹配 `StandardFieldDictLink` 的 UniqueConstraint(field_id, dict_id) N:M 语义。
    """


@dataclass
class V2DictDef:
    dict_name: str
    dict_code: str = ""
    scope: str = "standard"
    description: str | None = None
    items: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class V2StandardParseResult:
    name: str | None = None
    code: str | None = None
    category: str = "custom"
    description: str | None = None
    groups: list[V2GroupDef] = field(default_factory=list)
    tables: list[V2TableDef] = field(default_factory=list)
    fields: list[V2FieldDef] = field(default_factory=list)
    dictionaries: list[V2DictDef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recognized: bool = False  # False 表示不是 4-sheet 模板，调用方应回退旧解析器


def _rows_of(wb, title: str) -> list[tuple]:
    """取指定 sheet 的非空行。"""
    for ws in wb.worksheets:
        if ws.title == title:
            return [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    return []


def _cell_str(row: tuple, idx: int) -> str:
    """取单元格字符串值，缺失/越界一律返回 ""。

    openpyxl 把空单元格读作 `None`，直接 `str(None)` 会得到字面量
    `"None"` —— 早前判定「首列为空」用的是 `row[0] is None`，一旦该行
    后面有值就会误判成「整行空」而丢弃。统一走这里消掉这个坑。
    """
    if idx >= len(row):
        return ""
    val = row[idx]
    return "" if val is None else str(val).strip()


def _to_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    s = str(val).strip().lower()
    if s in ("是", "y", "yes", "true", "1", "必填", "主键", "√", "✓"):
        return True
    if s in ("否", "n", "no", "false", "0", ""):
        return False
    return default


def _to_int(val) -> int | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ("长度", "精度"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


# 「字典编码」单元格的分隔符：半角/全角逗号、分号、竖线、空白。
# 中文 Excel 里用户习惯用「，」，手输常用「;」，全角分号也一并接受。
_DICT_CODE_SEPARATORS = re.compile(r"[,，;；|\s]+")


def _split_dict_codes(val) -> list[str]:
    """拆分「字典编码」单元格为编码列表（保序、去重、去空）。

    v2.22.3 新增。容忍多种分隔符是因为该列由人在 Excel 里手填，
    与其余按固定下标解析的列不同，格式噪声必须在这里吸收掉。
    """
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        raw = [str(v) for v in val]
    else:
        raw = _DICT_CODE_SEPARATORS.split(str(val))
    seen: set[str] = set()
    codes: list[str] = []
    for item in raw:
        code = item.strip()
        if not code or code in ("字典编码", "dict_code"):
            continue
        if code in seen:          # 同格重复编码不该产生两条关联（会撞 uq_sfdl_field_dict）
            continue
        seen.add(code)
        codes.append(code)
    return codes


def parse_standard_excel_v2(file_path: str) -> V2StandardParseResult:
    """解析 v2 四 Sheet 模板，返回结构化定义。

    兼容模板与导出两套 sheet 命名：
      - 标准信息 / 层组定义 / 数据表定义 / 字段定义（模板）
      - 标准信息 / 层组 / 数据表 / 字段（导出）
    """
    result = V2StandardParseResult()
    try:
        wb = load_workbook(file_path, read_only=True, data_only=True)
    except Exception as e:
        result.warnings.append(f"Excel 解析失败: {e}")
        return result

    sheet_names = [ws.title for ws in wb.worksheets]
    # 识别标志：出现任一模板 sheet 即视为 v2 结构
    if not any(s in sheet_names for s in ("标准信息", "层组定义", "数据表定义", "字段定义", "字典定义")):
        wb.close()
        return result
    result.recognized = True

    # 1. 标准信息
    for row in _rows_of(wb, "标准信息"):
        if len(row) < 2 or row[0] is None or str(row[0]).strip() in ("字段名", "值"):
            continue
        key = str(row[0]).strip()
        val = row[1]
        if key == "标准名称":
            result.name = str(val).strip() if val is not None else None
        elif key == "标准编码":
            result.code = str(val).strip() if val is not None else None
        elif key == "分类":
            result.category = str(val).strip() if val is not None else "custom"
        elif key == "描述":
            result.description = str(val).strip() if val is not None else None

    # 2. 层组定义（模板）/ 层组（导出）
    g_sheet = next((s for s in ("层组定义", "层组") if s in sheet_names), None)
    if g_sheet:
        for row in _rows_of(wb, g_sheet)[1:]:
            if not row or row[0] is None or str(row[0]).strip() in ("层组名", "group_name"):
                continue
            result.groups.append(V2GroupDef(
                group_name=str(row[0]).strip(),
                group_code=str(row[1]).strip() if len(row) > 1 and row[1] is not None else "",
                parent_code=str(row[2]).strip() if len(row) > 2 and row[2] is not None else "",
                description=str(row[3]).strip() if len(row) > 3 and row[3] is not None else None,
            ))

    # 3. 数据表定义（模板）/ 数据表（导出）
    t_sheet = next((s for s in ("数据表定义", "数据表") if s in sheet_names), None)
    if t_sheet:
        for row in _rows_of(wb, t_sheet)[1:]:
            if not row or row[0] is None or str(row[0]).strip() in ("层组编码", "层组名", "group_code"):
                continue
            result.tables.append(V2TableDef(
                layer_code=str(row[0]).strip() if row[0] is not None else "",
                table_name=str(row[1]).strip() if len(row) > 1 and row[1] is not None else "",
                table_code=str(row[2]).strip() if len(row) > 2 and row[2] is not None else "",
                table_type=str(row[3]).strip().lower() if len(row) > 3 and row[3] is not None else "attribute",
                geometry_type=str(row[4]).strip() if len(row) > 4 and row[4] is not None else None,
                description=str(row[5]).strip() if len(row) > 5 and row[5] is not None else None,
            ))

    # 4. 字段定义（模板）/ 字段（导出）
    #    列: 表编码|字段名|中文名|数据类型|长度|精度|必填|主键|描述[|字典编码]
    #    第 10 列「字典编码」为 v2.22.3 新增的可选列 —— 旧 9 列模板 len(row) <= 9，
    #    走「留空即无绑定」分支，向后兼容不报错。
    f_sheet = next((s for s in ("字段定义", "字段") if s in sheet_names), None)
    if f_sheet:
        order = 0
        for row in _rows_of(wb, f_sheet)[1:]:
            if len(row) < 2 or row[1] is None or str(row[1]).strip() in ("字段名", "field_name"):
                continue
            fname = str(row[1]).strip()
            if not fname:
                # 模板末尾的填写说明行（只在末列有文字）不是字段定义，跳过。
                # 少了这行会被当成 field_name="" 的幽灵字段，再因表编码为空报一条
                # 「未在数据表定义中声明」的噪音告警。
                continue
            dtype = str(row[3]).strip().lower() if len(row) > 3 and row[3] is not None else "string"
            if dtype not in _VALID_TYPES:
                result.warnings.append(f"字段 '{fname}' 类型 '{dtype}' 不可识别，使用默认 string")
                dtype = "string"
            result.fields.append(V2FieldDef(
                table_code=str(row[0]).strip() if row[0] is not None else "",
                field_name=fname,
                field_alias=str(row[2]).strip() if len(row) > 2 and row[2] is not None else None,
                data_type=dtype,
                length=_to_int(row[4] if len(row) > 4 else None),
                precision=_to_int(row[5] if len(row) > 5 else None),
                is_required=_to_bool(row[6] if len(row) > 6 else None),
                is_primary_key=_to_bool(row[7] if len(row) > 7 else None),
                description=str(row[8]).strip() if len(row) > 8 and row[8] is not None else None,
                ordering=order,
                dict_codes=_split_dict_codes(row[9] if len(row) > 9 else None),
            ))
            order += 1

    # 5. 字典定义（模板）/ 字典（导出）— 每字典一行，字典项展开为多行
    #    列: 字典编码 | 字典名称 | 适用范围 | 字典项编码 | 字典项名称 | 描述
    d_sheet = next((s for s in ("字典定义", "字典") if s in sheet_names), None)
    if d_sheet:
        dict_map: dict[str, V2DictDef] = {}
        codeless_warned: set[str] = set()
        for row in _rows_of(wb, d_sheet)[1:]:
            # _rows_of 已滤掉全空行，这里每行至少有一个非空单元格。
            # 单元格取值统一走 _cell_str：openpyxl 空格读作 None，直接
            # str() 会得到字面量 "None"（早前 `row[0] is None` 的写法
            # 会把「只有名称、没填编码」的整行一并丢掉）。
            code = _cell_str(row, 0)
            name = _cell_str(row, 1)
            if code in ("字典编码", "dict_code"):
                continue
            # 字典编码是**字段绑定**的连接键，但字典实体本身不依赖它。
            # 早前首列为空即 continue，等于把整个字典静默丢掉 —— 用户填了
            # 名称却看不到任何提示。与 CSV 归档侧对齐：保留实体 + 告警，
            # 字典项也不跟着丢。名称也空才是真正的空行，跳过。
            if not code and not name:
                continue
            key = code or f"@name:{name}"
            d = dict_map.get(key)
            if d is None:
                scope = _cell_str(row, 2).lower() or "standard"
                description = _cell_str(row, 5)
                d = V2DictDef(
                    dict_code=code,
                    dict_name=name or code,
                    scope=scope,
                    description=description or None,
                )
                dict_map[key] = d
                if not code and name not in codeless_warned:
                    codeless_warned.add(name)
                    result.warnings.append(
                        f"字典「{name}」缺少字典编码：字典已保留，但该字典无法参与字段绑定，"
                        f"请补上编码后重新导入")
            item_code = _cell_str(row, 3)
            item_name = _cell_str(row, 4)
            if item_code:
                d.items.append((item_code, item_name))
        result.dictionaries = list(dict_map.values())

    wb.close()
    return result
