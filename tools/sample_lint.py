"""样本体检。

采集回来的 TXT 未经检查就进回归集，是后面所有指标失真的源头。
本脚本做七项检查，不通过的样本不得用于回归。

用法：
    python tools/sample_lint.py data/samples
    python tools/sample_lint.py data/samples/S001__京0105刑初123号.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cedas.ingest.base import RawBlock
from cedas.ingest.cleaner import clean
from cedas.ingest.txt_reader import EncodingDetectionError, decode

FILENAME_RE = re.compile(r"^(S\d{3,4})__.+\.txt$")
MIN_PARAGRAPHS = 8
MIN_CHARS = 800

REQUIRED_ANCHORS = ("判决如下",)


class Report:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.problems: list[str] = []
        self.notes: list[str] = []

    def fail(self, message: str) -> None:
        self.problems.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_file(path: Path) -> Report:
    report = Report(path)

    match = FILENAME_RE.match(path.name)
    if not match:
        report.fail("文件名不符合 {样本编号}__{案号}.txt，例：S001__京0105刑初123号.txt")

    try:
        text, encoding = decode(path.read_bytes())
        report.note(f"编码 {encoding}")
    except EncodingDetectionError as exc:
        report.fail(str(exc))
        return report

    text = text.replace("\r\n", "\n")
    if len(text) < MIN_CHARS:
        report.fail(f"文本仅 {len(text)} 字，疑似未完整复制（阈值 {MIN_CHARS}）")

    blocks = [RawBlock(text=line) for line in text.split("\n")]
    paragraphs = clean(blocks)

    if len(paragraphs) < MIN_PARAGRAPHS:
        report.fail(
            f"清洗后仅 {len(paragraphs)} 段（阈值 {MIN_PARAGRAPHS}）。"
            "样本可能被人工整理过，失去真实噪声后回归结论不可信"
        )

    joined = "".join(p.text for p in paragraphs)
    for anchor in REQUIRED_ANCHORS:
        if anchor not in joined:
            report.fail(f"缺少锚点「{anchor}」，无法定位主文")

    # 人工整理痕迹：所有行都以句末标点结尾，说明断行被修过
    raw_lines = [line.strip() for line in text.split("\n") if line.strip()]
    if raw_lines:
        terminal = sum(1 for line in raw_lines if line.endswith(("。", "；", "：", "！", "？")))
        ratio = terminal / len(raw_lines)
        if ratio > 0.9 and len(raw_lines) > 20:
            report.fail(
                f"超过 90% 的行以句末标点结尾（{ratio:.0%}），"
                "疑似手工整理过断行，请重新全选复制"
            )
        if any(len(line) > 120 for line in raw_lines):
            report.fail("存在超长行（>120 字），可能是复制时丢失了换行")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="样本体检")
    parser.add_argument("target", help="样本目录或单个 .txt 文件")
    parser.add_argument("--quiet", action="store_true", help="只输出不通过的样本")
    args = parser.parse_args(argv)

    target = Path(args.target)
    files = sorted(target.glob("*.txt")) if target.is_dir() else [target]
    if not files:
        print(f"未找到任何 .txt：{target}")
        return 2

    failures = 0
    for path in files:
        report = check_file(path)
        if report.ok:
            if not args.quiet:
                detail = "；".join(report.notes)
                print(f"[通过] {path.name}  {detail}")
        else:
            failures += 1
            print(f"[不通过] {path.name}")
            for problem in report.problems:
                print(f"    - {problem}")

    print(f"\n合计 {len(files)} 份，不通过 {failures} 份")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
