"""真实生效判决回归：每份样本对应一种会让抽取出错的判决书结构。

样本**全部来自真实生效判决**（案号、法院、日期、主文都是真的，当事人姓名
已由公开渠道化名为「某」）。这不是随手写的示例：每一份都对应一种真实文书里
存在的结构，而且加进来时都曾让某条规则出错——错误记录在 `docs/12` 第 2 节。

为什么不用自编样本
------------------
用自己写的文本测自己的规则，命中率必然是 100%，这个数字拿给业务方看是有害的。
自编样本只能回答「规则被改坏时会不会有人发现」，回答不了「规则在真实文书上
对不对」。2026-11-01 把原先 9 份自编样本换成真实判决，第一次跑就暴露出 8 处
真实缺陷（案号「刑初字第X号」抽空、强制措施「因涉嫌…被拘留」抽不到、
「撤销…缓刑宣告」被当成本案定罪、说理段串人……），见 `docs/12`。

样本不入库
----------
`data/samples/` 在 `.gitignore` 中（SECURITY.md：真实案件材料一律不入库）。
所以本文件的用例在样本缺失时 **skip 而不是 fail**——新克隆的仓库跑出
一片 skip 是预期行为，不是环境坏了。

真值从哪来
----------
`SAMPLES` 里的每个数字都是**回判决书原文抄的**，不是从抽取输出复制的。
两者一旦混同，测试就只是在验证「代码没变」，而不是「代码对」。

断言的写法有意区分**抽取**与**阻断**：抽取只看「有没有认对」，阻断看
「认对之后能不能出文」。两者的失败原因不同，混在一个断言里出问题时
分不清是规则错还是判据错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from cedas.domain.enums import CombinationKind
from cedas.extract import extract
from cedas.extract.combination import detect_combination
from cedas.extract.doc_type import CRIMINAL_CIVIL_JUDGMENT, CRIMINAL_JUDGMENT, detect_doc_kind
from cedas.ingest import read_document
from cedas.ingest.cleaner import clean
from cedas.pipeline import plan, prepare

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "data" / "samples"
EXAMPLES = ROOT / "examples"

# 与刑期计算有关的阻断码。身份信息缺失（E-ID / E-BIRTH / E-GENDER）不在此列：
# 真实判决书公开版本本来就不载身份证号，那不是规则的问题，也不该拿它当断言。
# 用它们当断言，等于要求样本造假。
TERM_BLOCKERS = ("E-START", "E-END", "E-LIFE")


@dataclass(frozen=True)
class Person:
    """一名被告人的预期抽取结果。"""

    name: str
    charges: tuple[str, ...]
    months: int | None
    combination: CombinationKind = CombinationKind.NONE
    probation_months: int | None = None
    # 判决书正文载明的刑期起止日（「即自X日起至Y日止」）。未载明为 None——
    # 实测 53% 的主文不写起止日（见 docs/08），None 和日期一样是断言。
    declared: tuple[date, date] | None = None


@dataclass(frozen=True)
class Sample:
    file: str
    kind: str
    people: tuple[Person, ...]
    # 给出生效日期后仍应阻断的**刑期类**码。空表示这份样本能出文。
    blocked: tuple[str, ...] = field(default_factory=tuple)


SAMPLES: tuple[Sample, ...] = (
    Sample(
        # 一案二人：一人两罪（有期徒刑 + 拘役）数罪并罚，一人一罪。
        # 末段载明起止日，可用于核对「拘役二个月 + 有期徒刑六个月」并罚取有期徒刑。
        # 第二人的注释段与「如不服本判决」挤在同一自然段（上一行以「）」结尾，
        # 清洗层不认为句子结束），锚点不切开这一段，第二个人的起止日就抽不到。
        file="S001__冀0131刑初371号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(
            Person("史某伟", ("盗窃", "危险驾驶"), 6, CombinationKind.MERGE,
                   declared=(date(2024, 9, 19), date(2025, 3, 18))),
            Person("史某国", ("盗窃",), 6,
                   declared=(date(2024, 9, 19), date(2025, 3, 18))),
        ),
    ),
    Sample(
        # 先并后减（刑法 70 条，漏罪）。判据词「已经执行的刑期」只出现在
        # 「本院认为」段，主文只写「与前罪刑罚……并罚」——形态判定必须能
        # 从说理段兜底，否则会退化成第 69 条。
        # 载明的 2020-05-11 是**前罪**的刑期起算日；首部「另查明」里还有一处
        # 「刑期自2020年5月11日起至2021年11月10日止」，那是前罪自己的刑期，
        # 按日期形状取就会覆盖本案这一处。
        file="S002__苏0723刑初53号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(Person("张某", ("盗窃",), 30, CombinationKind.MERGE_THEN_DEDUCT,
                       declared=(date(2020, 5, 11), date(2022, 11, 10))),),
        blocked=("E-START-COMBINED",),
    ),
    Sample(
        # 先减后并（刑法 71 条，又犯新罪）。主文写「连同前罪没有执行的有期徒刑
        # 二年八个月」——是「没有执行的有期徒刑」，不是「没有执行的刑罚」，
        # 差两个字，按固定短语匹配会落到第 69 条去。
        # 载明区间比「起算日 + 刑期 − 1 日」短 22 天，正对应原文那句
        # 「共折抵二十二日」——这是系统算不出来、只能靠人核的部分。
        file="S003__甬余刑初字第633号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(Person("颜某", ("盗窃",), 34, CombinationKind.DEDUCT_THEN_MERGE,
                       declared=(date(2014, 4, 17), date(2017, 1, 25))),),
        blocked=("E-START-COMBINED",),
    ),
    Sample(
        # 同案两名被告人分别对应刑法 86 条（撤销假释）与 77 条（撤销缓刑）。
        # 说理段把两人的情形写在同一句里，段落归属只能给一个人——形态判定
        # 若吃说理段，必有一人拿到不属于他的「撤销缓刑」。
        # 首部「另查明」还写着前罪刑期（2008-06-30～2014-06-29）、假释考验期、
        # 缓刑考验期三处「自…起至…止」，都不是本案的刑期起止。
        file="S004__梁刑初字第12号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(
            Person("宿某建", ("抢劫",), 132, CombinationKind.DEDUCT_THEN_MERGE,
                   declared=(date(2013, 9, 28), date(2024, 9, 27))),
            Person("张某祥", ("抢劫",), 132, CombinationKind.REVOKE_PROBATION,
                   declared=(date(2013, 10, 16), date(2024, 5, 1))),
        ),
        blocked=("E-START-COMBINED",),
    ),
    Sample(
        # 原生无期：没有并罚，阻断原因纯粹是「无期徒刑没有起止日」。
        file="S005__2020云28刑初130号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(Person("李某", ("运输毒品",), None),),
        blocked=("E-LIFE",),
    ),
    Sample(
        # 无期 + 漏罪并罚（刑法 70 条）。此时 E-LIFE 应压过 E-START-COMBINED：
        # 无期案件本来就没有起算日可言，再报一条「请填写起算日」是噪音，
        # 复核的人会被引去做一件无意义的事。
        file="S006__陕01刑初90号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(Person("魏某红", ("故意伤害",), None, CombinationKind.MERGE_THEN_DEDUCT),),
        blocked=("E-LIFE",),
    ),
    Sample(
        # 管制：非羁押主刑，起算日取判决生效日而非羁押日；羁押折抵 1:2。
        file="S007__2020豫1527刑初98号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(Person("余某某", ("盗窃",), 8),),
    ),
    Sample(
        # 拘役，一案三人。主文不载明起止日，起算日取逮捕日，各自折抵天数不同。
        # 三人的注释段连着排、段内都没有姓名，全靠「归属到上一个被告人」接上；
        # 载明区间分别少 5 / 3 / 1 天，正是原文写的「折抵5日 / 3日 / 1日」。
        file="S008__2024冀0481刑初101号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(
            Person("陈某", ("帮助信息网络犯罪活动",), 3,
                   declared=(date(2024, 1, 16), date(2024, 4, 10))),
            Person("刘某", ("帮助信息网络犯罪活动",), 2,
                   declared=(date(2024, 1, 16), date(2024, 3, 12))),
            Person("胡某凯", ("帮助信息网络犯罪活动",), 2,
                   declared=(date(2024, 1, 16), date(2024, 3, 14))),
        ),
    ),
    Sample(
        # 一案三人，全部宣告缓刑。主文写「犯盗窃罪判处有期徒刑二年」——
        # 「罪」与「判处」之间**没有逗号**，逐项配对的正则必须容忍。
        file="S009__2023豫1381刑初1103号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(
            Person("李某华", ("盗窃",), 24, probation_months=24),
            Person("李某文", ("盗窃",), 24, probation_months=24),
            # 「掩饰、隐瞒犯罪所得罪」是一个罪，不是「掩饰」+「隐瞒犯罪所得」。
            Person("金某杰", ("掩饰、隐瞒犯罪所得",), 12, probation_months=12),
        ),
    ),
    Sample(
        # 单位犯罪：被告单位判项不生成执行通知书，且不得并进自然人的罪名表。
        file="S010__2023沪0113刑初923号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(Person("王某", ("虚开增值税专用发票", "虚开发票"), 12, CombinationKind.MERGE,
                       probation_months=12),),
    ),
    Sample(
        # 刑事附带民事：文书类型独立；「责令赔偿…经济损失」不是罚金。
        file="S011__2024渝0117刑初457号.txt",
        kind=CRIMINAL_CIVIL_JUDGMENT,
        people=(Person("阙某云", ("交通肇事",), 18),),
    ),
    Sample(
        # 无期 + 先减后并（「与原犯故意杀人罪未执行的刑罚即无期徒刑」）。
        file="S012__2018豫07刑初35号.txt",
        kind=CRIMINAL_JUDGMENT,
        people=(Person("朱某", ("故意伤害",), None, CombinationKind.DEDUCT_THEN_MERGE),),
        blocked=("E-LIFE",),
    ),
)


def _paragraphs(sample: Sample):
    path = SAMPLES_DIR / sample.file
    if not path.exists():
        pytest.skip(f"缺少真实样本 {sample.file}（data/samples 不入库，见 SECURITY.md）")
    blocks, _ = read_document(path)
    return path, clean(blocks)


def _term_blockers(item) -> set[str]:
    return {
        blocker.split(" ", 1)[0]
        for blocker in item.blockers
        if blocker.startswith(TERM_BLOCKERS)
    }


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.file[:4])
def test_document_kind(sample: Sample):
    """文书类型决定文件名里那一段，判错不会报错但名字会错。"""
    _, paragraphs = _paragraphs(sample)
    assert detect_doc_kind(paragraphs).name == sample.kind


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.file[:4])
def test_defendants(sample: Sample):
    """人数、顺序、罪名、决定执行刑期、并罚形态、缓刑考验期。"""
    _, paragraphs = _paragraphs(sample)
    result = extract(paragraphs)

    assert [d.display_name for d in result.defendants] == [p.name for p in sample.people]

    for defendant, expected in zip(result.defendants, sample.people):
        assert tuple(str(c.charge.value) for c in defendant.crimes) == expected.charges
        assert defendant.combination is expected.combination
        months = defendant.merged_months.value
        assert months == expected.months, f"{expected.name} 的决定执行刑期不对"
        assert defendant.probation.months.value == expected.probation_months
        assert defendant.declared_span == (
            expected.declared if expected.declared else (None, None)
        ), f"{expected.name} 的载明起止日不对"


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.file[:4])
def test_blocking(sample: Sample):
    """给出生效日期后，该阻断的刑期类问题必须阻断，不该阻断的一条都不能有。"""
    path, _ = _paragraphs(sample)
    report = plan(
        prepare(path),
        require_template=False,
        dry_run=True,
        effective_date=date(2024, 2, 1),
    )
    codes = {code for item in report.items for code in _term_blockers(item)}
    assert codes == set(sample.blocked)


def test_combined_start_is_never_guessed():
    """并罚案件的起算日不给就是没给，不得回落到羁押日。

    S004 的张某祥，首部最早的羁押日是 2011-07-29——那是**前案**聚众斗殴的
    拘留日。本案判决书载明的起止是 2013-10-16 至 2024-05-01。程序若「聪明」
    地取最早羁押日，会多算两年近三个月，而文书上看不出任何异常。
    """
    path = SAMPLES_DIR / "S004__梁刑初字第12号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S004")

    report = plan(prepare(path), require_template=False, dry_run=True)
    item = next(i for i in report.items if i.defendant == "张某祥")

    assert item.start is None
    assert item.end is None
    assert item.start_source == ""
    assert any(b.startswith("E-START-COMBINED") for b in item.blockers)


@pytest.mark.parametrize(
    ("file", "name", "start", "end"),
    [
        # 两组的起止日都是判决书原文载明的，不是推算出来的期望值。
        # S004 宿某建：「即从2013年9月28日起至2024年9月27日止」，十一年。
        ("S004__梁刑初字第12号.txt", "宿某建", date(2013, 9, 28), date(2024, 9, 27)),
        # S002 张某：「即自2020年5月11日起至2022年11月10日止」，二年六个月。
        ("S002__苏0723刑初53号.txt", "张某", date(2020, 5, 11), date(2022, 11, 10)),
    ],
)
def test_combined_start_by_hand(file: str, name: str, start: date, end: date):
    """人工指定起算日后能出文，且届满日按「起算日 + 刑期 − 1 日」，与原文吻合。"""
    path = SAMPLES_DIR / file
    if not path.exists():
        pytest.skip(f"缺少真实样本 {file}")

    report = plan(
        prepare(path),
        require_template=False,
        dry_run=True,
        start_date=start,
    )
    item = next(i for i in report.items if i.defendant == name)

    assert _term_blockers(item) == set()
    assert item.start == start
    assert item.end == end
    assert any("W-START-MANUAL" in warning for warning in item.warnings)


def test_declared_span_is_never_used_as_value():
    """载明起止日只作校验，不进 span（ADR-017）。

    起算日没给就是没给：不能因为原文写了「即自2014年4月17日起」就把起算日填上，
    也不能因为载明的止日更短就把届满日改短。两件事都做过一次，就再也分不清
    「文书上的日期」是抄的还是算的。
    """
    path = SAMPLES_DIR / "S003__甬余刑初字第633号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S003")

    report = plan(prepare(path), require_template=False, dry_run=True)
    item = report.items[0]

    assert item.start is None and item.end is None
    assert (item.text_start, item.text_end) == (date(2014, 4, 17), date(2017, 1, 25))
    # 载明的日子要出现在阻断提示里供人誊抄——拦住了却不给日期，等于让人回原文翻。
    blocker = next(b for b in item.blockers if b.startswith("E-START-COMBINED"))
    assert "2014-04-17" in blocker and "2017-01-25" in blocker


def test_declared_offset_is_reported_in_days():
    """载明区间与刑期差几天要报出来，差值就是折抵天数。

    S003 原文写「共折抵二十二日」，系统算出 22 天——对得上，说明校验本身没错；
    S008 三人的「折抵5日 / 3日 / 1日」同理。这几个数看得见，承办人才知道
    「只是折抵，不是抽错了」。
    """
    path = SAMPLES_DIR / "S003__甬余刑初字第633号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S003")

    item = plan(prepare(path), require_template=False, dry_run=True).items[0]
    assert any(w.startswith("V2") and "短 22 天" in w for w in item.warnings)


def test_declared_end_mismatch_is_reported():
    """起算日算得出、止日对不上时，必须报 V1 并写明差几天。"""
    path = SAMPLES_DIR / "S008__2024冀0481刑初101号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S008")

    report = plan(prepare(path), require_template=False, dry_run=True)
    gaps = {}
    for item in report.items:
        assert item.consistency == "不一致"
        warning = next(w for w in item.warnings if w.startswith("V1"))
        gaps[item.defendant] = warning

    assert "晚 5 天" in gaps["陈某"]
    assert "晚 3 天" in gaps["刘某"]
    assert "晚 1 天" in gaps["胡某凯"]


def test_matching_declared_span_raises_no_warning():
    """算出来与原文一致时不许有 V1/V2——校验恒报警，等于没有校验。"""
    path = SAMPLES_DIR / "S001__冀0131刑初371号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S001")

    report = plan(
        prepare(path),
        require_template=False,
        dry_run=True,
        effective_date=date(2025, 2, 1),
    )
    for item in report.items:
        assert item.consistency == "一致"
        assert not [w for w in item.warnings if w[:2] in ("V1", "V2")]


def test_control_offsets_are_flagged():
    """管制要按羁押 1 日折抵 2 日，而羁押天数系统不猜，必须提示人工填。

    漏掉这条提示，管制案件的届满日会整体偏后（S007 实际羁押 14 天，
    应折抵 28 天），而文书上完全看不出来。
    """
    path = SAMPLES_DIR / "S007__2020豫1527刑初98号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S007")

    report = plan(
        prepare(path),
        require_template=False,
        dry_run=True,
        effective_date=date(2020, 12, 8),
    )
    item = report.items[0]

    assert item.start == date(2020, 12, 8), "管制起算日应取生效日期，不是羁押日"
    assert any("W-CONTROL-OFFSET" in warning for warning in item.warnings)


def test_unit_crime_is_reported():
    """单位犯罪要提示，否则复核的人会以为漏抽了一个被告人。"""
    path = SAMPLES_DIR / "S010__2023沪0113刑初923号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S010")

    blocks, _ = read_document(path)
    result = extract(clean(blocks))

    assert any(warning.code == "A006" for warning in result.warnings)


def test_units_do_not_leak_into_natural_person():
    """被告单位的判项不得并进自然人罪名表。

    S010 的「一、被告单位某某公司1犯虚开增值税专用发票罪……决定执行罚金五万元」
    既没有自然人姓名，又含「决定执行」，曾被判成续段抵到被告人王某名下，
    两个罪名抽成四个。
    """
    path = SAMPLES_DIR / "S010__2023沪0113刑初923号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S010")

    blocks, _ = read_document(path)
    result = extract(clean(blocks))

    assert len(result.defendants) == 1
    assert len(result.defendants[0].crimes) == 2


def test_revocation_paragraph_is_not_a_conviction():
    """「撤销…缓刑宣告」那一项是被撤销的前罪，不是本案定罪。

    S004 主文第三项写「撤销……对被告人张某祥犯聚众斗殴罪，判处有期徒刑
    一年六个月缓刑二年的缓刑宣告」。若照常配对，张某祥会多出「聚众斗殴」
    一罪，并罚形态也会因为罪名数变成 2 而被判成第 69 条。
    """
    path = SAMPLES_DIR / "S004__梁刑初字第12号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S004")

    blocks, _ = read_document(path)
    result = extract(clean(blocks))
    zhang = next(d for d in result.defendants if d.display_name == "张某祥")

    assert tuple(str(c.charge.value) for c in zhang.crimes) == ("抢劫",)
    assert zhang.combination is CombinationKind.REVOKE_PROBATION


def test_measure_after_case_clause():
    """「2013年9月28日因涉嫌抢劫罪被……刑事拘留」——日期与「被」之间夹着案由。

    要求「日」后紧跟「被」的正则会把这一整串漏掉，羁押起始日缺失，
    起算日与届满日随之整条断掉。
    """
    path = SAMPLES_DIR / "S004__梁刑初字第12号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S004")

    blocks, _ = read_document(path)
    result = extract(clean(blocks))
    su = next(d for d in result.defendants if d.display_name == "宿某建")

    assert su.custody_start == date(2013, 9, 28)


def test_year_is_inherited_from_non_measure_date():
    """「于2020年7月8日被抓获，同年7月10日被……刑事拘留」——年份来自「抓获」那句。

    「抓获」不是法定强制措施，不进措施正则；若只在措施句里取年份，
    紧随其后的「同年7月10日」会因年份未知被整段跳过。
    """
    path = SAMPLES_DIR / "S007__2020豫1527刑初98号.txt"
    if not path.exists():
        pytest.skip("缺少真实样本 S007")

    blocks, _ = read_document(path)
    result = extract(clean(blocks))
    custody = result.defendants[0].custody

    kinds = [(seg.type.value, seg.start) for seg in custody]
    assert ("刑事拘留", date(2020, 7, 10)) in kinds


@pytest.mark.parametrize(
    ("text", "crimes", "expected"),
    [
        ("被告人张三犯盗窃罪，判处有期徒刑三年。", 1, CombinationKind.NONE),
        (
            "一、被告人张三犯盗窃罪，判处有期徒刑三年。二、数罪并罚，决定执行有期徒刑五年。",
            2,
            CombinationKind.MERGE,
        ),
        # 第 70 条：说理段才写得出「已经执行的刑期」
        ("已经执行的刑期，计算在本判决决定的刑期以内。", 1, CombinationKind.MERGE_THEN_DEDUCT),
        ("与前罪所判处的有期徒刑三年并罚，决定执行有期徒刑六年。", 1,
         CombinationKind.MERGE_THEN_DEDUCT),
        # 第 71 条
        ("与前罪未执行完毕的有期徒刑三年六个月并罚，决定执行有期徒刑六年。", 1,
         CombinationKind.DEDUCT_THEN_MERGE),
        ("连同前罪没有执行的有期徒刑二年八个月，决定执行有期徒刑二年十个月。", 1,
         CombinationKind.DEDUCT_THEN_MERGE),
        # 第 77 条：真实文书写「缓刑宣告」，不是「宣告缓刑」
        ("撤销本院（2022）京0105刑初111号刑事判决中对被告人宣告缓刑三年的执行部分。",
         1, CombinationKind.REVOKE_PROBATION),
        ("撤销……刑事判决书对被告人张某祥犯聚众斗殴罪，判处有期徒刑一年六个月"
         "缓刑二年的缓刑宣告。", 1, CombinationKind.REVOKE_PROBATION),
        # 撤销假释裁定书本身不含「假释」二字，靠「未执行」落第 71 条
        ("一、撤销山东省淄博市中级人民法院（2013）淄刑执字第841号刑事裁定书。"
         "二、被告人宿某建犯抢劫罪，判处有期徒刑十年六个月；与前罪未执行的刑罚并罚，"
         "决定执行有期徒刑十一年。", 1, CombinationKind.DEDUCT_THEN_MERGE),
    ],
)
def test_detect_combination(text: str, crimes: int, expected: CombinationKind):
    """形态判定的优先级：撤销缓刑/假释先于「前罪所判处」。"""
    assert detect_combination(text, crimes) is expected


def test_reasoning_only_when_verdict_is_silent():
    """主文说清楚了就不看说理；主文说不清楚才用说理兜底。

    两者混着用会把别人的情形套到这个人头上：S004 的说理段同时写
    「宿某建撤销假释」与「张某祥撤销缓刑」。
    """
    verdict = "被告人甲犯抢劫罪，判处有期徒刑十年；与前罪未执行的刑罚并罚，决定执行十一年。"
    reasoning = "被告人乙在缓刑考验期限内犯新罪，应当撤销缓刑。"

    assert detect_combination(verdict, 1, reasoning) is CombinationKind.DEDUCT_THEN_MERGE
    # 主文只有「并罚」「决定执行」，说不清是哪一种 → 才轮到说理
    assert (
        detect_combination("与前罪并罚，决定执行十一年。", 1, "已经执行的刑期计入新刑期。")
        is CombinationKind.MERGE_THEN_DEDUCT
    )


def test_sample_files_are_named_by_case_number():
    """样本文件名带真实案号，方便在文件夹里认出是哪一份。"""
    for sample in SAMPLES:
        path = SAMPLES_DIR / sample.file
        if not path.exists():
            continue
        assert "__" in sample.file and sample.file.endswith(".txt")


def test_baseline_example_still_runs():
    """`examples/` 仍保留一份合成基线，用于跑通命令与 PDF 双通道回归。

    它**不**参与命中率统计（docs/04 §1.6）：合成样本只能回答「规则被改坏时
    会不会有人发现」。结构覆盖已经全部由 `data/samples/` 的真实判决承担。
    """
    path = EXAMPLES / "S000__京0105刑初123号.txt"
    assert path.exists(), "命令演示与 PDF 通道回归依赖这份合成基线"

    blocks, _ = read_document(path)
    result = extract(clean(blocks))

    assert [d.display_name for d in result.defendants] == ["张三"]
    assert result.defendants[0].merged_months.value == 42


def _fingerprint(path: Path) -> tuple:
    """抽取结果的指纹：段落数、罪名、决定执行刑期、告警。"""
    blocks, _ = read_document(path)
    result = extract(clean(blocks))
    return (
        len(result.paragraphs),
        tuple(
            (tuple(str(c.charge.value) for c in d.crimes), d.merged_type.value, d.merged_months.value)
            for d in result.defendants
        ),
        tuple(str(w) for w in result.warnings),
    )


def test_pdf_channel_matches_txt():
    """有 PDF 版本的样本，两条通道抽出来的东西必须一样。

    PDF 通道多了字号与坐标，清洗层走的是另一条页眉判定分支。条款不同、
    结果就必须相同——否则「开发用 TXT、上线用 PDF」的两套表现迟早对不上。
    """
    pairs = [
        (txt, txt.with_suffix(".pdf"))
        for txt in sorted(EXAMPLES.glob("S*.txt"))
        if txt.with_suffix(".pdf").exists()
    ]
    assert pairs, "至少要有一份 PDF 版本用于双通道对照"

    for txt, pdf in pairs:
        assert _fingerprint(pdf) == _fingerprint(txt), f"{txt.name} 的两条通道结果不一致"
