"""按武器与速度比分段校准看见对手后的第一枪命中率。"""

from __future__ import annotations

import argparse
import math
import tomllib
from collections import defaultdict
from pathlib import Path

from cs2coach.audit import wilson
from cs2coach.baseline import DEMO_MANIFEST
from cs2coach.config import load_config
from cs2coach.features.aim import shot_targets
from cs2coach.features.moments import find_moments
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.summary.discover import _members, _supported

ROOT = Path(__file__).resolve().parents[1]


def demo_sources(root: Path) -> list[tuple[Path, str | None]]:
    """枚举 5E 和完美的原始 demo 及压缩包成员。"""
    sources = []
    for folder in (root / "data/demos/5e_demo", root / "data/demos/perfectworld_demo"):
        if not folder.is_dir():
            raise FileNotFoundError(f"demo 目录不存在：{folder}")
        for path in sorted(folder.iterdir()):
            if path.is_file() and _supported(path):
                members = _members(path)
                if not members:
                    raise ValueError(f"压缩包内没有 demo：{path}")
                sources.extend((path, member) for member in members)
    return sources


def parse_edges(value: str) -> list[float]:
    """边界必须严格递增，最后一段采用半开区间。"""
    try:
        edges = [float(part) for part in value.split(",")]
    except ValueError as exc:
        raise ValueError("--edges 应为逗号分隔的数字") from exc
    if len(edges) < 2 or any(not math.isfinite(edge) for edge in edges) or any(
            left >= right for left, right in zip(edges, edges[1:])):
        raise ValueError("--edges 至少两个严格递增的有限数字")
    return edges


def bucket(value: float | None, edges: list[float]) -> int | None:
    if value is None or not math.isfinite(value):
        return None
    return next((index for index, (left, right) in enumerate(zip(edges, edges[1:]))
                 if left <= value < right), None)


def samples(mm, max_distance: float, edges: list[float]):
    """每次有结局的交火仅取看见对手后的第一枪。"""
    for moment in mm.moments:
        if moment.kind not in ("kill", "death") or moment.engagement is None:
            continue
        if moment.through_smoke or moment.engagement.visibility_note == "smoke":
            continue
        if moment.context.distance is None or moment.context.distance > max_distance:
            continue
        shot = min((item for item in moment.engagement.shots if item.seen_opponent is True),
                   key=lambda item: item.tick, default=None)
        if shot is None or shot.crouched or shot.airborne:
            continue
        index = bucket(shot.speed_ratio, edges)
        if index is not None:
            yield shot.weapon, index, shot.hit


def render(counts: dict, edges: list[float], sources: int, players: int) -> str:
    columns = [f"[{left:g}, {right:g})" for left, right in zip(edges, edges[1:])]
    lines = ["# 看见对手后第一枪命中率", "", f"demo：{sources} 场；玩家：{players} 人次。",
             "排除隔烟、蹲下、空中和超过距离上限的交火；区间为 Wilson 95%。", "",
             "| 武器 | " + " | ".join(columns) + " |", "|---|" + "---:|" * len(columns)]
    for weapon in sorted({weapon for weapon, _ in counts}):
        cells = []
        for index in range(len(edges) - 1):
            hits, total = counts.get((weapon, index), (0, 0))
            if not total:
                cells.append("— (n=0)")
                continue
            low, high = wilson(hits, total - hits)
            cells.append(f"{100 * hits / total:.1f}% (n={total}，95% CI {100 * low:.1f}–{100 * high:.1f}%)")
        lines.append("| " + weapon + " | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def sources_for(kind: str) -> list[tuple[Path, str | None]]:
    sources = demo_sources(ROOT) if kind in ("amateur", "all") else []
    if kind in ("pro", "all"):
        manifest = tomllib.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))["demos"]
        sources.extend((ROOT / item["archive"], item["member"]) for item in manifest)
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demos", choices=("amateur", "pro", "all"), default="amateur")
    parser.add_argument("--max-distance", type=float, default=30.0)
    parser.add_argument("--edges", default="0,0.34,0.45,0.70,1.01")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_distance) or args.max_distance <= 0:
        parser.error("--max-distance 必须是正有限数")
    try:
        edges = parse_edges(args.edges)
    except ValueError as exc:
        parser.error(str(exc))
    cfg = load_config()
    sources = sources_for(args.demos)
    counts = defaultdict(lambda: [0, 0])
    players = 0
    for index, (path, member) in enumerate(sources, 1):
        print(f"[{index}/{len(sources)}] {path.name} :: {member or '原始 demo'}", flush=True)
        match = parse_match(resolve_demo(path, member=member), cfg)
        sight = Sight(match, cfg)
        if not sight.available:
            raise ValueError(f"视野计算不可用：{sight.unavailable_reason}")
        shots = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
        for sid, player in match.players.items():
            if sid <= 0 or player.coverage < cfg["parsing"]["min_target_coverage"]:
                continue
            mm = find_moments(match, sid, cfg, sight=sight, shots_with_targets=shots)
            players += 1
            for weapon, group, hit in samples(mm, args.max_distance, edges):
                counts[(weapon, group)][0] += int(hit)
                counts[(weapon, group)][1] += 1
    result = render(counts, edges, len(sources), players)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result, encoding="utf-8")
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
