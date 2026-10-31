"""TXT 通道（开发期专用）。

因保密要求，开发阶段只能获得人工从 PDF 全选复制得到的纯文本。
本通道**只用于规则开发与回归**，不得出现在生产流程中（ADR-005）。

输出 ``RawBlock`` 时 ``page`` / ``font_size`` 恒为 ``None``。
"""

from __future__ import annotations

from pathlib import Path

from .base import RawBlock

# 按序尝试。gb18030 覆盖 GBK/GB2312；utf-16 用于记事本「Unicode」另存。
_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "utf-16")


class EncodingDetectionError(ValueError):
    """所有候选编码均无法解码。调用方应提示用户转存 UTF-8。"""


def decode(data: bytes) -> tuple[str, str]:
    """按序尝试解码，返回 (文本, 实际编码)。"""
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise EncodingDetectionError(
        "无法识别文本编码，请用记事本另存为 UTF-8 后重试。"
    )


def read(path: Path | str) -> list[RawBlock]:
    """读取 TXT，按行切成 ``RawBlock``。

    每行一个块——行结构是后续段落重建的唯一依据，不得在此合并。
    """
    file_path = Path(path)
    text, _ = decode(file_path.read_bytes())
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    blocks: list[RawBlock] = []
    for line in text.split("\n"):
        blocks.append(RawBlock(text=line))
    return blocks


__all__ = ["EncodingDetectionError", "decode", "read"]
