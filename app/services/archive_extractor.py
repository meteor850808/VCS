"""压缩包解压服务 — 支持 .zip/.tar/.tar.gz，逐文件提取、分类与验证。

v2.5 变更：
- 新增 classify_extracted_files() 统一文件分类
- 新增 find_gdb_dirs() GDB 目录检测
- 新增 filter_gdb_internal_files() GDB 内部文件过滤
- 新增 SHP 组件分组 group_shp_components()
- 移除 contains_gdb() / extract_gdb_to_dir()（不再需要二次解压）
"""

import io
import os
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractedFile:
    original_name: str     # 压缩包内文件路径，如 "gdb_folder/xxx.gdbtable"
    content: bytes
    file_size: int


@dataclass
class ExtractResult:
    files: list[ExtractedFile] = field(default_factory=list)
    total_size: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ShpGroup:
    """一个 Shapefile 组件组（.shp + 伴生文件）。"""
    shp: ExtractedFile | None = None
    companions: dict[str, ExtractedFile] = field(default_factory=dict)  # ext → ExtractedFile

    @property
    def has_shp(self) -> bool:
        return self.shp is not None

    @property
    def has_companion(self) -> bool:
        return bool(self.companions)

    @property
    def companion_contents(self) -> dict[str, bytes]:
        """返回 {ext: content} 映射，用于 Fiona 读取。"""
        result = {}
        if self.shp:
            result['.shp'] = self.shp.content
        for ext, ef in self.companions.items():
            result[ext] = ef.content
        return result


@dataclass
class ClassifiedFiles:
    """分类后的文件集合。"""
    gdb_groups: dict[str, list[ExtractedFile]] = field(default_factory=dict)  # gdb_dir → files
    shp_groups: dict[str, ShpGroup] = field(default_factory=dict)            # stem|parent → ShpGroup
    vectors: list[ExtractedFile] = field(default_factory=list)               # 其他矢量文件
    tables: list[ExtractedFile] = field(default_factory=list)                # CSV/XLSX
    ignored: list[ExtractedFile] = field(default_factory=list)               # 忽略的文件


_ARCHIVE_EXTENSIONS = frozenset({'.zip', '.tar', '.gz', '.tgz', '.tar.gz'})

# GDB 内部文件扩展名（不需要存储为独立 StoredFile）
_GDB_INTERNAL_EXTS = frozenset({
    '.gdbtable', '.gdbindexes', '.spx', '.atx', '.oix',
    '.Freelist', '.Catalog', '.gdb',  # 目录标记
})

# SHP 伴生文件扩展名
_SHP_COMPANION_EXTS = frozenset({'.dbf', '.shx', '.prj', '.cpg', '.sbn', '.sbx'})

# 其他矢量格式
_VECTOR_EXTS = frozenset({'.geojson', '.json', '.gpkg', '.kml', '.gml', '.dxf', '.obj'})

# 表格格式
_TABLE_EXTS = frozenset({'.csv', '.xlsx', '.xls'})


def is_archive(filename: str) -> bool:
    """判断文件扩展名是否为支持的压缩格式。"""
    name = filename.lower().strip()
    if name.endswith('.tar.gz') or name.endswith('.tgz'):
        return True
    ext = Path(name).suffix
    return ext in _ARCHIVE_EXTENSIONS


def extract_archive(content: bytes, filename: str, max_file_size: int, max_total_size: int) -> ExtractResult:
    """解压压缩包，返回提取的文件列表。"""
    result = ExtractResult()
    name_lower = filename.lower().strip()

    if name_lower.endswith('.zip'):
        _extract_zip(content, result, max_file_size, max_total_size)
    elif name_lower.endswith('.tar.gz') or name_lower.endswith('.tgz') or name_lower.endswith('.tar'):
        _extract_tar(content, result, max_file_size, max_total_size)
    else:
        result.errors.append(f"不支持的压缩格式: {filename} (仅支持 .zip / .tar / .tar.gz)")

    return result


# ── 文件分类 ──────────────────────────────────────────────────────────────────

def find_gdb_dirs(files: list[ExtractedFile]) -> set[str]:
    """从文件列表中检测所有 GDB 目录路径。

    Returns:
        GDB 目录路径集合，如 {"data/city.gdb", "test.gdb"}
    """
    gdb_dirs: set[str] = set()
    for ef in files:
        path = ef.original_name.replace('\\', '/')
        # 查找 .gdb/ 目录结构
        parts = path.split('/')
        for i, part in enumerate(parts):
            if part.lower().endswith('.gdb'):
                gdb_dir = '/'.join(parts[:i + 1])
                gdb_dirs.add(gdb_dir)
                break
    return gdb_dirs


def is_gdb_internal_file(file_path: str, gdb_dirs: set[str]) -> bool:
    """判断文件是否在 GDB 目录内部。"""
    path = file_path.replace('\\', '/')
    for gdb_dir in gdb_dirs:
        if path.startswith(gdb_dir + '/') or path.startswith(gdb_dir + '\\'):
            return True
    return False


def classify_extracted_files(files: list[ExtractedFile]) -> ClassifiedFiles:
    """将解压出的文件分类到不同的处理组。

    分类规则：
    1. GDB 目录内部的文件 → gdb_groups（按 GDB 目录分组）
    2. SHP 及其伴生文件 → shp_groups（按 stem+dir 分组）
    3. 其他矢量格式 → vectors
    4. 表格格式 → tables
    5. 其余 → ignored
    """
    result = ClassifiedFiles()

    # 第一步：检测 GDB 目录
    gdb_dirs = find_gdb_dirs(files)

    for ef in files:
        path = ef.original_name.replace('\\', '/')
        ext = Path(path).suffix.lower()

        # 1. GDB 内部文件
        if gdb_dirs and is_gdb_internal_file(path, gdb_dirs):
            # 提取 GDB 目录路径作为 key
            for gdb_dir in gdb_dirs:
                if path.startswith(gdb_dir + '/') or path.startswith(gdb_dir + '\\'):
                    result.gdb_groups.setdefault(gdb_dir, []).append(ef)
                    break
            continue

        # 2. SHP 组件
        if ext in _SHP_COMPANION_EXTS or ext == '.shp':
            stem = Path(path).stem.lower()
            parent = str(Path(path).parent)
            key = f"{stem}|{parent}"
            group = result.shp_groups.setdefault(key, ShpGroup())
            if ext == '.shp':
                group.shp = ef
            else:
                group.companions[ext] = ef
            continue

        # 3. 其他矢量文件
        if ext in _VECTOR_EXTS:
            result.vectors.append(ef)
            continue

        # 4. 表格文件
        if ext in _TABLE_EXTS:
            result.tables.append(ef)
            continue

        # 5. 忽略
        result.ignored.append(ef)

    return result


# ── 内部解压实现 ──────────────────────────────────────────────────────────────

def _fix_zip_filename(info) -> str:
    """修复 ZIP 条目中文文件名乱码（借鉴自单位 FileuploadService 方案）。

    Python zipfile 在条目无 UTF-8 flag 时按 cp437 解码文件名，
    中文 ZIP（GBK 编码，Windows 常见）会被误读为乱码。
    还原逻辑：cp437 → gbk 尝试；还原后含中文则采用，否则保留原名。
    """
    name = info.filename
    if not name:
        return name
    # 已含中文（UTF-8 flag 正确设置）→ 无需修复
    if any('\u4e00' <= ch <= '\u9fff' for ch in name):
        return name
    try:
        fixed = name.encode('cp437').decode('gbk')
        if any('\u4e00' <= ch <= '\u9fff' for ch in fixed):
            return fixed
    except Exception:
        pass
    return name


def _extract_zip(content: bytes, result: ExtractResult, max_file_size: int, max_total_size: int):
    """解压 .zip 文件（自动修复中文文件名乱码）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # 用 ZipInfo 对象读取，避免文件名（修复后）与索引不匹配
                _add_extracted(zf.read(info), _fix_zip_filename(info), result, max_file_size, max_total_size)
    except zipfile.BadZipFile as e:
        result.errors.append(f"ZIP 解压失败: {e}")


def _extract_tar(content: bytes, result: ExtractResult, max_file_size: int, max_total_size: int):
    """解压 .tar/.tar.gz 文件。"""
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode='r:*') as tf:
            for member in tf.getmembers():
                if member.isfile():
                    f = tf.extractfile(member)
                    if f is None:
                        continue
                    _add_extracted(f.read(), member.name, result, max_file_size, max_total_size)
    except tarfile.TarError as e:
        result.errors.append(f"TAR 解压失败: {e}")


def _add_extracted(
    data: bytes, name: str, result: ExtractResult,
    max_file_size: int, max_total_size: int,
):
    """验证并添加解压后的文件到结果集。"""
    clean = name.replace('\\', '/')
    if '..' in clean or clean.startswith('/'):
        result.errors.append(f"跳过危险路径: {name}")
        return
    if len(data) > max_file_size:
        result.errors.append(f"跳过过大文件: {name} ({len(data)} > {max_file_size})")
        return
    if result.total_size + len(data) > max_total_size:
        result.errors.append(f"解压总量超限，跳过: {name}")
        return

    base = Path(clean).name
    if base.startswith(('._', '.DS_Store', 'Thumbs.db')):
        return

    ef = ExtractedFile(
        original_name=clean,
        content=data,
        file_size=len(data),
    )
    result.files.append(ef)
    result.total_size += len(data)
