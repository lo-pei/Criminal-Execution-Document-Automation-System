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
- 主文：罪名（多罪名以「、」分隔）、主刑种类与月数、罚金数额、缓刑、
  判决书载明的刑期起止日（仅作校验，见 ADR-017）。
- **未覆盖**：单位犯罪、法定代表人、辩护人、前科与累犯认定、"总和刑期"表述下的
  数罪并罚并罚规则（字段字典 §2.2 已标【待确认】）、户籍地与经常居住地不一致。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from typing import Optional

from ..calc.term_parser import (
    ParsedTerm,
    TermParseError,
    cn_number_to_int,
    parse_duration_months,
    parse_term,
)
from ..domain.enums import AdditionalType, CustodyType
from ..domain.models import (
    AdditionalPunishment,
    Case,
    Crime,
    CustodySegment,
    Defendant,
    FieldValue,
    ProseParagraph,
)
from .anchors import Anchors, text_before_end_marker
from .combination import detect_combination, revokes_probation

# --- 首部：身份信息 --------------------------------------------------------
# 「被告人张三，男，1985年3月2日出生……」——姓名后紧跟标点是最稳的判据，
# 主文里的「被告人张三犯盗窃罪」用逗号或直接接动词，不会误命中。
#
# 句号也算：简易程序或被告人身份信息不全的判决书写「被告人颜某。曾因犯盗窃罪……」
# （见（2014）甬余刑初字第633号），姓名后直接断句，只认逗号会让整份文书的
# 被告人一个都认不出，连带主文归属全部落空。
_NAME_RE = re.compile(r"^(?:原审)?被告人([\u4e00-\u9fa5]{1,4})[，,。]")
_GENDER_RE = re.compile(r"[，,](男|女)[，,]")
_BIRTH_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日出生")
_ID_RE = re.compile(r"公民身份号码\s*([0-9]{17}[0-9Xx])")
_ETHNICITY_RE = re.compile(r"[，,]([\u4e00-\u9fa5]{1,4})族[，,]")
# 住址。「住」前面若是「居」，那是「监视居住」「指定居所监视居住」「居住地」，
# 不是住址字段：（2014）甬余刑初字第633号首部没有任何住址信息，
# 却因为「依法监视居住，同月6日被依法变更为刑事拘留……」抽出一串强制措施文字，
# 复核界面上看起来像是把羁押经过错填进了住址栏。
_DOMICILE_RE = re.compile(r"(?<!居)住([^。；;，,]{2,40})")
_EDUCATION_RE = re.compile(r"(文盲|小学|初中|高中|中专|大专|本科|硕士|博士|研究生)(?:文化|学历)")
_DETENTION_PLACE_RE = re.compile(r"现羁押于([^。；;]+?)[。；;]")
# 强制措施。「被」后面常插着机关名：「于2024年9月19日被平山县公安局刑事拘留」
# 「经平山县人民检察院批准，于2024年10月24日被执行逮捕」——真实判决十有八九这么写，
# 只认「被+措施名」会一条都抽不到，羁押起始日随即缺失，起算日整条断掉。
#
# 日期与「被」之间还要能容纳案由：（2014）梁刑初字第12号写的是
# 「2013年9月28日因涉嫌抢劫罪被梁山县公安局刑事拘留，同年10月23日经梁山县
# 人民检察院批准被逮捕」——「因涉嫌抢劫罪」夹在日期和「被」之间，
# 「经…批准」夹在日期和「被」之间。要求「日」后紧跟「被」会把两处都漏掉，
# 整份判决书的羁押信息全空。允许中间夹至多 15 字，但不跨句（不越过，。；）。
_MEASURE_RE = re.compile(
    r"(?:于|自)?(?:(\d{4})年|同年)(\d{1,2})月(\d{1,2})日"
    r"[^，。；]{0,15}?"
    r"(?:被|由|起被)"
    r"[^，。；]{0,20}?"
    r"(刑事拘留|逮捕|取保候审|监视居住|指定居所监视居住)"
)
# 供「同年」补年份用：段内任意一处四位数年份，不要求紧跟强制措施。
_YEAR_RE = re.compile(r"(\d{4})年")

# --- 主文：定罪与量刑 ------------------------------------------------------
# 罪名与刑期必须**成对**取：判决书主文写「犯A罪，判处X；犯B罪，判处Y」，
# 只取第一个「判处」会让数罪并罚的后几个罪名全部没有刑期，
# 决定执行刑期也会被错当成第一个罪的刑期（数罪并罚样本实测差 78 个月）。
_PAIR_RE = re.compile(r"犯([^，,。；]+)[，,]?\s*判处([^，,。；]+)")
# 「数罪并罚，决定执行有期徒刑九年」——这一句才是执行通知书要的刑期，
# 它不在「犯X罪，判处Y」的句式里，必须单独取。
_DECISION_RE = re.compile(r"决定执行([^，,。；]+)")
# 无配对时的兜底（判决书写「判处被告人张三有期徒刑三年」这类非常规句式）
_SENTENCE_RE = re.compile(r"判处([^，,。；]+)")
# 罚金数额中文、阿拉伯混用：「五千元」「10 万元」「人民币5000元」
_FINE_RE = re.compile(r"并处罚金(?:人民币)?([\d.]+|[一二三四五六七八九十百千]+)(万)?元")
_PROBATION_RE = re.compile(r"缓刑([^，,。；;、）)]{1,12})")
# 免予/免除刑事处罚。判项里的固定用语，说理段里出现的是「可免予」这类主张，
# 因此额外要求同段出现本人姓名（见 _apply_exemption）。
_EXEMPT_RE = re.compile(r"(?:免予|免除)刑事处罚")
# 无罪判项：「被告人X无罪」「被告人X犯盗窃罪，无罪」。
_INNOCENT_RE = re.compile(r"无罪\s*[。；;]?\s*$")
# 免刑判项里的罪名：「被告人X犯盗窃罪，免予刑事处罚」。
_CHARGE_ONLY_RE = re.compile(r"犯([^，,。；;]{1,40}?罪)")
# 主文里「被告人」后紧跟的姓名不再用正则取，改为与已知姓名直接比对
# （见 ``index_body``）。分项编号前缀，判续段前要先剥掉。
_SECTION_PREFIX_RE = re.compile(r"^[（(]?[一二三四五六七八九十]{1,3}[）)、．.]\s*")
# 续段特征：数罪并罚的「决定执行」、先并后减的「与前罪…并罚」、
# 撤销缓刑的「撤销…」「把前罪…」都自成一段，且段内不再出现姓名。
_CONTINUATION_HEADS = ("犯", "数罪并罚", "决定执行", "与前罪", "将前罪", "把前罪", "撤销")

_CUSTODY_TYPES = {
    "刑事拘留": CustodyType.DETENTION,
    "逮捕": CustodyType.ARREST,
    "取保候审": CustodyType.BAIL,
    "监视居住": CustodyType.RESIDENTIAL,
    "指定居所监视居住": CustodyType.DESIGNATED_RESIDENTIAL,
}

# --- C 组：判决书载明的刑期起止日 ------------------------------------------
# 真实文书写「即自2024年9月19日起至2025年3月18日止」，也写「即从2013年9月28日
# 起至2024年9月27日止」；「即」可省，「自」「从」两用，日期与「起」之间不空格。
_SPAN_YEAR = r"(?:\d{4}|[零〇○OoＯ一二三四五六七八九]{4})"
_SPAN_MONTH_DAY = r"(?:\d{1,2}|[零〇○OoＯ一二三四五六七八九十]{1,3})"
_SPAN_RE = re.compile(
    rf"(?:即)?(?:自|从)({_SPAN_YEAR})年({_SPAN_MONTH_DAY})月({_SPAN_MONTH_DAY})日"
    # 「止」可有可无：（2014）台椒刑初字第570号写「即自二O一四年三月三日起至
    # 二O一八年三月二日。」——没有「止」字，硬性要求就整条抽不到。
    rf"起\s*至({_SPAN_YEAR})年({_SPAN_MONTH_DAY})月({_SPAN_MONTH_DAY})日\s*止?"
)
# 同一份判决书里「自…起至…止」至少还有两种别的意思，都长得一模一样：
#
#   另查明：被告人曾因犯抢劫罪……刑期自2008年6月30日起至2014年6月29日止。（前罪）
#   宿某建假释考验期自2013年2月4日起至2014年6月29日止。（假释考验期）
#
# 按日期形状取，前罪的刑期就会覆盖本案的刑期，而且是静默覆盖。判据用主文刑期注释
# 段的固定用语——前罪句与考验期句都不会写「判决执行之日起计算」或「折抵」。
_SPAN_MARKERS = (
    "判决执行之日起计算",
    "判决执行以前先行羁押",
    "判决执行前先行羁押",
    "折抵",
)
_EXAMINATION_PERIOD = "考验期"
# 主文里的刑期注释段以「（刑期」「（主刑期」起头，段内没有姓名。
_SPAN_NOTE_HEADS = ("（刑期", "(刑期", "（主刑期", "(主刑期")


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


_CN_ZERO = str.maketrans({"〇": "0", "○": "0", "O": "0", "o": "0", "Ｏ": "0", "零": "0"})
_CN_DIGIT_CHARS = {
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


def _year_value(text: str) -> Optional[int]:
    """年份转整数。支持「2014」与「二〇一四」「二O一四」三种写法。

    年份必须**逐位**读：「二〇一四」逐位是 2/0/1/4，按位值读会得到 4。
    月、日则相反，要按位值读（「二十九日」是 29 不是 2 和 9），两者不能共用一个函数。

    真实文书里「零」常被写成拉丁字母 O 或圈号：「二O一四年三月三日」
    （（2014）台椒刑初字第570号）。不认这一种，该案的载明起止日一个都抽不到。
    """
    text = text.strip()
    if text.isdigit():
        return int(text)
    normalized = "".join(_CN_DIGIT_CHARS.get(ch, ch) for ch in text.translate(_CN_ZERO))
    return int(normalized) if len(normalized) == 4 and normalized.isdigit() else None


def _month_day_value(text: str) -> Optional[int]:
    """月、日转整数。「十二」「二十九」要按位值读。"""
    text = text.strip()
    if text.isdigit():
        return int(text)
    try:
        return cn_number_to_int(text.translate(_CN_ZERO))
    except TermParseError:
        return None


def _to_date(text: Optional[str], month: str, day: str) -> Optional[date]:
    if text is None:
        return None
    year = _year_value(text)
    month_value = _month_day_value(month)
    day_value = _month_day_value(day)
    if year is None or month_value is None or day_value is None:
        return None
    try:
        return date(year, month_value, day_value)
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

    「同年」的年份来源不限于强制措施句。真实文书常见这样的连写：

        于2020年7月8日被抓获，同年7月10日被淮滨县公安局刑事拘留，
        同年7月24日被取保候审。

    「抓获」不是法定强制措施，`_MEASURE_RE` 不匹配它，若只在措施句里取年份，
    这一串会因年份未知被整段跳过，羁押 14 天凭空消失。所以段内任何一处
    `YYYY年` 都要先登记下来，供其后的「同年」沿用。
    """
    segments: list[CustodySegment] = []
    year: Optional[int] = None

    for paragraph in block:
        text = paragraph.text
        for match in _MEASURE_RE.finditer(text):
            if match.group(1):
                year = int(match.group(1))
            elif year is None:
                # 「同年」且块内尚无年份：从本段此前的日期里补
                prior = _YEAR_RE.findall(text[: match.start()])
                if prior:
                    year = int(prior[-1])
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


def _charge_names(raw: str) -> list[str]:
    """从一个「…罪」串里拆出罪名。

    顿号只有在**每一段都以「罪」结尾**时才是罪名分隔符：
    「盗窃罪、诈骗罪」是两个罪，而「掩饰、隐瞒犯罪所得罪」是一个罪——
    按顿号无条件切会把后者切成「掩饰」与「隐瞒犯罪所得」两个不存在的罪名，
    复核界面里看着像抽错了，实际是拆分规则错了。
    """
    text = raw.strip().rstrip("。；;，, ")
    if not text.endswith("罪"):
        return []
    parts = [part.strip() for part in re.split(r"[、和及]", text) if part.strip()]
    if len(parts) > 1 and all(part.endswith("罪") for part in parts):
        return [part[:-1] for part in parts]
    return [text[:-1]]


def index_body(
    body: Sequence[ProseParagraph],
    defendants: Sequence[Defendant],
) -> dict[int, list[ProseParagraph]]:
    """按主文顺序把段落归给被告人。

    判决书主文的一「项」常常只写一次姓名，后面用「犯B罪，判处…」
    「数罪并罚，决定执行…」续写，**续段里没有姓名**。只按「段落含姓名」
    匹配会把续段丢掉，而「决定执行」刑期恰恰都在续段里——
    实测数罪并罚样本因此少算 78 个月。

    归类规则：段落出现「被告人+已知姓名」即切换当前被告人；出现并罚、
    决定执行或续段开头词则沿用；其余（退赔、没收、附带民事赔偿）断开归属，
    避免把民事赔偿金额当成罚金。
    """
    seq_by_name = {str(d.name.value): d.seq for d in defendants if d.name.is_present}
    # 长名先匹配：同案有「张三」与「张三丰」时，短名会抢先吃进长名那段。
    ordered = sorted(seq_by_name, key=len, reverse=True)
    blocks: dict[int, list[ProseParagraph]] = {}
    current: Optional[int] = None

    for paragraph in body:
        text = text_before_end_marker(paragraph.text).strip()
        if not text:
            continue
        # 落款标记与最后一项判决可能同段，切掉后半再判归属，免得那句
        # 「如不服本判决……被告人可……」把段落算到某个被告名下。
        if text != paragraph.text:
            paragraph = replace(paragraph, text=text)
        # 用已知姓名直接比对，不用「被告人+2~4 个汉字」的正则：
        # 「…对被告人郑十一宣告缓刑…」里的姓名后面跟的是动词而不是标点，
        # 正则的右界条件会把这一段整个漏掉，连带丢掉它后面那句「决定执行」。
        hit = next((name for name in ordered if f"被告人{name}" in text), None)
        if hit is not None:
            current = seq_by_name[hit]
        elif "被告单位" in text:
            # 单位判项不是这个自然人的。这一段既没有自然人姓名，又常含
            # 「决定执行」，会被判成续段抵到上一个被告名下：
            # （2023）沪0113刑初923号「一、被告单位某某公司1犯虚开增值税专用发票罪，
            # 判处罚金……决定执行罚金五万元」就这么并进了王某的罪名表，
            # 两个罪名抽成四个。单位部分不生成执行通知书（另有 A006 提示），直接跳过。
            current = None
        elif _is_continuation(text):
            pass  # 续段，仍属上一个被告人
        else:
            current = None

        if current is not None:
            blocks.setdefault(current, []).append(paragraph)
    return blocks


def _is_continuation(text: str) -> bool:
    """这段是否在续写上一个被告人的量刑（数罪并罚、先并后减等）。

    先剥掉「二、」「（三）」这类分项编号：「二、数罪并罚，决定执行…」
    段首是编号而不是特征词，不剥就判不出来。
    """
    if "并罚" in text or "决定执行" in text:
        return True
    stripped = _SECTION_PREFIX_RE.sub("", text)
    # 刑期注释段：「（刑期从判决执行之日起计算……即自X日起至Y日止）」。它自成一段、
    # 段内没有姓名，真实判决里三名被告人的注释段连着排（（2024）冀0481刑初101号），
    # 不并进上一个被告人，C 组的载明起止日一个都抽不到。
    if stripped.startswith(_SPAN_NOTE_HEADS):
        return True
    return stripped.startswith(_CONTINUATION_HEADS)


def _attach_fine(crime: Crime, tail: str, paragraph: ProseParagraph) -> None:
    """把本罪之后、下一罪之前的罚金挂到本罪上。"""
    match = _FINE_RE.search(tail)
    if match is None:
        return
    amount = _money(match.group(1), bool(match.group(2)))
    if amount is None:
        return
    crime.additional.append(
        AdditionalPunishment(type=AdditionalType.FINE, fine_amount=amount, raw_text=match.group(0))
    )


def _attach_probation(defendant: Defendant, tail: str, paragraph: ProseParagraph) -> None:
    """缓刑考验期。只在「判处…」之后找，避免把「撤销缓刑三年」当成宣告缓刑。"""
    if defendant.probation.applied.value:
        return
    match = _PROBATION_RE.search(tail)
    if match is None:
        return
    defendant.probation.applied = _fv(True, paragraph, "body_probation@v1")
    try:
        months = parse_duration_months(match.group(1))
    except TermParseError:
        return
    defendant.probation.months = _fv(months, paragraph, "body_probation@v1")


def _verdict_text(text: str) -> str:
    """取段落里属于判决主文的那部分。

    「本院认为……依照……之规定，判决如下：」在真实文书里常常是**同一个自然段**，
    清洗层按段落切分不会把它拆开，于是 ``anchors.body`` 的第一段里混着整段说理。

    说理段不能参与形态判定，理由具体到案子：

        （2014）梁刑初字第12号「本院认为」里写「被告人宿某建假释考验期限内犯新罪，
        应当撤销假释……被告人张某祥在缓刑考验期限内犯新罪，应当撤销假释」——
        两名被告人的情形在同一句里，而段落归属只能给其中一个人。按段归属，
        另一个人就会拿到不属于他的「撤销缓刑」，本来该判第 71 条的被判成第 77 条。

    说理里还会出现「不构成数罪并罚」这类反向表述，拿它当正向线索更危险。
    所以形态判定只认「判决如下」之后的文字。
    """
    _, separator, tail = text.partition("判决如下")
    return tail if separator else text


def _is_revocation_paragraph(text: str) -> bool:
    """是否为「撤销缓刑 / 撤销假释」的单列一项。

    判据交给 :func:`cedas.extract.combination.revokes_probation`，与本模块外的
    形态判定共用一份实现。这里**不再自己写一份**「撤销与缓刑同现」——两处各自
    演化，会出现形态判定说不是撤销缓刑、罪名抽取说这一项是撤销缓刑的报告，
    而这种自相矛盾只有对着原文逐字看才发现。

    这类段落里也含「犯X罪，判处Y」，但那是**被撤销的前罪**，不是本案的定罪：

        三、撤销山东省梁山县人民法院（2013）梁刑初字第65号刑事判决书对被告人
            张某祥犯聚众斗殴罪，判处有期徒刑一年六个月缓刑二年的缓刑宣告。

    若照常配对，本案罪名表会多出「聚众斗殴」一罪，并罚形态也会因为
    罪名数变成 2 而被判成第 69 条，而正确答案是第 77 条撤销缓刑并罚。

    反过来，「撤销……刑事判决中第二项，即……判处有期徒刑六个月，缓刑六个月」
    撤的是原判项，缓刑六个月是那一项的内容，不算撤销缓刑段——它的实刑与缓刑
    都要照常参与配对，否则再审改判的判决会抽不到本案刑期。
    """
    return revokes_probation(text)


def _apply_exemption(defendant: Defendant, block: Sequence[ProseParagraph]) -> None:
    """认「免予刑事处罚」「宣告无罪」这两类没有刑期的结论。

    只看判项段，且姓名必须同段出现。理由：说理段里「辩护人建议宣告张某无罪」
    「被告人系从犯，可免予刑事处罚」都是**主张**，不是结论；把它们当结论，
    会把一份该出文书的判决判成不出文书，而且不报错。

    判项段的形状是固定的：``二、原审被告人纪某犯盗窃罪，免予刑事处罚。``
    因此要求「姓名 + 罪名 + 免予刑事处罚」同段，或「姓名 + 无罪」结尾。
    """
    if not defendant.name.is_present or defendant.exempt.is_present:
        return
    name = str(defendant.name.value)
    for paragraph in block:
        text = paragraph.text
        if name not in text:
            continue
        # 「撤销…」那类段落里也可能出现姓名与「免予」，但它撤的是前案
        if _is_revocation_paragraph(text):
            continue
        if _EXEMPT_RE.search(text) or _INNOCENT_RE.search(text):
            defendant.exempt = _fv(True, paragraph, "body_exempt@v1")
            return


def _attach_charges_without_term(
    defendant: Defendant, block: Sequence[ProseParagraph]
) -> None:
    """从「被告人X犯A罪，免予刑事处罚」里只取罪名。

    只在同段同时出现本人姓名与免刑/无罪结论时执行。这个限制不能放宽：
    说理段几乎每段都有「被告人X犯A罪」的复述，无差别地抽会把说理里的罪名
    当成判项，而判项与说理的罪名不一致恰恰是再审案件最常见的争点。
    """
    if defendant.crimes or not defendant.name.is_present:
        return
    name = str(defendant.name.value)
    for paragraph in block:
        text = paragraph.text
        if name not in text or _is_revocation_paragraph(text):
            continue
        if not (_EXEMPT_RE.search(text) or _INNOCENT_RE.search(text)):
            continue
        found = _CHARGE_ONLY_RE.search(text)
        if found is None:
            continue
        for charge in _charge_names(found.group(1)):
            crime = Crime(raw_text=text)
            crime.charge = _fv(charge, paragraph, "body_charge@v1")
            defendant.crimes.append(crime)
        return


def _apply_body(defendant: Defendant, block: Sequence[ProseParagraph]) -> None:
    """在该被告人的主文段落里逐项配对「犯X罪，判处Y」，再单独取决定执行刑期。

    三条取舍：

    1. **一副刑期只挂一个罪。**「犯A罪、B罪，判处有期徒刑三年」这种一句判两罪
       的写法，把同一份刑期挂到两个罪上等于编数据，宁可留空由人工补。
    2. **「决定执行」优先。** 数罪并罚的每个罪各有一份刑期，执行通知书要的是
       并罚后的那一份。先取「决定执行」，没有再退回第一项「判处」。
    3. **罪名与刑期同句配对。** 只 search 第一个「判处」，遇到数罪并罚会把
       第一个罪的刑期当作全案刑期。
    """
    if not defendant.name.is_present:
        return

    first: Optional[tuple[ParsedTerm, ProseParagraph]] = None
    decision: Optional[tuple[ParsedTerm, ProseParagraph]] = None

    for paragraph in block:
        text = paragraph.text
        # 撤销缓刑/假释那一项不是本案定罪，整段不参与配对与附加刑挂载。
        # 该段的文本仍会进入 detect_combination（那里用的是整块合并文本）。
        if _is_revocation_paragraph(text):
            continue
        matches = list(_PAIR_RE.finditer(text))

        for position, match in enumerate(matches):
            names = _charge_names(match.group(1))
            if not names:
                continue
            try:
                parsed: Optional[ParsedTerm] = parse_term(match.group(2))
            except TermParseError:
                # 刑期文本异常时仍保留罪名，刑期留空由人工补——不能因为一句话
                # 解析失败就丢掉整个罪名。
                parsed = None

            tail_end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
            tail = text[match.end() : tail_end]
            single_term = len(names) == 1

            for index, name in enumerate(names):
                crime = Crime(raw_text=text)
                crime.charge = _fv(name, paragraph, "body_charge@v1")
                if parsed is not None and single_term:
                    crime.principal_type = _fv(
                        parsed.principal_type.value, paragraph, "body_principal@v1"
                    )
                    if parsed.months is not None:
                        crime.term_months = _fv(parsed.months, paragraph, "body_term@v1")
                if index == 0:
                    _attach_fine(crime, tail, paragraph)
                defendant.crimes.append(crime)

            # 决定执行刑期缺位时的退路。一句里写两罪只判一份刑期
            # （「犯A罪、B罪，判处有期徒刑四年」）时，这一份就是全案刑期——
            # 但要挂到具体罪名上就是编数据，故只用于 merged，不给 crime。
            if first is None and parsed is not None:
                first = (parsed, paragraph)
            _attach_probation(defendant, tail, paragraph)

        for match in _DECISION_RE.finditer(text):
            try:
                parsed_decision = parse_term(match.group(1))
            except TermParseError:
                continue
            decision = (parsed_decision, paragraph)

    chosen = decision or first
    if chosen is None:
        # 免刑与无罪判项不写「判处」，配对正则整条落空，但罪名仍要抽：
        #   「二、原审被告人纪某犯盗窃罪，免予刑事处罚。」
        # 不抽的话报告上就是「缺罪名」，而原文其实写得很清楚。
        _attach_charges_without_term(defendant, block)
        return

    parsed, paragraph = chosen
    extractor = "body_decision@v1" if decision is not None else "body_term@v1"
    defendant.merged_type = _fv(parsed.principal_type.value, paragraph, extractor)
    if parsed.months is not None:
        defendant.merged_months = _fv(parsed.months, paragraph, extractor)
    defendant.merged_days = parsed.days


def _apply_text_span(defendant: Defendant, block: Sequence[ProseParagraph]) -> None:
    """抽判决书载明的刑期起止日（C 组 ``text_start`` / ``text_end``）。

    只抽**主文刑期注释段**里的那一处。取到就停——一名被告人的主文里只应有一处
    载明起止日，第二处要么是前罪、要么是考验期，都不该覆盖本案的。

    抽到的值**只用于校验**（ADR-017）。载明值等于「起算日 + 刑期 − 羁押折抵」，
    折抵天数首部读不出来（指定居所监视居住、看守所外羁押都算），拿推算值去换它
    等于用一条规则盖住另一条规则；但反过来，推算值也可能是对的、载明值才是笔误，
    所以两边都摆出来，交人裁定。
    """
    for paragraph in block:
        text = _verdict_text(paragraph.text)
        if not any(marker in text for marker in _SPAN_MARKERS):
            continue
        for match in _SPAN_RE.finditer(text):
            # 「假释考验期自X起至Y止」与刑期注释段可能同段出现，按上下文排除。
            if _EXAMINATION_PERIOD in text[max(0, match.start() - 8) : match.start()]:
                continue
            start = _to_date(match.group(1), match.group(2), match.group(3))
            end = _to_date(match.group(4), match.group(5), match.group(6))
            if start is None or end is None or end < start:
                continue
            defendant.text_start = _fv(start, paragraph, "body_span@v1")
            defendant.text_end = _fv(end, paragraph, "body_span@v1")
            return


_REASONING_MARKERS = (
    "本院认为",
    "本院再审认为",
    "本院二审认为",
    "本院经审理认为",
    "本院再审审理认为",
)


def _reasoning_paragraphs(
    paragraphs: Sequence[ProseParagraph], anchors: Optional[Anchors]
) -> list[ProseParagraph]:
    """取说理段：最后一个「本院认为」起，到主文锚点之前。

    **不能只看主文区间。** 「本院认为……判决如下：」在有些文书里是一个自然段，
    在另一些里是两个独立段——（2017）苏08刑再2号就是独立段。只从主文区间里找，
    说理段落在首部一侧，永远取不到，``detect_combination`` 的说理兜底就此失效，
    形态一路回落到第 69 条。

    起点取**最后一个**「本院认为」，不取第一个：再审与二审会在引述部分写
    「原审法院认为」「本院一审认为」，从第一个起算，原审的说理也会被当成本案的。
    找不到就不给——宁可不兜底，也不能把首部的前科复述当说理。

    末端含主文锚点那一段。说理与「判决如下」同一个自然段时，这一段既是说理又
    是主文起点，排掉它等于把兜底断掉；（2014）甬余刑初字第633号一类文书全是
    这种合段写法，漏掉的正是最需要兜底的那一批。段内由「判决如下」切开，
    判项部分在 :func:`_reasoning_sentences` 里已经被截掉。
    """
    if anchors is None:
        return []
    limit = min(anchors.body.start + 1, len(paragraphs))
    start: Optional[int] = None
    for index in range(limit):
        if any(marker in paragraphs[index].text for marker in _REASONING_MARKERS):
            start = index
    if start is None:
        return []
    return list(paragraphs[start:limit])


def _reasoning_sentences(block: Sequence[ProseParagraph], name: str) -> str:
    """从说理段里挑出**提到该被告人**的句子。

    「本院认为」段常把几名被告人的情形写在同一段：「被告人宿某建假释考验期限内
    犯新罪，应当撤销假释……被告人张某祥在缓刑考验期限内犯新罪，应当撤销假释」。
    整段拿去做形态判据，段落归属只能给一个人，另一个必然被套上别人的情形。
    按句切、按姓名筛，各人只拿自己那一句。

    ``block`` 里可能混着「依照……之规定，判决如下：」那一段，它前面的内容属判决
    理由，照常参与筛选；这一段之后才是判项，不能算说理。
    """
    if not name:
        return ""
    collected: list[str] = []
    for paragraph in block:
        before = paragraph.text.partition("判决如下")[0]
        for sentence in re.split(r"[。；]", before):
            if name in sentence:
                collected.append(sentence)
    return "。".join(collected)


def _mark_absent_verdicts(
    defendants: Sequence[Defendant],
    body: Sequence[ProseParagraph],
) -> None:
    """标出主文里根本没被提到的被告人。

    再审「维持原判」、二审只改其中一人时，首部照列全部当事人，主文却只对改了
    的那个作出判项。（2017）苏08刑再2号的孙某富即如此：主文三项全在说彭某球，
    对他只有一句「维持……第二、三项」，没有一个「判处」。

    不给这个标记，报告上会连报「罪名缺失」「主刑未识别」「无起算日」——看起来
    像抽取坏了。承办人照着这三条去原文找，找不到，最后会手填一个不存在的新刑期，
    而对这个人的正确处置恰恰是**不出执行通知书**（原判没被撤销，仍按原判执行）。

    判据是「主文里一次都没出现这个名字」，不是「分到的段落为空」：段落归属可能
    因为同姓、指代写法把段落分错，而「整篇主文没提过这个人」是事实层面的。
    """
    texts = [paragraph.text for paragraph in body]
    for defendant in defendants:
        if not defendant.name.is_present:
            continue
        name = str(defendant.name.value)
        if any(name in text for text in texts):
            continue
        defendant.verdict_absent = _fv(True, body[0] if body else None, "body_absent@v1")


def extract_defendants(
    paragraphs: Sequence[ProseParagraph],
    anchors: Optional[Anchors],
) -> list[Defendant]:
    """抽取全部被告人。锚点缺失时返回空列表，由上层决定是否阻断。"""
    if anchors is None:
        return []

    defendants: list[Defendant] = []
    for seq, block in enumerate(split_blocks(paragraphs, anchors.head), start=1):
        defendants.append(_parse_identity(block, seq))

    # 先按主文把段落分给各人，再逐人处理：数罪并罚的续段没有姓名，
    # 逐人全文搜索会把同一段「决定执行」按顺序只给第一个人。
    body = index_body(paragraphs[anchors.body], defendants)
    reasoning = _reasoning_paragraphs(paragraphs, anchors)
    for defendant in defendants:
        block = body.get(defendant.seq, [])
        _apply_body(defendant, block)
        _apply_exemption(defendant, block)
        _apply_text_span(defendant, block)
        defendant.combination = detect_combination(
            "".join(_verdict_text(paragraph.text) for paragraph in block),
            len(defendant.crimes),
            reasoning=_reasoning_sentences(
                reasoning, str(defendant.name.value) if defendant.name.is_present else ""
            ),
        )
    _mark_absent_verdicts(defendants, paragraphs[anchors.body])
    return defendants


def attach_to_case(case: Case, defendants: Sequence[Defendant]) -> None:
    """把被告人挂到案件上，并回填「是否数罪并罚」需要的一致性提示。"""
    case.defendants = list(defendants)


__all__ = ["attach_to_case", "extract_defendants", "index_body", "split_blocks"]
