"""GDB (File Geodatabase) 处理器 — 从已解压的 GDB 目录直接提取要素入库 PostGIS。

v2.10 变更：
- 新增 gdb_read_lock：同一 GDB 目录的并发读取互斥（GDAL 数据源非线程安全，
  多个 asyncio.to_thread 线程同时 fiona.open 同一 GDB 会触发 Permission denied）
- read_layer_features 打开/读取失败改为抛出异常（迭代时传播），
  由调用方捕获并回填 stats["error"] → 上传响应 warning 可见（此前只 logger.warning，
  失败静默 → feature_count=0 无提示）

v2.9 变更：
- 新增 prepare_gdb / read_layer_features：不再生成 GeoJSON 中间产物，
  要素直接以原生几何（POINT/LINESTRING/POLYGON/Multi* 等）写入 PostGIS 分表。

设计说明：
- GDB 是目录型格式（.gdb 目录内包含 .gdbtable/.gdbindexes 等二进制文件），
  传统做法是整体转成 GeoJSON 再二次展开，既膨胀内存又丢失原生几何语义。
  新做法：GDB 目录解压后写入临时目录，Fiona 逐 layer 读取要素，
  直接由调用方写入 PostGIS spatial 分表（原生几何类型 + 源 CRS SRID）。
- 调用方负责临时目录生命周期：prepare_gdb 返回 tmp_dir，
  在全部 layer 处理完成后统一清理（见 data.py _load_gdb_group_features_async）。
"""

import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field

import fiona

from app.services.archive_extractor import ExtractedFile
from app.services.format_detection import normalize_stored_geometry_type

logger = logging.getLogger(__name__)

# ── GDB 读取互斥锁（按 gdb_path 粒度，进程级）──────────────────────────────
# GDAL 数据集对象非线程安全：同一 GDB 目录被多个线程同时 fiona.open/迭代时，
# Windows 上会因句柄/状态冲突抛 CPLE_OpenFailedError(Permission denied)，
# 导致并发 GDB 层子任务要素静默为 0。以 GDB 路径为粒度互斥，保证任何时刻
# 每个 GDB 目录只有一个线程在读取。
_GDB_LOCKS: dict[str, threading.Lock] = {}
_GDB_LOCKS_GUARD = threading.Lock()


def gdb_read_lock(gdb_path: str) -> threading.Lock:
    """返回指定 GDB 路径的进程级互斥锁（惰性创建，线程安全）。"""
    with _GDB_LOCKS_GUARD:
        lock = _GDB_LOCKS.get(gdb_path)
        if lock is None:
            lock = threading.Lock()
            _GDB_LOCKS[gdb_path] = lock
        return lock


# ── 新接口：直接入库 PostGIS ─────────────────────────────────────────────────

@dataclass
class GdbLayerMeta:
    """单个 GDB layer 的元数据（不含要素数据）。"""
    name: str
    crs_wkt: str | None = None
    geometry_type: str | None = None


@dataclass
class GdbGroupInfo:
    """一个 GDB 组的准备结果（临时目录 + 层元数据）。"""
    gdb_path: str
    gdb_basename: str
    layers: list[GdbLayerMeta] = field(default_factory=list)
    tmp_dir: str = ""


def prepare_gdb(
    gdb_files: list[ExtractedFile],
    gdb_dir_name: str,
    tmp_dir: str | None = None,
) -> GdbGroupInfo:
    """将 GDB 目录写入临时目录，列出全部 layer 及其 CRS（不读取要素数据）。

    Args:
        gdb_files: GDB 目录内的所有已解压文件。
        gdb_dir_name: GDB 目录名（如 "data/city.gdb"）。
        tmp_dir: 临时目录路径（None 则自动创建）。

    Returns:
        GdbGroupInfo：gdb_path / gdb_basename / layers（每层 name + crs_wkt）/ tmp_dir。
        tmp_dir 由调用方负责在全部 layer 处理完成后清理（shutil.rmtree）；
        若自动创建且无可处理 layer，本函数会立即自行清理并将 tmp_dir 置空。
        无法定位 .gdb 目录时返回 gdb_path="" 的空对象。
    """
    if not gdb_files:
        return GdbGroupInfo(gdb_path="", gdb_basename="", tmp_dir="")

    own_tmp = not tmp_dir
    if own_tmp:
        tmp_dir = tempfile.mkdtemp(prefix='vcs_gdb_')

    info = GdbGroupInfo(gdb_path="", gdb_basename="", tmp_dir=tmp_dir or "")

    def _cleanup() -> None:
        """仅自动创建的目录由本函数负责清理；调用方传入的目录由其自身管理。"""
        if own_tmp:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                info.tmp_dir = ""
            except Exception:
                pass

    try:
        # 将 GDB 文件写入临时目录
        for ef in gdb_files:
            dest = os.path.join(tmp_dir, ef.original_name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not ef.original_name.endswith('/'):
                with open(dest, 'wb') as f:
                    f.write(ef.content)

        # 查找 .gdb 目录的实际路径
        gdb_path = None
        for ef in gdb_files:
            path = ef.original_name.replace('\\', '/')
            parts = path.split('/')
            for i, part in enumerate(parts):
                if part.lower().endswith('.gdb'):
                    gdb_path = os.path.join(tmp_dir, *parts[:i + 1])
                    break
            if gdb_path:
                break

        if not gdb_path or not os.path.isdir(gdb_path):
            logger.warning(f"GDB 目录不存在: {gdb_dir_name}")
            _cleanup()
            return info

        info.gdb_path = gdb_path
        info.gdb_basename = os.path.basename(gdb_path).replace('.gdb', '')

        # 仅读取各 layer 元数据（crs / schema 几何类型），不加载要素
        for layer in fiona.listlayers(gdb_path):
            try:
                with fiona.open(gdb_path, layer=layer) as src:
                    crs_wkt = src.crs.to_wkt() if src.crs else None
                    geom_type = None
                    try:
                        geom_type = src.schema.get("geometry") if src.schema else None
                        # 【v2.10.2】fiona schema 会报告维度声明（"3D MultiPolygon"/"Polygon ZM"），
                        # 剥离维度并归一化为五类（Point/LineString/Polygon/3d/table），
                        # 与 SHP/GeoJSON 路径的 geometry_type 语义保持一致，避免 2D 数据被标成 3D。
                        geom_type = normalize_stored_geometry_type(geom_type)
                    except Exception:
                        pass
                info.layers.append(GdbLayerMeta(name=layer, crs_wkt=crs_wkt, geometry_type=geom_type))
            except Exception as layer_e:
                logger.warning(f"GDB layer 元数据读取失败 {layer}: {layer_e}")
                continue

        if not info.layers:
            # 无任何可读 layer（或全部读取失败）：立即清理自动创建的目录
            _cleanup()
        return info

    except Exception as gdb_e:
        logger.warning(f"GDB 准备失败: {gdb_e}")
        _cleanup()
        return GdbGroupInfo(gdb_path="", gdb_basename="", tmp_dir="")


def read_layer_features(gdb_path: str, layer_name: str):
    """流式读取单个 GDB layer 的全部要素（生成器，不整层载入内存）。

    v2.10：改为 yield 生成器，配合 write_features_batch_sync 分页落库，
    大图层（数十万要素）内存占用恒定、无截断。

    v2.10.1：打开/读取失败不再吞异常——生成器函数体在首次迭代时才执行，
    异常会从迭代处（write_features_batch_sync 的 for 循环）向上传播，
    由调用方（_sync_load_gdb_layer）捕获并回填 stats["error"]，
    保证上传响应 warning 可见（此前静默 → 要素为 0 且无提示）。

    Yields:
        dict: {geometry(GeoJSON dict), properties(dict),
               feature_index(int), layer_name(str)}
    """
    with fiona.open(gdb_path, layer=layer_name) as src:
        for idx, feat in enumerate(src):
            geom = feat.get("geometry")
            props = feat.get("properties") or {}
            if geom and geom.get("type"):
                yield {
                    "geometry": geom,
                    "properties": dict(props),
                    "feature_index": idx,
                    "layer_name": layer_name,
                }
