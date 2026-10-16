"""分析文字的数字占位符：检查与排版共用同一取值口径。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from cs2coach.baseline import compare
from cs2coach.features.models import MatchMoments, Moment
from cs2coach.features.problems import ProblemReport


SLOT = re.compile(r"\{([^{}]*)\}")
METRICS = {
    "ttd": ("time_to_damage_ms", "time_to_damage_samples"),
    "crosshair": ("crosshair_placement_deg", "crosshair_placement_samples"),
    "counter_strafing": ("counter_strafing_pct", "counter_strafing_shots"),
    "spray": ("spray_accuracy_pct", "spray_shots"),
    "spotted": ("spotted_accuracy_pct", "spotted_shots"),
    "headshot": ("headshot_accuracy_pct", "hits"),
}
MOMENT_FIELDS = ("round side people round_time place opponent_place distance health opponent_health equip opponent_equip "
                 "ttd seen_to_shot shot_to_hit opponent_ttd crosshair shots hits hit_rate speed "
                 "opponent_speed trade_delay teammate_distance first_hit_shot shot_order seen_order contact "
                 "situation value threshold gap").split()
ROUND_FIELDS = "situation people round_time distance count".split()
MATCH_FIELDS = ("score rounds rounds_played kills deaths assists value threshold gap " + " ".join(METRICS) +
                " " + " ".join(f"{m}_samples" for m in METRICS)).split()
FREQ_FIELDS = "hits expected excess chance ratio matches_with_hits matches_above_pro".split()
COUNT_LABELS = {
    "kills": "击杀", "deaths": "死亡", "assists": "关键助攻", "flash_assists": "闪光助攻",
    "opening_kills": "首杀", "opening_deaths": "首死", "saw_first_kills": "先看见对手的击杀",
    "shot_first_kills": "先开枪的击杀", "trade_kills": "补枪击杀", "traded_deaths": "被队友补回的死亡",
    "missed_trades": "漏补记录", "no_shot_deaths": "交火阵亡中没有朝击杀者开枪的次数（不含武器未就绪）",
    "unused_utility_deaths": "交火阵亡中身上还有未使用道具的次数",
}
PROBLEM_VALUES = {
    "slow_reaction": ("ttd_ms", "ttd_ms", "ms"),
    "poor_crosshair_placement": ("crosshair_placement_deg", "crosshair_placement_deg", "deg"),
    "spray_control": ("hit_pct", "expected_hit_pct", "pct"),
    "awp_unscoped_shot": ("distance_m", "distance_m", "m"),
    "glock_long_range": ("distance_m", "distance_m", "m"),
    "spray_while_moving": ("moving_ratio", "moving_ratio", "ratio"),
}


# 模型常写的同义字段（本地千问实测写过 location）
FIELD_ALIASES = {"location": "place", "opponent_location": "opponent_place", "position": "place"}


@dataclass(frozen=True)
class SlotError:
    code: str
    slot: str
    message: str


def _fmt(value, unit="count") -> str:
    if unit == "ms":
        return f"{value:.0f} 毫秒"
    if unit == "s":
        return f"{value:.1f} 秒"
    if unit == "round_seconds":
        return f"{value:.0f} 秒"
    if unit == "lead":
        return f"{value:.2f} 秒"
    if unit == "deg":
        return f"{value:.1f}°"
    if unit == "pct":
        return f"{round(value, 1):g}%"  # 2/3 → 66.7%，12.5 → 12.5%，33.0 → 33%
    if unit == "ratio":
        return f"{value:.2f} 倍"  # 与频率表的倍数列一致
    if unit == "m":
        return f"{value:.0f} 米"
    if unit == "units":
        return f"{value:.0f} 单位"
    if unit == "speed":
        return f"{value:.0f} 单位/秒"
    if unit == "round":
        return f"第 {value} 回合"
    if unit == "shot":
        return f"第 {value} 发"
    return f"{value:g}" if isinstance(value, float) else str(value)


# 占位符已带单位；AI 在后面重复写的单位（含常见英文缩写）排版时去掉，前面重复的"第"同理
# 按单位长度从长到短匹配："单位/秒"必须先于"秒"，"毫秒"先于"秒"
_UNIT_ECHO = {"单位/秒": ("单位/秒", "u/s"), "毫秒": ("毫秒", "ms"), "单位": ("单位", "u"), "回合": ("回合",),
              "秒": ("秒",), "米": ("米", "m"), "°": ("°", "度"), "%": ("%", "％"), "发": ("发",), "倍": ("倍", "×")}
# 纯数字（次数、人数、血量等）后面接这些单位，说明把它当成了别的量，例如把"多出的次数"写成百分比
_UNIT_AFTER_PLAIN = re.compile(r"^\s*(%|％|°|度|毫秒|ms|秒|米)")
_PLAIN = re.compile(r"^\d+(?:\.\d+)?$")


def _echo_length(rendered: str, rest: str) -> int:
    """rest 开头与 rendered 结尾单位重复的字符数。"""
    for unit, echoes in _UNIT_ECHO.items():
        if rendered.endswith(unit):
            stripped = rest.lstrip(" ")
            for echo in echoes:
                if stripped.lower().startswith(echo.lower()):
                    end = len(rest) - len(stripped) + len(echo)
                    # 英文缩写后面紧跟字母时不是单位（例如 "m" 开头的单词）
                    if (echo.isascii() and echo.isalpha() and end < len(rest)
                            and rest[end].isascii() and rest[end].isalpha()):  # 中文字符 isalpha() 也为真
                        continue
                    return end
            return 0
    return 0


def render_slots(text: str, resolver: Callable[[str], str | SlotError]) -> tuple[str, list[SlotError]]:
    """返回替换结果和错误；错误占位符留待调用方删除该条，不可直接排版。"""
    errors: list[SlotError] = []
    out: list[str] = []
    pos = 0
    for match in SLOT.finditer(text):
        out.append(text[pos:match.start()])
        rest = text[match.end():]
        result = resolver(match[1])
        if isinstance(result, SlotError):
            if match[0] not in result.message:
                result = SlotError(result.code, result.slot, f"{match[0]}：{result.message}")
            errors.append(result)
            out.append(match[0])
            pos = match.end()
            continue
        # AI 在占位符前重复写了"第""你""对手"（如“从第 {…first_hit_shot}”“你{…seen_order}”）时去掉重复
        for lead in ("第", "对手", "你"):
            if result.startswith(lead) and out and out[-1].rstrip(" ").endswith(lead):
                out[-1] = out[-1].rstrip(" ")[:-len(lead)]
                break
        if _PLAIN.match(result) and (unit := _UNIT_AFTER_PLAIN.match(rest)):
            errors.append(SlotError("slot_unit_mismatch", match[0],
                f"{match[0]} 显示为纯数字 {result}，后面不能接“{unit[1]}”：它不是百分比、时间、角度或距离。"
                "需要比例、时间等请改用对应的占位符，或去掉单位。"))
        out.append(result)
        pos = match.end() + _echo_length(result, rest)
    out.append(text[pos:])
    rendered = "".join(out)
    if "{" in SLOT.sub("", text) or "}" in SLOT.sub("", text):
        errors.append(SlotError("unknown_slot", "{...}", "占位符花括号未配对。"))
    return rendered, errors


def count_values(mm: MatchMoments) -> dict[str, int]:
    """按瞬间标签统计。kills、deaths、assists 是全部瞬间（含回合结束后、世界伤害等非交火阵亡），
    与事实卡、整场比分一致；opening_deaths、traded_deaths、no_shot_deaths、unused_utility_deaths
    只统计交火阵亡（combat）。摘要中的中文说明必须与这里一致。"""
    moments = mm.moments
    kills = [m for m in moments if m.kind == "kill"]
    deaths = [m for m in moments if m.kind == "death"]
    combat = [m for m in deaths if not {"after_round_end", "world_damage"}.intersection(m.tags)]
    return {
        "kills": len(kills), "deaths": len(deaths), "assists": sum(m.kind == "assist" for m in moments),
        "flash_assists": sum("flash_assist" in m.tags for m in moments if m.kind == "assist"),
        "opening_kills": sum("opening_kill" in m.tags for m in kills),
        "opening_deaths": sum("opening_death" in m.tags for m in combat),
        "saw_first_kills": sum("saw_opponent_first" in m.tags for m in kills),
        "shot_first_kills": sum("shot_first" in m.tags for m in kills),
        "trade_kills": sum("trade_kill" in m.tags for m in kills),
        "traded_deaths": sum("traded" in m.tags for m in combat),
        "missed_trades": len(mm.missed_trades),
        "no_shot_deaths": sum("no_shots_fired" in m.tags and "weapon_not_ready" not in m.tags for m in combat),
        "unused_utility_deaths": sum(bool(m.death and m.death.unused_grenades) for m in combat),
    }


def situation_text(side, own, enemy, planted, round_time) -> str | None:
    """程序给出的局面完整句，AI 不必自己从阵营、人数、是否下包推断（评测：CT 回防被写成"守包"）。"""
    if side not in ("T", "CT") or own is None or enemy is None:
        return None
    parts = [f"{side} 方"]
    people = f"{own}v{enemy}"
    if own == 1 and enemy >= 1:
        parts.append(f"{people} 残局")
    elif own > enemy:
        parts.append(f"{people}，人数占优")
    elif own < enemy:
        parts.append(f"{people}，人数劣势")
    else:
        parts.append(people)
    if planted is True:
        parts.append("已下包、守包" if side == "T" else "对方已下包、回防")
    elif planted is False:
        parts.append("未下包")
    if round_time is not None:
        parts.append(f"回合第 {round_time:.0f} 秒")
    return "，".join(parts)


def _missing(slot, source):
    note = source.engagement.visibility_note if isinstance(source, Moment) and source.engagement else None
    return SlotError("slot_unavailable", slot, f"{slot} 没有可用值" + (f"（visibility_note={note}）" if note else "。"))


def _moment_value(m: Moment, field: str, slot: str):
    from cs2coach.tools.digest import CONTACT_LABELS
    c, e, t = m.context, m.engagement, m.trade
    direct = {
        "round": (m.round, "round"), "side": (c.side, "text"),
        "people": (f"{c.alive_own}v{c.alive_enemy}", "text"),
        "round_time": (c.round_time_s, "round_seconds"), "distance": (c.distance, "m"),
        "place": (c.place, "text"), "opponent_place": (c.opponent_place, "text"),
        "situation": (situation_text(c.side, c.alive_own, c.alive_enemy, c.bomb_planted, c.round_time_s), "text"),
        "health": (c.own_health_before, "count"), "opponent_health": (c.opponent_health_before, "count"),
        "equip": (c.own_equip_value, "count"), "opponent_equip": (c.opponent_equip_value, "count"),
        "ttd": (e.time_to_damage_s * 1000 if e and e.time_to_damage_s is not None else None, "ms"),
        "seen_to_shot": (e.seen_to_first_shot_s * 1000 if e and e.seen_to_first_shot_s is not None else None, "ms"),
        "shot_to_hit": (e.first_shot_to_damage_s * 1000 if e and e.first_shot_to_damage_s is not None else None, "ms"),
        "opponent_ttd": (e.opponent_time_to_damage_s * 1000 if e and e.opponent_time_to_damage_s is not None else None, "ms"),
        "crosshair": (e.crosshair_placement_deg if e else None, "deg"),
        "shots": (e.shots_fired if e else None, "count"), "hits": (e.shots_hit if e else None, "count"),
        "hit_rate": (100 * e.shots_hit / e.shots_fired if e and e.shots_fired else None, "pct"),
        "speed": (e.player_speed_at_contact if e else None, "speed"),
        "opponent_speed": (e.opponent_speed_at_contact if e else None, "speed"),
        "trade_delay": (t.trade_delay_s if t else None, "s"),
        "teammate_distance": (t.nearest_teammate_distance if t else None, "units"),
        "contact": (CONTACT_LABELS.get(e.contact_type) if e else None, "text"),
        # 瞄准这名对手的第几发第一次命中（从 1 开始）；一发没中时不可用
        "first_hit_shot": (next((i for i, s in enumerate(e.shots, 1) if s.hit), None) if e else None, "shot"),
    }
    if field in ("shot_order", "seen_order"):
        lead = (e.first_shot_lead_s if field == "shot_order" else e.seen_first_lead_s) if e else None
        if lead is None:
            return _missing(slot, m)
        action = "开枪" if field == "shot_order" else "看见对手"
        # 占位符会嵌进 AI 的句子里，不带句号
        if lead == 0:
            return "双方同一 tick 开枪" if field == "shot_order" else "双方同时看见对方"
        who = "你" if lead > 0 else "对手"
        verb = action if lead > 0 else ("开枪" if field == "shot_order" else "看见你")
        return f"{who}先{verb}，领先 {abs(lead):.2f} 秒"
    value, unit = direct[field]
    return _fmt(value, unit) if value is not None else _missing(slot, m)


def make_slot_resolver(mm: MatchMoments | None = None, report: ProblemReport | None = None,
                       score=None, frequency=None, baseline=None, summary=None,
                       problem: str | None = None, targets=(), overview=False):
    """构造单场或多场取值器；targets 是当前条目的引用边界，None 表示不限（训练计划不针对具体交火）。"""
    summary_mode = summary is not None
    matches = {m.number: m for m in summary.matches} if summary_mode else {}
    any_target = targets is None
    target_set = set(targets or ())

    def resolve(slot: str):
        parts = slot.split(".")
        if len(parts) < 2:
            return SlotError("unknown_slot", slot, "占位符应为 {对象.字段}。")
        obj, field = parts[0], FIELD_ALIASES.get(parts[1], parts[1])
        if obj == "count" and len(parts) == 2:
            if field not in COUNT_LABELS:
                return SlotError("unknown_slot", slot, "count 可用字段：" + "、".join(COUNT_LABELS))
            values = ({k: sum(count_values(summary.detail(m.number)[0])[k] for m in summary.matches)
                       for k in COUNT_LABELS} if summary_mode else count_values(mm))
            return str(values[field])
        if obj == "freq" and len(parts) == 3:
            row = next((r for r in (summary.frequency if summary_mode else frequency or []) if r.problem == parts[1]), None)
            allowed = FREQ_FIELDS if summary_mode else FREQ_FIELDS[:5]
            if parts[2] in allowed and row:
                key = {"expected": "expected_hits", "excess": "excess_hits"}.get(parts[2], parts[2])
                value = getattr(row, key)
                if value is None:
                    return _missing(slot, mm)
                if parts[2] == "chance":
                    return _fmt(value * 100, "pct")
                if parts[2] == "ratio":
                    return _fmt(value, "ratio")
                # 预期次数、多出次数是小数，与频率表一致保留一位（实测曾显示成 7.90291）
                return f"{value:.1f}" if parts[2] in ("expected", "excess") else _fmt(value, "count")
            return SlotError("unknown_slot", slot, "freq.<问题编号> 可用字段：" + "、".join(allowed))
        if obj == "pro" and len(parts) == 3:
            metric = METRICS.get(parts[1])
            if metric is None or parts[2] not in ("median", "p25", "p75", "better_than"):
                return SlotError("unknown_slot", slot, "pro.<指标> 可用字段：median、p25、p75、better_than；指标：" + "、".join(METRICS))
            stat = (summary.aim_overall.get(metric[0]).median if summary_mode and metric[0] in summary.aim_overall
                    else getattr(mm.aim_stats, metric[0], None) if mm and mm.aim_stats else None)
            comp = compare(metric[0], stat, baseline)
            if comp is None:
                return _missing(slot, mm)
            key = {"median": "pro_median", "p25": "pro_p25", "p75": "pro_p75", "better_than": "better_than_pro_pct"}[parts[2]]
            return _fmt(comp[key], "pct" if parts[2] == "better_than" or metric[0].endswith("_pct")
                        else "ms" if metric[0].endswith("_ms") else "deg")
        if obj == "match" and len(parts) == 2 or summary_mode and obj in matches and len(parts) == 2:
            fields = MATCH_FIELDS if obj == "match" else ["score", "kills", "deaths", "rounds_played"]
            if field not in fields:
                return SlotError("unknown_slot", slot, f"{obj} 可用字段：" + "、".join(fields))
            if field in ("value", "threshold", "gap"):
                mapping = PROBLEM_VALUES.get(problem)
                hit = next((h for h in report.hits if h.target == "match" and h.problem == problem), None) if report else None
                if not mapping or not hit:
                    return _missing(slot, mm)
                ev, th, unit = mapping
                a, b = hit.evidence.get(ev), hit.thresholds.get(th)
                value = a if field == "value" else b if field == "threshold" else a - b if a is not None and b is not None else None
                return _fmt(value, unit) if value is not None else _missing(slot, mm)
            game = matches.get(obj) if obj != "match" else None
            if game:
                data = {"score": f"{game.score[0]}:{game.score[1]}", "kills": game.kills,
                        "deaths": game.deaths, "rounds_played": game.rounds_played}
                return str(data[field])
            games = summary.matches if summary_mode else None
            if field == "score":
                if summary_mode:
                    wins = sum(g.score[0] > g.score[1] for g in games)
                    losses = sum(g.score[0] < g.score[1] for g in games)
                    return f"{wins} 胜 {losses} 负"
                return f"{score[0]}:{score[1]}" if score else _missing(slot, mm)
            if field in ("rounds", "rounds_played", "kills", "deaths", "assists"):
                if summary_mode:
                    return str(sum(getattr(g, field if field != "rounds" else "rounds") for g in games))
                return str(mm.rounds if field == "rounds" else mm.rounds_played if field == "rounds_played"
                           else count_values(mm)[field])
            metric_name = field.removesuffix("_samples")
            metric, samples = METRICS[metric_name]
            if summary_mode:
                item = summary.aim_overall.get(metric)
                value = item.matches if field.endswith("_samples") and item else item.median if item else None
            else:
                value = getattr(mm.aim_stats, samples if field.endswith("_samples") else metric, None) if mm.aim_stats else None
            if value is None:
                return _missing(slot, mm)
            return _fmt(value, "ms" if metric.endswith("_ms") else "deg" if metric.endswith("_deg")
                        else "pct" if metric.endswith("_pct") else "count") if not field.endswith("_samples") else str(value)
        if overview:
            return SlotError("slot_target_not_cited", slot, "总览仅能使用 match、pro、freq、count 和多场 M 编号。")
        prefix, _, local = obj.partition(":") if summary_mode else ("", "", obj)
        if summary_mode:
            if prefix not in matches or not local:
                return SlotError("unknown_slot", slot, "瞬间对象需写成 M序号:R回合-瞬间。")
            local_mm, local_report = summary.detail(prefix)
        else:
            local_mm, local_report = mm, report
        moment = next((m for m in local_mm.moments if m.id == local), None)
        round_match = re.fullmatch(r"R(\d+)(?:\[(\d+)\])?", local)
        if moment:
            if len(parts) != 2 or field not in MOMENT_FIELDS:
                return SlotError("unknown_slot", slot, "瞬间可用字段：" + "、".join(MOMENT_FIELDS))
            if not any_target and obj not in target_set:
                return SlotError("slot_target_not_cited", slot, f"{obj} 不在本条 targets 中。")
            if field in ("value", "threshold", "gap"):
                if problem is None:
                    return SlotError("slot_unavailable", slot, "value/threshold/gap 只能用在问题条目的分析里"
                                     "（按该条问题取值）；训练计划、总览里请改用 pro.* 或直接删去。")
                mapping = PROBLEM_VALUES.get(problem)
                hit = next((h for h in local_report.hits if h.target == local and h.problem == problem), None)
                if not mapping or not hit:
                    return _missing(slot, moment)
                ev, th, unit = mapping
                a, b = hit.evidence.get(ev), hit.thresholds.get(th)
                value = a if field == "value" else b if field == "threshold" else a - b if a is not None and b is not None else None
                return _fmt(value, unit) if value is not None else _missing(slot, moment)
            return _moment_value(moment, field, slot)
        if round_match:
            if len(parts) != 2 or field not in ROUND_FIELDS:
                return SlotError("unknown_slot", slot, "漏补回合可用字段：" + "、".join(ROUND_FIELDS))
            cited = f"{prefix}:{local[:local.index('[')]}" if summary_mode and "[" in local else (
                f"{prefix}:{local}" if summary_mode else local.split("[")[0])
            if not any_target and cited not in target_set:
                return SlotError("slot_target_not_cited", slot, f"{cited} 不在本条 targets 中。")
            records = [r for r in local_mm.missed_trades if r.round == int(round_match[1])]
            if not records:
                return _missing(slot, local_mm)
            if field == "count":
                return str(len(records))
            index = int(round_match[2]) if round_match[2] else None
            if index is None and len(records) > 1:
                return SlotError("slot_unavailable", slot, "该回合有多条漏补记录，请使用 [1]、[2] 指定记录。")
            if index is not None and not 1 <= index <= len(records):
                return SlotError("slot_unavailable", slot, f"漏补记录序号超出范围，共 {len(records)} 条。")
            record = records[(index or 1) - 1]
            value = {"situation": situation_text(record.side, record.alive_own, record.alive_enemy,
                                                 record.bomb_planted, record.round_time_s),
                     "people": f"{record.alive_own}v{record.alive_enemy}" if record.alive_own is not None and record.alive_enemy is not None else None,
                     "round_time": record.round_time_s, "distance": record.distance}[field]
            return _fmt(value, "round_seconds" if field == "round_time" else "units" if field == "distance" else "text") if value is not None else _missing(slot, local_mm)
        return SlotError("unknown_slot", slot, "未知占位符对象；可用 match、pro、freq、count、瞬间或漏补回合。")

    def candidates() -> list[str]:
        """本条可以使用的全部占位符名称（不含 value/threshold/gap 以外依赖问题的字段判断）。"""
        names = [f"match.{f}" for f in MATCH_FIELDS if f not in ("value", "threshold", "gap")]
        names += [f"pro.{m}.{f}" for m in METRICS for f in ("median", "p25", "p75", "better_than")]
        rows = summary.frequency if summary_mode else frequency or []
        names += [f"freq.{r.problem}.{f}" for r in rows for f in (FREQ_FIELDS if summary_mode else FREQ_FIELDS[:5])]
        names += [f"count.{k}" for k in COUNT_LABELS]
        if summary_mode:
            names += [f"{n}.{f}" for n in matches for f in ("score", "kills", "deaths", "rounds_played")]
        for target in targets or ():
            local = target.partition(":")[2] if summary_mode else target
            if re.fullmatch(r"R\d+", local):
                names += [f"{target}.count"] + [f"{target}[{i}].{f}" for i in (1, 2, 3)
                                                 for f in ("people", "round_time", "distance")]
            else:
                names += [f"{target}.{f}" for f in MOMENT_FIELDS]
        return names

    resolve.candidates = candidates
    return resolve


_LEADING_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def suggest_slots(resolver, numbers: list[str], limit: int = 2) -> dict[str, list[str]]:
    """给文字里直接写出的数字找显示值相同的占位符，用于修改提示。

    本条引用的瞬间、回合优先，其次整场与职业，最后计数和频率；同一优先级里对上的占位符太多时
    （小整数常见）视为含义不明，返回空列表，提示“请按含义选择”。完全对不上的数字不出现在结果里。
    """
    names = getattr(resolver, "candidates", lambda: [])()
    shown: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        value = resolver(name)
        if isinstance(value, SlotError):
            continue
        rank = 0 if not name.startswith(("match.", "pro.", "count.", "freq.")) else (
            1 if name.startswith(("match.", "pro.")) else 2)
        keys = {value.replace(" ", "").lower()} if re.fullmatch(r"\d+v\d+", value) else set()
        found = _LEADING_NUMBER.search(value.replace(":", " "))
        if found:
            keys.add(found.group())
        if ":" in value:  # 比分 16:14
            keys.update(value.split(":"))
        for key in keys:
            shown.setdefault(key, []).append((rank, "{" + name + "}"))

    def lookup(number: str):
        variants = [number, number.rstrip("0").rstrip(".") if "." in number else number]
        if "." in number:  # 写成 6.27 而显示为 6.3 时，按一位小数再找
            try:
                variants += [f"{round(float(number), 1):g}", f"{round(float(number)):g}"]
            except ValueError:
                pass
        for v in variants:
            if v in shown:
                return shown[v]
        return None

    out = {}
    for number in numbers:
        hits = lookup(number)
        if hits is None:
            continue
        best = min(rank for rank, _ in hits)
        top = list(dict.fromkeys(name for rank, name in hits if rank == best))
        out[number] = top[:limit] if len(top) <= 3 else []
    return out


MOMENT_GUIDE = [
    ("situation", "**局面完整句**：阵营、人数、是否下包（守包 / 回防）、回合第几秒。讲局势时优先用它，不要自己从人数和阵营推",
     "CT 方，1v3 残局，对方已下包、回防，回合第 47 秒"),
    ("round", "回合", "第 5 回合"), ("side", "阵营", "CT"), ("people", "交火时双方存活人数", "5v4"),
    ("round_time", "回合开始后的秒数，写作“回合第{…round_time}”", "24 秒"),
    ("place / opponent_place", "自己 / 对手所在位置（游戏地名）", "LongA"), ("distance", "与对手的距离", "19 米"),
    ("health / opponent_health", "交火前血量", "53"), ("equip / opponent_equip", "本回合装备价值", "4700"),
    ("ttd", "看见到第一次命中（Time to Damage）", "781 毫秒"),
    ("seen_to_shot / shot_to_hit", "TTD 拆成的两段：看见→开枪、开枪→命中。只是 TTD 的一部分，不要拿它和 threshold 比较", "594 毫秒"),
    ("opponent_ttd", "对手从看见你到第一次打中你", "641 毫秒"),
    ("crosshair", "看见到第一次命中准星转过的角度", "21.7°"),
    ("shots / hits", "瞄准这名对手开的枪数 / 命中数", "6"), ("hit_rate", "这次交火的命中率（程序计算）", "33.3%"),
    ("first_hit_shot", "第几发**第一次打中**。不是“弹道从第几发开始飘”", "第 8 发"), ("speed / opponent_speed", "接触时速度", "209 单位/秒"),
    ("trade_delay", "被补回或补枪用时", "1.3 秒"), ("teammate_distance", "阵亡时最近存活队友的距离", "480 单位"),
    ("shot_order / seen_order", "谁先开枪 / 谁先看见的完整句（不带句号）", "你先开枪，领先 0.27 秒"),
    ("contact", "接触类型的完整句（谁拉出、谁架点）", "自己移动进入对手视野、对手几乎静止：主动拉出，对手在架点"),
    ("value / threshold / gap", "本条问题在这次交火的数值 / 职业同武器阈值（75 分位等）/ 两者之差",
     "781 毫秒 / 531 毫秒 / 250 毫秒"),
]


def slot_guidance(mm: MatchMoments | None = None, summary=None) -> str:
    """在摘要里公布完整字段表、显示样式与计数口径。"""
    values = ({k: sum(count_values(summary.detail(m.number)[0])[k] for m in summary.matches)
               for k in COUNT_LABELS} if summary is not None else count_values(mm))
    prefix = "M1:R5-kill2" if summary is not None else "R5-kill2"
    lines = ["## 占位符",
        "总览、analysis 里不写任何数字（包括“两次”“三发”这类中文数字），需要数字时写 {对象.字段}，程序会换成带单位的数值。",
        "**占位符已经带单位，后面不要再写单位**：写“用了{" + prefix + ".ttd}”，不要写“{" + prefix + ".ttd}ms”。"
        "次数、人数、血量等显示为纯数字，可以在后面写“次”“人”，但不能写“%”“秒”。",
        "训练文字（training、training_plan）可以写计划数字（如 3 组 30 次），也可以用占位符。",
        "",
        f"### 瞬间：{{{prefix}.字段}}（只能用本条 targets 中的瞬间）",
        "| 字段 | 含义 | 显示示例 |", "|---|---|---|"]
    lines += [f"| {f} | {m} | {e} |" for f, m, e in MOMENT_GUIDE]
    lines += ["“职业同武器 75 分位”请用 {瞬间.threshold}，它按本条问题和这次交火的武器取值，比较对象是 {瞬间.value}"
              "（出枪慢时就是整个 TTD），不要拿 seen_to_shot 等分段去比；不要用 pro.*（那是整场指标）。",
              "两个交火的特征要分开写：一次先开枪、一次同时开枪，就不能写“都先开枪”。程序会核对“都……”“始终……”是否对每个交火都成立。",
        "",
        "### 漏补回合：{R9.字段}（只能用本条 targets 中的回合）",
        "count：本回合漏补次数。一个回合只有一条记录时可直接写 {R9.distance}；有多条时写 {R9[1].distance}、{R9[2].distance}。"
        "字段：situation（队友阵亡时的局面完整句）、people（人数）、round_time（回合第几秒）、distance（与阵亡队友的距离，单位）。",
        "",
        "### 整场：{match.字段}（多场时为整批合计或各场中位数）",
        "score（比分）、rounds（总回合）、rounds_played（在场回合）、kills、deaths、assists；"
        "六项枪法 ttd、crosshair、counter_strafing、spray、spotted、headshot，加 _samples 为样本数。",
        "### 职业：{pro.指标.字段}",
        "指标同上六项；字段 median（中位数）、p25、p75、better_than（优于多少比例的职业样本，显示如 8%）。"
        "例：{pro.ttd.median}、{pro.crosshair.better_than}。",
        "### 频率：{freq.问题编号.字段}",
        "hits（本场次数）、expected（按职业频率预期次数）、excess（多出的次数，纯数字，不是百分比）、"
        "chance（偶然概率，显示如 0.1%）、ratio（倍数，显示如 2.93 倍）"
        + ("；多场另有 matches_with_hits（出现场数）、matches_above_pro（高于职业的场数）。" if summary is not None else "。"),
        "### 计数：{count.名称}（程序统计，纯数字）"]
    lines += [f"- {key}：{COUNT_LABELS[key]}，本{'批' if summary is not None else '场'} {value}" for key, value in values.items()]
    if summary is not None:
        lines += ["### 场次：{M3.字段}", "score、kills、deaths、rounds_played。"]
    lines += ["", "总览只能用 match、pro、freq、count" + ("、M 场次" if summary is not None else "") + " 这几类。"
              "涉及谁先开枪、谁先看见、谁在架点，优先用 shot_order、seen_order、contact 完整句，避免方向写反。"]
    return "\n".join(lines)


def render_submission(sub, factory) -> dict:
    """最终提交统一渲染；调用前必须已通过同一 resolver 的检查。"""
    result = sub.model_dump()
    result["overview"], errors = render_slots(sub.overview, factory(None, (), True))
    for group in ("findings", "patterns", "highlights"):
        for i, entry in enumerate(getattr(sub, group)):
            result[group][i]["analysis"], found = render_slots(entry.analysis, factory(entry.problem, entry.targets))
            errors.extend(found)
            if group == "findings":
                result[group][i]["training"], found = render_slots(entry.training, factory(entry.problem, entry.targets))
                errors.extend(found)
    for i, item in enumerate(sub.training_plan):
        result["training_plan"][i], found = render_slots(item, factory(None, None))
        errors.extend(found)
    if errors:
        # 正常情况下诊断流程已删掉或替换含错误占位符的条目，这里只是兜底：绝不把 {…} 印进报告
        result["overview"] = SLOT.sub("（数据缺失）", result["overview"])
        result["training_plan"] = [SLOT.sub("（数据缺失）", item) for item in result["training_plan"]]
        for group in ("findings", "patterns", "highlights"):
            for item in result[group]:
                for key in ("analysis", "training"):
                    if key in item:
                        item[key] = SLOT.sub("（数据缺失）", item[key])
        result["slot_errors"] = [{"code": e.code, "slot": e.slot, "message": e.message} for e in errors]
    return result
