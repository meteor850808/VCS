# -*- coding: utf-8 -*-
"""DWG → DXF 转换服务（LibreDWG dwg2dxf 集成）。

GDAL/Fiona 无 DWG 驱动，本模块借助 LibreDWG（GNU GPL 开源）的 dwg2dxf.exe
将 DWG 转为 DXF 后复用既有 Fiona DXF 解析链路，实现 DWG 要素提取。

工具部署位置: <项目>/tools/libredwg/
  - dwg2dxf.exe 及其运行时 DLL（libredwg-0.dll / libiconv-2.dll / libpcre2-*.dll）
  - 来源: https://github.com/LibreDWG/libredwg/releases (v0.14 win64)

降级策略: 工具缺失或转换失败时返回空列表并记录告警，不中断上传流程。
"""
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根/tools/libredwg（本文件位于 app/services/，向上三级）
_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools" / "libredwg"
_DWG2DXF = _TOOLS_DIR / "dwg2dxf.exe"

# 单文件转换超时（秒）——大图纸兜底，防止子进程挂死拖垮上传线程池
CONVERT_TIMEOUT_SECONDS = 120


def _resolve_dwg2dxf() -> str | None:
    """定位 dwg2dxf 可执行文件，找不到返回 None。

    优先级：
    1) 模块属性 `_DWG2DXF` 指向真实文件（打包的 win64 exe / 测试覆盖）；
    2) Windows：仅用打包工具，忽略 PATH（避免误用未知版本）；
    3) POSIX：系统安装（apt/源码 install 后位于 PATH）。
    """
    if _DWG2DXF.is_file():
        return str(_DWG2DXF)
    if sys.platform == "win32":
        return None
    return shutil.which("dwg2dxf")


def is_dwg_converter_available() -> bool:
    """检查 LibreDWG dwg2dxf 工具是否可用（平台感知）。"""
    return _resolve_dwg2dxf() is not None


def dwg_to_dxf(dwg_path: str, timeout: int = CONVERT_TIMEOUT_SECONDS) -> str | None:
    """将 DWG 转换为 DXF，返回生成的 DXF 路径；失败返回 None。

    Args:
        dwg_path: 源 DWG 文件路径
        timeout: 子进程超时秒数

    Note:
        返回的 DXF 为临时文件，调用方负责用后清理。
    """
    exe = _resolve_dwg2dxf()
    if exe is None:
        logger.warning("LibreDWG 未部署（dwg2dxf 不可用），跳过 DWG 转换")
        return None

    workdir = tempfile.mkdtemp(prefix="dwg_conv_")
    dxf_path = os.path.join(workdir, "out.dxf")
    try:
        proc = subprocess.run(
            [exe, "-y", "-o", dxf_path, str(dwg_path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=str(_TOOLS_DIR) if sys.platform == "win32" else None,
        )
        if proc.returncode != 0 or not os.path.isfile(dxf_path):
            stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
            logger.warning(
                "DWG 转换失败 %s: exit=%s %s",
                Path(dwg_path).name, proc.returncode, " | ".join(stderr_tail),
            )
            shutil.rmtree(workdir, ignore_errors=True)
            return None
        # LibreDWG 的非致命告警（Unstable Class 等）记 debug 即可
        if proc.stderr:
            logger.debug("dwg2dxf 告警 %s: %s", Path(dwg_path).name, proc.stderr[:500])
        return dxf_path
    except subprocess.TimeoutExpired:
        logger.warning("DWG 转换超时(>%ss) %s", timeout, Path(dwg_path).name)
        shutil.rmtree(workdir, ignore_errors=True)
        return None
    except Exception as e:
        logger.warning("DWG 转换异常 %s: %s", Path(dwg_path).name, e)
        shutil.rmtree(workdir, ignore_errors=True)
        return None


def cleanup_converted_dxf(dxf_path: str | None) -> None:
    """清理转换产物及其临时工作目录。"""
    if not dxf_path:
        return
    try:
        workdir = os.path.dirname(dxf_path)
        shutil.rmtree(workdir, ignore_errors=True)
    except Exception:
        pass
