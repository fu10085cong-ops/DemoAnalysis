"""结构化结论的纯函数检查：编号、前提、数字和敏感说法均可追溯。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal
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
    "planted": lambda m: m.context.bomb_planted is True,
    "never": lambda m: False,
}

# 从旧引用模块原样迁移，仅用于拒绝分析文字里的引用；检查规则不变。
CITATION = re.compile(r"\[\[\s*([A-Za-z0-9\-]+)\s*\]\]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PEOPLE = re.compile(r"(?<![A-Za-z0-9_.])(\d+)\s*[vV]\s*(\d+)(?![A-Za-z0-9_.])")
_ROUND = re.compile(r"^R(\d+)$")
_TEXT_ROUND = re.compile(r"第\s*(\d+)\s*回合|(?<![A-Za-z0-9_])R(\d+)(?!\d)")
_MOMENT_ID = re.compile(r"(?<![A-Za-z0-9_])R\d+-(?:kill\d+|death|assist\d+)(?![A-Za-z0-9_])")
_SUMMARY_ID = re.compile(r"(?<![A-Za-z0-9_])M\d+:R\d+(?:-(?:kill\d+|death|assist\d+))?(?![A-Za-z0-9_])")
_SUMMARY_TARGET = re.compile(r"^(M\d+):(R\d+(?:-(?:kill\d+|death|assist\d+))?)$")
_TEXT_MATCH = re.compile(r"第\s*(\d+)\s*场")
_WEAPONS = re.compile("|".join(re.escape(s) for s in sorted(WEAPON_LABELS.values(), key=len, reverse=True)), re.I)
_APPROX_BEFORE = re.compile(r"(?:约|大约|接近|左右)\s*$")
_APPROX_AFTER = re.compile(r"^\s*(?:%|毫秒|秒|度|米|发|次|人)?\s*左右")
_QUANTILE_BEFORE = re.compile(r"(?<![A-Za-z0-9_])[pP]$")
_QUANTILE_AFTER = re.compile(r"^\s*分位")


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
    """数字只要与数据相差不到文字最后一位的一个单位就匹配。"""
    digits = len(token.partition(".")[2])
    return abs(Decimal(str(value)) - Decimal(token)) < Decimal(1).scaleb(-digits)


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
        if (int(float(token)) in cfg["quantile_labels"] and float(token).is_integer()
                and (_QUANTILE_BEFORE.search(clean[:match.start()])
                     or _QUANTILE_AFTER.match(clean[match.end():]))):
            continue
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


@dataclass
class _TargetData:
    moments: list[Moment]
    records: list
    rounds: set[int]
    numbers: list[float]
    hit_problems: set[str]
    predicate_moments: list[Moment]


def _single_resolver(mm: MatchMoments, report: ProblemReport):
    by_id = {m.id: m for m in mm.moments}

    def resolve(target: str, pid: str) -> tuple[_TargetData | None, str]:
        if target == "match":
            if pid != "spray_control":
                return None, f"match 只能用于 spray_control，当前问题为 {pid}。"
            moments, records, rounds = [], [], set()
            predicate = mm.moments
        elif target in by_id:
            moments, records = [by_id[target]], []
            rounds = {moments[0].round}
            predicate = moments
        else:
            found = _ROUND.fullmatch(target)
            if not found or not 1 <= int(found[1]) <= mm.rounds:
                return None, f"{target} 不存在，本场共 {mm.rounds} 回合；瞬间编号须来自 mm.moments。"
            number = int(found[1])
            moments, records = [], [r for r in mm.missed_trades if r.round == number]
            rounds, predicate = {number}, []
        hits = [h for h in report.hits if h.target == target]
        numbers = _numeric_leaves([m.model_dump() for m in moments])
        numbers.extend(_numeric_leaves([r.model_dump() for r in records]))
        for hit in hits:
            numbers.extend(_numeric_leaves(hit.evidence))
            numbers.extend(_numeric_leaves(hit.thresholds))
        return _TargetData(moments, records, rounds, numbers,
                           {h.problem for h in hits}, predicate), ""
    return resolve


def _summary_resolver(summary):
    matches = {match.number: match for match in summary.matches}

    def resolve(target: str, pid: str) -> tuple[_TargetData | None, str]:
        parts = _SUMMARY_TARGET.fullmatch(target)
        if not parts or parts[1] not in matches:
            return None, f"{target} 不存在；跨场编号须使用 M序号:R回合或 M序号:R回合-瞬间。"
        number, local = parts.groups()
        mm, report = summary.detail(number)
        data, error = _single_resolver(mm, report)(local, pid)
        if data is None or local == "match":
            return None, error or f"{target} 不存在。"
        return data, ""
    return resolve


def _check_entries(sub: ReportSubmission, cfg: dict, whole_numbers: list[float],
                   resolve, disabled: set[str], summary_count: int = 0) -> list[Violation]:
    out: list[Violation] = []
    other_paths = []
    seen_findings: set[str] = set()

    def add(path, code, message):
        out.append(Violation(path=path, code=code, message=message))

    for group in ("findings", "patterns", "highlights"):
        for i, entry in enumerate(getattr(sub, group)):
            path, pid = f"{group}[{i}]", entry.problem
            if group == "findings":
                if pid in seen_findings:
                    add(path + ".problem", "duplicate_problem",
                        f"问题 {pid} 已在前一条 finding 中出现；同一问题只能保留一条代表性交火。")
                seen_findings.add(pid)
                if len(entry.targets) > cfg["checker"]["max_finding_targets"]:
                    add(path + ".targets", "too_many_targets",
                        f"finding 最多关联 {cfg['checker']['max_finding_targets']} 个 target，实际为 {len(entry.targets)} 个。")
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
            allowed = _numeric_leaves(len(entry.targets)) + whole_numbers
            for j, target in enumerate(entry.targets):
                target_path = f"{path}.targets[{j}]"
                data, error = resolve(target, pid)
                if data is None:
                    add(target_path, "unknown_target", error)
                    continue
                moments.extend(data.moments)
                records.extend(data.records)
                rounds.update(data.rounds)
                allowed.extend(data.numbers)
                if known and pid != "other" and pid not in disabled and pid not in data.hit_problems:
                    found = "、".join(sorted(data.hit_problems)) or "没有任何命中"
                    add(target_path, "precondition_failed", f'{target} 不满足“{spec.label}”（{pid}）：该 target 实际检测到的问题为 {found}。')
            for number in rounds:
                allowed.extend(_numeric_leaves(number))

            text_path = path + ".analysis"
            clean = _WEAPONS.sub(" ", entry.analysis)
            id_pattern = _SUMMARY_ID if summary_count else _MOMENT_ID
            identifiers = CITATION.findall(clean) + id_pattern.findall(CITATION.sub(" ", clean))
            if identifiers:
                add(text_path, "id_in_text", f"分析文字出现编号 { '、'.join(dict.fromkeys(identifiers)) }；编号只能放在 targets 中。")
            for match in _TEXT_ROUND.finditer(clean):
                number = int(next(v for v in match.groups() if v is not None))
                if number not in rounds:
                    add(text_path, "round_mismatch", f"文字提到第 {number} 回合，但本条 targets 的回合为 {sorted(rounds)}。")
            if summary_count:
                for match in _TEXT_MATCH.finditer(clean):
                    number = int(match[1])
                    if not 1 <= number <= summary_count:
                        add(text_path, "match_mismatch", f"文字提到第 {number} 场，但汇总只有 {summary_count} 场。")
            description = _describe(moments, records)
            people = {(m.context.alive_own, m.context.alive_enemy) for m in moments}
            people.update((r.alive_own, r.alive_enemy) for r in records
                          if r.alive_own is not None and r.alive_enemy is not None)
            out.extend(_number_violations(entry.analysis, text_path, allowed, people, description, cfg["checker"]))
            # 用户确认：match 可用整场逐枪数据核对说法，但不扩大其允许数值集合。
            predicate_moments = moments
            if "match" in entry.targets and pid == "spray_control":
                predicate_moments = resolve("match", pid)[0].predicate_moments
            for phrase in cfg["checker"]["phrases"]:
                match = re.search(phrase["pattern"], entry.analysis)
                if match is None:
                    continue
                require = phrase["require"]
                supported = (any(PREDICATES[require](m) for m in predicate_moments)
                             or (require == "trade" and bool(records))
                             or (require == "planted" and any(r.bomb_planted is True for r in records)))
                if not supported:
                    actual = _describe(predicate_moments, records)
                    message = ("round_time_s 是已经过去的秒数，请写成\"回合第 N 秒\"。"
                               if require == "never" else
                               f'“{match.group()}”需要至少一个 target 满足 {require}，实际均不满足。{actual}')
                    add(text_path, "phrase_unsupported", message)

    if len(other_paths) > cfg["checker"]["max_other"]:
        add("findings/patterns/highlights", "too_many_other",
            f'other 合计 {len(other_paths)} 条，上限为 {cfg["checker"]["max_other"]}；位置：{"、".join(other_paths)}。')
    return out


def _check_overview(sub: ReportSubmission, digest: str, cfg: dict,
                    id_pattern: re.Pattern, summary_count: int = 0) -> list[Violation]:
    allowed = [float(m.group()) for m in _NUMBER.finditer(digest) if math.isfinite(float(m.group()))]
    people = {tuple(int(v) for v in m.groups()) for m in _PEOPLE.finditer(digest)}
    out = _number_violations(sub.overview, "overview", allowed, people, "总览数字及人数应来自 digest 原文。", cfg["checker"])
    identifiers = CITATION.findall(sub.overview) + id_pattern.findall(CITATION.sub(" ", sub.overview))
    if identifiers:
        out.append(Violation(path="overview", code="id_in_text", message="总览中不能写引用编号：" + "、".join(identifiers)))
    if summary_count:
        for match in _TEXT_MATCH.finditer(sub.overview):
            number = int(match[1])
            if not 1 <= number <= summary_count:
                out.append(Violation(path="overview", code="match_mismatch",
                    message=f"总览提到第 {number} 场，但汇总只有 {summary_count} 场。"))
    return out


def check_submission(sub: ReportSubmission, mm: MatchMoments, report: ProblemReport,
                     digest: str, cfg: dict, whole_numbers: list[float] | None = None) -> list[Violation]:
    """单场检查；分析可引用整场数值，训练文字不作数字或敏感说法检查。"""
    return (_check_overview(sub, digest, cfg, _MOMENT_ID)
            + _check_entries(sub, cfg, whole_numbers or [], _single_resolver(mm, report),
                             set(report.disabled) | set(cfg["problems"]["disabled"])))


def check_summary_submission(sub: ReportSubmission, summary, digest: str, cfg: dict,
                             whole_numbers: list[float]) -> list[Violation]:
    """跨场检查与单场共用前提、数字、敏感说法规则。"""
    disabled = set(cfg["problems"]["disabled"])
    for match in summary.matches:
        disabled.update(summary.detail(match.number)[1].disabled)
    return (_check_overview(sub, digest, cfg, _SUMMARY_ID, len(summary.matches))
            + _check_entries(sub, cfg, whole_numbers, _summary_resolver(summary), disabled,
                             len(summary.matches)))
