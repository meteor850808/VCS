"""文件名验证与安全工具。"""

from pathlib import Path

from fastapi import HTTPException, status

from app.core.constants import DANGEROUS_EXTENSIONS, ARCHIVE_EXTENSIONS


def validate_filename(filename: str, allow_archives: bool = False) -> str:
    """验证文件名安全：拒绝路径遍历、空字节和危险扩展名。

    Args:
        filename: 待验证的文件名。
        allow_archives: 是否允许压缩包扩展名（data.py 中为 True，convert.py 中为 False）。

    Returns:
        验证通过的文件名。

    Raises:
        HTTPException 400: 文件名无效或包含危险扩展名。
    """
    if not filename or not filename.strip():
        raise HTTPException(status_code=400, detail="文件名不能为空")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="文件名包含非法路径字符")
    if "\x00" in filename:
        raise HTTPException(status_code=400, detail="文件名包含非法字符")

    ext = Path(filename).suffix.lower()
    blocked = DANGEROUS_EXTENSIONS - (ARCHIVE_EXTENSIONS if allow_archives else set())
    if ext in blocked:
        raise HTTPException(status_code=400, detail=f"不允许上传 {ext} 类型的文件")
    return filename
