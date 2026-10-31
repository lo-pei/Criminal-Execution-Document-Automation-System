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
from .config import APP_TITLE, DB_PATH, SUPPORTED_EXTENSIONS


def _read_blocks(path: Path):
    """按扩展名选择通道。两条通道产出同一结构（ADR-005）。"""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        from .ingest import txt_reader

        return txt_reader.read(path), "txt"
    if suffix == ".pdf":
        from .ingest import pdf_reader

        return pdf_reader.read(path), "pdf"
    raise SystemExit(
        f"不支持的文件类型 {suffix!r}，仅支持：{'、'.join(SUPPORTED_EXTENSIONS)}"
    )


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

    if parsed.months:
        print(f"中文刑期：{months_to_chinese(parsed.months)}")

    if args.start and parsed.months:
        start = date.fromisoformat(args.start)
        end = compute_end_date(start, parsed.months)
        print(f"起算日：{start.isoformat()}")
        print(f"届满日：{end.isoformat()}")
        print(f"释放日：{compute_release_date(end).isoformat()}")
    return 0


def _cmd_initdb(_args: argparse.Namespace) -> int:
    from .storage.db import Database

    with Database(DB_PATH) as db:
        db.init_schema()
    print(f"台账已初始化：{DB_PATH}")
    return 0


def _cmd_ui(_args: argparse.Namespace) -> int:
    from .ui import launch

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

    initdb_parser = subparsers.add_parser("initdb", help="初始化台账数据库")
    initdb_parser.set_defaults(func=_cmd_initdb)

    ui_parser = subparsers.add_parser("ui", help="启动图形界面（默认）")
    ui_parser.set_defaults(func=_cmd_ui)

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
