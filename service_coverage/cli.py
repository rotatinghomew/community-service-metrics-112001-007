"""命令行入口：python3 -m service_coverage run [选项]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .metrics import compute
from .report import render_json, render_markdown
from .sources import load_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="service_coverage",
        description="区域服务覆盖分析：多期边界、责任移交与精度分级下的归属汇总",
    )
    parser.add_argument(
        "--fixtures", default="fixtures",
        help="来源表目录（含 boundaries/population/requests/transfers 四个 JSON），默认 fixtures",
    )
    parser.add_argument(
        "--unified-version", default="new",
        help="统一空间口径使用的边界版本，默认 new",
    )
    parser.add_argument("--md", dest="md_out", help="Markdown 报告输出路径（缺省打印到标准输出）")
    parser.add_argument("--json", dest="json_out", help="JSON 结果输出路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sources = load_sources(args.fixtures)
        result = compute(sources, unified_version=args.unified_version)
    except (ValueError, FileNotFoundError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    markdown = render_markdown(result)
    if args.md_out:
        Path(args.md_out).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    if args.json_out:
        Path(args.json_out).write_text(render_json(result), encoding="utf-8")

    failed = [c for c in result.integrity if not c.ok]
    if failed:
        print(f"警告：{len(failed)} 项完整性校验未通过", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
