"""用真实判决书主文回归刑期计算。

数据：``data/public-corpus/cases.jsonl``——北大法宝公开裁判文书的主文摘录，
每条含案号、法院、审结日、罪名与**判决主文原文**。本工具从主文里解析出
「主刑 + 起止日」三元组，用 ``cedas.calc`` 复算届满日，与文书载明值逐条比对。

本工具只做回归，不改数据、不联网、不写台账。

判定结果四类：

============  ==========================================================
MATCH         复算值与文书载明一致
MISMATCH      不一致——分两种：解析口径错（bug），或案件本身有折抵/已执行
              部分刑期（真实差异，需人工判读，工具只标出来不判对错）
NO_DATE       主文只写「刑期从判决执行之日起计算」，没写起止日
UNPARSED      主刑表述解析不出来
============  ==========================================================

用法::

    python tools/corpus_regress.py            # 人读表格
    python tools/corpus_regress.py --json     # 机器读，CI 用
    python tools/corpus_regress.py --strict   # 出现 MISMATCH 时退出码 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cedas.calc import parse_term  # noqa: E402
from cedas.calc.sentence import compute_end_date  # noqa: E402
from cedas.calc.term_parser import TermParseError  # noqa: E402

CORPUS = ROOT / "data" / "public-corpus" / "cases.jsonl"

# 「决定执行有期徒刑十年」优先于「判处有期徒刑十年，判处拘役一个月」——
# 数罪并罚句里前几个是各罪刑期，只有决定执行的那个才是最终执行刑期。
_DECIDED_RE = re.compile(r"决定执行(有期徒刑|拘役|管制)([^，,。；;（(）)]*)")
_SENTENCE_RE = re.compile(r"判处(有期徒刑|拘役|管制)([^，,。；;（(）)]*)")

# 「自2026年4月5日起至2036年4月4日止」「从2026年2月23日起至2027年8月22日止」
_DATE_RANGE_RE = re.compile(
    r"(?:自|从)\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*起?\s*"
    r"至\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*止?"
)


def _to_date(year: str, month: str, day: str) -> date:
    return date(int(year), int(month), int(day))


def _pick_term(result: str) -> tuple[str, str]:
    """返回 (刑期表述, 取用方式)。取决定执行刑期，没有则取第一处「判处」。"""
    decided = _DECIDED_RE.search(result)
    if decided:
        return decided.group(1) + decided.group(2), "决定执行"

    sentenced = _SENTENCE_RE.search(result)
    if sentenced:
        return sentenced.group(1) + sentenced.group(2), "判处"

    return "", ""


def check_case(case: dict) -> dict:
    """单条回归。返回可直接序列化的判定结果。"""
    result = case.get("result", "")
    term_text, picked_by = _pick_term(result)

    record = {
        "id": case["id"],
        "case_no": case["case_no"],
        "court": case["court"],
        "term": term_text,
        "picked_by": picked_by,
        "verdict": "",
        "detail": "",
    }

    if not term_text:
        record["verdict"] = "UNPARSED"
        record["detail"] = "主文里找不到「判处/决定执行 + 主刑」"
        return record

    try:
        parsed = parse_term(term_text)
    except TermParseError as exc:
        record["verdict"] = "UNPARSED"
        record["detail"] = f"{type(exc).__name__}: {exc}"
        return record

    record["principal"] = parsed.principal_type.value
    record["months"] = parsed.months
    record["days"] = parsed.days

    dates = _DATE_RANGE_RE.search(result)
    if dates is None:
        record["verdict"] = "NO_DATE"
        record["detail"] = "主文未载明起止日，需录入生效日期后另算"
        return record

    start = _to_date(*dates.group(1, 2, 3))
    claimed_end = _to_date(*dates.group(4, 5, 6))
    record["start"] = start.isoformat()
    record["claimed_end"] = claimed_end.isoformat()

    if parsed.months is None:
        record["verdict"] = "UNPARSED"
        record["detail"] = "无确定期限的主刑不应带起止日"
        return record

    computed = compute_end_date(start, parsed.months, parsed.days)
    record["computed_end"] = computed.isoformat()

    if computed == claimed_end:
        record["verdict"] = "MATCH"
        return record

    record["verdict"] = "MISMATCH"
    record["delta_days"] = (claimed_end - computed).days
    record["detail"] = (
        f"文书载明 {claimed_end.isoformat()}，复算 {computed.isoformat()}，"
        f"相差 {(claimed_end - computed).days} 天"
    )
    if "数罪并罚" in result or "前罪" in result:
        record["hint"] = "数罪并罚/前罪已羁押，起止日不等于「起算日 + 刑期 − 1 日」"
    return record


def load_corpus(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用真实判决书主文回归刑期计算")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    parser.add_argument("--strict", action="store_true", help="出现 MISMATCH 时退出码 1")
    parser.add_argument("--corpus", type=Path, default=CORPUS, help="语料文件路径")
    args = parser.parse_args(argv)

    cases = load_corpus(args.corpus)
    records = [check_case(case) for case in cases]
    summary: dict[str, int] = {}
    for record in records:
        summary[record["verdict"]] = summary.get(record["verdict"], 0) + 1

    if args.json:
        print(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2))
    else:
        print(f"语料 {len(records)} 条：{args.corpus}")
        for record in records:
            flag = {"MATCH": "✓", "MISMATCH": "✗", "NO_DATE": "—", "UNPARSED": "?"}[record["verdict"]]
            print(
                f"{flag} {record['id']} {record['case_no']} "
                f"{record.get('principal', '')}{record.get('months', '')}个月"
                f"{('+' + str(record['days']) + '日') if record.get('days') else ''}"
                f"  {record['verdict']}"
                + (f"  {record['detail']}" if record["detail"] else "")
            )
        print()
        print("汇总：" + "，".join(f"{k} {v}" for k, v in sorted(summary.items())))
        if "MISMATCH" in summary:
            print("提示：MISMATCH 不等于 bug。数罪并罚、前罪已羁押的案件，"
                  "起止日按实际羁押日扣减，本就不等于「起算日 + 刑期 − 1 日」。")

    if args.strict and summary.get("MISMATCH"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
