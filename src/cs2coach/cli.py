"""命令行入口：analyze 单场分析与 summary 多场程序汇总。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from time import perf_counter
from datetime import datetime
from pathlib import Path

from cs2coach.config import ConfigError, load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.parsing.loader import DemoLoadError, demo_fingerprint, resolve_demo
from cs2coach.report.index_page import write_index


_DEMO_ID_FILE = ".demo_id"


def _output_folder(root: Path, demo: Path, name: str, steamid: int, fingerprint: str | None = None) -> Path:
    """<输出根目录>/<玩家名>_<steamid>/<比赛名>/。

    按 steamid 归到同一个玩家文件夹：同一个人在不同平台的名字可能不同（如 5E 和完美），
    已有以 _<steamid> 结尾的文件夹时沿用它，不按新名字另建。

    fingerprint（demo_fingerprint）用来区分文件名相同的不同比赛：比赛文件夹里的 .demo_id 记录它属于哪份 demo，
    同名文件夹已属于另一份 demo 时改用 <比赛名>_<指纹前 8 位>。没有记录的旧文件夹由第一次重跑的 demo 接管。
    """
    existing = sorted(p for p in root.glob(f"*_{steamid}") if p.is_dir()) if root.is_dir() else []
    if existing:
        player_dir = existing[0]
    else:
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", name.strip()) or "player"
        player_dir = root / f"{safe_name}_{steamid}"
    folder = player_dir / demo.stem
    if fingerprint is None:
        return folder
    for candidate in (folder, player_dir / f"{demo.stem}_{fingerprint[:8]}"):
        owner = _folder_owner(candidate)
        if owner is None or owner == fingerprint:
            return candidate
    return player_dir / f"{demo.stem}_{fingerprint[:16]}"


def _folder_owner(folder: Path) -> str | None:
    try:
        return (folder / _DEMO_ID_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _claim_folder(folder: Path, fingerprint: str) -> None:
    (folder / _DEMO_ID_FILE).write_text(fingerprint + "\n", encoding="utf-8")


def _check_report_page(page, cfg: dict) -> None:
    """输出一致性问题，保留报告供进一步审查。"""
    from cs2coach.report.page_check import check_page

    issues = check_page(page, cfg)
    if issues:
        print(f"报告页一致性检查：{len(issues)} 处")
        for issue in issues:
            print(f"  {issue}")


def _refresh_index(root: Path) -> None:
    """在输出根目录刷新可点击的报告目录。"""
    path = write_index(root)
    print(f"报告目录：{path}")


def _archive_reports(folder: Path, names: tuple[str, ...] = ("report.md", "submission.json", "report_data.json", "report.html")) -> None:
    """重跑前保存指定文件；同一分钟内重跑也不覆盖历史版本。"""
    for name in names:
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
    analyze.add_argument("--target", default=None, help="报告对比的目标分段 id")
    analyze.add_argument("--ai", action="store_true", help="调用 AI 生成中文复盘报告（模型在配置的 [ai] 段，需要对应的 API 凭据）")
    analyze.add_argument("--provider", choices=("deepseek", "mimo", "claude", "local"), help="覆盖配置中的 AI 模型服务")
    analyze.add_argument("--model", default=None, help="覆盖所选模型服务的模型名，例如 deepseek-v4-pro")
    summary = sub.add_parser("summary", help="汇总同一玩家的多场 demo（程序部分）")
    summary.add_argument("demo", nargs="*", type=Path, help="指定 demo；省略时递归扫描 --demos-dir")
    summary.add_argument("--demo-member", action="append", default=[], help="指定压缩包中的 demo；多个路径时按顺序对应")
    summary.add_argument("--player", required=True, help="目标玩家 SteamID64")
    summary.add_argument("--demos-dir", type=Path, default=Path("data/demos"), help="自动扫描的 demo 根目录")
    summary.add_argument("--out", type=Path, default=Path("data/output"), help="输出目录")
    summary.add_argument("--config", type=Path, default=None, help="阈值配置文件（TOML）")
    summary.add_argument("--target", default=None, help="报告对比的目标分段 id")
    summary.add_argument("--ai", action="store_true", help="调用 AI 生成多场中文复盘报告")
    summary.add_argument("--provider", choices=("deepseek", "mimo", "claude", "local"), help="覆盖配置中的 AI 模型服务")
    summary.add_argument("--model", default=None, help="覆盖所选模型服务的模型名，例如 deepseek-v4-pro")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    args = _build_parser().parse_args(argv)
    if args.command == "analyze":
        return _analyze(args)
    if args.command == "summary":
        return _summary(args)
    return 1


def _apply_ai_overrides(cfg: dict, args: argparse.Namespace) -> None:
    """命令行的 --provider / --model 覆盖配置；只改本次运行，不写回配置文件。"""
    if args.provider:
        cfg["ai"]["provider"] = args.provider
    if args.model:
        cfg["ai"].setdefault(cfg["ai"]["provider"], {})["model"] = args.model


def _page_tiers(cfg: dict, args: argparse.Namespace) -> dict:
    from cs2coach.report.page_data import load_tiers

    if args.target:
        cfg["radar"]["default_tier"] = args.target
    tiers = load_tiers(cfg)
    if cfg["radar"]["default_tier"] not in tiers:
        print(f"提示：目标分段 {cfg['radar']['default_tier']} 的基准尚未构建，本次报告不含分段对比。")
        return {}
    return tiers


def _summary(args: argparse.Namespace) -> int:
    from cs2coach.baseline import load_baseline
    from cs2coach.summary.aggregate import build_summary
    from cs2coach.summary.discover import find_player_demos, inspect_player_demos
    from cs2coach.summary.render import render_summary_problems
    from cs2coach.summary.basic import build_summary_l0, render_summary_basic_report

    if not args.player.isdigit():
        print(f"错误: --player 应为 SteamID64（17 位数字），收到 {args.player!r}", file=sys.stderr)
        return 1
    if args.demo_member and not args.demo:
        print("错误: --demo-member 只能与指定 demo 一起使用", file=sys.stderr)
        return 1
    archives = [path for path in args.demo if path.name.lower().endswith((".zip", ".rar"))]
    if len(args.demo_member) > len(archives):
        print("错误: --demo-member 数量不能超过压缩包数量", file=sys.stderr)
        return 1
    selected = dict(zip(archives, args.demo_member))
    try:
        cfg = load_config(args.config)
        _apply_ai_overrides(cfg, args)
        steamid = int(args.player)
        refs = (inspect_player_demos(steamid, [(path, selected.get(path)) for path in args.demo])
                if args.demo else find_player_demos(steamid, args.demos_dir))
    except (ConfigError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    for item in getattr(refs, "skipped", []):
        print(f"扫描跳过 {item.match_id}：{item.reason}", flush=True)
    if not refs:
        print(f"错误: 没有找到玩家 {steamid} 的可用 demo", file=sys.stderr)
        return 1
    baseline = load_baseline()
    result = build_summary(steamid, refs, cfg, baseline)
    folder = _output_folder(args.out, Path("汇总.dem"), result.primary_name, steamid)
    folder.mkdir(parents=True, exist_ok=True)
    _archive_reports(folder, ("problems.md", "summary.json", "basic_report.md", "report_data.json", "report.html"))
    problems = folder / "problems.md"
    summary_file = folder / "summary.json"
    problems.write_text(render_summary_problems(result), encoding="utf-8")
    summary_file.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    basic = folder / "basic_report.md"
    basic.write_text(render_summary_basic_report(result, build_summary_l0(result, cfg), baseline), encoding="utf-8")
    print(f"已写入 {basic}")
    from cs2coach.report.page_data import build_summary_page
    from cs2coach.report.page_html import render_page

    page = build_summary_page(result, cfg, _page_tiers(cfg, args))
    (folder / "report_data.json").write_text(page.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    _check_report_page(page, cfg)
    (folder / "report.html").write_text(render_page(page), encoding="utf-8", newline="\n")
    _refresh_index(args.out)
    print(f"汇总完成：{len(result.matches)} 场，在场 {result.rounds_played_total} 回合，"
          f"跳过 {len(result.skipped)} 场")
    if args.ai:
        outcome = _summary_ai(result, cfg, baseline, folder)
        if outcome:
            return outcome
    print(f"输出目录：{folder}")
    print(f"报告页：{folder / 'report.html'}")
    return 0


def _summary_ai(summary, cfg: dict, baseline: dict | None, folder: Path) -> int:
    from cs2coach.ai.diagnose import DiagnosisError
    from cs2coach.ai.providers import ProviderError, make_provider
    from cs2coach.env import load_env_file
    from cs2coach.summary.diagnose import diagnose_summary

    if cfg["ai"]["provider"] != "local":
        load_env_file(Path(".env"))
    name = cfg["ai"]["provider"]
    model = cfg["ai"].get(name, {}).get("model", "?")
    print(f"正在生成多场 AI 复盘报告（{name} / {model}）……", flush=True)
    started = perf_counter()
    provider = None
    try:
        provider = make_provider(cfg) if name == "local" else None
        d = diagnose_summary(summary, cfg, baseline, provider)
    except (DiagnosisError, ProviderError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        _write_failed_audit(folder / "submission.json", exc, name, model)
        _local_time(name, started, provider)
        return 1
    except Exception as exc:
        msg = _api_error_message(exc)
        if msg is None:
            raise
        print(f"错误: {msg}", file=sys.stderr)
        _local_time(name, started, provider)
        return 1
    _archive_reports(folder, ("report.md", "submission.json"))
    report_file = folder / "report.md"
    audit_file = folder / "submission.json"
    report_file.write_text(d.report, encoding="utf-8")
    audit_file.write_text(json.dumps({
        "submission": d.submission.model_dump(),
        "rendered_submission": d.rendered_submission,
        "violations_history": [[v.model_dump() for v in group] for group in d.violations_history],
        "dropped": [item.model_dump() for item in d.dropped],
        "raw_calls": d.raw_calls,
        "run": _run_info(d),
        "prompt_tokens_history": d.prompt_tokens_history,
        "usage_turns": [asdict(turn) for turn in d.usage_turns],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n" + d.report)
    u = d.usage
    print(f"已写入 {report_file}")
    print(f"已写入 {audit_file}")
    print(f"{d.provider} / {d.model}，工具调用 {len(d.tool_calls)} 次，结论修正 {d.repairs} 次；"
          f"{len(d.usage_turns)} 轮；输入 {u.input_tokens:,} tokens（其中缓存命中 {u.cache_read_tokens:,}）、"
          f"输出 {u.output_tokens:,} tokens（其中思考 {u.reasoning_tokens:,}）")
    _local_time(name, started, provider)
    return 0


def _run_info(d) -> dict:
    """模型、修正次数、工具调用与 token 用量，供评测汇总（scripts/eval_batch.py）读取。"""
    u = d.usage
    return {"provider": d.provider, "model": d.model, "repairs": d.repairs, "tool_calls": len(d.tool_calls),
            "turns": len(d.usage_turns), "input_tokens": u.input_tokens,
            "cache_read_tokens": u.cache_read_tokens, "output_tokens": u.output_tokens,
            "reasoning_tokens": u.reasoning_tokens}


def _write_failed_audit(audit_path: Path, exc: Exception, provider: str = "?", model: str = "?") -> None:
    """生成失败时另存审计（每轮违规、最后一次提交、原始参数），不覆盖上一次成功的 submission.json。"""
    audit = getattr(exc, "audit", None)
    if not audit:
        return
    turns = audit.get("usage_turns", [])
    audit = {**audit, "run": {"provider": provider, "model": model,
             "turns": len(turns),
             **{key: sum(turn.get(key, 0) for turn in turns)
                for key in ("input_tokens", "cache_read_tokens", "output_tokens", "reasoning_tokens")}}}
    failed = audit_path.with_name("submission_failed.json")
    failed.parent.mkdir(parents=True, exist_ok=True)
    failed.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"失败过程已写入 {failed}", file=sys.stderr)


def _local_time(name: str, started: float, provider) -> None:
    if name == "local":
        tokens = getattr(provider, "prompt_tokens_history", [])
        print(f"本地模型总耗时 {perf_counter() - started:.1f} 秒；各轮 prompt_tokens：{tokens}")


def _analyze(args: argparse.Namespace) -> int:
    # demoparser2 / pandas 导入较慢，放到真正需要时再导入，--help 和参数错误能立即返回
    from cs2coach.parsing.match import DemoParseError, parse_match

    if not args.player.isdigit():
        print(f"错误: --player 应为 SteamID64（17 位数字），收到 {args.player!r}", file=sys.stderr)
        return 1
    try:
        cfg = load_config(args.config)
        _apply_ai_overrides(cfg, args)
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
    from cs2coach.features.lobby import lobby_lines
    from cs2coach.features.visibility import Sight
    from cs2coach.features.aim import shot_targets
    from cs2coach.baseline import load_baseline
    from cs2coach.features.problems import detect_problems, problem_frequency
    from cs2coach.report.console import format_moments
    from cs2coach.report.facts import render_facts_file
    from cs2coach.report.problems import render_problem_report

    m = match.meta
    print(f"地图 {m.map_name}，{m.tick_rate} tick，{len(match.rounds)} 个回合，服务器 {m.server_name}\n")
    sight = Sight(match, cfg)
    shots_with_targets = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
    moments = find_moments(match, player.steamid, cfg, sight=sight, shots_with_targets=shots_with_targets)
    moments.lobby = lobby_lines(match, player.steamid, cfg, sight, target_moments=moments,
                                shots_with_targets=shots_with_targets)
    print(format_moments(moments))

    fingerprint = demo_fingerprint(demo)
    folder = _output_folder(args.out, demo, player.name, player.steamid, fingerprint)
    folder.mkdir(parents=True, exist_ok=True)
    _claim_folder(folder, fingerprint)
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
    # 基础版报告（免费档，纯程序）：总是生成，单独成文件，不覆盖 AI 版 report.md
    from cs2coach.report.basic import build_l0, render_basic_report
    from cs2coach.tools.digest import match_score
    names = {sid: p.name for sid, p in match.players.items()}
    basic = folder / "basic_report.md"
    basic.write_text(render_basic_report(moments, build_l0(moments, detected, frequencies, cfg, names), frequencies,
                                         match_score(match, player.steamid), baseline, cfg, detected), encoding="utf-8")
    print(f"已写入 {basic}")
    from cs2coach.report.page_data import build_match_page
    from cs2coach.report.page_html import render_page

    page = build_match_page(moments, detected, frequencies, cfg, _page_tiers(cfg, args))
    (folder / "report_data.json").write_text(page.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    _check_report_page(page, cfg)
    (folder / "report.html").write_text(render_page(page), encoding="utf-8", newline="\n")
    _refresh_index(args.out)
    if not args.ai:
        print("加上 --ai 生成 AI 复盘报告")
        print(f"输出目录：{folder}")
        print(f"报告页：{folder / 'report.html'}")
        return 0
    result = _diagnose(match, moments, cfg, folder / "report.md")
    print(f"输出目录：{folder}")
    print(f"报告页：{folder / 'report.html'}")
    return result


def _diagnose(match, moments, cfg, out: Path) -> int:
    from cs2coach.ai.diagnose import DiagnosisError, diagnose
    from cs2coach.ai.providers import ProviderError, make_provider
    from cs2coach.baseline import load_baseline
    from cs2coach.env import load_env_file

    if cfg["ai"]["provider"] != "local":
        load_env_file(Path(".env"))  # 远端 API key 可放在 .env，本地模型不需要读取

    name = cfg["ai"]["provider"]
    model = cfg["ai"].get(name, {}).get("model", "?")
    print(f"\n正在生成 AI 复盘报告（{name} / {model}）……", flush=True)
    started = perf_counter()
    provider = None
    try:
        provider = make_provider(cfg) if name == "local" else None
        d = diagnose(match, moments, cfg, load_baseline(), provider)
    except (DiagnosisError, ProviderError) as e:
        print(f"错误: {e}", file=sys.stderr)
        _write_failed_audit(out.with_name("submission.json"), e, name, model)
        _local_time(name, started, provider)
        return 1
    except Exception as e:  # 各家 SDK 的网络、认证、限流错误
        msg = _api_error_message(e)
        if msg is None:
            raise
        print(f"错误: {msg}", file=sys.stderr)
        _local_time(name, started, provider)
        return 1

    out.write_text(d.report, encoding="utf-8")
    audit = out.with_name("submission.json")
    audit.write_text(json.dumps({
        "submission": d.submission.model_dump(),
        "rendered_submission": d.rendered_submission,
        "violations_history": [[v.model_dump() for v in group] for group in d.violations_history],
        "dropped": [item.model_dump() for item in d.dropped],
        "raw_calls": d.raw_calls,
        "run": _run_info(d),
        "prompt_tokens_history": d.prompt_tokens_history,
        "usage_turns": [asdict(turn) for turn in d.usage_turns],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n" + d.report)
    u = d.usage
    print(f"已写入 {out}")
    print(f"已写入 {audit}")
    print(f"{d.provider} / {d.model}，工具调用 {len(d.tool_calls)} 次，结论修正 {d.repairs} 次；"
          f"{len(d.usage_turns)} 轮；输入 {u.input_tokens:,} tokens（其中缓存命中 {u.cache_read_tokens:,}）、"
          f"输出 {u.output_tokens:,} tokens（其中思考 {u.reasoning_tokens:,}）")
    _local_time(name, started, provider)
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
