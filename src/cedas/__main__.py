"""命令行入口。

无参数时启动图形界面；带子命令时执行对应的开发/排查动作。
生产用户只会用到图形界面，子命令面向开发与现场排障。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .config import (
    APP_HOME,
    APP_TITLE,
    DB_PATH,
    RULES_DIR,
    describe_paths,
    ensure_data_dirs,
)


def _read_blocks(path: Path):
    """按扩展名选择通道。两条通道产出同一结构（ADR-005）。"""
    from .ingest import UnsupportedFormatError, read_document

    try:
        return read_document(path)
    except UnsupportedFormatError as exc:
        raise SystemExit(str(exc)) from exc


def _cmd_clean(args: argparse.Namespace) -> int:
    from .ingest.cleaner import clean

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")

    blocks, channel = _read_blocks(path)
    paragraphs = clean(blocks)

    print(f"# 通道={channel}  原始块={len(blocks)}  段落={len(paragraphs)}")
    print(f"# 页码信息：{'有' if any(p.page for p in paragraphs) else '无（TXT 通道降级）'}")
    for paragraph in paragraphs:
        location = f"P{paragraph.page}" if paragraph.page else "  -"
        print(f"[{paragraph.index:03d}] {location} {paragraph.kind.value:<4} | {paragraph.text}")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from .extract import extract
    from .ingest.cleaner import clean

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")

    blocks, _ = _read_blocks(path)
    paragraphs = clean(blocks)
    result = extract(paragraphs)

    payload = result.to_dict()
    payload["source"] = path.name
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_calc(args: argparse.Namespace) -> int:
    from datetime import date

    from .calc import compute_end_date, compute_release_date, parse_term
    from .render.formatting import months_to_chinese

    parsed = parse_term(args.term)
    print(f"主刑：{parsed.principal_type.value}")
    print(f"月数：{parsed.months if parsed.months is not None else '无确定期限'}")
    if parsed.days:
        print(f"日数：{parsed.days}")

    if parsed.months:
        print(f"中文刑期：{months_to_chinese(parsed.months, parsed.days)}")

    if args.start and parsed.months:
        start = date.fromisoformat(args.start)
        end = compute_end_date(start, parsed.months, parsed.days)
        print(f"起算日：{start.isoformat()}")
        print(f"届满日：{end.isoformat()}")
        print(f"释放日：{compute_release_date(end).isoformat()}")
    return 0


def _arg_date(text: Optional[str], name: str):
    """解析 ``YYYY-MM-DD``。写错就当场报，不要留给下游算出离谱日期。"""
    if not text:
        return None
    from datetime import date

    try:
        return date.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"{name} 格式应为 YYYY-MM-DD，收到：{text!r}") from None


def _cmd_render(args: argparse.Namespace) -> int:
    """端到端：判决书 → 抽取 → 刑期 → 模板填充 → 归档 → 台账。"""
    from .pipeline import generate
    from .render import MappingNotFoundError, TemplateNotFoundError

    try:
        report = generate(
            args.input,
            doc_type=args.doc_type,
            output_dir=args.out,
            defendant_names=args.defendant,
            start_date=_arg_date(args.start_date, "--start-date"),
            effective_date=_arg_date(args.effective_date, "--effective-date"),
            when=_arg_date(args.date, "--date"),
            mapping_path=args.mapping,
            template_path=args.template,
            dry_run=args.dry_run,
            allow_missing=args.allow_missing,
            record=not args.no_db,
        )
    except (MappingNotFoundError, TemplateNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    except FileNotFoundError as exc:
        raise SystemExit(f"文件不存在：{exc}") from exc

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    print(f"# 源文件  {report.source}")
    print(f"# 通道={report.channel}  段落={report.paragraph_count}  文书类型={report.doc_type}")
    print(f"# 案号    {report.case_no or '（缺失）'}   法院 {report.court_name or '（缺失）'}")
    print(f"# 映射    {report.mapping}")
    print(f"# 模板    {report.template}")
    if report.dry_run:
        print("# 预演模式：不落盘、不写台账")
    print("-" * 72)

    for index, item in enumerate(report.items, start=1):
        mark = "阻断" if not item.ok else ("降级" if item.degraded else "完成")
        print(f"[{index}] {item.defendant}  {mark}")
        if item.start or item.end:
            source = f"（{item.start_source}）" if item.start_source else ""
            start = item.start.isoformat() if item.start else "—"
            end = item.end.isoformat() if item.end else "—"
            print(f"    刑期  {start}{source} 至 {end}")
        if item.path:
            print(f"    落盘  {item.path}")
        for blocker in item.blockers:
            print(f"    阻断  {blocker}")
        for warning in item.warnings:
            print(f"    提示  {warning}")

    for warning in report.warnings:
        print(f"    抽取  {warning}")

    print("-" * 72)
    print(f"生成 {len(report.rendered)} 份，阻断 {len(report.blocked)} 份。")
    return 0 if report.ok else 1


def _cmd_initdb(_args: argparse.Namespace) -> int:
    from .storage.db import Database

    ensure_data_dirs()
    with Database(DB_PATH) as db:
        db.init_schema()
    print(f"台账已初始化：{DB_PATH}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """现场排障：打印路径、依赖版本与可写性检查。

    内网机器无法远程连调，交付后第一轮问题基本都是「数据写到哪了」「依赖缺没缺」。
    本命令要求在不联网、不打开界面的前提下给出全部判断依据。

    ``--json`` 输出供构建脚本与自动化消费。**脚本不要解析中文文本**：
    Windows 控制台代码页为 GBK，管道重定向时输出编码随环境变化，
    按中文匹配会在构建机上莫名失败。
    """
    checks: list[dict[str, object]] = []
    problems: list[str] = []

    try:
        root = ensure_data_dirs()
        probe = root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append({"name": "可写性", "ok": True, "detail": str(root)})
    except OSError as exc:
        checks.append({"name": "可写性", "ok": False, "detail": str(exc)})
        problems.append(f"数据目录不可写：{exc}")

    for module, label in (("pymupdf", "PyMuPDF"), ("docx", "python-docx"), ("docxtpl", "docxtpl")):
        try:
            __import__(module)
            checks.append({"name": f"依赖 {label}", "ok": True, "detail": ""})
        except ImportError as exc:
            checks.append({"name": f"依赖 {label}", "ok": False, "detail": str(exc)})
            problems.append(f"缺少依赖 {label}：{exc}")

    try:
        from .ui import launch  # noqa: F401

        checks.append({"name": "依赖 Tkinter", "ok": True, "detail": ""})
    except Exception as exc:
        checks.append({"name": "依赖 Tkinter", "ok": False, "detail": str(exc)})
        problems.append(f"Tkinter 不可用：{exc}")

    rule_count = len(list(RULES_DIR.glob("*.json"))) + len(
        list((APP_HOME / "resources" / "rules").glob("*.json"))
    )
    checks.append({"name": "规则文件", "ok": rule_count > 0, "detail": f"{rule_count} 个"})
    if rule_count == 0:
        problems.append("未找到任何规则文件，resources/rules 缺失或未随包分发")

    ok = not problems

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "ok": ok,
                    "app": APP_TITLE,
                    "version": __version__,
                    "python": sys.version.split()[0],
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "rule_count": rule_count,
                    "paths": describe_paths(),
                    "checks": checks,
                    "problems": problems,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if ok else 1

    print(f"{APP_TITLE}  v{__version__}")
    print(f"Python {sys.version.split()[0]}   frozen={getattr(sys, 'frozen', False)}")
    print("-" * 56)
    for key, value in describe_paths().items():
        print(f"{key:<18}{value}")
    print("-" * 56)
    for check in checks:
        mark = "正常" if check["ok"] else "失败"
        print(f"{check['name']!s:<18}{mark}  {check['detail']}")
    print("-" * 56)
    if problems:
        print("存在问题：")
        for item in problems:
            print(f"  · {item}")
        return 1
    print("自检通过：无网络依赖，环境完整。")
    return 0


def _cmd_ui(_args: argparse.Namespace) -> int:
    from .ui import launch

    ensure_data_dirs()
    return launch()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cedas", description=APP_TITLE)
    parser.add_argument("--version", action="version", version=f"cedas {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    clean_parser = subparsers.add_parser("clean", help="清洗判决书并打印段落列表")
    clean_parser.add_argument("input", help="判决书路径（.pdf 或 .txt）")
    clean_parser.set_defaults(func=_cmd_clean)

    extract_parser = subparsers.add_parser("extract", help="抽取字段并输出 JSON")
    extract_parser.add_argument("input", help="判决书路径（.pdf 或 .txt）")
    extract_parser.set_defaults(func=_cmd_extract)

    calc_parser = subparsers.add_parser("calc", help="解析刑期并计算届满日")
    calc_parser.add_argument("term", help="刑期表述，如「有期徒刑三年六个月」")
    calc_parser.add_argument("--start", help="起算日，格式 YYYY-MM-DD")
    calc_parser.set_defaults(func=_cmd_calc)

    render_parser = subparsers.add_parser(
        "render", help="生成执行文书并归档（抽取 → 刑期 → 填充 → 台账）"
    )
    render_parser.add_argument("input", help="判决书路径（.pdf 或 .txt）")
    render_parser.add_argument("--doc-type", default="执行通知书", help="文书类型，对应映射表文件名")
    render_parser.add_argument(
        "--defendant", action="append", metavar="姓名", help="只生成指定被告人，可重复"
    )
    render_parser.add_argument("--out", help="归档根目录，默认 data/output")
    render_parser.add_argument("--start-date", help="人工指定刑期起算日，YYYY-MM-DD")
    render_parser.add_argument("--effective-date", help="判决生效日，YYYY-MM-DD")
    render_parser.add_argument("--date", help="文书日期（落款与文件名），默认当日")
    render_parser.add_argument("--mapping", help="直接指定映射表 JSON")
    render_parser.add_argument("--template", help="直接指定 .docx 模板")
    render_parser.add_argument("--dry-run", action="store_true", help="只组装上下文，不落盘")
    render_parser.add_argument(
        "--allow-missing", action="store_true", help="核心字段缺失仍生成（降级，报告标注）"
    )
    render_parser.add_argument("--no-db", action="store_true", help="不写台账")
    render_parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    render_parser.set_defaults(func=_cmd_render)

    initdb_parser = subparsers.add_parser("initdb", help="初始化台账数据库")
    initdb_parser.set_defaults(func=_cmd_initdb)

    ui_parser = subparsers.add_parser("ui", help="启动图形界面（默认）")
    ui_parser.set_defaults(func=_cmd_ui)

    doctor_parser = subparsers.add_parser("doctor", help="自检：路径、依赖、可写性（现场排障用）")
    doctor_parser.add_argument("--json", action="store_true", help="以 JSON 输出，供脚本消费")
    doctor_parser.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        args.func = _cmd_ui
        return args.func(args)

    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
