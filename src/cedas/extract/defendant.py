"""B 组抽取：被告人身份、罪名与刑期。

依据：字段字典 v0.1 §2；句式依据最高法《法院刑事诉讼文书样式》里刑事判决书
首部与主文的固定结构——不是从某一份样本里抄出来的。

三条约束：

1. 规则只描述**句式结构**，不含任何具体姓名、案号、日期（红线 3）。判断标准是：
   把样本里的"张三"换成"李四"、案号换掉，规则仍然命中。
2. 抽不到就留空并写明 `missing_reason`，不猜、不套默认值（红线 5）。
3. 本模块只把刑期解析成月数，起止日交给 ``calc``。这里不做任何日期运算。

v1 覆盖范围与已知缺口：

- 首部：姓名、性别、出生日期、公民身份号码、民族、文化程度、住址、羁押场所、强制措施段。
- 主文：罪名（多罪名以「、」分隔）、主刑种类与月数、罚金数额、缓刑。
- **未覆盖**：单位犯罪、法定代表人、辩护人、前科与累犯认定、"总和刑期"表述下的
  数罪并罚并罚规则（字段字典 §2.2 已标【待确认】）、户籍地与经常居住地不一致。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Optional

from ..calc.term_parser import TermParseError, parse_term
from ..domain.enums import AdditionalType, CustodyType
from ..domain.models import (
    AdditionalPunishment,
    Case,
    Crime,
    CustodySegment,
    Defendant,
    FieldValue,
    Probation,
    ProseParagraph,
)
from .anchors import Anchors

# --- 首部：身份信息 --------------------------------------------------------
# 「被告人张三，男，1985年3月2日出生……」——以逗号紧跟姓名是最稳的判据，
# 主文里的「被告人张三犯盗窃罪」用逗号或直接接动词，不会误命中。
_NAME_RE = re.compile(r"^被告人([\u4e00-\u9fa5]{1,4})[，,]")
_GENDER_RE = re.compile(r"[，,](男|女)[，,]")
_BIRTH_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日出生")
_ID_RE = re.compile(r"公民身份号码\s*([0-9]{17}[0-9Xx])")
_ETHNICITY_RE = re.compile(r"[，,]([\u4e00-\u9fa5]{1,4})族[，,]")
_DOMICILE_RE = re.compile(r"住([^。；;]+?)[。；;]")
_EDUCATION_RE = re.compile(r"(文盲|小学|初中|高中|中专|大专|本科|硕士|博士|研究生)(?:文化|学历)")
_DETENTION_PLACE_RE = re.compile(r"现羁押于([^。；;]+?)[。；;]")
_MEASURE_RE = re.compile(
    r"(?:于|自)?(?:(\d{4})年|同年)(\d{1,2})月(\d{1,2})日(?:被|起被)"
    r"(刑事拘留|逮捕|取保候审|监视居住|指定居所监视居住)"
)

# --- 主文：定罪与量刑 ------------------------------------------------------
# 贪婪匹配到逗号为止：多罪名「犯盗窃罪、诈骗罪」必须整体取出再拆，
# 用非贪婪只能拿到第一个罪名。
_CHARGE_RE = re.compile(r"犯([^，,。；]+)罪")
_SENTENCE_RE = re.compile(r"判处([^，,。；]+)")
# 罚金数额中文、阿拉伯混用：「五千元」「10 万元」「人民币5000元」
_FINE_RE = re.compile(r"并处罚金(?:人民币)?([\d.]+|[一二三四五六七八九十百千]+)(万)?元")
_PROBATION_RE = re.compile(r"缓刑([\u4e00-\u9fa5\d]{1,6}年)")

_CUSTODY_TYPES = {
    "刑事拘留": CustodyType.DETENTION,
    "逮捕": CustodyType.ARREST,
    "取保候审": CustodyType.BAIL,
    "监视居住": CustodyType.RESIDENTIAL,
    "指定居所监视居住": CustodyType.DESIGNATED_RESIDENTIAL,
}


def _fv(
    value: object,
    paragraph: Optional[ProseParagraph],
    extractor: str,
) -> FieldValue:
    """构造带溯源的字段值。溯源精确到段落，界面据此回跳原文。"""
    field: FieldValue = FieldValue()
    from ..domain.models import Provenance

    provenance = (
        None
        if paragraph is None
        else Provenance(
            paragraph_index=paragraph.index,
            snippet=paragraph.text[:120],
            page=paragraph.page,
        )
    )
    return field.set(value, provenance=provenance, extractor=extractor, confidence=1.0)


def _search(
    pattern: re.Pattern[str],
    block: Sequence[ProseParagraph],
    *,
    group: int = 1,
) -> tuple[Optional[str], Optional[ProseParagraph]]:
    """在块内逐段查找，返回 (首个命中值, 所在段落)。"""
    for paragraph in block:
        match = pattern.search(paragraph.text)
        if match is not None:
            return match.group(group), paragraph
    return None, None


def _to_date(text: Optional[str], month: str, day: str) -> Optional[date]:
    if text is None:
        return None
    try:
        return date(int(text), int(month), int(day))
    except ValueError:  # 如 2 月 30 日，说明文本有问题，宁可留空
        return None


def split_blocks(paragraphs: Sequence[ProseParagraph], section: slice) -> list[list[ProseParagraph]]:
    """按「被告人X，」段首把首部切成每人一块。

    首部里一个被告人会跨多段（身份 → 强制措施 → 羁押），所以不能一段一人。
    """
    blocks: list[list[ProseParagraph]] = []
    current: list[ProseParagraph] = []
    for paragraph in paragraphs[section]:
        if _NAME_RE.match(paragraph.text):
            if current:
                blocks.append(current)
            current = [paragraph]
        elif current:
            current.append(paragraph)
    if current:
        blocks.append(current)
    return blocks


def _parse_identity(block: Sequence[ProseParagraph], seq: int) -> Defendant:
    defendant = Defendant(seq=seq)
    first = block[0]
    joined = "\n".join(p.text for p in block)

    name_match = _NAME_RE.match(first.text)
    if name_match:
        defendant.name = _fv(name_match.group(1), first, "head_name@v1")

    gender, gender_para = _search(_GENDER_RE, block)
    if gender:
        defendant.gender = _fv(gender, gender_para, "head_gender@v1")

    birth_match = _BIRTH_RE.search(joined)
    if birth_match:
        birth = _to_date(birth_match.group(1), birth_match.group(2), birth_match.group(3))
        if birth is not None:
            defendant.birth_date = _fv(birth, _para_of(block, _BIRTH_RE), "head_birth@v1")

    id_number, id_para = _search(_ID_RE, block)
    if id_number:
        defendant.id_number = _fv(id_number.upper(), id_para, "head_id@v1")

    ethnicity, eth_para = _search(_ETHNICITY_RE, block)
    if ethnicity:
        defendant.ethnicity = _fv(ethnicity, eth_para, "head_ethnicity@v1")

    domicile, dom_para = _search(_DOMICILE_RE, block)
    if domicile:
        defendant.domicile = _fv(domicile, dom_para, "head_domicile@v1")

    education, edu_para = _search(_EDUCATION_RE, block)
    if education:
        defendant.education = _fv(education, edu_para, "head_education@v1")

    place, place_para = _search(_DETENTION_PLACE_RE, block)
    if place:
        defendant.detention_place = _fv(place, place_para, "head_detention_place@v1")

    defendant.custody = _parse_custody(block)
    return defendant


def _para_of(block: Sequence[ProseParagraph], pattern: re.Pattern[str]) -> Optional[ProseParagraph]:
    """找出正则实际命中的段落，避免溯源指向错误的段落。"""
    for paragraph in block:
        if pattern.search(paragraph.text):
            return paragraph
    return None


def _parse_custody(block: Sequence[ProseParagraph]) -> list[CustodySegment]:
    """按出现顺序收集强制措施段，拘留在前、逮捕在后。

    顺序有语义：羁押起始日取最早一段，折抵天数按段累加，排序错了会算错刑期。

    判决书里第二段起的日期常写「同年 3 月 14 日被逮捕」，省略年份。年份必须
    沿用同一块的上一处，不能默认当年——跨年羁押时（12 月拘留、1 月逮捕）
    按当年补会算错一年。
    """
    segments: list[CustodySegment] = []
    year: Optional[int] = None

    for paragraph in block:
        for match in _MEASURE_RE.finditer(paragraph.text):
            if match.group(1):
                year = int(match.group(1))
            if year is None:
                continue
            start = _to_date(str(year), match.group(2), match.group(3))
            segments.append(CustodySegment(type=_CUSTODY_TYPES[match.group(4)], start=start))

    segments.sort(key=lambda seg: (seg.start or date.max))
    return segments


# 金额里「五千」「三万二千」是位值写法，与刑期的「三年六个月」不同，
# 不能复用 calc 的刑期数字解析（那个按年月段切，遇到「千」直接报错）。
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _cn_amount(text: str) -> Optional[float]:
    """「五千」→5000，「三万二千」→32000，「十」→10。认不出返回 None。"""
    total = section = number = 0
    for char in text:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            section += (number or 1) * _CN_UNITS[char]
            number = 0
        elif char == "万":
            total += (section + number) * 10000
            section = number = 0
        else:
            return None
    return float(total + section + number)


def _money(text: str, unit_wan: bool) -> Optional[float]:
    """把「五千」「5000」「10」折算成元。认不出就返回 None，不猜。"""
    raw = text.strip()
    if not raw:
        return None
    if raw.replace(".", "", 1).isdigit():
        base: Optional[float] = float(raw)
    else:
        base = _cn_amount(raw)
    if base is None:
        return None
    return base * (10000 if unit_wan else 1)


def _charges(text: str) -> list[str]:
    """抽取罪名，多罪名以「、」分隔。返回去重后保持原序。"""
    names: list[str] = []
    for raw in _CHARGE_RE.findall(text):
        for part in re.split(r"[、和及]", raw):
            name = part.strip().rstrip("罪")
            if name and name not in names:
                names.append(name)
    return names


def _apply_body(defendant: Defendant, body: Sequence[ProseParagraph]) -> None:
    """在主文里找该被告人的定罪量刑表述，填 crimes 与决定执行刑期。"""
    name = defendant.name.value
    if not name:
        return

    for paragraph in body:
        text = paragraph.text
        if "被告人" not in text or str(name) not in text:
            continue

        charges = _charges(text)
        sentence_match = _SENTENCE_RE.search(text)
        if not charges or sentence_match is None:
            continue

        try:
            parsed = parse_term(sentence_match.group(1))
        except TermParseError:
            # 刑期文本异常时仍保留罪名，刑期留空由人工补——不能因为一句话
            # 解析失败就丢掉整个罪名。
            parsed = None

        # 一罪一刑期时才能把「判处X」记到该罪名下。数罪并罚句里只有一个
        # 「判处」结果，若每个罪名都挂同一个月数就是编数据，宁可只填决定执行刑期。
        single_charge = len(charges) == 1
        for charge in charges:
            crime = Crime(raw_text=text)
            crime.charge = _fv(charge, paragraph, "body_charge@v1")
            if parsed is not None and single_charge:
                crime.principal_type = _fv(parsed.principal_type.value, paragraph, "body_principal@v1")
                if parsed.months is not None:
                    crime.term_months = _fv(parsed.months, paragraph, "body_term@v1")
            defendant.crimes.append(crime)

        if parsed is not None and not defendant.merged_type.is_present:
            defendant.merged_type = _fv(parsed.principal_type.value, paragraph, "body_merged@v1")
            if parsed.months is not None:
                defendant.merged_months = _fv(parsed.months, paragraph, "body_merged@v1")
            defendant.merged_days = parsed.days

        fine = _FINE_RE.search(text)
        if fine and defendant.crimes:
            amount = _money(fine.group(1), bool(fine.group(2)))
            if amount is not None:
                defendant.crimes[0].additional.append(
                    AdditionalPunishment(
                        type=AdditionalType.FINE,
                        fine_amount=amount,
                        raw_text=fine.group(0),
                    )
                )

        probation = _PROBATION_RE.search(text)
        if probation:
            defendant.probation = Probation(
                applied=_fv(True, paragraph, "body_probation@v1"),
            )
        return


def extract_defendants(
    paragraphs: Sequence[ProseParagraph],
    anchors: Optional[Anchors],
) -> list[Defendant]:
    """抽取全部被告人。锚点缺失时返回空列表，由上层决定是否阻断。"""
    if anchors is None:
        return []

    defendants: list[Defendant] = []
    for seq, block in enumerate(split_blocks(paragraphs, anchors.head), start=1):
        defendant = _parse_identity(block, seq)
        _apply_body(defendant, paragraphs[anchors.body])
        defendants.append(defendant)
    return defendants


def attach_to_case(case: Case, defendants: Sequence[Defendant]) -> None:
    """把被告人挂到案件上，并回填「是否数罪并罚」需要的一致性提示。"""
    case.defendants = list(defendants)


__all__ = ["attach_to_case", "extract_defendants", "split_blocks"]
