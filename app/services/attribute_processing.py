"""属性处理 — DBF/CSV/GeoJSON 读取、编码检测、关键字段保护。"""

import csv
import logging
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AttributeResult:
    total_input: int = 0
    total_output: int = 0
    warnings: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    sample_data: list[dict] = field(default_factory=list)


from app.core.constants import CRITICAL_FIELDS


def process_attributes(
    file_path: str,
    select_fields: list[str] | None = None,
    target_encoding: str = "UTF-8",
    preserve_critical_fields: bool = True,
) -> AttributeResult:
    """读取文件属性，进行字段过滤和编码转换。"""
    p = Path(file_path)
    ext = p.suffix.lower()

    try:
        if ext in ('.dbf',):
            return _process_dbf(file_path, select_fields, target_encoding, preserve_critical_fields)
        elif ext in ('.csv',):
            return _process_csv(file_path, select_fields, target_encoding, preserve_critical_fields)
        elif ext in ('.geojson', '.json'):
            return _process_geojson_attrs(file_path, select_fields, preserve_critical_fields)
        else:
            return AttributeResult(warnings=[f"不支持属性读取的格式: {ext}"])
    except Exception as e:
        return AttributeResult(warnings=[f"属性处理失败: {e}"])


def _ensure_critical_fields(select_fields: list[str] | None) -> list | None:
    """确保 YWBH 和 FEATURECODE 被包含在输出中。"""
    if select_fields is None:
        return None
    result = list(select_fields)
    for f in CRITICAL_FIELDS:
        if f not in result and f.upper() not in (x.upper() for x in result):
            result.append(f)
    return result


@dataclass
class _DBFField:
    name: str
    field_type: str  # C, N, F, I, L, D, T, M, G, B
    length: int
    decimal: int


def _process_dbf(
    file_path: str,
    select_fields: list[str] | None,
    target_encoding: str,
    preserve_critical: bool,
) -> AttributeResult:
    """解析 DBF 文件（dBase III/IV 格式），包含字段定义解析和记录读取。"""
    import chardet

    warnings = []
    fields: list[str] = []
    field_defs: list[_DBFField] = []
    sample = []

    with open(file_path, 'rb') as f:
        header = f.read(32)
        if len(header) < 32:
            return AttributeResult(warnings=["无效的 DBF 文件头"])
        num_records = struct.unpack('<I', header[4:8])[0]
        header_len = struct.unpack('<H', header[8:10])[0]
        record_len = struct.unpack('<H', header[10:12])[0]

        # 解析字段定义（32 bytes each in header area）
        f.seek(32)
        while f.tell() < header_len - 1:
            raw = f.read(32)
            if len(raw) < 32 or raw[0] in (0x0D, 0):
                break
            name = raw[:11].split(b'\x00')[0].decode('ascii', errors='replace').strip()
            if not name:
                continue
            field_defs.append(_DBFField(
                name=name,
                field_type=chr(raw[11]),
                length=raw[16],
                decimal=raw[17],
            ))
            fields.append(name)

        # 检测编码（读取部分数据做 chardet 分析）
        f.seek(header_len)
        raw_data = f.read(min(65536, num_records * record_len))
        detected = chardet.detect(raw_data)
        encoding = detected.get('encoding') or target_encoding or 'gbk'
        # DBF 常见编码为 GBK/CP936
        if encoding.upper() in ('ASCII',):
            encoding = 'gbk'

        # 读取记录样本（前 100 条）
        f.seek(header_len)
        record_num = 0
        while record_num < num_records and len(sample) < 100:
            raw_record = f.read(record_len)
            if len(raw_record) < record_len:
                break
            record_num += 1
            deleted_flag = raw_record[0]
            if deleted_flag == 0x2A:  # 已删除记录
                continue

            row = {}
            offset = 1
            for df in field_defs:
                raw_val = raw_record[offset:offset + df.length]
                # 去除尾部空白（DBF 字段定长，空格填充）
                val_str = raw_val.rstrip(b' \x00').decode(encoding, errors='replace')
                if df.field_type in ('N', 'F'):
                    val_str = val_str.strip()
                    row[df.name] = val_str if val_str else None
                elif df.field_type == 'I':
                    if len(raw_val) >= 4:
                        row[df.name] = struct.unpack('<i', raw_val[:4])[0]
                    else:
                        row[df.name] = None
                elif df.field_type == 'L':
                    row[df.name] = val_str.strip() if val_str.strip() else None
                elif df.field_type == 'D':
                    val_str = val_str.strip()
                    row[df.name] = val_str if val_str else None
                else:
                    row[df.name] = val_str if val_str.strip() else None
                offset += df.length

            sample.append(row)

    if not fields:
        return AttributeResult(total_input=num_records, total_output=num_records,
                               fields=[], warnings=["DBF 中未发现字段定义"])

    selected = select_fields or fields
    if preserve_critical:
        selected = _ensure_critical_fields(select_fields)

    return AttributeResult(
        total_input=num_records,
        total_output=num_records,
        fields=fields,
        sample_data=sample,
        warnings=warnings,
    )


def _process_csv(
    file_path: str,
    select_fields: list[str] | None,
    target_encoding: str,
    preserve_critical: bool,
) -> AttributeResult:
    """解析 CSV 文件。"""
    import chardet

    # 检测编码
    with open(file_path, 'rb') as f:
        raw = f.read(65536)
    detected = chardet.detect(raw)
    encoding = detected['encoding'] or 'utf-8'

    sample = []
    fields = []
    total = 0

    try:
        with open(file_path, encoding=encoding, errors='replace', newline='') as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            for row in reader:
                total += 1
                if len(sample) < 100:
                    sample.append(dict(row))
    except Exception as e:
        logger.warning("CSV 读取失败: %s", e)

    if preserve_critical:
        select_fields = _ensure_critical_fields(select_fields)

    # Apply field filtering
    if select_fields:
        fields = [f for f in fields if f in select_fields]
        sample = [{k: v for k, v in row.items() if k in select_fields} for row in sample]

    return AttributeResult(
        total_input=total,
        total_output=total,
        fields=fields,
        sample_data=sample,
    )


def _process_geojson_attrs(
    file_path: str,
    select_fields: list[str] | None,
    preserve_critical: bool,
) -> AttributeResult:
    """解析 GeoJSON 属性字段。"""
    import json

    with open(file_path, encoding='utf-8') as f:
        data = json.load(f)

    features = data.get('features', [])
    fields = []
    sample = []

    if features:
        props = features[0].get('properties', {}) or {}
        fields = list(props.keys())
        for feat in features[:100]:
            p = feat.get('properties', {})
            if p:
                sample.append(dict(p))

    if preserve_critical:
        select_fields = _ensure_critical_fields(select_fields)

    # Apply field filtering
    if select_fields:
        fields = [f for f in fields if f in select_fields]
        sample = [{k: v for k, v in row.items() if k in select_fields} for row in sample]

    return AttributeResult(
        total_input=len(features),
        total_output=len(features),
        fields=fields,
        sample_data=sample,
    )
