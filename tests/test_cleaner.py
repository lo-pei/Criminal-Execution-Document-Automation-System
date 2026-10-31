"""清洗层测试。

重点覆盖三类会导致下游全面失效的行为：软换行拼接、日期跨行、噪声剔除。
"""

from __future__ import annotations

import pytest

from cedas.domain.enums import ParagraphKind
from cedas.ingest.base import RawBlock
from cedas.ingest.cleaner import clean, clean_text, normalize


def test_normalize_converts_fullwidth_digits():
    assert normalize("１９８５年") == "1985年"


def test_normalize_variant_year_to_digits():
    assert normalize("二〇二三年五月一日") == "2023年五月一日"
    assert normalize("二○二二年三月") == "2022年三月"


def test_normalize_removes_spaces_between_chinese():
    assert normalize("被 告 人 张 三") == "被告人张三"


def test_normalize_keeps_chinese_punctuation():
    """全角转半角只针对数字与字母——把「，」转成「,」会破坏断句。"""
    assert "，" in normalize("被告人张三，男")


def test_soft_wrap_is_joined():
    paragraphs = clean_text("被告人张三，男，1985年3月2日出生，公民身份号码\n110101198503021234，汉族。")
    assert len(paragraphs) == 1
    assert "公民身份号码110101198503021234" in paragraphs[0].text


def test_date_split_across_lines_is_joined():
    paragraphs = clean_text("判处有期徒刑三年，刑期自2023年\n1月1日起至2026年\n12月31日止。")
    assert len(paragraphs) == 1
    assert "2023年1月1日起" in paragraphs[0].text
    assert "2026年12月31日止" in paragraphs[0].text


def test_sentence_boundary_keeps_new_paragraph():
    paragraphs = clean_text("本院认为，其行为已构成盗窃罪。\n判决如下：")
    assert len(paragraphs) == 2


def test_page_number_is_dropped():
    paragraphs = clean_text("正文第一段。\n- 3 -\n正文第二段。")
    assert len(paragraphs) == 2
    assert all("3" not in p.text for p in paragraphs)


def test_page_label_is_dropped():
    paragraphs = clean_text("正文第一段。\n第 3 页 / 共 5 页\n正文第二段。")
    assert len(paragraphs) == 2


def test_repeated_running_head_is_dropped():
    text = "\n".join(["内部资料 注意保密", "正文一。", "内部资料 注意保密", "正文二。", "内部资料 注意保密", "正文三。"])
    paragraphs = clean_text(text)
    assert [p.text for p in paragraphs] == ["正文一。", "正文二。", "正文三。"]


def test_section_marker_starts_new_paragraph():
    text = "判决如下：\n一、被告人张三犯盗窃罪，判处有期徒刑三年。\n二、责令退赔人民币三千元。"
    paragraphs = clean_text(text)
    assert len(paragraphs) == 3
    assert paragraphs[1].kind is ParagraphKind.SECTION


def test_case_no_paragraph_is_classified():
    paragraphs = clean_text("（2023）京0105刑初123号\n被告人张三，男。")
    assert paragraphs[0].kind is ParagraphKind.CASE_NO


def test_txt_channel_has_no_page_numbers():
    blocks = [RawBlock(text="正文。")]
    paragraphs = clean(blocks)
    assert paragraphs[0].page is None


def test_pdf_channel_keeps_page_numbers():
    blocks = [RawBlock(text="正文。", page=2, font_size=12.0)]
    paragraphs = clean(blocks)
    assert paragraphs[0].page == 2


def test_heading_by_font_size():
    blocks = [
        RawBlock(text="刑事判决书", page=1, font_size=22.0),
        RawBlock(text="正文内容较长的一段文字，用于确定正文字号的中位数。", page=1, font_size=12.0),
    ]
    paragraphs = clean(blocks)
    assert paragraphs[0].kind is ParagraphKind.HEADING


def test_sample_paragraphs_are_reasonable(sample_blocks):
    paragraphs = clean(sample_blocks)
    texts = [p.text for p in paragraphs]

    assert any(t == "刑事判决书" for t in texts)
    assert any(t.startswith("（2023）京0105刑初123号") for t in texts)
    assert any("判决如下" in t for t in texts)
    assert any(t.startswith("一、被告人张三犯盗窃罪") for t in texts)
    assert any(t.startswith("2023年") for t in texts), "落款年份应被规范化为阿拉伯数字"
    assert not any("二〇二三" in t for t in texts)


def test_signature_line_stands_alone():
    """落款行与日期不得被软换行拼成一段。"""
    paragraphs = clean_text("审判长李四\n二〇二三年五月一日\n书记员赵六")
    assert [p.text for p in paragraphs] == ["审判长李四", "2023年五月一日", "书记员赵六"]
    assert all(p.kind is ParagraphKind.SIGNATURE for p in paragraphs)


def test_empty_input_returns_empty_list():
    assert clean([]) == []


@pytest.mark.parametrize("raw", ["", "   ", "\n\n\n"])
def test_blank_input_is_safe(raw):
    assert clean_text(raw) == []


# ---------------------------------------------------------------------------
# 页眉判定：PDF 通道用「页面位置 + 字号」，TXT 通道退回跨页重复计数
# ---------------------------------------------------------------------------

PAGE_HEIGHT = 842.0


def _layout_block(text, page, size, y):
    """构造带版式信息的块。y 是行的上沿，按 A4 页高校验相对位置。"""
    return RawBlock(
        text=text,
        page=page,
        font_size=size,
        bbox=(72.0, y, 500.0, y + size),
        page_height=PAGE_HEIGHT,
    )


def test_small_font_running_header_is_dropped_on_every_page():
    """页眉字号小于正文时，首页与次页的页眉都要删。"""
    blocks = [
        _layout_block("北京市朝阳区人民法院 刑事判决书", 1, 9.0, 40.0),
        _layout_block("北京市朝阳区人民法院", 1, 22.0, 90.0),
        _layout_block("公诉机关北京市朝阳区人民检察院。", 1, 14.0, 200.0),
        _layout_block("北京市朝阳区人民法院 刑事判决书", 2, 9.0, 40.0),
        _layout_block("本院认为，其行为已构成盗窃罪。", 2, 14.0, 200.0),
    ]
    texts = [p.text for p in clean(blocks)]

    assert "北京市朝阳区人民法院刑事判决书" not in texts
    assert "北京市朝阳区人民法院" in texts, "22pt 大标题在顶部，但不是页眉，必须保留"
    assert len(texts) == 3


def test_body_font_header_only_dropped_after_first_page():
    """页眉字号与正文相同时，靠跨页重复识别；首页那次保留，避免误删标题。"""
    blocks = [
        _layout_block("某某人民法院", 1, 14.0, 40.0),
        _layout_block("公诉机关某某人民检察院。", 1, 14.0, 200.0),
        _layout_block("某某人民法院", 2, 14.0, 40.0),
        _layout_block("本院认为，其行为已构成盗窃罪。", 2, 14.0, 200.0),
    ]
    texts = [p.text for p in clean(blocks)]

    assert texts.count("某某人民法院") == 1
    assert len(texts) == 3


def test_single_page_small_header_is_also_dropped():
    """单页文书的小字页眉同样删掉——字号这一条线索不依赖页数。"""
    blocks = [
        _layout_block("北京市朝阳区人民法院 刑事判决书", 1, 9.0, 40.0),
        _layout_block("公诉机关北京市朝阳区人民检察院。", 1, 14.0, 200.0),
        _layout_block("现已审理终结。", 1, 14.0, 230.0),
    ]
    assert "北京市朝阳区人民法院刑事判决书" not in [p.text for p in clean(blocks)]


def test_bottom_line_is_not_treated_as_footer():
    """页面底部的正文行没有句末标点，也不得被当成页脚删掉。"""
    blocks = [
        _layout_block("公诉机关北京市朝阳区人民检察院。", 1, 14.0, 200.0),
        _layout_block("被告人张三于该日被抓获归案", 1, 14.0, 780.0),
        _layout_block("本院认为，其行为已构成盗窃罪。", 2, 14.0, 200.0),
    ]
    texts = [p.text for p in clean(blocks)]
    assert any(t == "被告人张三于该日被抓获归案" for t in texts)
