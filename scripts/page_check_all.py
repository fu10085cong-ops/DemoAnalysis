"""逐场逐人检查临时业余分段的报告页，不调用 AI，不输出玩家身份。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cs2coach.baseline import load_baseline
from cs2coach.config import load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.features.aim import shot_targets
from cs2coach.features.lobby import _lobby_inputs, lobby_line
from cs2coach.features.moments import find_moments
from cs2coach.features.problems import detect_problems, problem_frequency
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.parallel import default_jobs, map_ordered
from cs2coach.report.page_check import check_page
from cs2coach.report.page_data import build_match_page, load_tiers
from scripts.build_tier_baseline import TIER_CONFIG, demo_entries


def check_one_demo(path: Path, member: str | None, cfg: dict, pro: dict, tiers: dict) -> list[tuple]:
    """检查一场 demo 的全部合格玩家，返回按玩家顺序的 ("ok", 违规列表) 或 ("error", 异常类型)。

    "玩家NN" 编号由主进程在合并结果时按输入顺序分配，本子进程不打印也不编号。
    """
    match = parse_match(resolve_demo(path, member=member), cfg)
    sight = Sight(match, cfg)
    if not sight.available:
        raise ValueError("视野不可用")
    shots = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
    inputs = _lobby_inputs(match, cfg)
    game = []
    for sid, player in match.players.items():
        if sid <= 0 or player.coverage < cfg["parsing"]["min_target_coverage"]:
            continue
        try:
            mm = find_moments(match, sid, cfg, sight=sight, shots_with_targets=shots)
            mm.lobby = [lobby_line(match, sid, cfg, mm, inputs=inputs)]
            report = detect_problems(mm, pro, cfg)
            game.append((mm, report))
        except Exception as exc:
            game.append((None, f"{type(exc).__name__}"))
    # 先备齐同场统计，让平均线与最佳玩家也和 CLI 的单场报告一致。
    lobby = [mm.lobby[0] for mm, _ in game if mm is not None]
    results = []
    for mm, report in game:
        if mm is None:
            results.append(("error", report))
            continue
        mm.lobby = lobby
        page = build_match_page(mm, report, problem_frequency(report, mm, pro, cfg), cfg, tiers)
        results.append(("ok", check_page(page, cfg)))
    return results


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=None,
                        help="并行进程数（默认取配置 tools.jobs 与 CPU 核数的较小值）")
    args = parser.parse_args(argv)
    cfg, pro = load_config(), load_baseline()
    jobs = args.jobs if args.jobs is not None else default_jobs(cfg)
    tiers = load_tiers(cfg)
    if pro is None or "amateur_tmp" not in tiers:
        print("缺少职业基准或临时业余分段基准，无法检查")
        return 1
    tier = next(t for t in tomllib.loads(TIER_CONFIG.read_text(encoding="utf-8"))["tier"]
                if t["id"] == "amateur_tmp")
    try:
        entries = demo_entries(tier)
    except OSError as exc:
        print(f"无法读取分段 demo 目录：{type(exc).__name__}")
        return 1
    rules = ("维度冲突", "结论选题不符", "口径或单位为空", "数量超限", "优点百分位不足", "优点样本不足", "漏补例子无命中依据")
    counts, examples = Counter(dict.fromkeys(rules, 0)), defaultdict(list)
    players = checked = failed = 0

    def report_progress(index: int, outcome) -> None:
        print(f"比赛{index + 1:02d} [{index + 1}/{len(entries)}] 解析中", flush=True)

    outcomes = map_ordered(check_one_demo, entries, jobs, args=(cfg, pro, tiers),
                           on_done=report_progress)
    for index, outcome in enumerate(outcomes, 1):
        if outcome.error is not None:
            failed += 1
            print(f"  无法检查：{outcome.error.split(':', 1)[0]}", flush=True)
            continue
        for status, detail in outcome.value:
            players += 1
            label = f"玩家{players:02d}"
            if status == "error":
                failed += 1
                print(f"  {label} 无法检查：{detail}", flush=True)
                continue
            checked += 1
            for issue in detail:
                rule = issue.split("：", 1)[0]
                counts[rule] += 1
                if len(examples[rule]) < 5:
                    examples[rule].append(f"比赛{index:02d} {label} {issue}")
    for rule, count in sorted(counts.items()):
        print(f"{rule}：{count} 处")
        for example in examples[rule]:
            print(f"  {example}")
    print(f"已检查 {checked} 人次，无法检查 {failed} 项")
    print(f"报告页一致性检查：{sum(counts.values())} 处违规")
    return int(sum(counts.values()) > 0 or failed > 0 or checked == 0)


if __name__ == "__main__":
    sys.exit(main())
