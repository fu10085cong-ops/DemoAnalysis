"""命令行入口：python -m cs2coach analyze <demo路径> --player <steamid64>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cs2coach.config import ConfigError, load_config
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
    analyze.add_argument("--config", type=Path, default=None, help="阈值配置文件（TOML），只需写要覆盖的条目")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    args = _build_parser().parse_args(argv)
    if args.command == "analyze":
        return _analyze(args)
    return 1


def _analyze(args: argparse.Namespace) -> int:
    # demoparser2 / pandas 导入较慢，放到真正需要时再导入，--help 和参数错误能立即返回
    from cs2coach.parsing.match import DemoParseError, parse_match

    if not args.player.isdigit():
        print(f"错误: --player 应为 SteamID64（17 位数字），收到 {args.player!r}", file=sys.stderr)
        return 1
    try:
        cfg = load_config(args.config)
        demo = resolve_demo(args.demo, member=args.demo_member)
        print(f"demo: {demo}")
        match = parse_match(demo, cfg)
        player = match.require_player(int(args.player), cfg["parsing"]["min_target_coverage"])
    except (ConfigError, DemoLoadError, DemoParseError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    for w in match.warnings:
        print(f"警告: {w}", file=sys.stderr)

    from cs2coach.features.moments import find_moments
    from cs2coach.report.console import format_moments

    m = match.meta
    print(f"地图 {m.map_name}，{m.tick_rate} tick，{len(match.rounds)} 个回合，服务器 {m.server_name}\n")
    moments = find_moments(match, player.steamid, cfg)
    print(format_moments(moments))

    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{demo.stem}_{player.steamid}_moments.json"
    out.write_text(moments.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")
    print("AI 诊断尚未实现（当前处于第三步：决定性瞬间与指标）")
    return 0
