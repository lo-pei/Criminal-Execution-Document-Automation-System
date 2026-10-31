"""输入层的公共结构。

两条通道（PDF 生产 / TXT 开发）都产出 ``RawBlock``，再由同一个
:func:`cedas.ingest.cleaner.clean` 处理。这是 ADR-005 的硬约束：
**不得为 TXT 单独写一套清洗逻辑**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RawBlock:
    """从文档中提取的原始文本块。

    属性缺失即为该通道的能力降级，而非错误：

    ==========  ==========  ==============  ========
    通道        page        font_size       bbox
    ==========  ==========  ==============  ========
    PDF         有          有              有
    TXT         None        None            None
    ==========  ==========  ==============  ========
    """

    text: str
    page: Optional[int] = None
    font_size: Optional[float] = None
    bbox: Optional[tuple[float, float, float, float]] = None

    @property
    def has_layout(self) -> bool:
        """是否携带版式信息。TXT 通道恒为 ``False``。"""
        return self.font_size is not None or self.bbox is not None


__all__ = ["RawBlock"]
