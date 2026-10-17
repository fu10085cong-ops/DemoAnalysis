"""构建匿名分段基准：--tier <id> 或 --all；检测始终沿用职业阈值。"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from pathlib import Path
from statistics import median

from cs2coach.baseline import load_baseline, problem_rates
from cs2coach.config import load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.features.aim import shot_targets
from cs2coach.features.lobby import lobby_line, _lobby_inputs
from cs2coach.features.moments import find_moments
from cs2coach.features.radar import RADAR_METRICS, player_metrics
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.parallel import default_jobs, map_ordered
from cs2coach.summary.discover import _match_id, _members, _supported

ROOT = Path(__file__).resolve().parents[1]
TIER_CONFIG = ROOT / "config/tiers.toml"
TIER_DIR = ROOT / "src/cs2coach/baselines/tiers"


def parse_one_tier_demo(path: Path, member: str | None, cfg: dict) -> tuple[list, list[float]]:
    """解析一场分段 demo，返回 (该场全部合格玩家的瞬间列表, 跳过的覆盖率列表)。

    本子进程不打印；跳过的覆盖率供主进程按原内容和顺序打印。
    """
    match = parse_match(resolve_demo(path, member=member), cfg)
    sight = Sight(match, cfg)
    if not sight.available:
        raise ValueError(f"视野计算不可用：{sight.unavailable_reason}")
    shots = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
    inputs = _lobby_inputs(match, cfg)
    game, skipped = [], []
    for sid, player in match.players.items():
        if player.coverage < cfg["parsing"]["min_target_coverage"]:
            skipped.append(player.coverage)
            continue
        mm = find_moments(match, sid, cfg, sight=sight, shots_with_targets=shots)
        mm.lobby = [lobby_line(match, sid, cfg, mm, inputs=inputs)]
        game.append(mm)
    if not game:
        raise ValueError("没有合格玩家")
    return game, skipped


def build_tier_baseline(tier: dict, mms: list, demo_count: int,
                        pro_baseline: dict, cfg: dict) -> dict:
    """仅输出数值分布与总数，身份、文件路径、原清单均不进入结果。"""
    samples = [player_metrics(line, cfg) for mm in mms for line in mm.lobby if line.steamid == mm.player]
    metrics = {}
    for metric in RADAR_METRICS:
        values = sorted(sample[metric] for sample in samples
                        if sample[metric] is not None and math.isfinite(sample[metric]))
        metrics[metric] = {"median": median(values) if values else None,
                           "n": len(values), "values": values}
    return {"version": 1, "id": tier["id"], "name": tier["name"],
            "source": f"{demo_count} 份 demo、{len(mms)} 人次", "metrics": metrics,
            "problem_rates": problem_rates(mms, pro_baseline, cfg)}


def demo_entries(tier: dict) -> list[tuple[Path, str | None]]:
    """递归发现全部支持的格式，排除解压缓存，同名比赛只收一次。"""
    if "manifest" in tier:
        rows = tomllib.loads((ROOT / tier["manifest"]).read_text(encoding="utf-8"))["demos"]
        return [(ROOT / row["archive"], row.get("member")) for row in rows]
    entries, seen = [], set()
    for folder in tier["dirs"]:
        root = ROOT / folder
        if not root.is_dir():
            raise FileNotFoundError(f"分段 demo 目录不存在：{root}")
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".extracted" in path.parts or not _supported(path):
                continue
            for member in _members(path):
                ident = _match_id(path, member)
                if ident not in seen:
                    seen.add(ident)
                    entries.append((path, member))
    return entries


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--tier")
    choice.add_argument("--all", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--jobs", type=int, default=None,
                        help="并行进程数（默认取配置 tools.jobs 与 CPU 核数的较小值）")
    args = parser.parse_args(argv)
    tiers = tomllib.loads(TIER_CONFIG.read_text(encoding="utf-8"))["tier"]
    selected = tiers if args.all else [t for t in tiers if t["id"] == args.tier]
    if not selected:
        parser.error("分段 id 不存在")
    cfg, pro = load_config(args.config), load_baseline()
    jobs = args.jobs if args.jobs is not None else default_jobs(cfg)
    if pro is None:
        print("缺少职业基准，无法按职业阈值统计问题频率", file=sys.stderr)
        return 1
    failed = False
    for tier in selected:
        entries = demo_entries(tier)

        def report_progress(index: int, outcome, _tier=tier) -> None:
            print(f"{_tier['id']} [{index + 1}/{len(entries)}] 解析中", flush=True)

        outcomes = map_ordered(parse_one_tier_demo, entries, jobs, args=(cfg,), on_done=report_progress)
        mms, successful = [], 0
        for outcome in outcomes:
            if outcome.error is not None:
                print(f"  失败，跳过：{outcome.error.split(':', 1)[0]}", flush=True)
                continue
            game, skipped = outcome.value
            for coverage in skipped:
                print(f"  跳过覆盖率不足的玩家（{coverage:.1%}）", flush=True)
            mms.extend(game)
            successful += 1
            print(f"  成功：{len(game)} 人次", flush=True)
        if not mms:
            print(f"{tier['id']} 没有成功样本，保留已有基准", file=sys.stderr)
            failed = True
            continue
        data = build_tier_baseline(tier, mms, successful, pro, cfg)
        TIER_DIR.mkdir(parents=True, exist_ok=True)
        output = TIER_DIR / f"{tier['id']}.json"
        temp = output.with_suffix(".json.part")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        temp.replace(output)
        print(f"source: {data['source']}", flush=True)
        for metric, stats in data["metrics"].items():
            print(f"  {metric}: n={stats['n']}", flush=True)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
