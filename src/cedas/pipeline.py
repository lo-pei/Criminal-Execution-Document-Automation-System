"""端到端流程：判决书 → 清洗 → 抽取 → 刑期 → 模板填充 → 归档 → 台账。

这一层是**唯一**知道「一份判决书要出几份文书、每份叫什么名字、落在哪个目录」
的地方。下游各层都不认识「文书」这个概念：``extract`` 只认识字段，``render``
只认识占位符，``storage`` 只认识路径。界面与命令行都调这里，避免两套命名规则。

三条设计取舍：

1. **阻断优于残次。** 核心字段缺失时不生成文书，报告里写明阻断原因。
   ``allow_missing`` 只是现场应急口子，用时报告会标记 ``degraded``——
   生成一份缺身份证号的执行通知书，比不生成更危险（§3.7 同理）。

2. **起算日两级来源，且来源必须可见。** 判决书载明则用载明的（实测 53% 的主文
   不载明）；退回「判决生效日」是 C 组人工录入的法定路径，不是猜测。但报告里
   必须写出 ``start_source``，否则复核的人无从判断这份文书能不能直接用。

3. **版本号在渲染前探测。** 同案同人同文书类型重复生成不覆盖旧件，
   文件名带 ``v2``/``v3``，台账记版本。法院要的是可追溯，不是最新版。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .calc import compute_span, resolve_start
from .config import DB_PATH, OUTPUT_DIR, REQUIRED_FIELDS, ensure_data_dirs
from .domain.enums import PrincipalType
from .domain.models import Case, Defendant, ProseParagraph, SentenceSpan
from .extract import extract
from .ingest import read_document
from .ingest.cleaner import clean
from .render import build_context, find_mapping, find_template, load, render
from .render.template import TemplateNotFoundError
from .storage.archive import archive_path, next_version
from .storage.db import Database

DEFAULT_DOC_TYPE = "执行通知书"

_PRINCIPAL_BY_VALUE = {member.value: member for member in PrincipalType}

# 阻断码。前缀 E = error（阻断），W = warning（放行但要人看）。
# 码值稳定，界面与脚本按码判断，不要按中文比。
_REQUIRED_LABELS = {
    "case_no": ("E-CASE-NO", "案号"),
    "court_name": ("E-COURT", "法院名称"),
    "defendant.name": ("E-NAME", "被告人姓名"),
    "defendant.gender": ("E-GENDER", "性别"),
    "defendant.birth_date": ("E-BIRTH", "出生日期"),
    "defendant.id_number": ("E-ID", "公民身份号码"),
    "defendant.crimes": ("E-CHARGES", "罪名"),
}


@dataclass
class DocumentItem:
    """一名被告人对应的一份文书。"""

    seq: int
    defendant: str
    path: Optional[Path] = None
    version: int = 1
    rendered: bool = False
    degraded: bool = False
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    start_source: str = ""
    start: Optional[date] = None
    end: Optional[date] = None
    context: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "defendant": self.defendant,
            "ok": self.ok,
            "rendered": self.rendered,
            "degraded": self.degraded,
            "version": self.version,
            "path": str(self.path) if self.path else None,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "start_source": self.start_source,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "context": self.context,
        }


@dataclass
class PipelineReport:
    """一次生成的全部结果。界面据此决定「哪些成功了、哪些卡住了」。"""

    source: Path
    channel: str
    doc_type: str
    case_no: str
    court_name: str
    paragraph_count: int
    mapping: Optional[Path]
    template: Optional[Path]
    items: list[DocumentItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def rendered(self) -> list[DocumentItem]:
        return [item for item in self.items if item.rendered]

    @property
    def blocked(self) -> list[DocumentItem]:
        return [item for item in self.items if not item.ok]

    @property
    def candidates(self) -> list[DocumentItem]:
        """未被阻断、可以出文的条目。预演阶段它们 ``rendered`` 仍为 False。"""
        return [item for item in self.items if item.ok]

    @property
    def ok(self) -> bool:
        return bool(self.items) and not self.blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "channel": self.channel,
            "doc_type": self.doc_type,
            "case_no": self.case_no,
            "court_name": self.court_name,
            "paragraph_count": self.paragraph_count,
            "mapping": str(self.mapping) if self.mapping else None,
            "template": str(self.template) if self.template else None,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "rendered_count": len(self.rendered),
            "blocked_count": len(self.blocked),
            "warnings": self.warnings,
            "items": [item.to_dict() for item in self.items],
        }


def _as_date(value: Any) -> Optional[date]:
    """``FieldValue`` / ``date`` / ``None`` 统一成 ``date``。"""
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, date) else None


def _principal(defendant: Defendant) -> PrincipalType:
    return _PRINCIPAL_BY_VALUE.get(str(defendant.merged_type.value), PrincipalType.UNKNOWN)


def resolve_span(
    case: Case,
    defendant: Defendant,
    *,
    start_date: Optional[date] = None,
    effective_date: Optional[date] = None,
) -> tuple[SentenceSpan, str]:
    """按 calc 层的起算日规则重算刑期区间，并返回起算日来源。

    与 ``extract`` 里那次计算的区别：抽取时还不知道判决生效日，只能取羁押起始日；
    到这里生效日可能已由人工录入（C 组），**管制**的起算日应改按生效日——
    这是 ``calc.resolve_start`` 早已写明的规则，所以此处直接调它，不再自己判。
    """
    principal = _principal(defendant)
    months = defendant.merged_months.value if isinstance(defendant.merged_months.value, int) else None
    effective = effective_date or _as_date(case.effective_date)

    start = start_date
    source = "人工指定" if start_date is not None else ""

    if start is None:
        start = resolve_start(
            principal,
            custody_start=defendant.custody_start,
            effective_date=effective,
        )
        if start is not None:
            source = "生效日期" if effective is not None and start == effective else "羁押起始日"
    if start is None:
        start = defendant.span.start
        source = "羁押起始日" if start is not None else ""

    span = compute_span(
        principal_type=principal,
        months=months,
        days=defendant.merged_days,
        start=start,
    )
    return span, source


def _present(case: Case, defendant: Defendant, path: str) -> bool:
    """按 ``config.REQUIRED_FIELDS`` 的路径判「有没有值」。

    路径有两种写法：``case_no``（案件级，直接取属性）与 ``defendant.name``
    （带 ``defendant.`` 前缀）。这里统一处理，避免调用方各自拆字符串。
    """
    parts = path.split(".")
    if parts[0] == "defendant":
        target: Any = defendant
        attribute = parts[1]
    else:
        target = case
        attribute = parts[-1]

    value = getattr(target, attribute, None)
    if attribute == "crimes":
        return bool(value)
    checker = getattr(value, "is_present", None)
    if checker is not None:
        return bool(checker)
    return value is not None


def check_blockers(case: Case, defendant: Defendant, span: SentenceSpan) -> list[str]:
    """核心字段与刑期完整性的阻断判定。

    判据只用「有没有」，不用「准不准」——准不准由复核界面与人负责，
    机器在这里多猜一步，错误就会被埋进正式文书。
    """
    blockers: list[str] = []

    for path in REQUIRED_FIELDS:
        if _present(case, defendant, path):
            continue
        code, label = _REQUIRED_LABELS.get(path, ("E-FIELD", path))
        blockers.append(f"{code} {label}缺失")

    if not defendant.merged_type.is_present:
        blockers.append("E-PRINCIPAL 主刑未识别")
    if not defendant.merged_months.is_present and defendant.merged_days <= 0:
        blockers.append("E-TERM 刑期未识别")
    if span.start is None:
        blockers.append("E-START 无起算日（判决书未载明，且未录入生效日期）")
    elif span.end is None:
        blockers.append("E-END 无届满日（主刑为无期/死刑，或刑期不完整）")

    return blockers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select(defendants: Sequence[Defendant], names: Optional[Sequence[str]]) -> list[Defendant]:
    if not names:
        return list(defendants)
    wanted = {name.strip() for name in names if name and name.strip()}
    return [d for d in defendants if d.display_name in wanted]


@dataclass
class PreparedDocument:
    """一份读完并抽完的判决书。

    界面在「选完文件」时调一次 :func:`prepare`，之后每次改生效日期、增删被告人都只
    重跑 :func:`plan`，不再重新解析 PDF。老机器上解析一份判决书要秒级时间，
    每改一个日期就重读一遍文件，用起来会明显卡。
    """

    source: Path
    channel: str
    paragraphs: list[ProseParagraph]
    case: Case
    defendants: list[Defendant]
    warnings: list[str] = field(default_factory=list)

    def defendant(self, seq: int) -> Optional[Defendant]:
        return next((d for d in self.defendants if d.seq == seq), None)


def prepare(source: Path | str) -> PreparedDocument:
    """读取判决书并抽取字段。**不做任何日期推算**，那属于 :func:`plan`。"""
    path = Path(source)
    blocks, channel = read_document(path)
    paragraphs = clean(blocks)
    result = extract(paragraphs)
    return PreparedDocument(
        source=path,
        channel=channel,
        paragraphs=list(paragraphs),
        case=result.case,
        defendants=list(result.defendants),
        warnings=[str(w) for w in result.warnings],
    )


def plan(
    document: PreparedDocument,
    *,
    doc_type: str = DEFAULT_DOC_TYPE,
    defendant_names: Optional[Sequence[str]] = None,
    start_date: Optional[date] = None,
    start_dates: Optional[dict[int, date]] = None,
    effective_date: Optional[date] = None,
    mapping_path: Optional[Path | str] = None,
    template_path: Optional[Path | str] = None,
    allow_missing: bool = False,
    dry_run: bool = False,
    require_template: bool = True,
) -> PipelineReport:
    """只算「能出什么、卡在哪」，**不落盘**。

    界面靠它铺第一屏：用户还没点「生成」，也该看到每个被告人缺哪一项。
    ``require_template=False`` 时模板缺失记告警而不抛异常——复核界面在没有模板的
    情况下也要能打开，否则用户连「缺什么字段」都看不到，只知道程序报错。
    """
    case = document.case
    if effective_date is not None:
        case.effective_date.set(effective_date, extractor="manual@entry")

    mapping = load(mapping_path) if mapping_path else load(find_mapping(doc_type))

    warnings = list(document.warnings)
    template: Optional[Path]
    try:
        template = Path(template_path) if template_path else find_template(mapping.template)
    except TemplateNotFoundError as error:
        if require_template:
            raise
        template = None
        warnings.append(f"W-TEMPLATE {error}")

    selected = _select(document.defendants, defendant_names)

    report = PipelineReport(
        source=document.source,
        channel=document.channel,
        doc_type=mapping.document_type,
        case_no=str(case.case_no.value or ""),
        court_name=str(case.court_name.value or ""),
        paragraph_count=len(document.paragraphs),
        mapping=mapping.source,
        template=template,
        warnings=warnings,
        dry_run=dry_run,
    )

    if not selected:
        report.warnings.append(
            "A004 未识别到被告人，无法生成文书"
            if not defendant_names
            else f"未找到被告人：{'、'.join(defendant_names)}"
        )
        return report

    for defendant in selected:
        item = _plan_item(
            case,
            defendant,
            mapping,
            start_date=(start_dates or {}).get(defendant.seq, start_date),
            effective_date=effective_date,
        )
        if item.blockers and allow_missing:
            item.degraded = True
            item.warnings.extend(f"W-DEGRADED {text}" for text in item.blockers)
            item.blockers = []
        report.items.append(item)

    return report


def _plan_item(
    case: Case,
    defendant: Defendant,
    mapping: Any,
    *,
    start_date: Optional[date],
    effective_date: Optional[date],
) -> DocumentItem:
    """单个被告人的阻断判定与上下文组装。"""
    span, start_source = resolve_span(
        case,
        defendant,
        start_date=start_date,
        effective_date=effective_date,
    )
    # 回写到被告人身上：映射表的派生字段（起日/止日/释放日）读的是
    # ``defendant.span``，不回写就会出现「报告说 6 月 1 日起算、文书里却是空白」。
    defendant.span = span

    item = DocumentItem(
        seq=defendant.seq,
        defendant=defendant.display_name,
        start=span.start,
        end=span.end,
        start_source=start_source,
    )
    item.blockers = check_blockers(case, defendant, span)
    if defendant.is_combined:
        item.warnings.append("W-COMBINED 数罪并罚，决定执行刑期按主文「决定执行」部分")
    if defendant.probation.applied.value:
        item.warnings.append("W-PROBATION 缓刑案件是否走本系统待业务方确认（口径 P5）")
    if start_source == "生效日期":
        item.warnings.append("W-START-EFFECTIVE 判决书未载明起算日，已按生效日期起算")
    if not case.effective_date.is_present:
        item.warnings.append("W-NO-EFFECTIVE 生效日期未录入，文书该处留空（C 组需人工补）")

    item.context = build_context(case, defendant, mapping)
    return item


def generate(
    source: Optional[Path | str] = None,
    *,
    prepared: Optional[PreparedDocument] = None,
    doc_type: str = DEFAULT_DOC_TYPE,
    output_dir: Optional[Path | str] = None,
    defendant_names: Optional[Sequence[str]] = None,
    start_date: Optional[date] = None,
    start_dates: Optional[dict[int, date]] = None,
    effective_date: Optional[date] = None,
    when: Optional[date] = None,
    mapping_path: Optional[Path | str] = None,
    template_path: Optional[Path | str] = None,
    dry_run: bool = False,
    allow_missing: bool = False,
    record: bool = True,
    db_path: Optional[Path | str] = None,
) -> PipelineReport:
    """生成执行文书。返回值即「发生了什么」，界面不需要再看别处。

    Args:
        source: 判决书路径（``.pdf`` 生产通道 / ``.txt`` 开发通道）。
        defendant_names: 只生成指定被告人；``None`` 表示全部。
        start_date: 人工指定的刑期起算日，优先级最高。
        effective_date: 判决生效日；判决书未载明起止日时据此起算。
        when: 文书日期（落款与文件名），默认当日。
        dry_run: 只组装上下文、不落盘，用于复核界面预演。
        allow_missing: 核心字段缺失仍生成（降级），报告标记 ``degraded``。
        record: 是否写台账。``False`` 只出文件。
    """
    if prepared is None and source is None:
        raise ValueError("generate 需要 source 或 prepared 之一")
    document = prepared if prepared is not None else prepare(source)  # type: ignore[arg-type]

    report = plan(
        document,
        doc_type=doc_type,
        defendant_names=defendant_names,
        start_date=start_date,
        start_dates=start_dates,
        effective_date=effective_date,
        mapping_path=mapping_path,
        template_path=template_path,
        allow_missing=allow_missing,
        dry_run=dry_run,
    )

    if dry_run or not report.candidates:
        return report

    _write(report, Path(output_dir) if output_dir is not None else OUTPUT_DIR, when or date.today())

    if record and report.rendered:
        _record(report, document.case, Path(output_dir) if output_dir is not None else OUTPUT_DIR, db_path)

    return report


def _write(report: PipelineReport, root: Path, when: date) -> None:
    """落盘。只处理未阻断的条目；模板缺失时在这里才报错（预演阶段不抛）。"""
    if report.template is None:
        detail = next((w for w in report.warnings if "模板" in w), "")
        raise TemplateNotFoundError(f"模板未就位，无法生成文书。{detail}")

    root.mkdir(parents=True, exist_ok=True)
    for item in report.items:
        if not item.ok:
            continue
        version = next_version(root, report.case_no, item.defendant, report.doc_type, when)
        target = archive_path(
            root,
            report.case_no,
            item.defendant,
            report.doc_type,
            when,
            version=version,
        )
        render(report.template, item.context, target, strict=True)
        item.path = target
        item.version = version
        item.rendered = True


def _record(
    report: PipelineReport,
    case: Case,
    root: Path,
    db_path: Optional[Path | str],
) -> None:
    """写台账。失败不阻断出文——文件已经在盘上，台账可事后补。"""
    ensure_data_dirs()
    database = Database(db_path or DB_PATH)
    try:
        database.init_schema()
        case_id = database.add_case(case)
        by_seq = {defendant.seq: defendant for defendant in case.defendants}
        for item in report.rendered:
            defendant = by_seq.get(item.seq)
            if defendant is None or item.path is None:  # pragma: no cover - 结构上不会发生
                continue
            defendant_id = database.add_defendant(case_id, defendant)
            try:
                relative = item.path.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - 归档一定在 root 之下
                relative = item.path.as_posix()
            database.record_document(
                defendant_id,
                report.doc_type,
                relative,
                version=item.version,
                sha256=_sha256(item.path),
            )
    finally:
        database.close()


__all__ = [
    "DEFAULT_DOC_TYPE",
    "DocumentItem",
    "PipelineReport",
    "PreparedDocument",
    "check_blockers",
    "generate",
    "plan",
    "prepare",
    "resolve_span",
]
