"""测试公共夹具。

样本为**合成文本**，不含任何真实案件信息（见 SECURITY.md §3）。
结构照真实刑事判决书的首部与主文，用于回归清洗与抽取。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cedas.ingest.base import RawBlock

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "examples"
SAMPLE_PATH = FIXTURE_DIR / "S000__京0105刑初123号.txt"
SAMPLE_TEXT = SAMPLE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def sample_text() -> str:
    return SAMPLE_TEXT


@pytest.fixture
def sample_blocks() -> list[RawBlock]:
    return [RawBlock(text=line) for line in SAMPLE_TEXT.split("\n")]
