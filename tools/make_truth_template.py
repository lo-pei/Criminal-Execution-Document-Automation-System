"""生成真值填写表（xlsx）。

真值表是回归测试的度量基准，因此**由脚本生成而非手工维护**：
字段增删时改这里重新生成，避免四张表与字段字典脱节。

用法：
    python tools/make_truth_template.py
    python tools/make_truth_template.py -o data/truth/00-真值填写表.xlsx

字段定义必须与 docs/00-字段字典-spec-v0.1.md 保持一致。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import openpyxl
except ImportError:  # pragma: no cover - 依赖缺失路径
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl>=3.1.0"])
    import openpyxl

from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


def xl_color(css_hex: str) -> str:
    value = css_hex.removeprefix("#").upper()
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB, got: {css_hex}")
    return "FF" + value


XL_HEADER_BG = xl_color("#4472C4")
XL_HEADER_FG = xl_color("#FFFFFF")
XL_KEY_BG = xl_color("#F2F2F2")
XL_INPUT_BG = xl_color("#F4F8FD")
XL_BORDER = xl_color("#BFBFBF")
XL_TITLE = xl_color("#1F3864")
XL_MUTED = xl_color("#595959")
XL_WARN = xl_color("#9C0006")

THIN = Side(style="thin", color=XL_BORDER)

DEFAULT_ROWS = 40
DEFAULT_SAMPLES = 20


@dataclass(frozen=True)
class Field:
    """列定义。kind 决定底纹、格式与是否预填。"""

    title: str
    kind: str  # key | text | date | enum | number
    width: float
    rule: str
    required: bool = False
    options: tuple[str, ...] = ()


KEY_FIELDS = (
    Field("序号", "key", 6, "系统预填，勿改动。用于跨表对齐。"),
    Field("样本编号", "key", 10, "系统预填，须与样本文件名前缀一致（S001…）。"),
    Field("被告人序号", "key", 11, "系统预填，1 表示该案第一名被告人。"),
)

NAME_FIELD = Field(
    "被告人姓名",
    "text",
    13,
    "照抄判决书首部，不含「被告人」三字。四张表中同一行的姓名必须一致。",
    True,
)

A_FIELDS = [
    *KEY_FIELDS,
    NAME_FIELD,
    Field("性别", "enum", 8, "男 / 女。", True, ("男", "女")),
    Field("出生日期", "date", 13, "格式 2023-05-01。判决书只写「现年45岁」的，留空并在 D 表说明，禁止反推。", True),
    Field("公民身份号码", "text", 22, "18 位，末位 X 大写。无则留空。", True),
    Field("民族", "text", 10, "如「汉族」，只填族名不含「族」以外的内容。"),
    Field("住址", "text", 40, "照抄，不缩写。判决书同时有户籍地与现住址的，填现住址，户籍地写备注。"),
    Field("文化程度", "text", 12, "如「初中文化」填「初中」。"),
    Field("是否在押", "enum", 10, "判决时是否处于羁押状态。取保候审、监视居住均填「否」。", True, ("是", "否")),
    Field("拘留日期", "date", 14, "刑事拘留起始日。多次拘留分不清的留空并在备注说明。"),
    Field("逮捕日期", "date", 13, "逮捕执行日。"),
    Field("取保/监居日期", "date", 16, "取保候审或监视居住起始日，两者都有时填较晚者。"),
    Field("羁押场所", "text", 20, "如「北京市朝阳区看守所」。在押案件必填。"),
    Field("前科情况", "text", 20, "照抄「曾因…被判…」的关键表述；无前科填「无」。"),
]

B_FIELDS = [
    *KEY_FIELDS,
    NAME_FIELD,
    Field("罪名1", "text", 16, "法定罪名，如「盗窃罪」。单罪案件只填本列。", True),
    Field("主刑1", "enum", 12, "管制 / 拘役 / 有期徒刑 / 无期徒刑 / 死刑。", True, ("管制", "拘役", "有期徒刑", "无期徒刑", "死刑")),
    Field("刑期1", "text", 15, "**原样照抄**，如「三年六个月」。不要换算成月数，系统负责换算。"),
    Field("附加刑1", "text", 22, "如「罚金人民币五千元」。无附加刑留空。"),
    Field("罪名2", "text", 16, "仅数罪并罚或一人多罪时填。"),
    Field("主刑2", "enum", 12, "同主刑1。", False, ("管制", "拘役", "有期徒刑", "无期徒刑", "死刑")),
    Field("刑期2", "text", 15, "原样照抄。"),
    Field("附加刑2", "text", 22, "同附加刑1。"),
    Field("罪名3", "text", 16, "超过 3 个罪名的，在第 3 列合并填写并在备注说明。"),
    Field("主刑3", "enum", 12, "同主刑1。", False, ("管制", "拘役", "有期徒刑", "无期徒刑", "死刑")),
    Field("刑期3", "text", 15, "原样照抄。"),
    Field("附加刑3", "text", 22, "同附加刑1。"),
    Field("数罪并罚", "enum", 12, "一人多罪且判决书写「数罪并罚」时填「是」。", True, ("是", "否")),
    Field("决定执行主刑", "enum", 14, "数罪并罚时为「决定执行」的主刑；单罪时与主刑1相同。", True, ("管制", "拘役", "有期徒刑", "无期徒刑", "死刑")),
    Field("决定执行刑期", "text", 16, "数罪并罚时照抄「决定执行有期徒刑X年」；单罪时与刑期1相同。**不要自行相加**。", True),
    Field("是否缓刑", "enum", 10, "判决主文出现「缓刑」填「是」。", True, ("是", "否")),
    Field("缓刑考验期", "text", 15, "如「四年」。仅缓刑案件填写。"),
    Field("罚金（元）", "number", 14, "只填数字。「人民币五万元」填 50000，不带单位与千分位。"),
    Field("没收财产", "text", 16, "如「没收个人全部财产」「没收财产人民币十万元」。"),
    Field("剥夺政治权利期限", "text", 18, "如「二年」「自刑满释放之日起三年」。"),
    Field("追缴退赔", "text", 22, "照抄追缴、责令退赔的表述要点。"),
    Field("附带民事赔偿（元）", "number", 18, "只填数字。无附带民事诉讼留空。"),
]

C_FIELDS = [
    *KEY_FIELDS,
    NAME_FIELD,
    Field("载明刑期起日", "date", 18, "判决书正文「刑期自X年X月X日起」的起日。**判决书未载明则留空**，不要推算。"),
    Field("载明刑期止日", "date", 18, "判决书正文「至X年X月X日止」的止日。未载明则留空。"),
    Field("羁押起始日", "date", 20, "按 A 表拘留/逮捕日期人工核定后的最早羁押日。未羁押留空。"),
    Field("备注", "text", 30, "文本起止日与羁押日明显矛盾的，在此写明。"),
]

D_FIELDS = [
    *KEY_FIELDS,
    NAME_FIELD,
    Field("判决日期", "date", 13, "落款日期。", True),
    Field("送达日期", "date", 13, "判决书送达被告人的日期。判决书本身不载明，由承办人提供。"),
    Field("生效日期", "date", 13, "一审为送达日 +10 日（无上诉）；二审为宣判日。**必填，否则无法生成执行通知书**。", True),
    Field("立案日期", "date", 13, "受理案件日期。判决书未载明则留空。"),
    Field("审判组织", "text", 20, "如「审判长李四、审判员王五、人民陪审员赵六」或「独任审判员李四」。"),
    Field("书记员", "text", 10, "落款书记员姓名。"),
    Field("公诉机关", "text", 22, "如「北京市朝阳区人民检察院」。"),
    Field("送达机关", "text", 16, "执行文书送达单位，如某监狱、某看守所、某司法所。"),
    Field("原文片段·案号行", "text", 40, "**必填**。复制含案号的那一整行原文。规则回归的第一锚点。", True),
    Field("原文片段·被告人行", "text", 40, "**必填**。复制该被告人首行原文（含姓名、性别、出生日期）。", True),
    Field("原文片段·判决主文", "text", 45, "**必填**。复制该被告人对应的判决主文那一项（含罪名与刑期）。", True),
    Field("缺失字段与原因", "text", 32, "判决书未载明的字段逐项写明，如「出生日期：仅载明年龄」。"),
    Field("备注", "text", 28, "其他需要说明的情况。"),
]

SHEETS: tuple[tuple[str, list[Field], str], ...] = (
    ("A-主体", A_FIELDS, "身份信息与强制措施"),
    ("B-量刑", B_FIELDS, "罪名、刑期与附加刑"),
    ("C-刑期", C_FIELDS, "刑期起止日与羁押起始日"),
    ("D-程序", D_FIELDS, "程序性字段与原文片段"),
)

ENUMS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("性别", ("男", "女"), "A 表"),
    ("是否在押", ("是", "否"), "A 表"),
    ("主刑种类", ("管制", "拘役", "有期徒刑", "无期徒刑", "死刑"), "B 表"),
    ("是否数罪并罚", ("是", "否"), "B 表"),
    ("是否缓刑", ("是", "否"), "B 表"),
)

INSTRUCTIONS: tuple[tuple[str, str], ...] = (
    ("title", "CEDAS 真值填写表"),
    ("sub", "刑事执行文书自动制作系统 · 样本标注"),
    ("gap", ""),
    ("h", "一、这张表是做什么的"),
    (
        "p",
        "这张表记录每份判决书里「正确的值应该是什么」。系统抽出来的结果要和这里的值逐字段比对，"
        "算出命中率；没有这张表，「命中率 98%」就无法度量，也无法判断某次规则改动是变好还是变差。",
    ),
    (
        "p",
        "一行代表「一名被告人在一份判决书中」。共同犯罪有三名被告人，就填三行；同一份判决书的其它字段不重复填。",
    ),
    ("gap", ""),
    ("h", "二、怎么填"),
    ("n", "1. 把样本 TXT 放到 data/samples/，命名 S001__案号.txt；编号要与本表「样本编号」一致。"),
    ("n", "2. 先填 A-主体，再填 B-量刑、C-刑期、D-程序。四张表同一行的「序号」必须相同。"),
    ("n", "3. 只填有内容的部分。判决书没写的字段就留空，不要猜、不要推算。"),
    ("n", "4. 留空的字段在 D 表「缺失字段与原因」里写一句原因，例如「出生日期：仅载明年龄」。"),
    ("n", "5. D 表的三处「原文片段」必须逐字复制，它们既是回归锚点，也是校验规则有没有抽错人的依据。"),
    ("gap", ""),
    ("h", "三、六条硬性规则"),
    ("n", "① 照抄不换算：刑期写「三年六个月」，不要写 42；金额写 50000，不要写「五万元」。"),
    ("n", "② 日期统一 2023-05-01 格式，「二〇二三年五月一日」要转成 2023-05-01。"),
    ("n", "③ 一人一行：被告人不得合并到同一行，也不得把两个罪拆成两行。"),
    ("n", "④ 决定执行刑期照抄判决主文，不要自己把各罪刑期相加。"),
    ("n", "⑤ 出生日期禁止按年龄反推——反推值会污染刑期计算。"),
    ("n", "⑥ 未羁押案件（取保候审、监视居住）的 C 表起止日留空，判决书里本来就没有。"),
    ("gap", ""),
    ("h", "四、四张表分别对应什么"),
    ("n", "A-主体：姓名、性别、出生日期、身份证号、住址、强制措施（拘留、逮捕、取保）。"),
    ("n", "B-量刑：罪名（最多 3 个）、主刑、刑期、附加刑、是否数罪并罚、是否缓刑。"),
    ("n", "C-刑期：判决书载明的刑期起止日、人工核定的羁押起始日。"),
    ("n", "D-程序：判决日期、生效日期、审判组织、公诉机关、送达机关、三处原文片段。"),
    ("gap", ""),
    ("h", "五、最容易填错的地方"),
    ("warn", "把「决定执行刑期」填成各罪刑期之和——数罪并罚的样本会因此失去校验价值。"),
    ("warn", "出生日期按「现年 45 岁」反推——直接污染 C 组计算。"),
    ("warn", "跨表行序错位——A 表第 5 行与 B 表第 5 行不是同一人，全部校验失效。"),
    ("warn", "原文片段截得太短（只写「判决如下」）——无法定位，回归失败。"),
    ("gap", ""),
    ("p", "填完以后运行：python tools/sample_lint.py data/samples 与 python -m pytest -q，两者都通过才算有效样本。"),
)


def _write_instruction_sheet(workbook) -> None:
    sheet = workbook.active
    sheet.title = "填写说明"
    sheet.sheet_view.showGridLines = False

    for column in range(1, 9):
        sheet.column_dimensions[get_column_letter(column)].width = 14

    row = 1
    for kind, text in INSTRUCTIONS:
        if kind == "title":
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
            cell = sheet.cell(row=row, column=1, value=text)
            cell.font = Font(size=18, bold=True, color=XL_TITLE)
            cell.alignment = Alignment(vertical="center")
            sheet.row_dimensions[row].height = 32
        elif kind == "sub":
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
            cell = sheet.cell(row=row, column=1, value=text)
            cell.font = Font(size=11, color=XL_MUTED)
        elif kind == "gap":
            sheet.row_dimensions[row].height = 8
        elif kind == "h":
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
            cell = sheet.cell(row=row, column=1, value=text)
            cell.font = Font(size=12, bold=True, color=XL_HEADER_BG)
            cell.alignment = Alignment(vertical="center")
            sheet.row_dimensions[row].height = 24
        else:
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
            cell = sheet.cell(row=row, column=1, value=text)
            cell.font = Font(size=11, color=XL_WARN if kind == "warn" else xl_color("#262626"))
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            lines = max(1, -(-len(text) // 48))
            sheet.row_dimensions[row].height = 17 * lines
        row += 1


def _write_data_sheet(
    workbook,
    name: str,
    fields: list[Field],
    *,
    rows: int,
    samples: int,
) -> None:
    sheet = workbook.create_sheet(name)
    last_column = len(fields)
    last_letter = get_column_letter(last_column)
    first_data_row = 2
    last_data_row = first_data_row + rows - 1

    # 表头（第 1 行）
    for index, field in enumerate(fields, start=1):
        cell = sheet.cell(row=1, column=index, value=field.title)
        cell.font = Font(bold=True, color=XL_HEADER_FG)
        cell.fill = PatternFill("solid", fgColor=XL_HEADER_BG)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN, left=THIN, right=THIN, top=THIN)
        requirement = "必填" if field.required else "选填"
        cell.comment = Comment(f"{field.rule}\n\n[{requirement}]", "CEDAS", width=320, height=140)
        sheet.column_dimensions[get_column_letter(index)].width = field.width

    sheet.row_dimensions[1].height = 34

    # 数据区（预留行，预填三列键）
    for offset in range(rows):
        row = first_data_row + offset
        prefilled = {
            "序号": offset + 1,
            "样本编号": f"S{offset // 2 + 1:03d}" if offset // 2 < samples else "",
            "被告人序号": offset % 2 + 1,
        }
        for index, field in enumerate(fields, start=1):
            cell = sheet.cell(row=row, column=index)
            if field.kind == "key":
                cell.value = prefilled.get(field.title)
                cell.fill = PatternFill("solid", fgColor=XL_KEY_BG)
                cell.font = Font(color=XL_MUTED)
                cell.alignment = Alignment(horizontal="center")
            else:
                cell.fill = PatternFill("solid", fgColor=XL_INPUT_BG)
                cell.alignment = Alignment(
                    vertical="top",
                    wrap_text=field.kind == "text" and field.width >= 28,
                    horizontal="right" if field.kind == "number" else "left",
                )
                if field.kind == "date":
                    cell.number_format = "yyyy-mm-dd"
                elif field.kind == "number":
                    cell.number_format = "#,##0"
            cell.border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

        sheet.row_dimensions[row].height = 20

    # 下拉验证
    validations: dict[tuple[str, ...], DataValidation] = {}
    for index, field in enumerate(fields, start=1):
        if field.kind != "enum" or not field.options:
            continue
        letter = get_column_letter(index)
        validation = validations.get(field.options)
        if validation is None:
            validation = DataValidation(
                type="list",
                formula1='"' + ",".join(field.options) + '"',
                allow_blank=True,
                showDropDown=False,
            )
            validation.error = "请从下拉列表中选择"
            validation.errorTitle = "取值不合法"
            sheet.add_data_validation(validation)
            validations[field.options] = validation
        validation.add(f"{letter}{first_data_row}:{letter}{last_data_row}")

    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = f"A1:{last_letter}{last_data_row}"
    sheet.sheet_properties.tabColor = "4472C4"


def _write_field_sheet(workbook) -> None:
    sheet = workbook.create_sheet("字段说明")
    headers = ("所属表", "字段名", "类型", "必填", "填写规则")
    widths = (12, 22, 10, 8, 90)
    type_labels = {
        "key": "预填",
        "text": "文本",
        "date": "日期",
        "enum": "枚举",
        "number": "数字",
    }

    for index, (title, width) in enumerate(zip(headers, widths, strict=True), start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.font = Font(bold=True, color=XL_HEADER_FG)
        cell.fill = PatternFill("solid", fgColor=XL_HEADER_BG)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=THIN, left=THIN, right=THIN, top=THIN)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 28

    row = 2
    for name, fields, _description in SHEETS:
        for field in fields:
            sheet.cell(row=row, column=1, value=name)
            sheet.cell(row=row, column=2, value=field.title)
            sheet.cell(row=row, column=3, value=type_labels.get(field.kind, field.kind))
            sheet.cell(row=row, column=4, value="是" if field.required else "")
            rule_cell = sheet.cell(row=row, column=5, value=field.rule)
            rule_cell.alignment = Alignment(wrap_text=True, vertical="top")
            sheet.cell(row=row, column=4).alignment = Alignment(horizontal="center")
            for column in range(1, 6):
                sheet.cell(row=row, column=column).border = Border(
                    left=THIN, right=THIN, top=THIN, bottom=THIN
                )
            sheet.row_dimensions[row].height = 30
            row += 1

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:E{row - 1}"
    sheet.sheet_properties.tabColor = "808080"


def _write_enum_sheet(workbook) -> None:
    sheet = workbook.create_sheet("枚举值")
    headers = ("枚举", "可选值", "用于")
    widths = (18, 44, 12)

    for index, (title, width) in enumerate(zip(headers, widths, strict=True), start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.font = Font(bold=True, color=XL_HEADER_FG)
        cell.fill = PatternFill("solid", fgColor=XL_HEADER_BG)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=THIN, left=THIN, right=THIN, top=THIN)
        sheet.column_dimensions[get_column_letter(index)].width = width

    for offset, (name, options, where) in enumerate(ENUMS, start=2):
        sheet.cell(row=offset, column=1, value=name)
        sheet.cell(row=offset, column=2, value="、".join(options))
        sheet.cell(row=offset, column=3, value=where)
        for column in range(1, 4):
            sheet.cell(row=offset, column=column).border = Border(
                left=THIN, right=THIN, top=THIN, bottom=THIN
            )
    sheet.sheet_properties.tabColor = "A6A6A6"


def build(output: Path, *, rows: int = DEFAULT_ROWS, samples: int = DEFAULT_SAMPLES) -> Path:
    workbook = openpyxl.Workbook()
    workbook.properties.title = "CEDAS 真值填写表"
    workbook.properties.creator = "CEDAS"

    _write_instruction_sheet(workbook)
    for name, fields, _description in SHEETS:
        _write_data_sheet(workbook, name, fields, rows=rows, samples=samples)
    _write_field_sheet(workbook)
    _write_enum_sheet(workbook)

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成真值填写表")
    parser.add_argument(
        "-o",
        "--output",
        default="data/truth/00-真值填写表.xlsx",
        help="输出路径",
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="预留填写行数")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="样本份数（每份 2 行）")
    args = parser.parse_args(argv)

    output = build(Path(args.output), rows=args.rows, samples=args.samples)
    total = sum(len(fields) for _name, fields, _d in SHEETS)
    print(f"已生成 {output}")
    print("工作表：填写说明、A-主体、B-量刑、C-刑期、D-程序、字段说明、枚举值")
    print(f"字段定义 {total} 条，预留 {args.rows} 行（覆盖 {args.samples} 份样本 × 2 名被告人）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
