"""单场体检卡和同场表：先计算结构化数据，再生成 Markdown。"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel

from cs2coach.baseline import compare
from cs2coach.features.models import LobbyLine, MatchMoments
from cs2coach.features.problems import PROBLEMS, FrequencyRow, ProblemReport
from cs2coach.features.ranking import candidate_targets
from cs2coach.report.basic import choose_problems
from cs2coach.tools.drills import drill_labels

_GRADE_LABELS = ("很好", "好", "一般", "偏弱", "需加强")


class CardRow(BaseModel):
    key: str
    label: str
    value_text: str | None
    rank: int | None
    ranked_count: int
    grade: int | None
    grade_label: str | None
    pro_better_than_pct: float | None
    problem_note: str | None


class SummaryCard(BaseModel):
    rows: list[CardRow]
    focus: CardRow | None
    focus_drills: list[str]
    highlight: str | None


def _value(line: LobbyLine, field: str) -> float | None:
    obj = line
    for part in field.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return float(obj) if math.isfinite(float(obj)) else None


def _format(field: str, value: float | None) -> str | None:
    if value is None:
        return None
    if field == "aim.crosshair_offset_m":
        return f"{value:.2f} 米"
    if field.endswith("_deg"):
        return f"{value:.1f}°"
    if field.endswith("_ms"):
        return f"{value:.0f} 毫秒"
    if field.endswith("_pct") or field == "accuracy_pct":
        return f"{value:.0f}%"
    return f"{value:.0f}"


def _rank(value: float | None, values: list[float], direction: str) -> int | None:
    if value is None:
        return None
    return 1 + sum(v > value if direction == "higher" else v < value for v in values)


def _extreme_marker(value: float, values: list[float], direction: str, mark_top: int) -> str:
    """并列组完整落入前/后名额才标；全列相同不标。"""
    if not values or min(values) == max(values):
        return ""
    better = sum(v > value if direction == "higher" else v < value for v in values)
    worse = sum(v < value if direction == "higher" else v > value for v in values)
    tied = len(values) - better - worse
    if better + tied <= mark_top:
        return "▲"
    if worse + tied <= mark_top:
        return "▼"
    return ""


def _highlight(mm: MatchMoments, problems: ProblemReport, cfg: dict) -> str | None:
    moments = {m.id: m for m in mm.moments}
    for pid in cfg["report"]["card"]["highlight_priority"]:
        targets = candidate_targets(mm, problems.hits, pid, len(problems.hits))
        choices = [moments[target] for target in targets if target in moments]
        if not choices:
            continue
        if pid == "won_duel_seen_first":
            choices.sort(key=lambda item: (
                item.engagement is None or item.engagement.time_to_damage_s is None,
                item.engagement.time_to_damage_s if item.engagement and item.engagement.time_to_damage_s is not None
                else math.inf, item.round, item.tick))
        elif pid == "good_trade":
            choices.sort(key=lambda item: (
                item.trade is None or item.trade.avenge_delay_s is None,
                item.trade.avenge_delay_s if item.trade and item.trade.avenge_delay_s is not None
                else math.inf, item.round, item.tick))
        m = choices[0]
        prefix = f"R{m.round}" + (f" 在 {m.context.place}" if m.context.place else "") + " "
        if pid == "won_duel_seen_first":
            e = m.engagement
            ttd = e.time_to_damage_s if e else None
            timing = (f"{Decimal(str(ttd)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} 秒打中并"
                      if ttd is not None and
                      0 <= ttd * 1000 <= cfg["report"]["card"]["highlight_show_ttd_max_ms"] else "")
            shot = "爆头击杀" if m.headshot else "击杀"
            situation = (f"，当时 {m.context.alive_own}v{m.context.alive_enemy}"
                         if m.context.alive_own != m.context.alive_enemy else "")
            return f"{prefix}先发现并先开枪，{timing}{shot}对手{situation}。"
        if pid == "good_trade":
            delay = m.trade.avenge_delay_s if m.trade else None
            timing = f"{delay:.2f} 秒内" if delay is not None else "及时"
            return f"{prefix}{timing}为队友补枪击杀。"
        if pid == "flash_assist":
            return f"{prefix}用闪光助攻队友击杀。"
    return None


def build_card(mm: MatchMoments, problems: ProblemReport, frequency: list[FrequencyRow],
               cfg: dict, baseline: dict | None) -> SummaryCard:
    card_cfg = cfg["report"]["card"]
    chosen = {r.problem: r for r in choose_problems(frequency)}
    frequency_by_id = {r.problem: r for r in frequency}
    target = next((line for line in mm.lobby if line.is_target), None)
    rows = []
    by_key = {}
    for dimension in card_cfg["dimensions"]:
        field, direction, pid = dimension["field"], dimension["direction"], dimension["problem"]
        values = [v for line in mm.lobby if (v := _value(line, field)) is not None]
        value = _value(target, field) if target is not None else None
        rank = _rank(value, values, direction)
        grade = None
        if rank is not None and len(values) >= card_cfg["min_ranked"]:
            pct = 100 * rank / len(values)
            grade = 1 + sum(pct > cutoff for cutoff in card_cfg["grade_cutoffs"])
        pro = compare(field.removeprefix("aim."), value, baseline) if field.startswith("aim.") else None
        hits = sum(h.problem == pid for h in problems.hits) if pid in chosen else 0
        note = f"但有 {hits} 次{PROBLEMS[pid].label}，见下文" if hits else None
        row = CardRow(key=dimension["key"], label=dimension["label"], value_text=_format(field, value),
                      rank=rank, ranked_count=len(values), grade=grade,
                      grade_label=_GRADE_LABELS[grade - 1] if grade else None,
                      pro_better_than_pct=pro["better_than_pro_pct"] if pro else None, problem_note=note)
        rows.append(row)
        by_key[row.key] = (row, pid)
    candidates = [(row, pid) for row, pid in by_key.values()
                  if row.grade in (4, 5) and pid and any(h.problem == pid for h in problems.hits)]
    if candidates:
        focus, focus_pid = max(candidates, key=lambda pair: (
            pair[0].rank or 0,
            (frequency_by_id[pair[1]].excess_hits or 0) if pair[1] in frequency_by_id else 0))
    else:
        focus, focus_pid = next(((row, pid) for selected in choose_problems(frequency)
                                 for row, pid in by_key.values() if pid == selected.problem), (None, None))
    labels = drill_labels(cfg)
    drills = [labels[d["id"]] for d in cfg["training"]["drills"]
              if focus_pid and focus_pid in d["problems"] and "min_aim_problems" not in d]
    return SummaryCard(rows=rows, focus=focus, focus_drills=drills,
                       highlight=_highlight(mm, problems, cfg))


def render_card(card: SummaryCard, mm: MatchMoments) -> list[str]:
    lines = ["## 本场体检", "", "| 维度 | 本场 | 同场位置 | 等级 |", "|---|---:|---|---|"]
    for row in card.rows:
        rank = f"同场第 {row.rank}（共 {row.ranked_count} 人）" if row.rank else "样本不足"
        if row.pro_better_than_pct is not None:
            rank += f"，优于 {row.pro_better_than_pct:.0f}% 职业样本"
        grade = f"{'●' * (6-row.grade)}{'○' * (row.grade-1)} {row.grade_label}" if row.grade else "—"
        if row.problem_note:
            grade += f"；{row.problem_note}"
        lines.append(f"| {row.label} | {row.value_text or '样本不足'} | {rank} | {grade} |")
    if card.focus:
        lines.extend(["", f"**最该练的一件事**：{card.focus.label}。"
                      + (f"推荐练习：{'、'.join(card.focus_drills)}。" if card.focus_drills else "")])
    if card.highlight:
        lines.extend(["", f"**本场高光**：{card.highlight}"])
    return lines


def _marked(line: LobbyLine, field: str, direction: str, lobby: list[LobbyLine], mark_top: int) -> str:
    value = _value(line, field)
    if value is None:
        return "—"
    values = [v for item in lobby if (v := _value(item, field)) is not None]
    return _extreme_marker(value, values, direction, mark_top) + (_format(field, value) or "—")


def render_lobby_tables(mm: MatchMoments, cfg: dict) -> list[str]:
    """先本队后对手，队内按击杀降序；每列按自己的方向标出同场极端值。"""
    lobby = mm.lobby
    target = next((line for line in lobby if line.is_target), None)
    own_team = target.team if target else 0
    ordered = sorted(lobby, key=lambda line: (line.team != own_team, -line.kills, line.name))
    mark_top = cfg["report"]["card"]["mark_top"]
    directions = {d["field"]: d["direction"] for d in cfg["report"]["card"]["dimensions"]}

    def cell(line: LobbyLine, field: str) -> str:
        return _marked(line, field, directions.get(field, "higher"), lobby, mark_top)

    def special(line: LobbyLine, attr: str, opposite: str | None = None, display: str | None = None) -> str:
        value = getattr(line, attr) - getattr(line, opposite) if opposite else getattr(line, attr)
        values = [(getattr(item, attr) - getattr(item, opposite) if opposite else getattr(item, attr))
                  for item in lobby]
        return _extreme_marker(value, values, "higher", mark_top) + (display or str(value))

    lines = ["## 同场对比", "", "### 枪法", "",
             "| 玩家 | K-D | 命中率 | 爆头率 | 出枪速度 | 预瞄 | 急停 | 压枪 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for i, line in enumerate(ordered):
        if i and ordered[i - 1].team == own_team and line.team != own_team:
            lines.append("| **对手** |  |  |  |  |  |  |  |")
        name = "▶你" if line.is_target else line.name.replace("|", "\\|")
        kd = special(line, "kills", "deaths", f"{line.kills}-{line.deaths}")
        lines.append(f"| {name} | {kd} | {cell(line, 'accuracy_pct')} | {cell(line, 'aim.headshot_accuracy_pct')} | "
                     f"{cell(line, 'aim.time_to_damage_ms')} | {cell(line, 'aim.crosshair_offset_m')} | "
                     f"{cell(line, 'aim.counter_strafing_pct')} | {cell(line, 'aim.spray_accuracy_pct')} |")
    lines.extend(["", "### 对局", "", "| 玩家 | 首杀（成功-失败） | 补枪击杀 | 闪光助攻 | 道具伤害 |",
                  "|---|---:|---:|---:|---:|"])
    for i, line in enumerate(ordered):
        if i and ordered[i - 1].team == own_team and line.team != own_team:
            lines.append("| **对手** |  |  |  |  |")
        name = "▶你" if line.is_target else line.name.replace("|", "\\|")
        opening = special(line, "opening_kills", "opening_deaths",
                          f"{line.opening_kills}-{line.opening_deaths}")
        lines.append(f"| {name} | {opening} | {special(line, 'trade_kills')} | "
                     f"{special(line, 'flash_assists')} | {cell(line, 'utility_damage')} |")
    return lines


def render_front(mm: MatchMoments, problems: ProblemReport, frequency: list[FrequencyRow],
                 cfg: dict, baseline: dict | None) -> list[str]:
    """基础版和 AI 版共用完全相同的程序生成前页。"""
    card = build_card(mm, problems, frequency, cfg, baseline)
    window = cfg["moments"]["trade_window_seconds"]
    note = cfg["report"]["card"]["note"].format(trade_window=f"{window:g}")
    return [*render_card(card, mm), "", *render_lobby_tables(mm, cfg), "", note,
            "", "下面是详细分析", ""]
