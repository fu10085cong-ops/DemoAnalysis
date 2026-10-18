"""整场摘要：直接放进提示词，让便宜的模型不调用工具也能写出报告（docs/product.md：程序一侧要"厚"）。

每个决定性瞬间一行，带编号；AI 引用编号，需要细节时再调用工具。
"""

from __future__ import annotations

from typing import Any

from cs2coach.baseline import METRICS, compare, pro_training_note
from cs2coach.config import load_config
from cs2coach.features.labels import GRENADE_LABELS, KIND_LABELS, ROUND_END_REASONS, absent_rounds_note, tag_label
from cs2coach.features.models import AimStats, BlindInfo, MatchMoments, Moment
from cs2coach.features.problems import PROBLEMS, FrequencyRow, ProblemReport
from cs2coach.report.evidence import evidence_sentence, missed_trade_context
from cs2coach.report.problems import frequency_table
from cs2coach.parsing.match import MatchData
from cs2coach.parsing.models import Team
from cs2coach.features.ranking import candidate_targets, ranked_hits
from cs2coach.tools.slots import slot_guidance

METRIC_LABELS = {
    "crosshair_offset_m": ("预瞄（初始偏差）", "米", "crosshair_offset_samples", "次交火"),
    "time_to_damage_ms": ("Time to Damage", "ms", "time_to_damage_samples", "次遭遇"),
    "crosshair_placement_deg": ("Crosshair Placement", "°", "crosshair_placement_samples", "次遭遇"),
    "counter_strafing_pct": ("Counter-Strafing", "%", "counter_strafing_shots", "发步枪"),
    "spray_accuracy_pct": ("Spray Accuracy", "%", "spray_shots", "发扫射"),
    "spotted_accuracy_pct": ("Spotted Accuracy", "%", "spotted_shots", "发"),
    "headshot_accuracy_pct": ("Headshot Accuracy", "%", "hits", "次命中"),
}


class TypedNumbers(list):
    """沿用数值列表接口，同时携带可作为百分比引用的来源值。"""

    def __init__(self, values=(), percentages=()):
        super().__init__(values)
        self.percentages = list(percentages)


def player_side(match: MatchData, steamid: int, round_number: int) -> str | None:
    """该回合玩家所在阵营；玩家不在场（中途掉线、重连前，fields.md 陷阱 24）时取队友的阵营。"""
    r = match.round(round_number)
    if r is None:
        return None
    tick = _round_tick(r)
    side = _side_at(match, steamid, tick)
    if side is not None:
        return side
    for mate in _teammates(match, steamid):
        side = _side_at(match, mate, tick)
        if side is not None:
            return side
    return None


def _round_tick(r) -> int:
    return r.freeze_end_tick if r.freeze_end_tick is not None else r.start_tick


def _side_at(match: MatchData, steamid: int, tick: int) -> str | None:
    st = match.state_at(steamid, tick)
    if st is None:
        return None
    try:
        return Team(int(st["team_num"])).name
    except (ValueError, TypeError):
        return None


def _teammates(match: MatchData, steamid: int) -> list[int]:
    """在玩家在场的第一个回合里与他同阵营的其他玩家。"""
    for r in match.rounds:
        tick = _round_tick(r)
        side = _side_at(match, steamid, tick)
        if side is not None:
            return [s for s in match.players if s != steamid and _side_at(match, s, tick) == side]
    return []


def build_digest(match: MatchData, mm: MatchMoments, baseline: dict[str, Any] | None,
                 problems: ProblemReport | None = None, frequency: list[FrequencyRow] | None = None,
                 cfg: dict[str, Any] | None = None) -> str:
    note = pro_training_note(mm.player, baseline, cfg or load_config())
    lines = [*([note, ""] if note else []), _header(match, mm), "",
             _aim_section(mm.aim_stats, baseline), "", summary_section(mm)]
    if frequency is not None:
        rules = (cfg or load_config())["problems"]
        lines.extend(["", "## 问题频率",
            f"突出问题需偶然概率低于 {rules['frequency_max_chance'] * 100:g}%，"
            f"且比职业频率预期至少多 {float(rules['frequency_min_excess']):g} 次。"
            "报告应优先讲突出问题；多出次数大于 0 但不突出的情况需要更多比赛确认。",
            "标为“不高于职业水平”的项目不是问题，不要作为问题提出。", ""])
        lines.extend(frequency_table(frequency))
    if problems is not None:
        lines.extend(["", "## 检测到的问题", "只能从以下命中中选择问题和亮点；other 除外。编号只放在 targets 中。",
            "检测口径：slow_reaction 和 poor_crosshair_placement 只能在打中过对手的交火中判断；死亡时如果没打中对手就无法判断。"
            "因此这两类问题在击杀中出现得更多，击杀和死亡的比例不说明问题。"
            "TTD 是从看见对手到第一次命中的时间，分为“看见→开枪”和“开枪→命中”，不能说成开第一枪用了相同时间。"
            "频率按每 10 回合计算，与职业比较的是整体频率，不按击杀和死亡分开。"])
        limit = (cfg or load_config())["checker"]["finding_candidates"]
        lines.append(f"每个问题按代表性排序（偏离职业阈值越大越靠前），finding 的 targets 只能从标“〔候选〕”的前 {limit} 个中选；"
                     "pattern 可以列出全部命中。")
        for pid, spec in PROBLEMS.items():
            hits = [h for h in problems.hits if h.problem == pid]
            if hits:
                ranked = ranked_hits(mm, hits, pid) + [h for h in hits if h.target == "match"]
                chosen = set(candidate_targets(mm, hits, pid, limit))
                lines.append(f"### {spec.label}（{pid}）")
                lines.extend(f"- {'〔候选〕' if h.target in chosen else ''}{h.target}：{evidence_sentence(h)}"
                             for h in ranked)
        if not problems.hits:
            lines.append("没有已启用规则的命中，不要将背景数据当作已检测的问题。")
    lines.extend(["", _moments_section(mm)])
    if mm.missed_trades:
        lines += ["", "## 漏补（队友阵亡时自己在附近、窗口内在雷达上发现过击杀者，但没有补上；距离不考虑视线，近似）"]
        for t in mm.missed_trades:
            situ = missed_trade_context(t.model_dump())
            lines.append(f"- [[R{t.round}]] tick {t.tick}：队友 {match.name_of(t.teammate)} 被 {match.name_of(t.killer)} 击杀，"
                         + (situ + " " if situ else "") + f"距离 {t.distance:.0f}"
                         + ("，后来别的队友补上了" if t.killer_traded_by_other else ""))
    if mm.excluded:
        lines += ["", "## 不参与分析的记录"]
        lines += [f"- [[R{x.round}]] tick {x.tick} {KIND_LABELS.get(x.kind, x.kind)}：{x.reason}" for x in mm.excluded]
    lines.extend(["", slot_guidance(mm)])
    return "\n".join(lines)


def whole_match_numbers(mm: MatchMoments, baseline: dict[str, Any] | None,
                        frequency: list[FrequencyRow], score: tuple[int, int]) -> list[float]:
    """仅收集整场统计、职业对比、问题频率及比分，排除逐次瞬间。"""
    values: list[float] = TypedNumbers([mm.rounds, mm.rounds_played, *score])
    stats = mm.aim_stats
    if stats is not None:
        for metric in METRICS:
            _, _, count_field, _ = METRIC_LABELS[metric]
            value = getattr(stats, metric)
            values.append(getattr(stats, count_field))
            if value is not None:
                values.append(value)
                if metric.endswith("_pct"):
                    values.percentages.append(value)
                comparison = compare(metric, value, baseline)
                if comparison is not None:
                    values.extend(comparison[key] for key in
                                  ("pro_median", "pro_p25", "pro_p75", "better_than_pro_pct"))
                    values.percentages.append(comparison["better_than_pro_pct"])
                    if metric.endswith("_pct"):
                        values.percentages.extend(comparison[key] for key in ("pro_median", "pro_p25", "pro_p75"))
    for row in frequency:
        values.append(10)
        values.extend((row.hits, row.per_10_rounds))
        if row.pro_per_10_rounds is not None:
            values.append(row.pro_per_10_rounds)
        if row.ratio is not None:
            values.append(row.ratio)
        for value in (row.expected_hits, row.excess_hits):
            if value is not None:
                values.append(value)
        if row.chance is not None:
            values.append(row.chance * 100)
            values.percentages.append(row.chance * 100)
    return values


def match_score(match: MatchData, steamid: int) -> tuple[int, int]:
    """按目标玩家逐回合的阵营统计比分，供摘要、替换总览和报告共用。"""
    won = lost = 0
    for r in match.rounds:
        side = player_side(match, steamid, r.number)
        won += side is not None and r.winner.name == side
        lost += side is not None and r.winner.name != side
    return won, lost


def _header(match: MatchData, mm: MatchMoments) -> str:
    sides, results = [], []
    won, lost = match_score(match, mm.player)
    for r in match.rounds:
        side = player_side(match, mm.player, r.number)
        win = side is not None and r.winner.name == side
        results.append(f"R{r.number}{side or '?'}{'胜' if win else '负'}")
        sides.append(side)
    reasons = "、".join(sorted({ROUND_END_REASONS.get(r.reason, str(r.reason)) for r in match.rounds}))
    return "\n".join([
        "## 比赛",
        f"地图 {mm.map_name}，{mm.rounds} 回合，目标玩家 {mm.player_name}。目标玩家所在队伍比分 {won}:{lost}。",
        "每回合（阵营+胜负）：" + " ".join(results),
        f"回合结束方式包括：{reasons}",
        *([absent_rounds_note(mm.absent_rounds)] if mm.absent_rounds else []),
    ])


def _aim_section(stats: AimStats | None, baseline: dict[str, Any] | None) -> str:
    if stats is None:
        return "## 整场枪法\n视野计算不可用，没有枪法统计。"
    src = baseline["source"] if baseline else "无"
    lines = ["## 整场枪法（Leetify 口径）", f"职业样本：{src}。样本只来自少数职业队伍，仅作参考。"]
    for m in METRICS:
        name, unit, n_field, n_unit = METRIC_LABELS[m]
        v = getattr(stats, m)
        n = getattr(stats, n_field)
        if v is None:
            lines.append(f"- {name}：样本不足（{n} {n_unit}）")
            continue
        c = compare(m, v, baseline)
        cmp = (f"；职业中位数 {c['pro_median']}{unit}（四分位 {c['pro_p25']}–{c['pro_p75']}），"
               f"优于 {c['better_than_pro_pct']}% 的职业样本") if c else ""
        lines.append(f"- {name}：{v}{unit}（{n} {n_unit}）{cmp}")
    return "\n".join(lines)


VISIBILITY_NOTES = {
    "not_seen": "这次交火中没有看见对手",
    "first_hit_unseen": "第一次打中对手时看不到他，之后看见了",
    "lost_sight": "之前看见过对手，事件发生时视线已中断",
    "seen_before_window": "看见对手已超过 3 秒",
    "seen_since_previous_kill": "本回合上一次击杀时就已经能看见这名对手（交火紧接着上一次击杀）",
    "seen_since_round_start": "回合开始时就能看见这名对手",
    "smoke": "首次看见时隔着烟，视野数据不计",
    "blind": "第一次看见对手时你正被闪白，这次交火不计算反应时间和准星",
    "no_map_data": "无视野数据",
}

CONTACT_LABELS = {
    "peek": "自己移动进入对手视野、对手几乎静止：主动拉出，对手在架点",
    "held": "自己几乎静止、对手移动进入视野：自己在架点，对手拉出",
    "both_moving": "双方都在移动",
    "both_still": "双方都几乎静止",
    "unclear": "有一方处在减速或急停的过渡状态，无法判断谁在架点",
}


def summary_section(mm: MatchMoments) -> str:
    """整场汇总：由程序统计并列出编号，AI 直接引用，不必自己数（避免数错、列错）。"""
    kills = [m for m in mm.moments if m.kind == "kill"]
    deaths = [m for m in mm.moments if m.kind == "death"]
    assists = [m for m in mm.moments if m.kind == "assist"]

    def ids(ms: list[Moment]) -> str:
        return "、".join(f"[[{m.id}]]" for m in ms) if ms else "无"

    def row(label: str, ms: list[Moment], total: list[Moment]) -> str:
        return f"  - {label}：{len(ms)}/{len(total)} 次：{ids(ms)}"

    def has(m: Moment, tag: str) -> bool:
        return tag in m.tags

    e = lambda m: m.engagement  # noqa: E731
    lines = ["## 整场汇总（程序统计；引用次数和编号时直接用这里的结果，不要自己重新数）"]
    lines.append(f"- 击杀 {len(kills)} 次（爆头 {sum(m.headshot for m in kills)}）")
    lines += [
        row("首杀", [m for m in kills if has(m, "opening_kill")], kills),
        row("补枪（替刚阵亡的队友报仇）", [m for m in kills if has(m, "trade_kill")], kills),
        row("残局中的击杀", [m for m in kills if any(t.startswith("clutch_") for t in m.tags)], kills),
        row("主动拉出、对手在架点时拿下", [m for m in kills if e(m) and e(m).contact_type == "peek"], kills),
    ]
    # 下面的规律只统计交火中的阵亡：回合结束后的死亡、摔死 / 被炸弹炸死不是对局行为
    other = [m for m in deaths if has(m, "after_round_end") or has(m, "world_damage")]
    combat = [m for m in deaths if m not in other]
    lines.append(f"- 死亡 {len(deaths)} 次，其中交火中阵亡 {len(combat)} 次"
                 + (f"（另有 {ids(other)} 是回合结束后或非玩家伤害致死，不计入下面的统计）" if other else ""))
    lines += [
        row("首死", [m for m in combat if has(m, "opening_death")], combat),
        row("人数优势时阵亡", [m for m in combat if has(m, "man_advantage")], combat),
        row("死时身上还有没用的道具", [m for m in combat if m.death and m.death.unused_grenades], combat),
        row("没朝击杀者开枪就阵亡（不含武器还不能开枪的）",
            [m for m in combat if has(m, "no_shots_fired") and not has(m, "weapon_not_ready")], combat),
        row("阵亡时武器还不能开枪（刚开过一枪，如 AWP 拉栓）", [m for m in combat if has(m, "weapon_not_ready")], combat),
        row("阵亡时看不到击杀者（被背身、侧身或穿点打死）", [m for m in combat if has(m, "died_without_seeing")], combat),
        row("对手先看见自己", [m for m in combat if has(m, "opponent_saw_first")], combat),
        row("主动拉出、对手在架点时阵亡", [m for m in combat if e(m) and e(m).contact_type == "peek"], combat),
        row("被闪致死", [m for m in combat if has(m, "died_blind")], combat),
        row("死于 AWP", [m for m in combat if m.weapon == "awp"], combat),
        row("被队友补回", [m for m in combat if has(m, "traded")], combat),
    ]
    lines.append(f"- 关键助攻 {len(assists)} 次（闪光助攻 {sum(has(m, 'flash_assist') for m in assists)}，"
                 f"伤害助攻 {sum(has(m, 'damage_assist') for m in assists)}）")
    if mm.missed_trades:
        lines.append(f"- 漏补 {len(mm.missed_trades)} 次（见文末，近似）")
    return "\n".join(lines)


def _moments_section(mm: MatchMoments) -> str:
    lines = [f"## 决定性瞬间（共 {len(mm.moments)} 个；[[编号]] 用于引用，get_moment 可查看完整数据）"]
    lines += [moment_line(m) for m in mm.moments]
    return "\n".join(lines)


def moment_line(m: Moment) -> str:
    c = m.context
    who = {"kill": "击杀", "death": "被击杀，对手", "assist": "助攻击杀"}[m.kind]
    flags = [f for f, on in (("爆头", m.headshot), ("穿烟", m.through_smoke), ("穿墙", m.wallbang),
                             ("盲狙", m.noscope)) if on]
    parts = [
        f"[[{m.id}]] {who} {m.opponent_name or '-'}（{m.weapon}{'，' + '、'.join(flags) if flags else ''}）",
        f"{c.side} {c.alive_own}v{c.alive_enemy}，第 {c.round_time_s:.0f} 秒" + ("，已下包" if c.bomb_planted else "")
        + (f"，距离 {c.distance:.0f} 米" if c.distance else "")
        + (f"，交火前血量 {c.own_health_before} vs {c.opponent_health_before}"
           if c.own_health_before is not None and c.opponent_health_before is not None
           and (c.own_health_before < 100 or c.opponent_health_before < 100) else ""),
        f"手持 {c.own_weapon or '-'} vs {c.opponent_weapon or '-'}，装备 {c.own_equip_value or '-'} vs {c.opponent_equip_value or '-'}",
    ]
    e = m.engagement
    if e is not None:
        first = "首枪中" if e.first_shot_hit else "首枪未中" if e.first_shot_hit is False else ""
        cs = f"急停 {e.counter_strafe_stopped}/{e.counter_strafe_shots}" if e.counter_strafe_shots else "急停 -"
        parts.append(f"开枪 {e.shots_fired} 中 {e.shots_hit}{'（' + first + '）' if first else ''}，{cs}，"
                     f"造成/受到伤害 {e.damage_dealt}/{e.damage_taken}")
        vis = []
        if e.seen_first_lead_s is not None:
            vis.append(f"先看见：{'自己' if e.seen_first_lead_s > 0 else '对手' if e.seen_first_lead_s < 0 else '同时'}"
                       f"（{abs(e.seen_first_lead_s):.2f}s）")
        if e.time_to_damage_s is not None:
            vis.append(f"TTD {e.time_to_damage_s * 1000:.0f}ms（看见→开枪 {_ms(e.seen_to_first_shot_s)}，"
                       f"开枪→命中 {_ms(e.first_shot_to_damage_s)}）")
        if e.shots_before_seen:
            vis.append(f"看见前已朝他开了 {e.shots_before_seen} 枪")
        if e.player_speed_at_contact is not None:
            vis.append(f"接触时速度 自己 {e.player_speed_at_contact:.0f} / 对手 {e.opponent_speed_at_contact:.0f} u/s"
                       f"（{CONTACT_LABELS[e.contact_type]}）")
        if e.crosshair_placement_deg is not None:
            vis.append(f"准星移动 {e.crosshair_placement_deg:.1f}°")
        if e.visibility_note:
            vis.append(VISIBILITY_NOTES[e.visibility_note])
        if vis:
            parts.append("，".join(vis))
        for subject, blind in (("对手", e.opponent_blind), ("自己", e.player_blind)):
            if blind is not None:
                parts.append(_blind_line(subject, blind))
    if m.trade is not None:
        if m.trade.traded is not None:
            near = f"，最近队友 {m.trade.nearest_teammate_distance:.0f}" if m.trade.nearest_teammate_distance else "，附近没有存活队友"
            parts.append(("被补回" if m.trade.traded else "未被补") + near)
        if m.trade.is_trade_kill:
            parts.append(f"补枪（队友阵亡 {m.trade.avenge_delay_s:.1f}s 后）")
    if m.death is not None:
        if m.death.weapon_ready is False:
            parts.append(f"阵亡时武器还不能开枪：上一枪（{m.death.last_shot_weapon}）到阵亡只有 {m.death.last_shot_gap_s:.2f}s，"
                         f"该武器两枪最短间隔 {m.death.min_shot_interval_s:.2f}s")
        if m.death.unused_grenades:
            parts.append("死时身上道具：" + "、".join(GRENADE_LABELS.get(g, g) for g in m.death.unused_grenades))
        if m.death.thrown_grenades:
            parts.append("本回合已投道具：" + "、".join(GRENADE_LABELS.get(g, g) for g in m.death.thrown_grenades))
        if m.death.blinded:
            parts.append(f"死时被闪（剩 {m.death.blind_remaining_s:.1f}s，{'队友' if m.death.blinded_by_teammate else '对手'}闪的）")
    if m.assist is not None:
        parts.append(("闪光助攻" if m.assist.flash_assist else f"伤害助攻，造成 {m.assist.damage_to_victim} 伤害"))
    if m.tags:
        parts.append("标签：" + "、".join(tag_label(t) for t in m.tags))
    return "- " + " | ".join(parts)


def _blind_line(subject: str, blind: BlindInfo) -> str:
    status = (f"事件时剩 {blind.remaining_at_event_s:.1f}s"
              if blind.blinded_at_event and blind.remaining_at_event_s is not None else "事件时已恢复")
    roles = {"self": "目标玩家本人", "teammate": "目标玩家的队友", "opponent": "该对手本人", "enemy": "对手的队友"}
    source = f"{roles[blind.thrower_role]}闪的" if blind.thrower_role is not None else "投掷者无法确定"
    return f"{subject}被闪（{status}，最长 {blind.max_duration_s:.1f}s，{source}）"


def _ms(s: float | None) -> str:
    return f"{s * 1000:.0f}ms" if s is not None else "-"
