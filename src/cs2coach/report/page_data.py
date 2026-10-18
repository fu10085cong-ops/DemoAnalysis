"""新版报告页的确定性数据；单场与多场复用同一套行和跳转模板。"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Literal

from pydantic import BaseModel, Field
import pandas as pd

from cs2coach.features.crosshair import crosshair_offset_sample
from cs2coach.features.highlights import round_time
from cs2coach.features.labels import weapon_label
from cs2coach.features.models import MatchMoments, Moment
from cs2coach.features.problems import ProblemReport, _poisson_tail
from cs2coach.features.radar import RADAR_METRICS, axis_values, median_axes, percentile, player_metrics, sample_count
from cs2coach.features.trades import trade_chance_skip
from cs2coach.report.basic import problem_texts
from cs2coach.report.evidence import evidence_sentence
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.tools.slots import situation_text

TIER_DIR = Path(__file__).resolve().parents[1] / "baselines/tiers"
# 名称、这个数是什么（页面上直接显示）、单位、换算倍数。按回合算的次数换成"每 10 回合"，与问题汇总一致（审查 044）
METRIC_TEXT = {
    "time_to_damage_ms": ("命中速度", "从看见对手到第一次打中他的时间（典型单场的中位数，1 秒以上的不算）", "毫秒", 1),
    "crosshair_offset_m": ("预瞄", "刚看见对手那一刻，准星离他头部的距离（典型单场的中位数，越小越好；5 米内的近身交火、跳跃中和转身才看到的对手不算）", "米", 1),
    "counter_strafing_pct": ("急停", "用步枪、看见敌人时开枪，已经停稳的比例", "%", 1),
    "spray_accuracy_pct": ("压枪", "用步枪连发 3 发以上时，子弹打中敌人的比例", "%", 1),
    "trade_kill_events_per_round": ("补枪", "每 10 回合补枪击杀的次数（一次击杀算一次）", "次", 10),
    "traded_death_pct": ("被队友补回", "自己阵亡后被队友补枪的比例", "%", 1),
    "missed_trade_pct": ("漏补率", "队友在你附近阵亡、你也看到了击杀者时，5 秒内没有补上的比例（越低越好；击杀者被其他队友补掉、只剩你一人、你正在换弹或正和别的对手交火的不算）", "%", 1),
    "utility_damage_per_round": ("道具伤害", "每 10 回合手雷和火造成的伤害", "点", 10),
    "enemies_flashed_per_flash": ("闪光效果", "每颗闪光平均闪到的敌人数", "人", 1),
}
PROBLEM_MEASURE = "每 10 回合出现的次数"
PROBLEM_METRICS = {
    "slow_reaction": "time_to_damage_ms", "poor_crosshair_placement": "crosshair_offset_m",
    "shot_while_moving": "counter_strafing_pct", "spray_while_moving": "counter_strafing_pct",
    "spray_control": "spray_accuracy_pct", "missed_trade": "missed_trade_pct",
    "post_plant_utility_unused": "utility_damage_per_round", "dry_peek_with_utility": "utility_damage_per_round",
}


class Command(BaseModel):
    playdemo: str
    jump: str


class Example(BaseModel):
    match_label: str
    round: int
    time: str
    situation: str
    what: str
    tick: int
    command: Command


class Row(BaseModel):
    id: str
    name: str
    axis: str | None
    you: float
    target: float | None
    unit: str
    measure: str = ""  # 这个数是什么，页面上显示在名称下面
    expected: float | None = None  # 问题：按目标分段的频率，这些回合里应有的次数
    percentile: float | None = None
    samples: int = 0
    evidence_ticks: dict[str, list[int]] = Field(default_factory=dict)  # 漏补命中的原始 tick，按比赛分开核查
    count: int
    severity: Literal["bad", "warn", "ok"]
    text: str
    examples: list[Example]


class Highlight(BaseModel):
    kind: str
    title: str
    match_label: str
    round: int
    time: str
    what: str
    tick: int
    command: Command


class Comparison(BaseModel):
    id: str
    name: str
    values: list[float | None]


class TierView(BaseModel):
    id: str
    name: str
    source: str
    you: list[float | None]
    comparisons: list[Comparison]
    verdict: str
    strengths: list[Row]
    problems: list[Row]


class ReportPage(BaseModel):
    kind: Literal["match", "summary"]
    title: str
    subtitle: str
    axes: list[dict]
    default_tier: str
    rounds_played: int = 0
    tiers: list[TierView]
    highlights: list[Highlight]


def _num(value: float) -> str:
    """与页面一致：10 以上保留 1 位小数，以下保留 2 位，去掉末尾的 0。"""
    return f"{value:.{1 if abs(value) >= 10 else 2}f}".rstrip("0").rstrip(".")


def load_tiers(cfg) -> dict[str, dict]:
    """只加载已经构建的分段文件；缺省分段的可用性由 CLI 提示。"""
    return {data["id"]: data for path in sorted(TIER_DIR.glob("*.json"))
            for data in [json.loads(path.read_text(encoding="utf-8"))]}


def commands(mm: MatchMoments, tick: int, cfg: dict) -> Command:
    review = cfg["review"]
    return Command(playdemo=review["playdemo_template"].format(
        subdir=review["demo_subdir"], demo_stem=Path(mm.demo).stem),
        jump=review["jump_template"].format(tick=tick))


def _metrics(mm, cfg):
    line = next((line for line in mm.lobby if line.steamid == mm.player), None)
    return player_metrics(line, cfg) if line else dict.fromkeys(RADAR_METRICS)


def _axis(metric, axes):
    return next((a["id"] for a in axes if metric in a["metrics"]), None)


def _example(mm, label, source, what, cfg) -> Example:
    context = source.context if isinstance(source, Moment) else source
    return Example(match_label=label, round=source.round,
        time=round_time(context.round_time_s or 0), situation=situation_text(context.side,
            context.alive_own, context.alive_enemy, context.bomb_planted, context.round_time_s) or "",
        what=what, tick=source.jump_tick if isinstance(source, Moment) else source.tick,
        command=commands(mm, source.jump_tick if isinstance(source, Moment) else source.tick, cfg))


def _moment_what(mm, moment):
    opponent = mm.player_names.get(moment.opponent, moment.opponent_name or "对手")
    if moment.kind == "assist":
        return f"闪光助攻击杀 {opponent}"
    return f"用 {weapon_label(moment.weapon)} " + (
        f"击杀 {opponent}" if moment.kind == "kill" else f"与 {opponent} 交火后阵亡")


def _strength_moments(mm, metric, limit, cfg):
    kills = [m for m in mm.moments if m.kind == "kill" and not
             {"teamkill", "after_round_end", "victim_is_bot"}.intersection(m.tags)]
    if metric == "crosshair_offset_m":
        valid = [(value, m) for m in kills if (value := crosshair_offset_sample(m, cfg)) is not None]
        return [m for _, m in sorted(valid, key=lambda pair: (pair[0], pair[1].tick))[:limit]]
    if metric == "time_to_damage_ms":
        field = "time_to_damage_s"
        valid = [m for m in kills if m.engagement and getattr(m.engagement, field) is not None]
        return sorted(valid, key=lambda m: (getattr(m.engagement, field), m.tick))[:limit]
    if metric == "counter_strafing_pct":
        return [m for m in kills if m.engagement and m.engagement.first_shot_stopped is True]
    if metric == "spray_accuracy_pct":
        from cs2coach.features.aim_stats import _spray_shots
        from cs2coach.parsing.weapons import RIFLES

        own = _metrics(mm, cfg)[metric]
        if own is None:
            return []
        valid = []
        for m in kills:
            if not m.engagement or not m.engagement.shots:
                continue
            shots = pd.DataFrame([s.model_dump() for s in sorted(m.engagement.shots, key=lambda s: s.tick)])
            # 每段连发单独比较；不能让另一段高命中率掩盖这段的失误。
            gap = cfg["aim_stats"]["spray_max_gap_seconds"] * mm.tick_rate
            runs = ((shots.tick.diff() > gap) | (shots.weapon != shots.weapon.shift())).cumsum()
            for _, run in shots.groupby(runs):
                spray = _spray_shots(run, mm.tick_rate, cfg["aim_stats"])
                spray = spray[(spray.seen_opponent == True) & spray.weapon.isin(RIFLES)]
                if not spray.empty and 100 * spray.hit.mean() >= own:
                    valid.append(m)
                    break
        return valid
    if metric == "trade_kill_events_per_round":
        return [m for m in kills if "trade_kill" in m.tags]
    if metric == "missed_trade_pct":
        trades = {(c.round, c.killer, c.my_trade_tick) for c in mm.trade_chances
                  if c.traded_by_me and trade_chance_skip(c, cfg) is None}
        return sorted((m for m in kills if (m.round, m.opponent, m.tick) in trades),
                      key=lambda m: m.tick)[:limit]
    if metric == "traded_death_pct":
        return [m for m in mm.moments if m.kind == "death" and "traded" in m.tags]
    if metric == "enemies_flashed_per_flash":
        return [m for m in mm.moments if "flash_assist" in m.tags]
    return []


def _rows(games, frequency, cfg, tier):
    radar = cfg["radar"]
    axes, limit = radar["axes"], radar["max_examples"]
    texts = problem_texts()
    problems = []
    rounds = sum(mm.rounds_played for mm, _, _ in games)
    rates = tier.get("problem_rates", {})

    def expected(row):
        rate = rates.get(row.problem, {})
        hits, total = rate.get("hits"), rate.get("rounds")
        if hits is None or hits < 0 or total is None or total <= 0:
            return None
        return rounds * (hits if hits else 0.5) / total

    candidates = sorted((r for r in frequency if r.hits > 0 and
                         (e := expected(r)) is not None and r.hits > e),
                        key=lambda r: (-(r.hits - expected(r)), -r.hits, r.problem))[:radar["max_problems"]]
    for row in candidates:
        exp = expected(row)
        rate = rates[row.problem]
        target = rate.get("per_10_rounds", 10 * rate["hits"] / rate["rounds"])
        excess = row.hits - exp
        severity = ("bad" if _poisson_tail(row.hits, exp) < cfg["problems"]["frequency_max_chance"]
                    and excess >= radar["problem_min_excess"] else "warn" if excess > 0 else "ok")
        examples, evidence_ticks = [], {}
        for mm, report, label in games:
            moments = {m.id: m for m in mm.moments}
            for hit in report.hits:
                if hit.problem != row.problem:
                    continue
                if row.problem == "missed_trade":
                    evidence = hit.evidence
                    if evidence.get("tick") is not None:
                        evidence_ticks.setdefault(label, []).append(evidence["tick"])
                    for trade in mm.missed_trades:
                        if (hit.target == f"R{trade.round}" and all(
                                getattr(trade, field) == evidence.get(field) for field in ("tick", "teammate", "killer"))):
                            examples.append(_example(mm, label, trade, evidence_sentence(hit), cfg))
                elif hit.target in moments:
                    examples.append(_example(mm, label, moments[hit.target], evidence_sentence(hit), cfg))
        examples.sort(key=lambda e: (next(i for i, g in enumerate(games) if g[2] == e.match_label), e.tick))
        unique = {(e.match_label, e.round, e.tick): e for e in examples}
        why = texts.get(row.problem, {}).get("why", "")
        problems.append(Row(id=row.problem, name=row.label, axis=_axis(PROBLEM_METRICS.get(row.problem), axes),
            you=row.per_10_rounds, target=target, unit="次", measure=PROBLEM_MEASURE, count=row.hits,
            expected=exp, severity=severity, evidence_ticks=evidence_ticks,
            text=why.split("。", 1)[0] + ("。" if why else ""), examples=list(unique.values())[:limit]))
    strengths = []
    values = [_metrics(mm, cfg) for mm, _, _ in games]
    metrics = {key: median(valid) if (valid := [s[key] for s in values if s[key] is not None]) else None
               for key in RADAR_METRICS}
    # 多场统一先算每场百分位，再取中位数；展示的原始值仍是典型单场。
    percentiles = {key: median(valid) if (valid := [p for s in values
                    if (p := percentile(key, s[key], tier, cfg)) is not None]) else None
                   for key in RADAR_METRICS}
    flagged = {row.axis for row in problems if row.severity != "ok" and row.axis}
    # 只从雷达上画出来的维度里选优点（道具使用已移出雷达）
    ranked = [(key, p) for key, p in percentiles.items() if (axis := _axis(key, axes)) is not None
              and axis not in flagged and p is not None and p >= radar["strength_min_percentile"]]
    for key, p in sorted(ranked, key=lambda pair: -pair[1])[:radar["max_strengths"]]:
        selected = [(mm, label, m) for mm, _, label in games for m in _strength_moments(mm, key, limit, cfg)]
        count = len(selected)
        samples = sum(sample_count(line, key) for (mm, _, _), values_for_match in zip(games, values)
                      for line in mm.lobby if line.steamid == mm.player and values_for_match[key] is not None)
        if key == "crosshair_offset_m":
            selected.sort(key=lambda item: (crosshair_offset_sample(item[2], cfg), item[2].tick))
        if key == "time_to_damage_ms":
            field = "time_to_damage_s"
            selected.sort(key=lambda item: (getattr(item[2].engagement, field), item[2].tick))
        selected = selected[:limit]
        selected.sort(key=lambda item: (next(i for i, g in enumerate(games) if g[2] == item[1]), item[2].tick))
        name, measure, unit, scale = METRIC_TEXT[key]
        if len(games) > 1:
            measure += "（典型单场）"
        median_value = tier.get("metrics", {}).get(key, {}).get("median")
        strengths.append(Row(id=key, name=name, axis=_axis(key, axes), you=metrics[key] * scale,
            target=None if median_value is None else median_value * scale, unit=unit, measure=measure, count=count,
            percentile=p, samples=samples, severity="ok", text=f"在{tier['name']}里排在第 {p:g} 百分位。",
            examples=[_example(mm, label, m, _moment_what(mm, m), cfg) for mm, label, m in selected]))
    return strengths, problems


def _verdict(kind, games, tier_name, problems, axes, values, cfg):
    bad = [row for row in problems if row.severity == "bad"]
    if bad:
        top = max(bad, key=lambda row: row.count - row.expected)
        scope = "这场" if kind == "match" else f"{len(games)} 场里"
        verdict = f"最该练的是{top.name}：{scope}出现 {top.count} 次，按{tier_name}的频率约 {round(top.expected)} 次。"
    else:
        period = "这场" if kind == "match" else "这段时间"
        verdict = f"{period}没有检出明显多于{tier_name}的问题。"
    valid = [(axis["name"], value) for axis, value in zip(axes, values) if value is not None]
    if len(valid) >= cfg["radar"]["verdict_min_axes"]:
        name, p = min(valid, key=lambda pair: pair[1])
        weak = cfg["radar"]["verdict_weak_percentile"]
        # 最低的维度也在门槛以上时不叫"最弱"（审查 045：第 51 百分位被说成最弱，读起来像短板）
        verdict += (f"雷达上最弱的是{name}（第 {round(p)} 百分位）。" if p < weak else
                    f"雷达上没有明显偏弱的维度，都在第 {weak:g} 百分位以上。")
    else:
        verdict += "可用的指标不够多，暂不判断整体强弱。"
    return verdict


def _build_page(kind, games, frequency, cfg, tiers, title, subtitle):
    radar = cfg["radar"]
    axes = radar["axes"]
    views = []
    for ident, tier in tiers.items():
        values = [axis_values(_metrics(mm, cfg), tier, axes, cfg) for mm, _, _ in games]
        comparisons = []
        if kind == "match":
            lobby = games[0][0].lobby
            all_values = [axis_values(player_metrics(line, cfg), tier, axes, cfg) for line in lobby]
            average = [mean(valid) if (valid := [v[i] for v in all_values if v[i] is not None]) else None
                       for i in range(len(axes))]
            comparisons.append(Comparison(id="match_avg", name="本场平均", values=average))
            if lobby:
                best = min(lobby, key=lambda line: (-(line.kills - line.deaths), -line.kills, line.steamid))
                comparisons.append(Comparison(id="match_best", name=f"本场最佳（{best.name}，{best.kills}/{best.deaths}）",
                    values=axis_values(player_metrics(best, cfg), tier, axes, cfg)))
        comparisons.append(Comparison(id="tier_median", name="参考中线（50）", values=[50] * len(axes)))
        strengths, problems = _rows(games, frequency, cfg, tier)
        you = median_axes(values, len(axes))
        views.append(TierView(id=ident, name=tier["name"], source=tier["source"],
            you=you, comparisons=comparisons, strengths=strengths, problems=problems,
            verdict=_verdict(kind, games, tier["name"], problems, axes, you, cfg)))
    order = {"clutch": 0, "multikill": 1, "opening_win": 2, "flash_assist": 3}
    ranked = sorted((((order.get(h.kind, 9), -h.size, index, h.tick), mm, label, h)
                     for index, (mm, _, label) in enumerate(games) for h in mm.highlights), key=lambda item: item[0])
    highlights = [Highlight(**h.model_dump(exclude={"size"}), match_label=label, command=commands(mm, h.tick, cfg))
                  for _, mm, label, h in ranked[:radar["max_highlights"]]]
    return ReportPage(kind=kind, title=title, subtitle=subtitle,
        axes=[{"id": a["id"], "name": a["name"]} for a in axes], default_tier=radar["default_tier"],
        rounds_played=sum(mm.rounds_played for mm, _, _ in games), tiers=views, highlights=highlights)


def build_match_page(mm: MatchMoments, report: ProblemReport, frequency, cfg, tiers: dict[str, dict]) -> ReportPage:
    return _build_page("match", [(mm, report, "本场")], frequency, cfg, tiers,
                       f"{mm.player_name} · {mm.map_name}", f"本场 · 在场 {mm.rounds_played} 回合")


def build_summary_page(summary: PlayerSummary, cfg, tiers: dict[str, dict]) -> ReportPage:
    games = [(*summary.detail(match.number), f"{match.map_name.removeprefix('de_').title()} {match.number}")
             for match in summary.matches]
    return _build_page("summary", games, summary.frequency, cfg, tiers,
                       f"{summary.primary_name} · 多场汇总",
                       f"{len(summary.matches)} 场 · 在场 {summary.rounds_played_total} 回合")
