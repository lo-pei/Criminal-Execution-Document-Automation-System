"""复核界面要显示的东西，以及显示成什么字。

这一层**不 import tkinter**，因此可以单独测试；``main_window`` 只负责摆放。

三条规矩，都来自「用户是法院书记员，不是工程师」：

1. **屏幕上一个技术词都不能有。** 不写「阻断」「队列」「A 组」「E-ID」，
   写「待补充」「待办」「案件信息」「缺公民身份号码」。
   错误码属于日志，不属于界面。
2. **一个对象最多一处红。** 一行里只有左侧一个状态标签，字段行只用输入框
   的样子表达「这里要你填」，不再额外刷红底。
3. **必填与选填要分开。** 必填缺失显示「未录入」，选填为空显示「—」——
   否则每个空单元格看起来都像在催人填。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Optional, Sequence

from ..domain.enums import CombinationKind, Consistency
from ..domain.models import Crime, Defendant, ProseParagraph
from ..pipeline import DocumentItem, PipelineReport, PreparedDocument
from ..render.formatting import format_cn_date, months_to_chinese
from ..storage.naming import NamePlan

# 阻断码 → 人话。键是码前缀（``blockers`` 的格式为 ``"E-ID 公民身份号码缺失"``）。
# 码是稳定的，界面按码查这句话，不解析后面的中文。
BLOCKER_HINTS = {
    "E-CASE-NO": "缺案号",
    "E-COURT": "缺法院名称",
    "E-NAME": "缺被告人姓名",
    "E-GENDER": "缺性别",
    "E-BIRTH": "缺出生日期",
    "E-ID": "缺公民身份号码",
    "E-CHARGES": "缺罪名",
    "E-PRINCIPAL": "没认出主刑",
    "E-TERM": "没认出刑期长短",
    "E-START": "还缺起算日（判决书没写，也没填生效日期）",
    "E-START-COMBINED": "并罚案件要人工填起算日",
    "E-END": "算不出届满日",
    "E-LIFE": "无期徒刑或死刑，没有刑期起止",
    "E-FIELD": "有必填项没填",
}

STATUS_BLOCKED = "待补充"
STATUS_READY = "可生成"
STATUS_DONE = "已生成"

GROUP_CASE = "案件信息"
GROUP_PERSON = "罪犯信息"
GROUP_TERM = "量刑与刑期"
GROUP_PROCEDURE = "程序"


def blocker_code(text: str) -> str:
    """从 ``"E-ID 公民身份号码缺失"`` 取出 ``E-ID``。"""
    return text.split(" ", 1)[0].strip()


def blocker_hint(text: str) -> str:
    """阻断原因转成界面用语。认不出的码回落为「有必填项没填」，不把码漏到界面上。"""
    return BLOCKER_HINTS.get(blocker_code(text), BLOCKER_HINTS["E-FIELD"])


@dataclass(frozen=True)
class FieldRow:
    """一行字段。界面上就是「名称 | 值 | 依据」。"""

    group: str
    label: str
    value: str
    path: str
    required: bool = False
    editable: bool = False
    placeholder: str = ""
    provenance: str = ""
    paragraph_index: Optional[int] = None
    note: str = ""
    empty_text: str = ""

    @property
    def missing_required(self) -> bool:
        return self.required and not self.value


@dataclass(frozen=True)
class QueueRow:
    """左侧待办里的一行（一名被告人一份文书）。"""

    seq: int
    title: str
    subtitle: str
    status: str
    detail: str
    blocked: bool


@dataclass(frozen=True)
class Summary:
    """标题下面那一句人话结论，以及主按钮该不该可按。"""

    headline: str
    detail: str
    can_generate: bool
    action: str


# ---------------------------------------------------------------------------
# 字段清单
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spec:
    group: str
    label: str
    path: str
    required: bool = False
    editable: bool = False
    kind: str = "text"  # text / date
    placeholder: str = ""
    empty_text: str = ""  # 非必填但空着值得提醒时用


SPECS: tuple[_Spec, ...] = (
    _Spec(GROUP_CASE, "案号", "case.case_no", required=True, editable=True),
    _Spec(GROUP_CASE, "法院名称", "case.court_name", required=True, editable=True),
    _Spec(GROUP_CASE, "公诉机关", "case.prosecutor_organ"),
    _Spec(GROUP_CASE, "案由", "case.cause"),
    _Spec(GROUP_PERSON, "姓名", "defendant.name", required=True, editable=True),
    _Spec(GROUP_PERSON, "性别", "defendant.gender", required=True, editable=True),
    _Spec(
        GROUP_PERSON,
        "出生日期",
        "defendant.birth_date",
        required=True,
        editable=True,
        kind="date",
        placeholder="如 1985-03-02",
    ),
    _Spec(GROUP_PERSON, "公民身份号码", "defendant.id_number", required=True, editable=True),
    _Spec(GROUP_PERSON, "民族", "defendant.ethnicity", editable=True),
    _Spec(GROUP_PERSON, "文化程度", "defendant.education", editable=True),
    _Spec(GROUP_PERSON, "住址", "defendant.domicile", editable=True),
    _Spec(GROUP_PERSON, "羁押场所", "defendant.detention_place", editable=True),
    _Spec(GROUP_TERM, "罪名", "defendant.crimes", required=True),
    _Spec(GROUP_TERM, "主刑", "defendant.merged_type"),
    _Spec(GROUP_TERM, "刑期", "defendant.term_text"),
    # 并罚形态只在真并罚时出现（未并罚显示「—」）。复核的人必须看到它：
    # 先并后减与先减后并的起算日口径不同，看错一种就会把日期算错。
    _Spec(GROUP_TERM, "并罚方式", "defendant.combination"),
    # 「判决书载明」不放进 SPECS：绝大多数判决书不写起止日（实测 53%），
    # 固定占一行会让每个案件都多一条「—」。有值时才插在「起算日」上面一行。
    _Spec(GROUP_TERM, "起算日", "defendant.span.start", editable=True, kind="date",
          placeholder="如 2023-03-01"),
    _Spec(GROUP_TERM, "届满日", "defendant.span.end"),
    _Spec(GROUP_TERM, "起算依据", "defendant.start_source"),
    _Spec(GROUP_PROCEDURE, "判决日期", "case.judgment_date", kind="date", editable=True,
          placeholder="如 2023-05-10"),
    _Spec(GROUP_PROCEDURE, "生效日期", "case.effective_date", editable=True, kind="date",
          placeholder="如 2023-05-21", empty_text="未录入 · 不填则文书中该处留空"),
    _Spec(GROUP_PROCEDURE, "审判组织", "case.panel", editable=True),
    _Spec(GROUP_PROCEDURE, "书记员", "case.clerk", editable=True),
)

GROUP_ORDER = (GROUP_CASE, GROUP_PERSON, GROUP_TERM, GROUP_PROCEDURE)


def _text(value: Any) -> str:
    """任意字段值转显示文本。枚举取 ``.value``，日期按中文习惯写。"""
    if value is None:
        return ""
    if isinstance(value, date):
        return format_cn_date(value)
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _charges(crimes: Sequence[Crime]) -> str:
    names = [str(crime.charge.value) for crime in crimes if crime.charge.is_present]
    return "、".join(names)


def _term(defendant: Defendant) -> str:
    months = defendant.merged_months.value if isinstance(defendant.merged_months.value, int) else None
    return months_to_chinese(months, defendant.merged_days)


def _raw(document: PreparedDocument, defendant: Defendant, path: str) -> Any:
    """按 ``path`` 取原值。返回 ``FieldValue`` 或计算值，交给 :func:`_text` 收尾。"""
    if path == "defendant.crimes":
        return _charges(defendant.crimes)
    if path == "defendant.term_text":
        return _term(defendant)
    if path == "defendant.combination":
        # 「未并罚」不当值显示：绝大多数案件都没并罚，一行恒有值的信息是噪音。
        return "" if defendant.combination is CombinationKind.NONE else defendant.combination
    if path == "defendant.text_span":
        start, end = defendant.declared_span
        if start is not None and end is not None:
            return f"{format_cn_date(start)} 至 {format_cn_date(end)}"
        # 只抽到半截就照实显示，不补齐：补哪一头都是编。
        return format_cn_date(start or end) if (start or end) else ""
    if path.startswith("defendant.span."):
        return getattr(defendant.span, path.rsplit(".", 1)[1], None)

    parts = path.split(".")
    target: Any = document.case if parts[0] == "case" else defendant
    value = getattr(target, parts[1], None)
    return getattr(value, "value", value)


def _provenance(document: PreparedDocument, defendant: Defendant, path: str) -> tuple[str, Optional[int]]:
    """取「依据」：字段来自原文第几段。计算值没有段落，返回空。"""
    if path.startswith("defendant.span.") or path in ("defendant.term_text", "defendant.combination"):
        return "", None
    if path == "defendant.text_span":
        paragraph = _text_span_provenance(defendant)
        if paragraph is None:
            return "", None
        # 载明值只作校验，不一致时这一行才是复核最该看的一行，把结论写在「依据」里
        # ——界面上一行只有一处可放宽的文字，值列必须留给原文日期。
        suffix = " · 与算法不一致" if defendant.span.consistency is Consistency.MISMATCH else ""
        return f"原文第 {paragraph + 1} 段{suffix}", paragraph
    if path == "defendant.crimes":
        for crime in defendant.crimes:
            provenance = crime.charge.provenance
            if provenance is not None:
                return f"原文第 {provenance.paragraph_index + 1} 段", provenance.paragraph_index
        return "", None
    parts = path.split(".")
    target: Any = document.case if parts[0] == "case" else defendant
    field_value = getattr(target, parts[1], None)
    provenance = getattr(field_value, "provenance", None)
    if provenance is None:
        return "", None
    if provenance.page is None:
        return f"原文第 {provenance.paragraph_index + 1} 段", provenance.paragraph_index
    return (
        f"原文第 {provenance.page} 页·第 {provenance.paragraph_index + 1} 段",
        provenance.paragraph_index,
    )


def _text_span_provenance(defendant: Defendant) -> Optional[int]:
    for field_value in (defendant.text_start, defendant.text_end):
        provenance = field_value.provenance
        if provenance is not None:
            return provenance.paragraph_index
    return None


def build_fields(
    document: PreparedDocument,
    defendant: Defendant,
    *,
    start_source: str = "",
) -> list[FieldRow]:
    """一名被告人的全部字段行，按「案件信息 → 罪犯信息 → 量刑 → 程序」排列。"""
    rows: list[FieldRow] = []
    for spec in SPECS:
        if spec.path == "defendant.span.start":
            declared = _declared_row(document, defendant)
            if declared is not None:
                rows.append(declared)
        display = _text(_raw(document, defendant, spec.path))
        provenance, index = _provenance(document, defendant, spec.path)
        note = ""
        if spec.path == "defendant.start_source" and start_source:
            # 界面上直接写人话（「按 3 月 1 日拘留起算」），不写「羁押起始日」这种表头话。
            display = _start_note(defendant, start_source) or start_source
        rows.append(
            FieldRow(
                group=spec.group,
                label=spec.label,
                value=display,
                path=spec.path,
                required=spec.required,
                editable=spec.editable,
                placeholder=spec.placeholder,
                provenance=provenance,
                paragraph_index=index,
                note=note,
                empty_text=spec.empty_text,
            )
        )
    return rows


def _declared_row(document: PreparedDocument, defendant: Defendant) -> Optional[FieldRow]:
    """「判决书载明」那一行。没载明就整行不出现。"""
    value = _raw(document, defendant, "defendant.text_span")
    if not value:
        return None
    provenance, index = _provenance(document, defendant, "defendant.text_span")
    note = (
        "与算出来的日期不一致，请核对原文"
        if defendant.span.consistency is Consistency.MISMATCH
        else ""
    )
    return FieldRow(
        group=GROUP_TERM,
        label="判决书载明",
        value=_text(value),
        path="defendant.text_span",
        provenance=provenance,
        paragraph_index=index,
        note=note,
    )


def display_value(row: FieldRow) -> str:
    """单元格里该显示什么。空值有三种，不能混为一谈。"""
    if row.value:
        return row.value
    if row.required:
        return "未录入"
    return row.empty_text or "—"


def _start_note(defendant: Defendant, start_source: str) -> str:
    """把起算日来源写成人话，例如「按 3 月 1 日拘留起算」。"""
    custody = defendant.custody_start
    if start_source == "羁押起始日" and custody is not None:
        return f"按 {custody.month} 月 {custody.day} 日拘留起算"
    if start_source == "生效日期":
        return "按判决生效日起算"
    if start_source == "人工指定":
        return "人工指定"
    return ""


def grouped(rows: Sequence[FieldRow]) -> list[tuple[str, list[FieldRow]]]:
    """按固定顺序分组，空组不返回。"""
    buckets: dict[str, list[FieldRow]] = {name: [] for name in GROUP_ORDER}
    for row in rows:
        buckets.setdefault(row.group, []).append(row)
    return [(name, items) for name, items in buckets.items() if items]


# ---------------------------------------------------------------------------
# 待办队列与结论
# ---------------------------------------------------------------------------


def _item_subtitle(item: DocumentItem) -> str:
    if item.start is None or item.end is None:
        return "刑期起止日期还没算出来"
    return f"{format_cn_date(item.start)} 至 {format_cn_date(item.end)}"


def build_queue(report: PipelineReport) -> list[QueueRow]:
    """待办列表。**待补充的排在前面**——漏看一份阻断的，代价最高。"""
    rows: list[QueueRow] = []
    for item in report.items:
        if not item.ok:
            status = STATUS_BLOCKED
            hints = "、".join(blocker_hint(text) for text in item.blockers)
            detail = f"还差 {len(item.blockers)} 项：{hints}"
        elif item.rendered:
            status = STATUS_DONE
            detail = f"已存到 {item.path.parent.name if item.path else ''} 文件夹"
        else:
            status = STATUS_READY
            detail = "核对无误就可以生成"
        rows.append(
            QueueRow(
                seq=item.seq,
                title=item.defendant,
                subtitle=_item_subtitle(item),
                status=status,
                detail=detail,
                blocked=not item.ok,
            )
        )
    rows.sort(key=lambda row: (0 if row.blocked else 1, row.seq))
    return rows


def _headline(report: PipelineReport) -> tuple[str, str]:
    items = report.items
    if not items:
        return (
            "这份判决书里没有认出被告人。",
            "请确认选的是判决书正文，或换一份再试。",
        )

    blocked = report.blocked
    if blocked:
        first = blocked[0]
        hints = "、".join(blocker_hint(text) for text in first.blockers)
        if len(items) == 1 and len(first.blockers) == 1:
            only = hints[1:] if hints.startswith("缺") else hints
            return (
                f"{first.defendant}这一份只差{only}。",
                "补上这一项就能生成，不必重新导入判决书。",
            )
        if len(items) == 1:
            return (
                f"{first.defendant}这一份还差 {len(first.blockers)} 项：{hints}。",
                "补齐下面的项就能生成，不必重新导入判决书。",
            )
        return (
            f"{len(blocked)} 名被告人的信息还不完整，{first.defendant}缺{hints}。",
            "左侧标「待补充」的都要先补齐，补齐后才能生成。",
        )

    if all(item.rendered for item in items):
        return (
            f"{len(items)} 份执行通知书已经生成。",
            "文件已按案号和被告人归档，可点「打开所在文件夹」查看。",
        )
    if len(items) == 1:
        return (
            "各项都齐了，可以生成执行通知书。",
            "生成前请核对下面的刑期起止日期，这是唯一由系统算出来的值。",
        )
    return (
        f"{len(items)} 名被告人的执行通知书都可以生成。",
        "生成前请核对每名被告人的刑期起止日期。",
    )


def summarize(report: PipelineReport, *, missing_template: bool = False) -> Summary:
    """标题下那一句结论 + 主按钮状态。"""
    headline, detail = _headline(report)

    if not report.items:
        return Summary(headline, detail, False, "生成执行通知书")
    if report.blocked:
        return Summary(headline, detail, False, "补齐后再生成")
    if missing_template:
        return Summary(
            "还差文书模板。",
            "请把法院的执行通知书 .docx 放进程序目录下的 resources/templates 文件夹，再重新打开。",
            False,
            "生成执行通知书",
        )
    return Summary(headline, detail, True, "生成执行通知书")


def missing_template(report: PipelineReport) -> bool:
    """模板没找到。预演阶段它被记成告警，这里按码判断，不比对中文。"""
    return report.template is None


def paragraph_text(document: PreparedDocument, index: Optional[int]) -> str:
    """取某段原文。界面点字段时用它高亮。"""
    if index is None or index < 0 or index >= len(document.paragraphs):
        return ""
    paragraph: ProseParagraph = document.paragraphs[index]
    return paragraph.text


def source_lines(document: PreparedDocument) -> list[tuple[int, str]]:
    """原文面板的全部行（段号, 文本）。段号从 1 开始，与「原文第 N 段」一致。"""
    return [(paragraph.index + 1, paragraph.text) for paragraph in document.paragraphs]


# ---------------------------------------------------------------------------
# 源文件改名
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenamePrompt:
    """改名确认框要显示的三样东西。"""

    title: str
    body: str
    can_apply: bool


def rename_prompt(plan: NamePlan) -> RenamePrompt:
    """把命名方案翻成人话。四种情况分开说，不能都弹一句「确定吗」。

    改名动的是用户自己的文件，说清「为什么改、改成什么、缺什么」比按钮本身重要。
    """
    if plan.target is None:
        return RenamePrompt(
            "还改不了文件名",
            f"{plan.reason}\n\n补齐这一项之后，文件名会跟着一起变。",
            False,
        )
    if plan.duplicate:
        return RenamePrompt(
            "这份文件已经在库里了",
            f"{plan.reason}\n\n没有改名，也没有删除。请自己确认留哪一份。",
            False,
        )
    if not plan.needs_rename:
        return RenamePrompt(
            "文件名已经规范",
            f"现在的名字就是按案号排的标准名称：\n{plan.source.name}",
            False,
        )

    note = ""
    if plan.missing:
        note = (
            f"\n\n名字里还缺{'、'.join(plan.missing)}（没认出来）。"
            "先把这几项补上再改，名字会更完整。"
        )
    return RenamePrompt(
        "改成规范文件名",
        f"现在：{plan.source.name}\n改后：{plan.target.name}{note}",
        True,
    )


# ---------------------------------------------------------------------------
# 人工补录
# ---------------------------------------------------------------------------


class EditError(ValueError):
    """补录的值不合法。消息直接显示给用户，写成人话。"""


_DATE_RE = re.compile(r"^\s*(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*$")


def parse_date(raw: str) -> date:
    """接受 ``2023-05-12``、``2023-5-12``、``2023年5月12日`` 三种写法。

    不要求补零——手输日期时没人会写 ``05``，为这个较真只会让人重敲一遍。
    """
    match = _DATE_RE.match(raw)
    if match is None:
        raise EditError("日期写法看不懂，请写成 2023-05-12 这样。")
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError as error:
        raise EditError("这个日期不存在，请检查年月日。") from error


_TARGETS: dict[str, tuple[str, str]] = {
    # path: (作用于 case/defendant, 属性名)
    "case.case_no": ("case", "case_no"),
    "case.court_name": ("case", "court_name"),
    "case.prosecutor_organ": ("case", "prosecutor_organ"),
    "case.cause": ("case", "cause"),
    "case.judgment_date": ("case", "judgment_date"),
    "case.effective_date": ("case", "effective_date"),
    "case.panel": ("case", "panel"),
    "case.clerk": ("case", "clerk"),
    "defendant.name": ("defendant", "name"),
    "defendant.gender": ("defendant", "gender"),
    "defendant.birth_date": ("defendant", "birth_date"),
    "defendant.id_number": ("defendant", "id_number"),
    "defendant.ethnicity": ("defendant", "ethnicity"),
    "defendant.education": ("defendant", "education"),
    "defendant.domicile": ("defendant", "domicile"),
    "defendant.detention_place": ("defendant", "detention_place"),
}

_DATE_PATHS = {
    "case.judgment_date",
    "case.effective_date",
    "defendant.birth_date",
}


def apply_edit(document: PreparedDocument, seq: int, path: str, raw: str) -> None:
    """把人工补录写回抽取结果。

    写入进 ``FieldValue.corrections``，原值留痕——复核界面改了什么是要有据可查的。
    起算日单独处理：它不存在于任何 ``FieldValue``，由调用方转成 ``start_date`` 传给
    :func:`cedas.pipeline.generate`。
    """
    target_name, attribute = _TARGETS.get(path, ("", ""))
    if not target_name:
        raise EditError("这一项暂不支持修改。")

    defendant = document.defendant(seq)
    if target_name == "defendant" and defendant is None:
        raise EditError("找不到这名被告人。")

    text = raw.strip()
    value: Any = text or None
    if path in _DATE_PATHS and text:
        value = parse_date(text)
    elif path == "defendant.gender" and text and text not in ("男", "女"):
        raise EditError("性别请填「男」或「女」。")

    target: Any = document.case if target_name == "case" else defendant
    field_value = getattr(target, attribute)
    field_value.correct(value, note="界面补录")


def edit_text(document: PreparedDocument, defendant: Defendant, path: str) -> str:
    """补录框里预填什么。日期给 ISO 写法，方便直接改数字。"""
    if path == "defendant.span.start":
        return defendant.span.start.isoformat() if defendant.span.start else ""
    raw = _raw(document, defendant, path)
    if isinstance(raw, date):
        return raw.isoformat()
    return "" if raw is None else str(raw)


__all__ = [
    "BLOCKER_HINTS",
    "GROUP_ORDER",
    "SPECS",
    "STATUS_BLOCKED",
    "STATUS_DONE",
    "STATUS_READY",
    "EditError",
    "FieldRow",
    "QueueRow",
    "RenamePrompt",
    "Summary",
    "apply_edit",
    "blocker_code",
    "blocker_hint",
    "build_fields",
    "build_queue",
    "display_value",
    "edit_text",
    "grouped",
    "missing_template",
    "paragraph_text",
    "parse_date",
    "rename_prompt",
    "source_lines",
    "summarize",
]
