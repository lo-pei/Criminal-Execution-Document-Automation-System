"""批量抽取摘要：把一份样本目录抽成人能一眼核对的表。

用途是采集后的自查——真实判决进来以后，先看这张表对不对，
再决定往 `tests/test_samples.py` 里写什么期望值。不要拿抽取结果直接当真值，
真值必须回原文核对。

用法：
    python tools/sample_report.py data/samples
    python tools/sample_report.py data/samples/S012__2024冀0481刑初101号.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cedas.extract import extract
from cedas.extract.doc_type import detect_doc_kind
from cedas.ingest import read_document
from cedas.ingest.cleaner import clean


def summarize(path: Path) -> list[str]:
    blocks, _ = read_document(path)
    paragraphs = clean(blocks)
    result = extract(paragraphs)

    lines = [f"=== {path.name}"]
    lines.append(f"  类型 {detect_doc_kind(paragraphs).name} | 案号 {result.case.case_no.value}"
                 f" | {result.case.court_name.value} | {result.case.judgment_date.value}")

    for d in result.defendants:
        charges = "、".join(str(c.charge.value) for c in d.crimes)
        custody = "；".join(
            f"{seg.type.value}{seg.start.isoformat() if seg.start else '?'}" for seg in d.custody
        ) or "无"
        months = d.merged_months.value
        lines.append(
            f"  {d.display_name:<6} 罪名[{charges}] 主刑[{d.merged_type.value}]"
            f" {months}月 并罚[{d.combination.value}]"
            f" 缓刑[{d.probation.applied.value}/{d.probation.months.value}]"
        )
        lines.append(f"         强制措施 {custody}")
        if d.span is not None:
            lines.append(
                f"         起止 {d.span.start} ~ {d.span.end}"
                f" 一致性 {d.span.consistency.value}"
            )
        # 载明值与计算值分行写：核对时用的正是「原文这么写 vs 系统这么算」这一栏，
        # 挤在一行会看串。判决书不写起止日是常态，此时只留空不解释。
        start, end = d.declared_span
        if start is not None or end is not None:
            lines.append(f"         载明 {start} ~ {end}")

    for w in result.warnings:
        lines.append(f"  ! {w}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量抽取摘要")
    parser.add_argument("target", help="样本目录或单个 .txt 文件")
    args = parser.parse_args(argv)

    target = Path(args.target)
    files = sorted(target.glob("*.txt")) if target.is_dir() else [target]
    if not files:
        print(f"未找到任何 .txt：{target}")
        return 2

    for path in files:
        for line in summarize(path):
            print(line)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
