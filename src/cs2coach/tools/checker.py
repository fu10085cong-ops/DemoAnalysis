"""结构化结论的纯函数检查：编号、前提、数字和敏感说法均可追溯。"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel

from cs2coach.features.labels import WEAPON_LABELS
from cs2coach.features.models import MatchMoments, Moment
from cs2coach.features.problems import CONTEXT_ONLY, PROBLEMS, ProblemReport

if TYPE_CHECKING:
    from cs2coach.tools.submission import ReportSubmission


class Violation(BaseModel):
    path: str
    code: str
    message: str


PREDICATES: dict[str, Callable[[Moment], bool]] = {
    "not_seen": lambda m: m.engagement is not None and m.engagement.visibility_note == "not_seen",
    "blind": lambda m: bool((m.engagement is not None and
                            (m.engagement.player_blind is not None or m.engagement.opponent_blind is not None))
                           or (m.death is not None and m.death.blinded)),
    "peek": lambda m: m.engagement is not None and m.engagement.contact_type == "peek",
    "held": lambda m: m.engagement is not None and m.engagement.contact_type in ("held", "peek"),
    "seen_order": lambda m: m.engagement is not None and m.engagement.seen_first_lead_s is not None,
    "shot_order": lambda m: m.engagement is not None and m.engagement.first_shot_lead_s is not None,
    "trade": lambda m: m.trade is not None,
    "stopped": lambda m: m.engagement is not None and (m.engagement.first_shot_stopped is not None
                            or any(s.stopped is not None for s in m.engagement.shots)),
    "spray": lambda m: m.engagement is not None and any(s.recoil_index is not None
                            and math.isfinite(s.recoil_index) and s.recoil_index >= 1 for s in m.engagement.shots),
    "weapon_not_ready": lambda m: m.death is not None and m.death.weapon_ready is False,
}

# 从旧引用模块原样迁移，仅用于拒绝分析文字里的引用；检查规则不变。
CITATION = re.compile(r"\[\[\s*([A-Za-z0-9\-]+)\s*\]\]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PEOPLE = re.compile(r"(?<![A-Za-z0-9_.])(\d+)\s*[vV]\s*(\d+)(?![A-Za-z0-9_.])")
_ROUND = re.compile(r"^R(\d+)$")
_TEXT_ROUND = re.compile(r"第\s*(\d+)\s*回合|(?<![A-Za-z0-9_])R(\d+)(?!\d)")
_MOMENT_ID = re.compile(r"(?<![A-Za-z0-9_])R\d+-(?:kill\d+|death|assist\d+)(?![A-Za-z0-9_])")
_WEAPONS = re.compile("|".join(re.escape(s) for s in sorted(WEAPON_LABELS.values(), key=len, reverse=True)), re.I)
_APPROX_BEFORE = re.compile(r"(?:约|大约|接近|左右)\s*$")
_APPROX_AFTER = re.compile(r"^\s*(?:%|毫秒|秒|度|米|发|次|人)?\s*左右")


def _numeric_leaves(value, path="") -> list[float]:
    """收集数值及单位换算；证据里的 JSON 列表也展开，布尔值不当作 0/1。"""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return []
        values = [value, value * 1000, value / 1000, value * 100]
        if "recoil_index" in path or "recoil_indices" in path:
            values.append(value + 1)
        return [v for v in values if math.isfinite(v)]
    if isinstance(value, str):
        if value.lstrip().startswith(("[", "{")):
            try:
                return _numeric_leaves(json.loads(value), path)
            except (ValueError, TypeError):
                pass
        return []
    if isinstance(value, dict):
        return [n for key, child in value.items() for n in _numeric_leaves(child, f"{path}.{key}")]
    if isinstance(value, (list, tuple)):
        return [n for child in value for n in _numeric_leaves(child, path)]
    return []


def _rounded_matches(value: float, token: str) -> bool:
    """按文字的小数位做四舍五入，避免 Python 的偶数舍入和二进制误差。"""
    digits = len(token.partition(".")[2])
    try:
        with localcontext() as ctx:
            ctx.prec = max(ctx.prec, len(str(value)) + len(token) + digits)
            return Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP) == Decimal(token)
    except InvalidOperation:
        return False


def _number_violations(text: str, path: str, allowed: list[float], people: set[tuple[int, int]],
                       explanation: str, cfg: dict) -> list[Violation]:
    clean = _WEAPONS.sub(" ", text)
    out = []
    for match in _PEOPLE.finditer(clean):
        pair = tuple(int(x) for x in match.groups())
        if pair not in people:
            actual = "、".join(f"{a}v{b}" for a, b in sorted(people)) or "无可核对人数"
            out.append(Violation(path=path, code="number_not_found",
                message=f'人数“{match.group()}”与 targets 数据不符；可核对人数：{actual}。{explanation}'))
    clean = _PEOPLE.sub(" ", clean)
    for match in _NUMBER.finditer(clean):
        token = match.group()
        x = float(token)
        near = bool(_APPROX_BEFORE.search(clean[:match.start()]) or _APPROX_AFTER.search(clean[match.end():]))
        ok = any(_rounded_matches(v, token) or (near and abs(x - v) <=
                 abs(v) * cfg["number_relative_tolerance"] + cfg["number_absolute_tolerance"]) for v in allowed)
        if not ok:
            out.append(Violation(path=path, code="number_not_found",
                message=f'文字中的数字 {token} 在允许数据中找不到。{explanation}'))
    return out


def _describe(moments: list[Moment], records: list) -> str:
    descriptions = []
    for m in moments:
        e, d = m.engagement, m.death
        values = {"alive_own": m.context.alive_own, "alive_enemy": m.context.alive_enemy,
                  "trade": m.trade.model_dump() if m.trade is not None else None,
                  "death.blinded": d.blinded if d else None,
                  "death.weapon_ready": d.weapon_ready if d else None}
        for name in ("time_to_damage_s", "crosshair_placement_deg", "visibility_note", "contact_type",
                     "seen_first_lead_s", "first_shot_lead_s", "first_shot_stopped"):
            values[name] = getattr(e, name) if e is not None else None
        values["player_blind"] = e.player_blind is not None if e is not None else False
        values["opponent_blind"] = e.opponent_blind is not None if e is not None else False
        values["shot_stopped"] = [s.stopped for s in e.shots] if e is not None else []
        values["recoil_index"] = [s.recoil_index for s in e.shots] if e is not None else []
        descriptions.append(f"{m.id} 实际数据：" + json.dumps(values, ensure_ascii=False))
    if records:
        descriptions.append("漏补枪记录：" + json.dumps([r.model_dump() for r in records], ensure_ascii=False))
    return "；".join(descriptions) if descriptions else "targets 中没有可核对的数据。"


def _check_entries(sub: ReportSubmission, mm: MatchMoments, report: ProblemReport, cfg: dict) -> list[Violation]:
    out: list[Violation] = []
    by_id = {m.id: m for m in mm.moments}
    actual_hits: dict[str, set[str]] = {}
    for h in report.hits:
        actual_hits.setdefault(h.target, set()).add(h.problem)
    disabled = set(report.disabled) | set(cfg["problems"]["disabled"])
    other_paths = []

    def add(path, code, message):
        out.append(Violation(path=path, code=code, message=message))

    for group in ("findings", "patterns", "highlights"):
        for i, entry in enumerate(getattr(sub, group)):
            path, pid = f"{group}[{i}]", entry.problem
            if pid == "other":
                other_paths.append(path)
            known = pid in PROBLEMS or pid == "other"
            if pid in CONTEXT_ONLY:
                add(path + ".problem", "context_only", f"{pid} 只能作背景：{CONTEXT_ONLY[pid]}")
            elif not known:
                add(path + ".problem", "unknown_problem", f"未知问题编号 {pid}，请使用提交工具 enum 中的编号。")
            if pid in disabled:
                add(path + ".problem", "disabled_problem", f"问题 {pid} 已被停用，不能提交。")
            spec = PROBLEMS.get(pid)
            highlight = spec is not None and spec.category == "highlight"
            if known and ((group == "highlights") != highlight):
                add(path + ".problem", "wrong_category", f"{pid} 的类别为 {spec.category if spec else 'other'}，不能放在 {group}。")
            if group == "patterns" and len(set(entry.targets)) < 2:
                add(path + ".targets", "pattern_too_few", f"规律至少需要 2 个不同 target，实际只有 {len(set(entry.targets))} 个。")

            moments, records, rounds = [], [], set()
            allowed = _numeric_leaves(len(entry.targets))
            for j, target in enumerate(entry.targets):
                target_path = f"{path}.targets[{j}]"
                valid = False
                if target == "match":
                    valid = pid == "spray_control"
                    if not valid:
                        add(target_path, "unknown_target", f"match 只能用于 spray_control，当前问题为 {pid}。")
                elif target in by_id:
                    m = by_id[target]
                    valid = True
                    moments.append(m)
                    rounds.add(m.round)
                    allowed.extend(_numeric_leaves(m.model_dump()))
                else:
                    match = _ROUND.fullmatch(target)
                    if match and 1 <= int(match[1]) <= mm.rounds:
                        valid = True
                        number = int(match[1])
                        rounds.add(number)
                        rows = [r for r in mm.missed_trades if r.round == number]
                        records.extend(rows)
                        allowed.extend(_numeric_leaves([r.model_dump() for r in rows]))
                    else:
                        add(target_path, "unknown_target", f"{target} 不存在，本场共 {mm.rounds} 回合；瞬间编号须来自 mm.moments。")
                if valid:
                    for h in report.hits:
                        if h.target == target:
                            allowed.extend(_numeric_leaves(h.evidence))
                            allowed.extend(_numeric_leaves(h.thresholds))
                    if known and pid != "other" and pid not in disabled and pid not in actual_hits.get(target, set()):
                        found = "、".join(sorted(actual_hits.get(target, set()))) or "没有任何命中"
                        add(target_path, "precondition_failed", f'{target} 不满足“{spec.label}”（{pid}）：该 target 实际检测到的问题为 {found}。')
            for number in rounds:
                allowed.extend(_numeric_leaves(number))

            text_path = path + ".analysis"
            clean = _WEAPONS.sub(" ", entry.analysis)
            identifiers = CITATION.findall(clean) + _MOMENT_ID.findall(CITATION.sub(" ", clean))
            if identifiers:
                add(text_path, "id_in_text", f"分析文字出现编号 { '、'.join(dict.fromkeys(identifiers)) }；编号只能放在 targets 中。")
            for match in _TEXT_ROUND.finditer(clean):
                number = int(next(v for v in match.groups() if v is not None))
                if number not in rounds:
                    add(text_path, "round_mismatch", f"文字提到第 {number} 回合，但本条 targets 的回合为 {sorted(rounds)}。")
            description = _describe(moments, records)
            people = {(m.context.alive_own, m.context.alive_enemy) for m in moments}
            out.extend(_number_violations(entry.analysis, text_path, allowed, people, description, cfg["checker"]))
            # 用户确认：match 可用整场逐枪数据核对说法，但不扩大其允许数值集合。
            predicate_moments = mm.moments if "match" in entry.targets and pid == "spray_control" else moments
            for phrase in cfg["checker"]["phrases"]:
                match = re.search(phrase["pattern"], entry.analysis)
                if match is None:
                    continue
                require = phrase["require"]
                supported = any(PREDICATES[require](m) for m in predicate_moments) or (require == "trade" and bool(records))
                if not supported:
                    actual = _describe(predicate_moments, records)
                    add(text_path, "phrase_unsupported", f'“{match.group()}”需要至少一个 target 满足 {require}，实际均不满足。{actual}')

    if len(other_paths) > cfg["checker"]["max_other"]:
        add("findings/patterns/highlights", "too_many_other",
            f'other 合计 {len(other_paths)} 条，上限为 {cfg["checker"]["max_other"]}；位置：{"、".join(other_paths)}。')
    return out


def check_submission(sub: ReportSubmission, mm: MatchMoments, report: ProblemReport,
                     digest: str, cfg: dict) -> list[Violation]:
    """检查提交内容；总览只引用摘要数值，训练文字不作数字或敏感说法检查。"""
    allowed = [float(m.group()) for m in _NUMBER.finditer(digest) if math.isfinite(float(m.group()))]
    people = {tuple(int(v) for v in m.groups()) for m in _PEOPLE.finditer(digest)}
    out = _number_violations(sub.overview, "overview", allowed, people, "总览数字及人数应来自 digest 原文。", cfg["checker"])
    identifiers = CITATION.findall(sub.overview) + _MOMENT_ID.findall(CITATION.sub(" ", sub.overview))
    if identifiers:
        out.append(Violation(path="overview", code="id_in_text", message="总览中不能写引用编号：" + "、".join(identifiers)))
    return out + _check_entries(sub, mm, report, cfg)
