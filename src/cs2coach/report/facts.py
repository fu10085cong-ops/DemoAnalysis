"""由程序生成中文事实卡：只描述已有数据，不评价、不调用 AI。"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping

from cs2coach.features.labels import GRENADE_LABELS, KIND_LABELS, absent_rounds_note, weapon_label
from cs2coach.features.models import BlindInfo, MatchMoments, MissedTrade, Moment
from cs2coach.report.evidence import missed_trade_context
from cs2coach.tools.digest import CONTACT_LABELS, VISIBILITY_NOTES


def moment_facts(m: Moment, names: Mapping[int, str]) -> list[str]:
    """按位置、装备、结果、交火、死亡状态、补枪的顺序返回事实句子，缺失数据省略。"""
    c = m.context
    out: list[str] = []
    position = []
    if _known(c.place):
        position.append(f"你在 {c.place}")
    if _known(c.opponent_place):
        name = names.get(m.opponent) or m.opponent_name
        who = f"对手 {name}" if _known(name) else "对手"
        position.append(f"{who} 在 {c.opponent_place}")
    if _known(c.distance):
        position.append(f"距离 {c.distance:.0f} 米")
    if position:
        out.append("，".join(position) + "。")

    weapons = []
    if _known(c.own_weapon):
        weapons.append(f"你手持 {c.own_weapon}")
    if _known(c.opponent_weapon):
        weapons.append(f"对手手持 {c.opponent_weapon}")
    equip = (f"本回合装备价值 {c.own_equip_value} 对 {c.opponent_equip_value}"
             if _known(c.own_equip_value) and _known(c.opponent_equip_value) else "")
    gear = [part for part in ("，".join(weapons), equip) if part]
    if gear:
        out.append("；".join(gear) + "。")
    if any(_known(h) and h < 100 for h in (c.own_health_before, c.opponent_health_before)):
        health = []
        if _known(c.own_health_before):
            health.append(f"你 {c.own_health_before}")
        if _known(c.opponent_health_before):
            health.append(f"对手 {c.opponent_health_before}")
        out.append("交火前血量：" + "，".join(health) + "。")

    flags = [label for label, on in (("爆头", m.headshot), ("穿烟", m.through_smoke),
                                      ("穿墙", m.wallbang), ("盲狙", m.noscope)) if on]
    weapon = weapon_label(m.weapon) + (f"（{'、'.join(flags)}）" if flags else " ")
    if m.kind == "kill" and _known(m.weapon):
        out.append(f"你用 {weapon}击杀了对手。")
    elif m.kind == "death" and _known(m.weapon):
        out.append(f"对手用 {weapon}击杀了你。" if m.opponent is not None else f"你因 {weapon_label(m.weapon)} 阵亡。")
    elif m.kind == "assist" and m.assist is not None:
        who = names.get(m.opponent) or m.opponent_name or "对手"
        detail = "闪光助攻" if m.assist.flash_assist else f"你对他造成了 {m.assist.damage_to_victim} 点伤害"
        out.append(f"你助攻击杀了 {who}：{detail}。")

    if m.engagement is not None:
        out.extend(_engagement_facts(m, names))
    if m.death is not None:
        out.extend(_death_facts(m, names))
    out.extend(_trade_facts(m, names))
    return out


def _engagement_facts(m: Moment, names: Mapping[int, str]) -> list[str]:
    e = m.engagement
    out = []
    if _known(e.seen_first_lead_s):
        out.append(_lead(e.seen_first_lead_s, "看见对手", "看见你", "双方同时看见对方。"))
    if e.visibility_note:
        if e.visibility_note == "not_seen":
            verb = "被击杀" if m.kind == "death" else "击杀"
            out.append(f"按视野计算，你{verb}前没有看见对手。")
        elif e.visibility_note == "no_map_data":
            out.append("缺少地图数据，没有计算视野。")
        elif e.visibility_note in VISIBILITY_NOTES:
            out.append(VISIBILITY_NOTES[e.visibility_note] + "。")
    if e.opponent_seen_while_blind:
        out.append("对手第一次看见你时正被闪白，不计算对手的反应时间。")
    if e.shots_fired == 0:
        out.append("你没有朝对手开枪。")
    elif _known(e.first_shot_lead_s):
        out.append(_lead(e.first_shot_lead_s, "开枪", "开枪", "双方同一 tick 开枪。"))
    if (_known(e.player_speed_at_contact) and _known(e.opponent_speed_at_contact)
            and e.contact_type in CONTACT_LABELS):
        out.append(f"接触时你的速度 {e.player_speed_at_contact:.0f}、对手 {e.opponent_speed_at_contact:.0f} 单位/秒："
                   f"{CONTACT_LABELS[e.contact_type]}。")
    if e.shots_fired > 0:
        shot = f"你开了 {e.shots_fired} 枪，命中 {e.shots_hit} 枪"
        first = []
        if e.first_shot_hit is not None:
            first.append("第一枪" + ("命中" if e.first_shot_hit else "未命中"))
        if e.first_shot_stopped is not None:
            first.append("开第一枪时" + ("已停稳" if e.first_shot_stopped else "未停稳"))
        out.append(shot + ("；" + "，".join(first) if first else "") + "。")
    if _known(e.time_to_damage_s):
        parts = []
        for label, value in (("看见→开枪", e.seen_to_first_shot_s), ("开枪→命中", e.first_shot_to_damage_s)):
            if _known(value):
                parts.append(f"{label} {value * 1000:.0f} 毫秒")
        out.append(f"从看见对手到第一次造成伤害用了 {e.time_to_damage_s * 1000:.0f} 毫秒"
                   + (f"（{'，'.join(parts)}）" if parts else "") + "。")
    if _known(e.crosshair_placement_deg):
        out.append(f"从看见对手到第一次造成伤害，准星转过了 {e.crosshair_placement_deg:.1f}°。")
    if _known(e.shots_before_seen) and e.shots_before_seen > 0:
        out.append(f"看见对手之前，你已朝他开了 {e.shots_before_seen} 枪。")
    if _known(e.opponent_time_to_damage_s):
        out.append(f"对手从看见你到第一次造成伤害用了 {e.opponent_time_to_damage_s * 1000:.0f} 毫秒。")
    for subject, info in (("对手", e.opponent_blind), ("你", e.player_blind)):
        if info is not None:
            out.append(_blind_fact(subject, info, m.kind, names))
    return out


def _lead(value: float, own_action: str, opponent_action: str, simultaneous: str) -> str:
    if value == 0:
        return simultaneous
    subject, action = ("你", own_action) if value > 0 else ("对手", opponent_action)
    return f"{subject}先{action}，领先 {abs(value):.2f} 秒。"


def _blind_fact(subject: str, info: BlindInfo, kind: str, names: Mapping[int, str]) -> str:
    event = {"kill": "击杀时", "death": "死亡时", "assist": "事件时"}[kind]
    if info.blinded_at_event:
        status = (f"{event}还剩 {info.remaining_at_event_s:.1f} 秒"
                  if _known(info.remaining_at_event_s) else f"{event}仍在致盲中")
    else:
        status = f"{event}已经恢复"
    parts = [status]
    if _known(info.max_duration_s):
        parts.append(f"这次致盲最长 {info.max_duration_s:.1f} 秒")
    parts.append(_thrower(info.thrower_role, info.thrower, names, subject))
    return f"{subject}在交火中被闪：" + "，".join(parts) + "。"


def _thrower(role: str | None, steamid: int | None, names: Mapping[int, str], subject: str) -> str:
    """按被闪对象说明投掷者，避免把对手闪你写成对手自闪。"""
    name = names.get(steamid)
    if role == "self":
        return "是你自己扔的闪" if subject == "你" else "是你扔的闪"
    if role == "opponent":
        return "是对手扔的闪" if subject == "你" else "是对手自己扔的闪"
    if role == "teammate":
        who = "队友" if subject == "你" else "你的队友"
        return f"是{who} {name} 扔的闪" if _known(name) else f"是{who}扔的闪"
    if role == "enemy":
        return f"是对手的队友 {name} 扔的闪" if _known(name) else "是对手队友扔的闪"
    return "投掷者无法确定"


def _death_facts(m: Moment, names: Mapping[int, str]) -> list[str]:
    d = m.death
    out = []
    if (d.weapon_ready is False and _known(d.last_shot_weapon) and _known(d.last_shot_gap_s)
            and _known(d.min_shot_interval_s)):
        out.append(f"你阵亡时武器还不能开枪：上一枪（{weapon_label(d.last_shot_weapon)}）到阵亡只有 {d.last_shot_gap_s:.2f} 秒，"
                   f"该武器两枪的最短间隔是 {d.min_shot_interval_s:.2f} 秒。")
    if d.blinded and (m.engagement is None or m.engagement.player_blind is None):
        role = None
        if d.blinded_by == m.player:
            role = "self"
        elif d.blinded_by is not None and d.blinded_by_teammate is not None:
            role = "teammate" if d.blinded_by_teammate else "opponent" if d.blinded_by == m.opponent else "enemy"
        remaining = f"，还剩 {d.blind_remaining_s:.1f} 秒" if _known(d.blind_remaining_s) else ""
        out.append(f"你阵亡时被闪{remaining}，{_thrower(role, d.blinded_by, names, '你')}。")
    out.append(f"你阵亡时身上还有：{_grenades(d.unused_grenades)}。" if d.unused_grenades else "你阵亡时身上没有道具。")
    out.append(f"本回合你在阵亡前投出了：{_grenades(d.thrown_grenades)}。"
               if d.thrown_grenades else "本回合你在阵亡前没有投出道具。")
    return out


def _trade_facts(m: Moment, names: Mapping[int, str]) -> list[str]:
    t = m.trade
    if t is None:
        return []
    out = []
    if t.traded:
        who = _named("队友", t.traded_by, names)
        delay = f" {t.trade_delay_s:.1f} 秒" if _known(t.trade_delay_s) else ""
        out.append(f"你阵亡后{delay}，{who} 击杀了对手（补回）。")
    elif t.traded is False:
        near = (f"当时最近的存活队友距离你 {t.nearest_teammate_distance:.0f} 单位"
                if _known(t.nearest_teammate_distance) else "当时附近没有存活的队友")
        out.append(f"你阵亡后没有被补回；{near}。")
    if t.is_trade_kill:
        delay = f" {t.avenge_delay_s:.1f} 秒" if _known(t.avenge_delay_s) else ""
        out.append(f"这是一次补枪：队友阵亡{delay}后，你击杀了击杀他的对手。")
    return out


def _grenades(grenades: list[str]) -> str:
    """道具按首次出现的顺序显示，同名道具合并数量。"""
    return "、".join(GRENADE_LABELS.get(g, g) + (f" ×{n}" if n > 1 else "")
                    for g, n in Counter(grenades).items())


def _known(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _named(role: str, steamid: int | None, names: Mapping[int, str]) -> str:
    name = names.get(steamid)
    return f"{role} {name}" if _known(name) else role


def render_moment_card(m: Moment, names: Mapping[int, str]) -> str:
    """一个瞬间的 Markdown 标题和事实列表。"""
    c = m.context
    context = [c.side]
    if _known(c.round_time_s):
        context.append(f"第 {c.round_time_s:.0f} 秒")
    context.append(f"{c.alive_own}v{c.alive_enemy}")
    if c.bomb_planted:
        context.append("已下包")
    title = f"### 〔{m.id}〕{KIND_LABELS[m.kind]}（{'，'.join(context)}）"
    return title + "\n\n" + "\n".join(f"- {line}" for line in moment_facts(m, names))


def missed_trade_facts(mt: MissedTrade, names: Mapping[int, str]) -> list[str]:
    """漏补只描述窗口内的击杀结果，不评价距离或机会。"""
    who = _named("队友", mt.teammate, names)
    other = "；对手随后被其他队友击杀" if mt.killer_traded_by_other else ""
    context = missed_trade_context(mt.model_dump())
    return ([context] if context else []) + [
        f"{who} 阵亡时离你 {mt.distance:.0f} 单位，你在补枪时间窗口内没有击杀击杀他的对手{other}。"]


def render_facts_file(mm: MatchMoments, names: Mapping[int, str]) -> str:
    """整场所有瞬间及漏补枪的事实卡，顺序沿用特征层输出。"""
    sections = [f"# 事实卡：{mm.player_name} · {mm.map_name}",
                f"共 {mm.rounds} 回合，{len(mm.moments)} 个瞬间。" + absent_rounds_note(mm.absent_rounds)]
    sections.extend(render_moment_card(m, names) for m in mm.moments)
    for mt in mm.missed_trades:
        sections.append(f"### 〔R{mt.round}〕漏补枪\n\n"
                        + "\n".join(f"- {line}" for line in missed_trade_facts(mt, names)))
    return "\n\n".join(sections).rstrip() + "\n"
