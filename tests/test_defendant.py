"""B 组抽取（被告人身份、罪名、刑期）测试。

全部用合成文本，句式照最高法刑事判决书样式。断言的重点不是"能抽到"，
而是三条容易出错的边界：跨段年份沿用、多被告人切分、抽不到时不猜。
"""

from __future__ import annotations

from datetime import date

from cedas.domain.enums import CustodyType, PrincipalType
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
