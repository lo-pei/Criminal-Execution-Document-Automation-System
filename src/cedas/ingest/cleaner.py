"""清洗层：把原始文本块重建成段落列表。

这是整个系统的地基。**两条输入通道共用本模块**，不得为 TXT 另写一套实现
（ADR-005）。清洗错了，后面所有的锚点与规则都会错，且难以察觉。

处理顺序固定，不得调换：

1. 字符规范化（全角数字/字母转半角、异体年份归一、中文间空格清理）
2. 噪声剔除（页码、跨页重复的页眉页脚、按版式位置判定的页眉）
3. 段落重建（软换行拼接、段落边界保留、日期跨行拼接、分项编号切分）

第 2 步有两条路径，取决于通道能力：TXT 只能靠「跨页重复计数」，PDF 还能用
「页面位置 + 字号」——这是双通道唯一的清洗差异，且都在本模块内完成（ADR-005）。
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from ..domain.enums import ParagraphKind
from ..domain.models import ProseParagraph
from .base import RawBlock

# 句末标点。行尾出现即视为段落边界。
TERMINAL_PUNCT = "。；：！？…"

# 分项编号：「一、」「（一）」「(一)」
_SECTION_RE = re.compile(r"^[（(]?[一二三四五六七八九十]{1,3}[）)、．.]")

# 纯页码行：「- 3 -」「3」「— 3 —」
_PAGE_NUMBER_RE = re.compile(r"^[\s\-—－_·]*\d{1,4}[\s\-—－_·]*$")

# 页码标签：「第3页」「第 3 页 / 共 5 页」「共 5 页 第 3 页」
_PAGE_LABEL_RE = re.compile(r"^第\s*\d{1,4}\s*页(\s*[/／,，]?\s*共\s*\d{1,4}\s*页)?$")
_PAGE_LABEL_ALT_RE = re.compile(r"^共\s*\d{1,4}\s*页\s*第\s*\d{1,4}\s*页$")

# 案号形态（仅用于段落分类，真正的抽取规则在 resources/rules/）
_CASE_NO_HINT_RE = re.compile(r"[（(]\s*\d{4}\s*[）)].{0,12}?号\s*$")

# 落款日期行，如「2023年五月一日」（年份已被规范化）
_DATE_LINE_RE = re.compile(r"^\d{4}年[一二三四五六七八九十]{1,2}月[一二三四五六七八九十]{1,3}日$")

# 落款行首词
_SIGNATURE_HEADS = (
    "审判长",
    "审判员",
    "人民陪审员",
    "书记员",
    "法官助理",
    "二〇",
    "二○",
)

# 标题关键词（无字号信息时的启发式判据）
_HEADING_KEYWORDS = (
    "判决书",
    "裁定书",
    "通知书",
    "人民法院",
    "人民检察院",
    "刑事附带民事",
)

_PART_HEADING_RE = re.compile(r"^第[一二三四五六七八九十]+部分")

# 法定结构段首词。判决书的段落以这些词开头是格式惯例，据此切段比靠标点更稳。
_PARAGRAPH_HEADS = (
    "被告人",
    "公诉机关",
    "附带民事诉讼原告人",
    "附带民事诉讼被告人",
    "辩护人",
    "委托诉讼代理人",
    "现羁押于",
    "因涉嫌",
    "本院",
    "书记员",
    "审判长",
    "审判员",
    "人民陪审员",
)

# 全角数字与拉丁字母 → 半角。**只转数字与字母**，不转中文标点：
# 把「，」转成「,」会破坏句末判定，是常见错误。
# 注意 str.translate 的键必须是码位（int），不能是字符。
_TRANSLATION = {
    **{code: code - 0xFEE0 for code in range(0xFF10, 0xFF1A)},  # ０-９
    **{code: code - 0xFEE0 for code in range(0xFF21, 0xFF3B)},  # Ａ-Ｚ
    **{code: code - 0xFEE0 for code in range(0xFF41, 0xFF5B)},  # ａ-ｚ
}

_CN_YEAR_DIGITS = {
    "〇": "0",
    "○": "0",
    "Ｏ": "0",
    "O": "0",
    "零": "0",
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}
_CN_YEAR_RE = re.compile(r"([〇○ＯO零一二三四五六七八九]{4})\s*年")


# 页眉判定：页面顶部占比、行字符上限、相对正文字号的上限比例
_HEADER_ZONE = 0.10
_HEADER_MAX_CHARS = 40
_HEADER_FONT_RATIO = 0.9


@dataclass
class _Line:
    """中间结构：一行文本及其通道信息。"""

    text: str
    page: Optional[int]
    font_size: Optional[float]
    src_index: int
    blank: bool = False
    bbox: Optional[tuple[float, float, float, float]] = None
    page_height: Optional[float] = None


def normalize(text: str) -> str:
    """字符级规范化。幂等。"""
    s = text.replace("\u3000", " ")
    s = s.translate(_TRANSLATION)
    s = _CN_YEAR_RE.sub(
        lambda m: "".join(_CN_YEAR_DIGITS[c] for c in m.group(1)) + "年", s
    )
    # 中文字符之间的空格是排版产物，删除
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    # 中文与标点之间的空格
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。、；：？！）】」』])", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _to_lines(blocks: Sequence[RawBlock]) -> list[_Line]:
    lines: list[_Line] = []
    for index, block in enumerate(blocks):
        text = block.text.replace("\r\n", "\n").replace("\r", "\n")
        for piece in text.split("\n"):
            lines.append(
                _Line(
                    text=piece,
                    page=block.page,
                    font_size=block.font_size,
                    src_index=index,
                    blank=not piece.strip(),
                    bbox=block.bbox,
                    page_height=block.page_height,
                )
            )
    return lines


def _median_font_size(lines: Sequence[_Line]) -> Optional[float]:
    sizes = [line.font_size for line in lines if line.font_size]
    if not sizes:
        return None
    return float(statistics.median(sizes))


def _repeated_texts(lines: Sequence[_Line], min_count: int = 3) -> set[str]:
    """跨页重复的短行 → 判定为页眉或页脚。

    这是 TXT 通道唯一可用的页眉页脚判据（无坐标、无字号）。
    只统计短行且不以句末标点结尾者，避免误伤正文。
    """
    counter: Counter[str] = Counter()
    for line in lines:
        text = normalize(line.text)
        if text and len(text) <= 30 and not text.endswith(tuple(TERMINAL_PUNCT)):
            counter[text] += 1
    return {text for text, count in counter.items() if count >= min_count}


def _header_keys(lines: Sequence[_Line], median_size: Optional[float]) -> set[tuple[str, int]]:
    """按页面位置判定页眉，返回 (文本, 页码) 集合（PDF 通道独有）。

    为什么不能只按位置删：判决书首行「XX人民法院」本身就是 22pt 大标题，
    也在页面顶部 10% 内，位置与页眉重叠。区分二者靠两条独立线索：

    * **字号**：页眉字号明显小于正文（9pt vs 14pt）。小字页眉每一页都删，
      包括首页。
    * **跨页重复**：字号与正文相当的老式页眉，只删第 2 页以后的出现。
      首页那次保留——它可能就是正文标题，删掉会丢法院名。

    单页文书里，小字页眉靠字号就能删；但若页眉字号与正文相当、又只出现
    一次，两条线索都不成立，此时保留——留一行噪音可以人工忽略，误删正文
    则不可恢复。
    """
    located = [line for line in lines if line.bbox and line.page_height]
    if not located:
        return set()

    pages_by_text: dict[str, set[int]] = {}
    for line in located:
        text = normalize(line.text)
        if text and len(text) <= _HEADER_MAX_CHARS and not text.endswith(tuple(TERMINAL_PUNCT)):
            pages_by_text.setdefault(text, set()).add(line.page or 0)

    keys: set[tuple[str, int]] = set()
    for line in located:
        text = normalize(line.text)
        if not text or len(text) > _HEADER_MAX_CHARS:
            continue
        if text.endswith(tuple(TERMINAL_PUNCT)):
            continue
        assert line.bbox is not None and line.page_height is not None
        if line.bbox[1] >= line.page_height * _HEADER_ZONE:
            continue  # 不在页面顶部带内

        page = line.page or 0
        small = (
            line.font_size is not None
            and median_size is not None
            and line.font_size <= median_size * _HEADER_FONT_RATIO
        )
        if small or (len(pages_by_text.get(text, ())) >= 2 and page > 1):
            keys.add((text, page))
    return keys


def _is_page_noise(text: str) -> bool:
    if _PAGE_NUMBER_RE.match(text) or _PAGE_LABEL_RE.match(text) or _PAGE_LABEL_ALT_RE.match(text):
        return True
    # 只含标点、不含任何文字与数字的行，一律视为版式残留
    return not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in text)


def _is_heading(text: str, font_size: Optional[float], median_size: Optional[float]) -> bool:
    """标题判定。

    有字号信息时优先用字号（PDF 通道）；否则退回启发式（TXT 通道）。
    这条分支是双通道能力差异的具体体现。
    """
    if len(text) > 30:
        return False
    if text.endswith(tuple(TERMINAL_PUNCT)):
        return False
    if font_size is not None and median_size and font_size >= median_size * 1.2:
        return True
    if any(keyword in text for keyword in _HEADING_KEYWORDS):
        return True
    return bool(_PART_HEADING_RE.match(text))


def _should_stand_alone(
    text: str,
    font_size: Optional[float],
    median_size: Optional[float],
) -> bool:
    """判断当前累积的段落是否应独占。

    标题、落款行在版式上都是独立文本块，一旦与后文拼接就会被彻底破坏，
    因此宁可多切不可少切。
    """
    if text.startswith(_SIGNATURE_HEADS) or _DATE_LINE_RE.match(text):
        return True
    return _is_heading(text, font_size, median_size)


def _needs_space(left: str, right: str) -> bool:
    """两行拼接时是否需要补空格。中文之间不补。"""
    if not left or not right:
        return False
    return left[-1].isascii() and left[-1].isalnum() and right[0].isascii() and right[0].isalnum()


def _join(lines: Sequence[_Line]) -> str:
    """拼接一个段落内的多行。软换行直接连，不补空格（中文场景）。"""
    pieces = [normalize(line.text) for line in lines]
    pieces = [piece for piece in pieces if piece]
    if not pieces:
        return ""

    result = pieces[0]
    for piece in pieces[1:]:
        result = f"{result} {piece}" if _needs_space(result, piece) else result + piece
    return result


def _classify(
    text: str,
    font_size: Optional[float],
    median_size: Optional[float],
) -> ParagraphKind:
    if _CASE_NO_HINT_RE.search(text):
        return ParagraphKind.CASE_NO
    if _SECTION_RE.match(text):
        return ParagraphKind.SECTION
    if text.startswith(_SIGNATURE_HEADS) or _DATE_LINE_RE.match(text):
        return ParagraphKind.SIGNATURE
    if _is_heading(text, font_size, median_size):
        return ParagraphKind.HEADING
    return ParagraphKind.BODY


def clean(
    blocks: Sequence[RawBlock],
    *,
    drop_running_heads: bool = True,
) -> list[ProseParagraph]:
    """把原始块列表重建为段落列表。

    Args:
        blocks: 任一通道产出的原始块。
        drop_running_heads: 是否剔除跨页重复的页眉页脚。关掉仅用于调试对照。

    Returns:
        段落列表，``index`` 从 0 连续编号。
    """
    lines = _to_lines(blocks)
    median_size = _median_font_size(lines)
    running = _repeated_texts(lines) if drop_running_heads else set()
    header_keys = _header_keys(lines, median_size) if drop_running_heads else set()

    paragraphs: list[ProseParagraph] = []
    buffer: list[_Line] = []
    pending_break = True

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = _join(buffer)
        if text:
            first = buffer[0]
            page = next((line.page for line in buffer if line.page is not None), None)
            paragraphs.append(
                ProseParagraph(
                    index=len(paragraphs),
                    text=text,
                    kind=_classify(text, first.font_size, median_size),
                    page=page,
                    font_size=first.font_size,
                    source_lines=(buffer[0].src_index, buffer[-1].src_index),
                )
            )
        buffer = []

    for line in lines:
        if line.blank:
            flush()
            pending_break = True
            continue

        text = normalize(line.text)
        if not text or _is_page_noise(text):
            continue
        if text in running or (text, line.page or 0) in header_keys:
            continue

        starts_new = (
            pending_break
            or _SECTION_RE.match(text) is not None
            or _CASE_NO_HINT_RE.search(text) is not None
            or text.startswith(_PARAGRAPH_HEADS)
            or _is_heading(text, line.font_size, median_size)
        )
        if not starts_new and buffer:
            previous = normalize(buffer[-1].text)
            # 案号行不以句读结尾（「（2011）汴刑终字第28号」以「号」收尾），
            # 紧跟其后的那一行会被并进来。二审判书里案号下面常常直接就是
            # 第一个当事人——「（2011）汴刑终字第28号上诉人（原审被告人）
            # 张某宾（绰号白宾）。」并成一段之后，这一段既不是段首称谓、
            # 也不再是案号，首部第一个被告人整条丢掉。案号一律独占一段。
            starts_new = previous.endswith(tuple(TERMINAL_PUNCT)) or bool(
                _CASE_NO_HINT_RE.search(previous)
            )

        if starts_new:
            flush()
        elif buffer and _should_stand_alone(_join(buffer), buffer[0].font_size, median_size):
            # 标题与落款行独占一段：PDF 中它们与正文是不同文本块，不得被软换行拼在一起
            flush()

        buffer.append(
            _Line(
                text,
                line.page,
                line.font_size,
                line.src_index,
                bbox=line.bbox,
                page_height=line.page_height,
            )
        )
        pending_break = False

    flush()
    return paragraphs


def clean_text(text: str, **kwargs) -> list[ProseParagraph]:
    """便捷入口：直接从一段文本清洗出段落。"""
    blocks = [RawBlock(text=line) for line in text.replace("\r\n", "\n").split("\n")]
    return clean(blocks, **kwargs)


__all__ = ["TERMINAL_PUNCT", "clean", "clean_text", "normalize"]
