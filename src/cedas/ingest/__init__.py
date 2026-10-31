"""输入层：读取与清洗。

两条通道（PDF 生产 / TXT 开发）都产出 ``RawBlock``，共用 ``cleaner.clean``。
"""

from .base import RawBlock
from .cleaner import clean, clean_text, normalize

__all__ = ["RawBlock", "clean", "clean_text", "normalize"]
