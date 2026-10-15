"""命令行入口：python -m cs2coach analyze <demo路径> --player <steamid64>"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from cs2coach.config import ConfigError, load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.parsing.loader import DemoLoadError, resolve_demo


def _output_folder(root: Path, demo: Path, name: str, steamid: int) -> Path:
    """<输出根目录>/<玩家名>_<steamid>/<比赛名>/。

    按 steamid 归到同一个玩家文件夹：同一个人在不同平台的名字可能不同（如 5E 和完美），
    已有以 _<steamid> 结尾的文件夹时沿用它，不按新名字另建。
    """
    existing = sorted(p for p in root.glob(f"*_{steamid}") if p.is_dir()) if root.is_dir() else []
    if existing:
        player_dir = existing[0]
    else:
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", name.strip()) or "player"
        player_dir = root / f"{safe_name}_{steamid}"
    return player_dir / demo.stem


def _archive_reports(folder: Path) -> None:
    """重跑前保存已有的 AI 报告；同一分钟内重跑也不覆盖历史版本。"""
    for name in ("report.md", "submission.json"):
        old = folder / name
        if not old.is_file():
            continue
        archive = folder / "历史版本"
        archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(old.stat().st_mtime).strftime("%Y%m%d-%H%M")
        target = archive / f"{old.stem}_{stamp}{old.suffix}"
        n = 2
        while target.exists():
            target = archive / f"{old.stem}_{stamp}_{n}{old.suffix}"
            n += 1
        old.replace(target)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cs2coach", description="CS2 demo 赛后复盘分析")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="分析一个 demo 并生成复盘报告")
    analyze.add_argument("demo", type=Path, help=".dem / .dem.bz2 / .dem.gz / .zip / .rar 文件路径")
    analyze.add_argument("--demo-member", default=None, help="压缩包内有多个 demo 时，指定其中一个（文件名或包内路径）")
    analyze.add_argument("--player", required=True, help="目标玩家 SteamID64")
    analyze.add_argument("--out", type=Path, default=Path("data/output"), help="输出目录")
    analyze.add_argument("--config", type=Path, default=None, help="阈值配置文件（TOML），只需写要覆盖的条目")
    analyze.add_argument("--ai", action="store_true", help="调用 AI 生成中文复盘报告（模型在配置的 [ai] 段，需要对应的 API 凭据）")
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
    from cs2coach.baseline import load_baseline
    from cs2coach.features.problems import detect_problems, problem_frequency
    from cs2coach.report.console import format_moments
    from cs2coach.report.facts import render_facts_file
    from cs2coach.report.problems import render_problem_report

    m = match.meta
    print(f"地图 {m.map_name}，{m.tick_rate} tick，{len(match.rounds)} 个回合，服务器 {m.server_name}\n")
    moments = find_moments(match, player.steamid, cfg)
    print(format_moments(moments))

    folder = _output_folder(args.out, demo, player.name, player.steamid)
    folder.mkdir(parents=True, exist_ok=True)
    _archive_reports(folder)
    out = folder / "moments.json"
    out.write_text(moments.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")
    facts = folder / "facts.md"
    facts.write_text(render_facts_file(moments, {sid: p.name for sid, p in match.players.items()}), encoding="utf-8")
    print(f"已写入 {facts}")
    problems = folder / "problems.md"
    baseline = load_baseline()
    detected = detect_problems(moments, baseline, cfg)
    frequencies = problem_frequency(detected, moments, baseline, cfg)
    problems.write_text(render_problem_report(detected, frequencies), encoding="utf-8")
    print(f"已写入 {problems}")
    if not args.ai:
        print("加上 --ai 生成 AI 复盘报告")
        print(f"输出目录：{folder}")
        return 0
    result = _diagnose(match, moments, cfg, folder / "report.md")
    print(f"输出目录：{folder}")
    return result


def _diagnose(match, moments, cfg, out: Path) -> int:
    from cs2coach.ai.diagnose import DiagnosisError, diagnose
    from cs2coach.ai.providers import ProviderError
    from cs2coach.baseline import load_baseline
    from cs2coach.env import load_env_file

    load_env_file(Path(".env"))  # API key 可以写在当前目录的 .env 里（已在 .gitignore 中）

    name = cfg["ai"]["provider"]
    model = cfg["ai"].get(name, {}).get("model", "?")
    print(f"\n正在生成 AI 复盘报告（{name} / {model}）……", flush=True)
    try:
        d = diagnose(match, moments, cfg, load_baseline())
    except (DiagnosisError, ProviderError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # 各家 SDK 的网络、认证、限流错误
        msg = _api_error_message(e)
        if msg is None:
            raise
        print(f"错误: {msg}", file=sys.stderr)
        return 1

    out.write_text(d.report, encoding="utf-8")
    audit = out.with_name("submission.json")
    audit.write_text(json.dumps({
        "submission": d.submission.model_dump(),
        "violations_history": [[v.model_dump() for v in group] for group in d.violations_history],
        "dropped": [item.model_dump() for item in d.dropped],
        "raw_calls": d.raw_calls,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n" + d.report)
    u = d.usage
    print(f"已写入 {out}")
    print(f"已写入 {audit}")
    print(f"{d.provider} / {d.model}，工具调用 {len(d.tool_calls)} 次，结论修正 {d.repairs} 次；"
          f"输入 {u.input_tokens} tokens（其中缓存命中 {u.cache_read_tokens}），输出 {u.output_tokens} tokens")
    return 0


def _api_error_message(e: Exception) -> str | None:
    """把 anthropic / openai SDK 的常见错误转成中文提示；不认识的错误返回 None（原样抛出）。"""
    import anthropic
    import openai

    if isinstance(e, TypeError) and "authentication" in str(e):
        # Claude：没有任何凭据时，SDK 在发请求前抛出 TypeError（"Could not resolve authentication method"）
        return "没有找到 Claude API 凭据。请设置环境变量 ANTHROPIC_API_KEY，或运行 `ant auth login`"
    if isinstance(e, (anthropic.AuthenticationError, openai.AuthenticationError)):
        return "API 认证失败，请检查 API key"
    if isinstance(e, (anthropic.RateLimitError, openai.RateLimitError)):
        return "请求过于频繁或额度不足，请稍后再试"
    if isinstance(e, (anthropic.APIStatusError, openai.APIStatusError)):
        return f"模型服务返回 {e.status_code}：{e.message}"
    if isinstance(e, (anthropic.APIConnectionError, openai.APIConnectionError)):
        return "无法连接模型服务，请检查网络"
    return None
