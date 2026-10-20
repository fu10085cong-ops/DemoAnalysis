"""命令行入口：python -m cs2coach analyze <demo路径> --player <steamid64>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cs2coach.console import ensure_utf8_stdio
from cs2coach.parsing.loader import DemoLoadError
from cs2coach.report.render import write_report
from cs2coach.service import AnalysisError, analyze_demo


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cs2coach", description="CS2 demo 赛后复盘分析")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="分析一个 demo 并生成复盘报告")
    analyze.add_argument("demo", type=Path, help=".dem / .dem.bz2 / .dem.gz / .zip / .rar 文件路径")
    analyze.add_argument("--demo-member", default=None, help="压缩包内有多个 demo 时，指定其中一个（文件名或包内路径）")
    analyze.add_argument("--player", required=True, type=int, help="目标玩家 SteamID64")
    analyze.add_argument("--out", type=Path, default=Path("data/output"), help="输出目录")
    analyze.add_argument("--config", type=Path, default=None, help="阈值配置文件（TOML）")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    args = _build_parser().parse_args(argv)
    if args.command == "analyze":
        try:
            report = analyze_demo(args.demo, args.player, demo_member=args.demo_member, config_path=args.config)
            json_path, markdown_path = write_report(report, args.out)
        except (DemoLoadError, AnalysisError, OSError, ValueError) as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        print(f"分析完成：{report['target_player']['name']} ({report['target_player']['steamid']})")
        print(f"JSON 报告：{json_path}")
        print(f"复盘报告：{markdown_path}")
        return 0
    return 1
