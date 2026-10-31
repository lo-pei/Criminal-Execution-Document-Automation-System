"""把一份 TXT 样本排版成「仿真实 PDF」，用于验证 PDF 通道。

为什么需要这个工具
------------------
生产环境只有 PDF，开发期只有人工提取的 TXT。两条通道共用 ``cleaner.clean()``
（ADR-005），但 PDF 通道独有的三样信息——页码、字号、坐标——恰恰是清洗层
判定页眉页脚与标题的依据，TXT 里没有，于是「在 TXT 上通过」不能推出
「在 PDF 上通过」（``docs/05`` §7 记的就是这个风险）。

本工具把已验证的 TXT 样本按法院文书的版式重新排成 PDF：A4、页边距、
大字号标题、正文按行宽折行、每页带页眉与「— N —」页脚。生成物是**合成文本**，
不含任何真实案件信息，可以进仓库。

它不追求印刷级还原，只复现 PDF 通道真正依赖的那几个特征：
文本块切分、视觉行、字号层级、跨页重复的页眉、页脚页码。

用法
----
    python tools/make_sample_pdf.py                     # 默认样本
    python tools/make_sample_pdf.py 某样本.txt -o out.pdf
    python tools/make_sample_pdf.py --dump              # 打印提取到的 dict 结构

依赖 PyMuPDF 与一台装有中文字体的 Windows。**不在交付包的运行依赖里**，
只在开发机跑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 版式：A4，单位 pt
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN_X = 72.0
MARGIN_TOP = 84.0
MARGIN_BOTTOM = 84.0

BODY_SIZE = 14.0  # 正文四号
TITLE_SIZE = 22.0  # 标题二号
CASE_NO_SIZE = 16.0  # 案号三号
HEADER_SIZE = 9.0
FOOTER_SIZE = 9.0

LINE_GAP = 1.62  # 行距倍数，判决书正文偏松
PARAGRAPH_GAP = 10.0  # 段间距

# 首页前两行按标题处理（法院名 + 文书名），第三行是案号
TITLE_LINES = 2

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TXT = ROOT / "examples" / "S000__京0105刑初123号.txt"

FONT_CANDIDATES = (
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
)


def pick_font(explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"字体不存在：{path}")
        return path
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    raise SystemExit(
        "找不到中文字体。用 --font 指定一个 .ttf/.ttc，例如 C:/Windows/Fonts/simsun.ttc"
    )


def char_width(ch: str, size: float) -> float:
    """估算单字宽度。中文全角占 1em，ASCII 半角约 0.5em。

    宋体的实际字宽与估算有零点几 pt 的偏差，用于折行足够；本工具不追求
    像素级还原，只要视觉行数与真实排版同量级。
    """
    return size if ord(ch) > 0x2E7F else size * 0.5


def text_width(text: str, size: float) -> float:
    return sum(char_width(ch, size) for ch in text)


def wrap(text: str, max_width: float, size: float) -> list[str]:
    """按可用行宽折行。这是「软换行」的来源——清洗层要把它拼回去。"""
    lines: list[str] = []
    current: list[str] = []
    width = 0.0
    for ch in text:
        step = char_width(ch, size)
        if current and width + step > max_width:
            lines.append("".join(current))
            current, width = [], 0.0
        current.append(ch)
        width += step
    if current:
        lines.append("".join(current))
    return lines or [""]


def load_paragraphs(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8-sig")
    return [line.strip() for line in raw.replace("\r\n", "\n").split("\n") if line.strip()]


def build(
    paragraphs: list[str],
    out_path: Path,
    font_path: Path,
    header: str,
    paragraphs_per_page_hint: int = 0,
) -> None:
    import pymupdf

    doc = pymupdf.open()
    usable_width = PAGE_WIDTH - 2 * MARGIN_X

    def new_page():
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        # 每页都要注册字体：PDF 字体资源是页级的
        page.insert_font(fontname="cn", fontfile=str(font_path))
        # 页眉：法院名 + 文书名，居中，小字
        if header:
            page.insert_text(
                ((PAGE_WIDTH - text_width(header, HEADER_SIZE)) / 2, 46),
                header,
                fontname="cn",
                fontsize=HEADER_SIZE,
            )
        return page

    page = new_page()
    y = MARGIN_TOP

    for index, paragraph in enumerate(paragraphs):
        if index < TITLE_LINES:
            size = TITLE_SIZE
        elif index == TITLE_LINES:
            size = CASE_NO_SIZE
        else:
            size = BODY_SIZE

        lines = wrap(paragraph, usable_width, size)
        for line in lines:
            step = size * LINE_GAP
            if y + step > PAGE_HEIGHT - MARGIN_BOTTOM:
                page = new_page()
                y = MARGIN_TOP
            x = MARGIN_X
            if size >= CASE_NO_SIZE:  # 标题与案号居中
                x = (PAGE_WIDTH - text_width(line, size)) / 2
            page.insert_text((x, y), line, fontname="cn", fontsize=size)
            y += step
        y += PARAGRAPH_GAP
        if paragraphs_per_page_hint and (index + 1) % paragraphs_per_page_hint == 0:
            page = new_page()
            y = MARGIN_TOP

    # 页脚页码放在最后统一写，避免分页时漏页
    total = doc.page_count
    for number, page in enumerate(doc, start=1):
        label = f"— {number} —"
        page.insert_text(
            ((PAGE_WIDTH - text_width(label, FOOTER_SIZE)) / 2, PAGE_HEIGHT - 46),
            label,
            fontname="cn",
            fontsize=FOOTER_SIZE,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 不裁剪字体的话，一个宋体就能让 PDF 涨到 9 MB，样本没法进仓库
    doc.subset_fonts()
    doc.save(str(out_path), deflate=True, garbage=4)
    doc.close()
    print(f"已生成 {out_path}（{total} 页，{out_path.stat().st_size / 1024:.1f} KB）")


def dump(path: Path) -> None:
    """打印提取结构，用于确认 block / line / span 的实际切分。"""
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        for page_no, page in enumerate(doc, start=1):
            print(f"=== page {page_no}  rect={page.rect} ===")
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                print(f"  block bbox={tuple(round(v, 1) for v in block['bbox'])}")
                for line in block["lines"]:
                    spans = [(s["size"], s["font"], s["text"][:20]) for s in line["spans"]]
                    print(f"    line bbox={tuple(round(v, 1) for v in line['bbox'])} spans={spans}")


def main() -> int:
    parser = argparse.ArgumentParser(description="把 TXT 样本排版成仿真实 PDF")
    parser.add_argument("txt", nargs="?", default=str(DEFAULT_TXT), type=Path)
    parser.add_argument("-o", "--out", type=Path, default=None)
    parser.add_argument("--font", default=None, help="中文字体路径")
    parser.add_argument("--header", default="北京市朝阳区人民法院　刑事判决书", help="每页页眉文字")
    parser.add_argument("--dump", action="store_true", help="只打印提取结构，不生成")
    args = parser.parse_args()

    txt_path: Path = args.txt
    if not txt_path.is_file():
        print(f"找不到样本：{txt_path}", file=sys.stderr)
        return 2

    out_path = args.out or txt_path.with_suffix(".pdf")

    if args.dump:
        dump(out_path)
        return 0

    paragraphs = load_paragraphs(txt_path)
    if not paragraphs:
        print("样本为空。", file=sys.stderr)
        return 2

    build(paragraphs, out_path, pick_font(args.font), args.header)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
