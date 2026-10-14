"""命令行入口：python -m cs2coach analyze <demo路径> --player <steamid64>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cs2coach.console import ensure_utf8_stdio
from cs2coach.parsing.loader import DemoLoadError, resolve_demo


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cs2coach", description="CS2 demo 赛后复盘分析")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="分析一个 demo 并生成复盘报告")
    analyze.add_argument("demo", type=Path, help=".dem / .dem.bz2 / .dem.gz / .zip / .rar 文件路径")
    analyze.add_argument("--demo-member", default=None, help="压缩包内有多个 demo 时，指定其中一个（文件名或包内路径）")
    analyze.add_argument("--player", required=True, help="目标玩家 SteamID64")
    analyze.add_argument("--out", type=Path, default=Path("data/output"), help="输出目录")
    analyze.add_argument("--config", type=Path, default=None, help="阈值配置文件（TOML）")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    args = _build_parser().parse_args(argv)
    if args.command == "analyze":
        try:
            demo = resolve_demo(args.demo, member=args.demo_member)
        except DemoLoadError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        print(f"demo: {demo}")
        # 分析流水线（特征 → 规则 → 报告）在后续步骤实现
        print("analyze 尚未实现（当前处于第一步：项目骨架 + 字段探索）", file=sys.stderr)
        return 2
    return 1
