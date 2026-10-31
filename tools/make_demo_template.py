"""生成合成「执行通知书」模板（.docx），供联调与回归使用。

模板原件来自法院、属受控材料，**不入库**（见 ``resources/templates/README.md``）。
但没有模板，``cedas render`` 这条链就没法在没有法院材料的机器上跑通、
也没法进 CI。本脚本按《法院刑事诉讼文书样式》的通行版式生成一份**合成模板**：

- 版式、占位符位置与真实模板同构（标题、案号、正文、逐格填写的表格、落款）
- 文书正文首行带「示例·合成·不得作为司法文书使用」标注，不会被误当正式文书
- 只使用段落与简单表格，不含内容控件、域代码、SmartArt——目标机办公软件版本低

用法：

    python tools/make_demo_template.py                      # 生成到 resources/templates/执行通知书.示例.docx
    python tools/make_demo_template.py --out D:/tmp/t.docx   # 指定路径
    python tools/make_demo_template.py --check               # 只校验占位符与映射表是否一致

``--check`` 回答一个具体问题：**模板里的占位符集合与映射表声明的字段集合是否一致**。
不一致意味着要么模板漏了字段（生成后该处空白），要么映射表写错了名（渲染直接报错）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "resources" / "templates" / "执行通知书.示例.docx"
MAPPING = ROOT / "resources" / "mappings" / "执行通知书.example.json"

BODY_FONT = "仿宋"
TITLE_FONT = "宋体"
NOTE = "（示例模板 · 合成文书 · 仅用于系统联调与回归，不得作为司法文书使用）"

TITLE = "执 行 通 知 书"

# 表格逐格填写，不用 docxtpl 的行循环（{%tr%}）——旧版 Word 存出的文档
# 里循环标记容易被拆坏，且法院模板基本都是固定行数。
TABLE_ROWS = (
    ("罪　　名", "{{罪名}}罪"),
    ("主　　刑", "{{主刑}}"),
    ("刑　　期", "{{刑期}}"),
    ("刑期起止", "{{刑期起日}} 至 {{刑期止日}}"),
    ("释放日期", "{{释放日期}}"),
    ("生效日期", "{{生效日期}}"),
)


def _style_run(run, *, font: str, size: float, bold: bool = False, gray: bool = False) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    run.font.name = font
    run.font.size = Pt(size)
    run.bold = bold
    if gray:
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    # 中文字体必须写 eastAsia，否则 Word 只认 latin 字体，正文回退成宋体
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)


def _paragraph(document, text: str, *, font: str = BODY_FONT, size: float = 16,
               bold: bool = False, align: str = "left", indent_chars: float = 0,
               space_after: float = 6, gray: bool = False):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    paragraph = document.add_paragraph()
    paragraph.alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
    }[align]
    paragraph.paragraph_format.line_spacing = 1.5
    paragraph.paragraph_format.space_after = Pt(space_after)
    if indent_chars:
        paragraph.paragraph_format.first_line_indent = Pt(size * indent_chars)
    _style_run(paragraph.add_run(text), font=font, size=size, bold=bold, gray=gray)
    return paragraph


def build(out: Path | str = DEFAULT_OUT) -> Path:
    """生成模板并返回路径。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(16)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)

    _paragraph(document, NOTE, font=BODY_FONT, size=9, align="center", gray=True, space_after=12)
    _paragraph(document, TITLE, font=TITLE_FONT, size=22, bold=True, align="center", space_after=10)
    _paragraph(document, "{{案号}}", font=BODY_FONT, size=14, align="center", space_after=16)

    _paragraph(document, "{{羁押场所}}：", font=BODY_FONT, size=16)
    _paragraph(
        document,
        "罪犯{{被告人}}（{{性别}}，{{出生日期}}出生，{{民族}}族，"
        "公民身份号码{{身份证号}}，住{{住址}}）因犯{{罪名}}罪，"
        "经本院于{{判决日期}}作出判决，判处{{主刑}}{{刑期}}。"
        "现将执行有关事项通知如下：",
        size=16,
        indent_chars=2,
        space_after=10,
    )

    table = document.add_table(rows=len(TABLE_ROWS), cols=2)
    table.style = "Table Grid"
    for row_index, (label, value) in enumerate(TABLE_ROWS):
        cells = table.rows[row_index].cells
        for cell, text, bold in ((cells[0], label, True), (cells[1], value, False)):
            cell.text = ""
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.line_spacing = 1.5
            _style_run(paragraph.add_run(text), font=BODY_FONT, size=14, bold=bold)

    _paragraph(document, "", size=10, space_after=6)
    _paragraph(
        document,
        "请依照《中华人民共和国刑事诉讼法》第二百六十四条的规定，"
        "将罪犯{{被告人}}交付执行。执行中如遇问题，请及时与本院联系。",
        size=16,
        indent_chars=2,
        space_after=20,
    )

    _paragraph(document, "{{法院名称}}", font=BODY_FONT, size=16, align="right", space_after=4)
    _paragraph(document, "{{判决日期}}", font=BODY_FONT, size=16, align="right", space_after=16)
    _paragraph(document, "书记员　{{书记员}}", font=BODY_FONT, size=16, align="right")

    document.save(str(target))
    return target


def placeholders(path: Path | str) -> set[str]:
    """读取模板内占位符集合（不依赖 docxtpl）。"""
    import re

    from docx import Document

    pattern = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
    found: set[str] = set()
    document = Document(str(path))
    texts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.extend(paragraph.text for paragraph in cell.paragraphs)
    for text in texts:
        found.update(match.group(1) for match in pattern.finditer(text))
    return found


def check(path: Path | str = DEFAULT_OUT, mapping: Path | str = MAPPING) -> list[str]:
    """比对模板占位符与映射表字段，返回问题清单（空表示一致）。"""
    declared = json.loads(Path(mapping).read_text(encoding="utf-8"))["fields"]
    in_template = placeholders(path)

    problems: list[str] = []
    missing = sorted(set(declared) - in_template)
    extra = sorted(in_template - set(declared))
    if missing:
        problems.append(f"映射表声明但模板未使用（生成后为空白）：{'、'.join(missing)}")
    if extra:
        problems.append(f"模板使用但映射表未声明（渲染会报未解析）：{'、'.join(extra)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成合成执行通知书模板")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出路径")
    parser.add_argument("--check", action="store_true", help="只校验占位符与映射表")
    args = parser.parse_args(argv)

    if args.check:
        problems = check(args.out)
        if problems:
            for problem in problems:
                print(f"[不一致] {problem}")
            return 1
        print(f"占位符一致：{len(placeholders(args.out))} 个")
        return 0

    target = build(args.out)
    problems = check(target)
    print(f"已生成：{target}")
    print(f"占位符：{len(placeholders(target))} 个")
    for problem in problems:
        print(f"[不一致] {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
