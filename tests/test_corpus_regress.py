"""真实判决书语料的回归测试。

语料见 ``data/public-corpus/cases.jsonl``——北大法宝公开裁判文书的主文摘录。
这些文书不参与训练，只用来回答一个问题：**写死的计算规则，和法院真实写法对不对得上。**

断言的是分布与关键样本，不是逐条写死数值：新增语料只要落进既有四类判定即可通过。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "public-corpus" / "cases.jsonl"


def _load_tool():
    """tools 不是包，按路径加载。避免为此改动打包结构。"""
    spec = importlib.util.spec_from_file_location(
        "corpus_regress", ROOT / "tools" / "corpus_regress.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    return [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def records(corpus) -> list[dict]:
    tool = _load_tool()
    return [tool.check_case(case) for case in corpus]


def test_every_record_is_traceable(corpus):
    """每条语料都得指回公开来源，否则不可复核。"""
    for case in corpus:
        assert case["id"] and case["case_no"] and case["court"]
        assert case["url"].startswith("https://www.pkulaw.com/")
        assert case["result"].strip()


def test_no_unparsed_verdicts(records):
    """主刑表述一律能解析。出现了就说明真实文书里有没覆盖的写法。"""
    bad = [r for r in records if r["verdict"] == "UNPARSED"]
    assert not bad, "以下主文解析失败：" + "，".join(f"{r['id']} {r['term']!r}" for r in bad)


def test_most_dated_cases_match(records):
    """带起止日的案件里，绝大多数应与「起算日 + 刑期 − 1 日」吻合。"""
    dated = [r for r in records if r["verdict"] in {"MATCH", "MISMATCH"}]
    assert len(dated) >= 15, "有起止日的样本太少，回归没有意义"

    matched = [r for r in dated if r["verdict"] == "MATCH"]
    assert len(matched) / len(dated) >= 0.9, (
        f"吻合率 {len(matched)}/{len(dated)}，低于 90%，先查是不是计算规则错了"
    )


def test_day_component_case_matches(records):
    """（2026）皖0881刑初147号：拘役一个月十五天，自 2026-09-30 至 2026-11-13。

    这条是「日」分量的定盘星。只按月算会得 2026-10-29，差 15 天；
    先加月再加日得 2026-11-13，与文书一致。
    """
    record = next(r for r in records if r["id"] == "PKU-0016")
    assert record["verdict"] == "MATCH"
    assert record["days"] == 15
    assert record["computed_end"] == "2026-11-13"


def test_single_mismatch_has_legal_reason(records):
    """唯一的不吻合必须是数罪并罚——前罪已羁押，起止日按实际羁押日扣减。"""
    mismatched = [r for r in records if r["verdict"] == "MISMATCH"]
    assert len(mismatched) <= 1, "出现多处不吻合，先确认是不是 bug 而非个案差异"
    for record in mismatched:
        assert record.get("hint"), f"{record['id']} 不吻合且没有合理解释"


def test_undated_cases_are_common(records):
    """过半主文不写起止日——这是「生效日期必须人工录入」的实证依据。

    若这个比例明显下降，说明法院写法变了，C 组的必填性判断要重新评估。
    """
    undated = [r for r in records if r["verdict"] == "NO_DATE"]
    total = len(records)
    assert undated, "一条 NO_DATE 都没有，先确认语料是否失真"
    ratio = len(undated) / total
    assert 0.2 <= ratio <= 0.9, f"无起止日比例 {ratio:.0%}，与既往观察（约 5 成）偏离过大"
