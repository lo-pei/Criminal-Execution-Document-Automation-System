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
