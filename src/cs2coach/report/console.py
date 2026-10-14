"""把决定性瞬间打印成便于人工核对的文本（开发用；正式报告由 AI 生成）。"""

from __future__ import annotations

from cs2coach.features.models import MatchMoments, Moment

TAG_LABELS = {
    "opening_kill": "首杀",
    "opening_death": "首死",
    "trade_kill": "补枪",
    "traded": "被补回",
    "not_traded": "未被补",
    "man_advantage": "人数优势",
    "man_disadvantage": "人数劣势",
    "post_plant": "下包后",
    "after_round_end": "回合已结束",
    "teamkill": "误杀队友",
    "victim_is_bot": "击杀 bot",
    "world_damage": "非玩家伤害",
    "no_shots_fired": "没开枪",
    "shot_first": "先开枪",
    "opponent_shot_first": "对手先开枪",
    "shot_same_tick": "同时开枪",
    "died_blind": "被闪致死",
    "unused_utility": "道具没用",
    "saw_opponent_first": "先看见对手",
    "opponent_saw_first": "对手先看见",
    "died_without_seeing": "没看见就被打死",
    "flash_assist": "闪光助攻",
    "damage_assist": "伤害助攻",
}
KIND_LABELS = {"kill": "击杀", "death": "死亡", "assist": "助攻"}


def tag_label(tag: str) -> str:
    if tag.startswith("clutch_"):
        return "残局" + tag.removeprefix("clutch_")
    return TAG_LABELS.get(tag, tag)


def format_moments(mm: MatchMoments) -> str:
    lines = [f"{mm.player_name}（{mm.player}） {mm.map_name}，{mm.rounds} 回合，决定性瞬间 {len(mm.moments)} 个"]
    if mm.visibility_unavailable:
        lines.append(f"视野计算不可用（{mm.visibility_unavailable}），Time to Damage 等指标为空")
    if mm.aim_stats is not None:
        lines += ["", "枪法（Leetify 口径）：" + _aim_line(mm.aim_stats)]
    lines.append("")
    for m in mm.moments:
        lines.append(_line(m))
    if mm.missed_trades:
        lines += ["", f"漏补（队友阵亡时自己在 {len(mm.missed_trades)} 次附近，没有补上；直线距离，不考虑视线）："]
        for t in mm.missed_trades:
            lines.append(f"  R{t.round} tick {t.tick}  距离 {t.distance:.0f}" + ("（别的队友补上了）" if t.killer_traded_by_other else ""))
    if mm.excluded:
        lines += ["", "不参与分析："]
        lines += [f"  R{x.round} tick {x.tick} {KIND_LABELS.get(x.kind, x.kind)}：{x.reason}" for x in mm.excluded]
    return "\n".join(lines)


def _aim_line(a) -> str:
    def f(v, unit, n):
        return f"{v}{unit}（{n}）" if v is not None else f"-（{n}）"
    return "，".join([
        "Time to Damage " + f(a.time_to_damage_ms, "ms", a.time_to_damage_samples),
        "Crosshair Placement " + f(a.crosshair_placement_deg, "°", a.crosshair_placement_samples),
        "Counter-Strafing " + f(a.counter_strafing_pct, "%", a.counter_strafing_shots),
        "Spray Accuracy " + f(a.spray_accuracy_pct, "%", a.spray_shots),
        "Spotted Accuracy " + f(a.spotted_accuracy_pct, "%", a.spotted_shots),
        "Headshot Accuracy " + f(a.headshot_accuracy_pct, "%", a.hits),
    ]) + "（括号内为样本数）"


def _line(m: Moment) -> str:
    c = m.context
    head = f"{m.id:<12} {KIND_LABELS[m.kind]} {m.opponent_name or '-'}（{m.weapon}{'，爆头' if m.headshot else ''}）"
    situ = f"{c.side} {c.alive_own}v{c.alive_enemy} {c.round_time_s:.0f}s"
    parts = [head, situ]
    e = m.engagement
    if e is not None:
        cs = f"急停 {e.counter_strafe_stopped}/{e.counter_strafe_shots}" if e.counter_strafe_shots else "急停 -"
        parts.append(f"开枪 {e.shots_fired} 中 {e.shots_hit}，{cs}，伤害 {e.damage_dealt}/{e.damage_taken}")
        if e.time_to_damage_s is not None:
            parts.append(f"TTD {e.time_to_damage_s * 1000:.0f}ms 准星 {e.crosshair_placement_deg}°")
    if m.death is not None and m.death.unused_grenades:
        parts.append("剩余道具 " + "、".join(m.death.unused_grenades))
    tags = "、".join(tag_label(t) for t in m.tags)
    return "  ".join(parts) + (f"  [{tags}]" if tags else "") + f"  → {m.jump_command}"
