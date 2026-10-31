"""输入层：读取与清洗。

两条通道（PDF 生产 / TXT 开发）都产出 ``RawBlock``，共用 ``cleaner.clean``。
"""

from __future__ import annotations

from pathlib import Path

from ..config import SUPPORTED_EXTENSIONS
from .base import RawBlock
from .cleaner import clean, clean_text, normalize


class UnsupportedFormatError(ValueError):
    """扩展名既不是 PDF 也不是 TXT。"""


def read_document(path: Path | str) -> tuple[list[RawBlock], str]:
    """按扩展名选择通道，返回 ``(原始块, 通道名)``。

    **通道选择只有这一处实现**：命令行、界面、端到端流程都从这里走，
    否则一旦新增一种格式，总有一个入口忘记改。
    """
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix == ".txt":
        from . import txt_reader

        return txt_reader.read(target), "txt"
    if suffix == ".pdf":
        from . import pdf_reader

        return pdf_reader.read(target), "pdf"
    raise UnsupportedFormatError(
        f"不支持的文件类型 {suffix!r}，仅支持：{'、'.join(SUPPORTED_EXTENSIONS)}"
    )


__all__ = [
    "RawBlock",
    "UnsupportedFormatError",
    "clean",
    "clean_text",
    "normalize",
    "read_document",
]
