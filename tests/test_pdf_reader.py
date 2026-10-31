"""PDF 通道回归测试。

样本 PDF 由 ``tools/make_sample_pdf.py`` 从未经改动的 TXT 样本排版生成：
同样的文字，加上真实文书才有的版式（页眉、页码、大字号标题、按行宽折行）。
因此两通道的清洗结果必须逐段相同——这是 ADR-005「共用同一个清洗层」的核心断言，
也是 ``docs/05`` §7 所记风险的最小可验证形式。

**这不能替代真实 PDF 回归。** 合成 PDF 的字体、排版引擎与法院实际用的
Word/WPS 导出并不相同，它证明的是「清洗层能用版式信息正确处理 PDF」，
不是「真实文书命中率达标」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pymupdf", reason="未安装 PyMuPDF，跳过 PDF 通道测试")

from cedas.ingest import pdf_reader, txt_reader
from cedas.ingest.cleaner import clean
from cedas.ingest.pdf_reader import PdfUnavailableError

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples"
PDF_PATH = EXAMPLE_DIR / "S000__京0105刑初123号.pdf"
TXT_PATH = EXAMPLE_DIR / "S000__京0105刑初123号.txt"


@pytest.fixture(scope="module")
def pdf_blocks():
    return pdf_reader.read(PDF_PATH)


def test_sample_files_exist():
    assert PDF_PATH.is_file(), "缺少 PDF 样本，先跑 tools/make_sample_pdf.py"
    assert TXT_PATH.is_file()


def test_blocks_carry_layout_information(pdf_blocks):
    """PDF 通道必须带上页码、字号、坐标与页高，否则页眉与标题判定无从下手。"""
    assert pdf_blocks
    for block in pdf_blocks:
        assert block.page in (1, 2)
        assert block.font_size and block.font_size > 0
        assert block.bbox and len(block.bbox) == 4
        assert block.page_height and block.page_height > 800
        assert block.has_layout


def test_page_numbers_are_recovered(pdf_blocks):
    """跨页后页码必须递增，段落重建才能按页定位。"""
    pages = [block.page for block in pdf_blocks]
    assert pages == sorted(pages)
    assert pages[0] == 1 and pages[-1] == 2


def test_heading_font_is_larger_than_body(pdf_blocks):
    """标题字号应显著大于正文，否则「标题独占一段」的判定会失效。"""
    sizes = {block.text: block.font_size for block in pdf_blocks}
    title = next(v for k, v in sizes.items() if k.startswith("刑事判决书"))
    body = next(v for k, v in sizes.items() if k.startswith("公诉机关"))
    assert title > body * 1.2


def test_running_header_and_page_number_are_dropped(pdf_blocks):
    """每页页眉与「— N —」页码不得进入段落列表。"""
    texts = [paragraph.text for paragraph in clean(pdf_blocks)]
    assert not any("—" in text and len(text) <= 8 for text in texts), "页码残留"
    assert not any(text == "北京市朝阳区人民法院刑事判决书" for text in texts), "页眉残留"


def test_body_paragraphs_are_rejoined(pdf_blocks):
    """PDF 里的软换行必须拼回整段，否则字段抽取会取到半句话。"""
    texts = [paragraph.text for paragraph in clean(pdf_blocks)]
    assert any("公民身份号码110101198503021234" in text for text in texts)
    assert any(text.endswith("向本院提起公诉。") for text in texts)


def test_both_channels_produce_identical_paragraphs(pdf_blocks):
    """同一份文书走两条通道，段落文本与类型必须完全一致。"""
    from_pdf = clean(pdf_blocks)
    from_txt = clean(txt_reader.read(TXT_PATH))

    assert [p.text for p in from_pdf] == [p.text for p in from_txt]
    assert [p.kind for p in from_pdf] == [p.kind for p in from_txt]
    assert [p.index for p in from_pdf] == list(range(len(from_pdf)))


def test_missing_file_raises(tmp_path):
    with pytest.raises(PdfUnavailableError):
        pdf_reader.read(tmp_path / "不存在.pdf")
