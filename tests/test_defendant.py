"""B 组抽取（被告人身份、罪名、刑期）测试。

全部用合成文本，句式照最高法刑事判决书样式。断言的重点不是"能抽到"，
而是三条容易出错的边界：跨段年份沿用、多被告人切分、抽不到时不猜。
"""

from __future__ import annotations

from datetime import date

from cedas.domain.enums import CombinationKind, CustodyType, PrincipalType
from cedas.extract.extractor import extract
from cedas.ingest.cleaner import clean_text

HEAD = (
    "被告人张三，男，1985年3月2日出生，公民身份号码110101198503021234，"
    "汉族，初中文化，住北京市朝阳区某路1号。"
)

SINGLE = f"""北京市朝阳区人民法院
刑事判决书
（2023）京0105刑初123号
{HEAD}
因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留，同年3月14日被逮捕。
现羁押于北京市朝阳区看守所。
本院认为，被告人张三以非法占有为目的，秘密窃取他人财物，数额较大。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑三年六个月，并处罚金人民币五千元。
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""


def _extract(text: str):
    return extract(clean_text(text))


def test_identity_fields_are_extracted():
    defendant = _extract(SINGLE).defendants[0]

    assert defendant.name.value == "张三"
    assert defendant.gender.value == "男"
    assert defendant.birth_date.value == date(1985, 3, 2)
    assert defendant.id_number.value == "110101198503021234"
    assert defendant.ethnicity.value == "汉"
    assert defendant.education.value == "初中"
    assert defendant.domicile.value == "北京市朝阳区某路1号"
    assert defendant.detention_place.value == "北京市朝阳区看守所"


def test_identity_fields_carry_provenance():
    """溯源必须指回原文段落，否则复核界面无法回跳。"""
    defendant = _extract(SINGLE).defendants[0]

    assert defendant.name.provenance is not None
    assert "被告人张三" in defendant.name.provenance.snippet
    assert defendant.detention_place.provenance.paragraph_index != defendant.name.provenance.paragraph_index


def test_same_year_is_carried_across_measures():
    """「同年3月14日被逮捕」省略年份，必须沿用上一段的年份。"""
    defendant = _extract(SINGLE).defendants[0]

    assert [seg.type for seg in defendant.custody] == [CustodyType.DETENTION, CustodyType.ARREST]
    assert defendant.custody[1].start == date(2023, 3, 14)
    assert defendant.custody_start == date(2023, 3, 1)


def test_charge_and_term_are_parsed():
    crime = _extract(SINGLE).defendants[0].crimes[0]

    assert crime.charge.value == "盗窃"
    assert crime.principal_type.value == PrincipalType.FIXED_TERM.value
    assert crime.term_months.value == 42, "三年六个月应折算 42 个月"


def test_fine_in_chinese_numerals_is_converted():
    crime = _extract(SINGLE).defendants[0].crimes[0]

    assert crime.additional
    assert crime.additional[0].fine_amount == 5000, "「五千元」应折算为 5000 元"


def test_span_is_computed_from_custody_start():
    """刑期届满日由 calc 计算：起算日 + 42 个月 − 1 日。"""
    defendant = _extract(SINGLE).defendants[0]

    assert defendant.span.start == date(2023, 3, 1)
    assert defendant.span.end == date(2026, 8, 31)
    assert defendant.span.months == 42


def test_multiple_defendants_are_split():
    """两名被告人的首部跨多段，必须按「被告人X，」切开，不能合成一人。"""
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留。
现羁押于北京市朝阳区看守所。
被告人李四，女，1990年7月8日出生，汉族，住北京市海淀区某街2号。
因涉嫌犯诈骗罪，于2023年2月1日被刑事拘留。
本院认为，二人的行为均已构成犯罪。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑三年。
二、被告人李四犯诈骗罪，判处有期徒刑二年。
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendants = _extract(text).defendants

    assert [d.name.value for d in defendants] == ["张三", "李四"]
    assert defendants[1].gender.value == "女"
    assert defendants[0].crimes[0].charge.value == "盗窃"
    assert defendants[1].crimes[0].charge.value == "诈骗"
    assert defendants[1].crimes[0].term_months.value == 24


def test_multiple_charges_in_one_sentence():
    """「犯盗窃罪、诈骗罪」应拆成两个 Crime，并标记为数罪。"""
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
现羁押于北京市朝阳区看守所。
本院认为，其行为已构成犯罪。
判决如下：
一、被告人张三犯盗窃罪、诈骗罪，判处有期徒刑四年。
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendant = _extract(text).defendants[0]

    assert [c.charge.value for c in defendant.crimes] == ["盗窃", "诈骗"]
    assert defendant.is_combined is True
    assert defendant.merged_months.value == 48


def test_missing_field_is_not_guessed():
    """没有身份证号就留空，不得用出生日期拼一个。"""
    text = SINGLE.replace("公民身份号码110101198503021234，", "")
    defendant = _extract(text).defendants[0]

    assert defendant.id_number.is_present is False
    assert defendant.name.is_present is True


def test_no_defendant_produces_warning():
    """首部没有「被告人X，」句式时，返回空列表并告警，不抛异常。"""
    text = """北京市朝阳区人民法院
刑事判决书
公诉机关指控被告人犯罪。
判决如下：
一、判处有期徒刑三年。
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    result = _extract(text)

    assert result.defendants == []
    assert any(w.code == "A004" for w in result.warnings)


def test_to_dict_serializes_defendants_with_iso_dates():
    payload = _extract(SINGLE).to_dict()

    assert len(payload["defendants"]) == 1
    defendant = payload["defendants"][0]
    assert defendant["birth_date"]["value"] == "1985-03-02"
    assert defendant["span"]["end"] == "2026-08-31"
    assert defendant["crimes"][0]["additional"][0]["fine_amount"] == 5000


# --- C 组：判决书载明的刑期起止日 ----------------------------------------------

DECLARED_TAIL = "（刑期从判决执行之日起计算；判决执行以前先行羁押的，羁押一日折抵刑期一日，即自{start}起至{end}止。罚金已缴纳）"


def test_declared_span_is_extracted_from_verdict_note():
    """「即自X日起至Y日止」要抽出来，并指回原文段落。"""
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留，同年3月14日被逮捕。
本院认为，被告人张三以非法占有为目的，窃取他人财物。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑六个月，并处罚金人民币五千元。
{DECLARED_TAIL.format(start="2023年3月1日", end="2023年8月31日")}
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendant = _extract(text).defendants[0]

    assert defendant.declared_span == (date(2023, 3, 1), date(2023, 8, 31))
    assert defendant.text_start.provenance is not None
    check = defendant.text_start.provenance
    assert "即自2023年3月1日起" in check.snippet


def test_declared_span_ignores_prior_sentence_and_examination_periods():
    """同一份判决书里「自…起至…止」还有三种别的意思，都不能当本案刑期。

    - 首部「另查明」写前罪的刑期起止（这一段往往还有前罪的起算日）；
    - 「假释考验期自X起至Y止」「缓刑考验期自X起至Y止」；
    - 本案主文的「即自X起至Y止」。

    按日期形状取，前罪那一处会静默覆盖本案的，而文书上看不出任何异常。
    """
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留。
另查明，被告人张三曾因犯盗窃罪，于2015年6月1日被判处有期徒刑一年，刑期自2014年12月1日起至2015年11月30日止，缓刑考验期自2015年6月12日起至2017年6月11日止。
本院认为，被告人张三构成盗窃罪。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑六个月，并处罚金人民币五千元。
{DECLARED_TAIL.format(start="2023年3月1日", end="2023年8月31日")}
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendant = _extract(text).defendants[0]

    assert defendant.declared_span == (date(2023, 3, 1), date(2023, 8, 31))


def test_declared_span_of_last_defendant_survives_merged_appeal_clause():
    """最后一名被告人的注释段与「如不服本判决」拼成一段时，仍要抽到。

    清洗层按「上一行有没有句末标点」决定拼不拼接，注释段以「）」结尾不算句末
    标点，于是这一段后半是上诉告知、前半是刑期注释。按段落归属只能二选一：
    整段划给落款，第二个人的起止日就永远抽不到，且不会有任何报错。
    """
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留。
被告人李四，女，1990年7月8日出生，汉族，住北京市海淀区某街2号。
因涉嫌犯诈骗罪，于2023年2月1日被刑事拘留。
本院认为，二人的行为均已构成犯罪。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑六个月。
（刑期从判决执行之日起计算；判决执行以前先行羁押的，羁押一日折抵刑期一日，即自2023年3月1日起至2023年8月31日止）
二、被告人李四犯诈骗罪，判处有期徒刑二年。
（刑期从判决执行之日起计算；判决执行以前先行羁押的，羁押一日折抵刑期一日，即自2023年2月1日起至2025年1月31日止）
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendants = _extract(text).defendants

    assert defendants[0].declared_span == (date(2023, 3, 1), date(2023, 8, 31))
    assert defendants[1].declared_span == (date(2023, 2, 1), date(2025, 1, 31))


def test_examination_period_alone_is_not_a_declared_span():
    """只有考验期的判决书，不得把考验期当成刑期起止。"""
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
本院认为，被告人张三构成盗窃罪。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑二年，缓刑二年（缓刑考验期自2023年3月1日起至2025年2月28日止）。
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendant = _extract(text).defendants[0]

    assert defendant.declared_span == (None, None)
    assert defendant.probation.months.value == 24


def test_mismatch_between_declared_and_computed_is_reported_at_extract():
    """抽取阶段就要报不一致，别等到渲染前最后一步。

    `cedas extract --json` 是采集样本、排查缺陷时唯一的观察窗口；
    校验只放在 render 里，等于把问题藏到最后一公里。
    """
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留，同年3月14日被逮捕。
本院认为，被告人张三构成盗窃罪。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑六个月，并处罚金人民币五千元。
{DECLARED_TAIL.format(start="2023年3月1日", end="2023年8月26日")}
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    result = _extract(text)

    assert result.defendants[0].span.consistency.value == "不一致"
    assert any(w.code == "V1" and "晚 5 天" in w.message for w in result.warnings)


def test_reasoning_in_its_own_paragraph_still_breaks_ties():
    """说理段独立成段时，形态判定仍然拿得到它。

    「本院认为……判决如下：」在有的文书里是一个自然段，在有的文书里是两个独立段。
    只从主文区间里找说理，后者永远取不到——兜底失效，形态一路回落到第 69 条，
    而这份判决其实是第 70 条（漏罪），起算日口径完全不同。
    """
    text = f"""北京市朝阳区人民法院
刑事判决书
{HEAD}
因涉嫌犯盗窃罪，于2023年3月1日被刑事拘留，同年3月14日被逮捕。
本院认为，被告人张三在刑罚执行完毕以前，发现判决宣告以前还有其他罪没有判决，
已经执行的刑期应当计算在新判决决定的刑期以内。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑一年六个月；与前罪并罚，决定执行有期徒刑六年。
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendant = _extract(text).defendants[0]

    assert defendant.combination is CombinationKind.MERGE_THEN_DEDUCT


def test_absent_verdict_is_a_conclusion_not_a_missing_field():
    """主文没对某人作出判项，不能报成一串「抽漏了」。

    再审维持原判、二审只改其中一人时，首部照列全部当事人，主文只写改了的那一个。
    报「罪名缺失 / 主刑未识别 / 无起算日」，承办人会回原文找一个并不存在的刑期，
    最后手填一个——而正确处置是这个人不出执行通知书。
    """
    text = """北京市朝阳区人民法院
刑事判决书
（2023）京0105刑初123号
被告人张三，男，1985年3月2日出生，公民身份号码110101198503021234，汉族。
被告人李四，男，1984年4月3日出生，公民身份号码110101198404031234，汉族。
本院认为，被告人张三、李四均构成盗窃罪。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑一年，并处罚金人民币三千元。
如不服本判决，可在接到判决书的第二日起十日内提出上诉。
"""
    defendants = {d.name.value: d for d in _extract(text).defendants}

    assert defendants["张三"].verdict_absent.is_present is False
    assert defendants["李四"].verdict_absent.is_present is True


def test_appellate_caption_with_alias_is_recognised():
    """二审首部写「上诉人（原审被告人）张三（绰号白宾）。」。

    只认段首「被告人」三个字，整份二审判决书会抽不到一个人——（2011）汴刑终字
    第28号 七名被告人只剩一个「原审被告人」写法的那位。姓名后面也不一定是逗号，
    绰号括号会把它顶掉，终止符必须容得下左括号。
    """
    text = """河南省开封市中级人民法院
刑事判决书
（2011）汴刑终字第28号
上诉人（原审被告人）张三（绰号白宾）。
上诉人（原审被告人）李四。
原审被告人王五。
本院认为，上诉人张三、李四及原审被告人王五均构成故意伤害罪。
判决如下：
一、被告人张三犯故意伤害罪，判处有期徒刑四年，决定执行有期徒刑四年。
二、被告人李四犯故意伤害罪，判处有期徒刑三年，决定执行有期徒刑三年。
三、被告人王五犯故意伤害罪，判处有期徒刑二年，决定执行有期徒刑二年。
本判决为终审判决。
"""
    names = [d.name.value for d in _extract(text).defendants]

    assert names == ["张三", "李四", "王五"]


def test_shared_span_paragraph_is_split_by_name():
    """七个人的起止日挤在同一个括号里，得按姓名分回去。

    这一段只归属最后一名被告人。按「取第一个匹配」，最后那位会拿到第一个人的
    十一年；按段落归属，其余六人一个都取不到。载明值只用于校验（ADR-017），
    校验拿错数据比没有数据更糟——它会把一个正确的推算值标成不一致。
    """
    text = """北京市朝阳区人民法院
刑事判决书
（2023）京0105刑初123号
被告人张三，男，1985年3月2日出生，公民身份号码110101198503021234，汉族。
被告人李四，男，1986年4月3日出生，公民身份号码110101198604031234，汉族。
本院认为，被告人张三、李四均构成盗窃罪。
判决如下：
一、被告人张三犯盗窃罪，判处有期徒刑十一年。
二、被告人李四犯盗窃罪，判处有期徒刑六年。
（刑期从判决执行之日起计算。判决执行以前先行羁押的，羁押一日折抵刑期一日，
即张三自2010年2月27日起至2021年2月26日止；李四自2010年2月27日起至2016年2月26日止）。
本判决为终审判决。
"""
    defendants = {d.name.value: d for d in _extract(text).defendants}

    assert defendants["张三"].declared_span == (date(2010, 2, 27), date(2021, 2, 26))
    assert defendants["李四"].declared_span == (date(2010, 2, 27), date(2016, 2, 26))


def test_name_only_in_span_note_is_still_an_absent_verdict():
    """姓名只在刑期注释段里出现，不等于主文对他有判项。

    二审维持原判的人，主文只有一句「维持……第五、六、七项」，但末尾的注释段
    会把全案当事人的姓名连着日期列一遍。按「姓名零出现」判，这三人全都漏标，
    报告上又是一串看起来像抽漏的码。
    """
    text = """北京市朝阳区人民法院
刑事判决书
（2023）京0105刑初123号
被告人张三，男，1985年3月2日出生，公民身份号码110101198503021234，汉族。
被告人李四，男，1986年4月3日出生，公民身份号码110101198604031234，汉族。
本院认为，被告人张三、李四均构成盗窃罪。
判决如下：
一、维持本院（2023）京0105刑初100号刑事判决的第三项；第一项中对被告人张三
的定罪部分及量刑部分。
二、被告人张三犯盗窃罪，判处有期徒刑十一年。
（刑期从判决执行之日起计算。即张三自2010年2月27日起至2021年2月26日止；
李四自2010年2月27日起至2016年2月26日止）。
本判决为终审判决。
"""
    defendants = {d.name.value: d for d in _extract(text).defendants}

    assert defendants["张三"].verdict_absent.is_present is False
    assert defendants["李四"].verdict_absent.is_present is True
