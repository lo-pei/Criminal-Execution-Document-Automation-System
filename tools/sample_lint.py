"""样本体检。

采集回来的 TXT 未经检查就进回归集，是后面所有指标失真的源头。
不通过的样本不得用于回归。

用法：
    python tools/sample_lint.py data/samples                    # 数据库导出（默认）
    python tools/sample_lint.py data/samples --source pdf       # PDF 全选复制
    python tools/sample_lint.py data/samples/S001__京0131刑初371号.txt
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

# 数据库导出（北大法宝 / 元典智库 MCP）与 PDF 全选复制，噪声形态不同，
# 不能共用一套「有没有被手工整理过」的判据，否则必然误杀其中一种：
#
#   PDF 复制     每行 30–40 字、大量行在句中截断、带页眉页码与「— N —」
#                每段一行、段尾基本都带标点、长段可达 600 字、无版式噪声
#   数据库导出
#
# 两种来源共同的「作弊」特征只有一个：**人把长段落切成长度整齐的短行**。
# 所以判据统一改成看行长分布，而不是看「行尾是否标点」——
# 后者在数据库导出的文本上恒为真，只会误杀真实语料。
NEAT_LINE_MIN = 25  # 人工切行常见的长度区间
NEAT_LINE_MAX = 45
NEAT_LINE_RATIO = 0.8  # 超过这个比例落在区间内，判为人工切行

# 网页残留：从数据库页面复制时常带上的尾巴
WEB_DEBRIS = (
    "上一篇",
    "下一篇",
    "扫一扫",
    "扫码",
    "下载APP",
    "关注公众号",
    "责任编辑",
    "浏览次数",
    "分享到",
)


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


def check_file(path: Path, source: str = "db", min_chars: int = MIN_CHARS) -> Report:
    report = Report(path)

    match = FILENAME_RE.match(path.name)
    if not match:
        report.fail("文件名不符合 {样本编号}__{案号}.txt，例：S001__京0131刑初371号.txt")

    try:
        text, encoding = decode(path.read_bytes())
        report.note(f"编码 {encoding}")
    except EncodingDetectionError as exc:
        report.fail(str(exc))
        return report

    text = text.replace("\r\n", "\n")
    if len(text) < min_chars:
        report.fail(f"文本仅 {len(text)} 字，疑似未完整复制（阈值 {min_chars}）")

    blocks = [RawBlock(text=line) for line in text.split("\n")]
    paragraphs = clean(blocks)

    if len(paragraphs) < MIN_PARAGRAPHS:
        report.fail(
            f"清洗后仅 {len(paragraphs)} 段（阈值 {MIN_PARAGRAPHS}）。"
            "样本可能被人工整理过，失去真实噪声后回归结论不可信"
        )
    report.note(f"{len(paragraphs)} 段")

    joined = "".join(p.text for p in paragraphs)
    for anchor in REQUIRED_ANCHORS:
        if anchor not in joined:
            report.fail(f"缺少锚点「{anchor}」，无法定位主文")

    raw_lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not raw_lines:
        report.fail("文件为空")
        return report

    # 共同判据：行长整齐 = 人工切行。真实文书的段落长短相差悬殊
    # （「刑事判决书」五个字的标题和六百字的「本院认为」并存），
    # 只有被人为切成 30–40 字一行时，才会绝大多数行都落在窄区间内。
    neat = sum(1 for line in raw_lines if NEAT_LINE_MIN <= len(line) <= NEAT_LINE_MAX)
    neat_ratio = neat / len(raw_lines)
    if len(raw_lines) > 20 and neat_ratio > NEAT_LINE_RATIO:
        report.fail(
            f"{neat_ratio:.0%} 的行长度落在 {NEAT_LINE_MIN}–{NEAT_LINE_MAX} 字之间，"
            "疑似人工整理过断行（真实文书的行长应长短悬殊），请重新全选复制"
        )

    if source == "pdf":
        # PDF 通道独有：页眉页码与软换行是清洗层的处理对象，被删掉就等于把待解问题删了
        terminal = sum(1 for line in raw_lines if line.endswith(("。", "；", "：", "！", "？")))
        ratio = terminal / len(raw_lines)
        if ratio > 0.9 and len(raw_lines) > 20:
            report.fail(
                f"超过 90% 的行以句末标点结尾（{ratio:.0%}），"
                "疑似手工整理过断行，请重新全选复制"
            )
        if any(len(line) > 120 for line in raw_lines):
            report.fail("存在超长行（>120 字），可能是复制时丢失了换行")
    else:
        # 数据库导出独有：本身没有软换行，但要确认没有把网页尾巴一起复制进来
        hit = [word for word in WEB_DEBRIS if word in joined]
        if hit:
            report.fail(f"残留网页元素：{'、'.join(hit)}")
        # 一行一句、每行都是完整段的导出文本，清洗层无从检验；
        # 这种样本可以用来验抽取，但不能用来验清洗，需要在报告里说清楚。
        if len(paragraphs) == len(raw_lines):
            report.note("每行即一段（数据库导出特征，不覆盖清洗层的软换行合并）")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="样本体检")
    parser.add_argument("target", help="样本目录或单个 .txt 文件")
    parser.add_argument("--quiet", action="store_true", help="只输出不通过的样本")
    parser.add_argument(
        "--source",
        choices=("db", "pdf"),
        default="db",
        help="样本来源：db=数据库导出（默认），pdf=PDF 全选复制",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=MIN_CHARS,
        help=f"正文最少字数（默认 {MIN_CHARS}）。examples/ 的最小基线是刻意精简的，"
        "用 `--min-chars 300` 检查版式而不误报",
    )
    args = parser.parse_args(argv)

    target = Path(args.target)
    files = sorted(target.glob("*.txt")) if target.is_dir() else [target]
    if not files:
        print(f"未找到任何 .txt：{target}")
        return 2

    failures = 0
    for path in files:
        report = check_file(path, source=args.source, min_chars=args.min_chars)
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
