"""结构化结论的纯函数检查：编号、前提、数字和敏感说法均可追溯。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from pydantic import BaseModel

from cs2coach.features.labels import WEAPON_LABELS
from cs2coach.features.models import MatchMoments, Moment
from cs2coach.features.problems import CONTEXT_ONLY, PROBLEMS, ProblemReport
from cs2coach.tools.drills import AIM_PROBLEMS
from cs2coach.features.ranking import candidate_targets
from cs2coach.tools.slots import SLOT, make_slot_resolver, render_slots, suggest_slots

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
    "not_planted": lambda m: m.context.bomb_planted is False,
    "never": lambda m: False,
    "death": lambda m: m.kind == "death",
    "kill": lambda m: m.kind == "kill",
}
# 与队友距离的说法：阈值来自配置，漏补枪记录的距离也算
DISTANCE_PREDICATES = {"teammate_far", "teammate_near"}
PHRASE_SCOPES = {"any", "all"}

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
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[ -]+[A-Za-z0-9]+)*")
_BOOK = re.compile(r"《([^》]+)》")
_EXPLICIT_MATCH = re.compile(r"(?<![A-Za-z0-9_])M\d+(?![A-Za-z0-9_:])")
# 具体的中文数字加量词；单独的“几次”“这几次”是模糊说法，不算数字，“十几次”“二十几发”算
_CHINESE_NUMBER = re.compile(r"(?:[两二三四五六七八九十百]+几?|几[十百]+几?)(?:次|发|枪|秒|米|回合|人|场|处|名)")


def _slot_violations(text: str, path: str, resolver, cfg: dict) -> list[Violation]:
    _, errors = render_slots(text, resolver)
    out = [Violation(path=path, code=e.code, message=e.message) for e in errors]
    clean = SLOT.sub(" ", text)
    clean = _WEAPONS.sub(" ", clean)
    for names in cfg["checker"]["map_aliases"].values():
        for name in names:
            clean = re.sub(re.escape(name), " ", clean, flags=re.I)
    for drill in cfg["training"]["drills"]:
        clean = re.sub(re.escape(drill["label"]), " ", clean, flags=re.I)
    for name in cfg["training"]["allowed_latin"]:
        clean = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", " ", clean, flags=re.I)
    # “75 分位”“p90”是分位名称，不是比赛数字
    labels = "|".join(str(n) for n in cfg["checker"]["quantile_labels"])
    if labels:
        clean = re.sub(rf"(?<![\d.])(?:{labels})\s*分位|(?<![A-Za-z0-9_])[pP](?:{labels})(?!\d)", " ", clean)
    # “1v3”是一个人数，整体推荐 people 占位符，不拆成两个数字
    people = list(dict.fromkeys(found.group().replace(" ", "").lower() for found in _PEOPLE.finditer(clean)))
    clean = _PEOPLE.sub(" ", clean)
    numbers = people + list(dict.fromkeys(found.group() for found in _NUMBER.finditer(clean)))
    if numbers:  # 一段文字只报一条，并按显示值推荐占位符，方便模型一次改完
        hints = suggest_slots(resolver, numbers)
        parts = [f"{n} → {'或'.join(hints[n])}" if hints.get(n) else
                 f"{n}（{'请按含义选择占位符' if n in hints else '没有对应的占位符，请删去或改写'}）"
                 for n in numbers]
        out.append(Violation(path=path, code="raw_number",
            message="文字里不能直接写数字，请改用占位符（占位符已带单位，后面不要再写单位）："
                    + "；".join(parts) + "。"))
    for found in _CHINESE_NUMBER.finditer(clean):  # “一次……另一次……”不在匹配范围内
        out.append(Violation(path=path, code="chinese_number",
            message=f"“{found.group()}”是带量词的中文数字；请改用占位符（如 count.*、first_hit_shot），"
                    "或改写成不含具体数量的说法。"))
    return out


def _map_mentions(text: str, cfg: dict) -> list[tuple[str, re.Match]]:
    aliases = [(alias, name) for name, names in cfg["checker"]["map_aliases"].items()
               for alias in names]
    aliases.sort(key=lambda pair: len(pair[0]), reverse=True)
    if not aliases:
        return []
    pattern = re.compile("|".join((r"(?<![A-Za-z0-9_])" + re.escape(alias) +
                       r"(?![A-Za-z0-9_])") if alias[0].isascii() else re.escape(alias)
                       for alias, _ in aliases), re.I)
    lookup = {alias.casefold(): name for alias, name in aliases}
    return [(lookup[m.group().casefold()], m) for m in pattern.finditer(text)]


def _map_violations(text: str, path: str, allowed: set[str], cfg: dict,
                    match_maps: dict[str, str] | None = None) -> list[Violation]:
    out = []
    mentions = _map_mentions(text, cfg)
    for name, match in mentions:
        paired = False
        if match_maps:
            # 在同一短语内查找最近的场次号，兼容“地图 …（M12）”和“M12 地图”。
            clause_start = max(text.rfind(c, 0, match.start()) for c in "，。；;\n") + 1
            ends = [text.find(c, match.end()) for c in "，。；;\n"]
            clause_end = min((end for end in ends if end >= 0), default=len(text))
            refs = list(_EXPLICIT_MATCH.finditer(text, clause_start, clause_end))
            if refs:
                following = [ref for ref in refs if 0 <= ref.start() - match.end() <= 30
                             and not any(match.end() <= other.start() < ref.start()
                                         for _, other in mentions if other is not match)]
                ref = (following[0] if following else
                       min(refs, key=lambda m: min(abs(m.start() - match.end()), abs(match.start() - m.end()))))
                actual = match_maps.get(ref.group())
                if actual and name != actual:
                    out.append(Violation(path=path, code="map_mismatch",
                        message=f"{ref.group()} 实际地图是 {actual}，不是 {match.group()}。"))
                    paired = True
        if name not in allowed and not paired:
            actual = "、".join(sorted(allowed)) or "无可核对地图"
            out.append(Violation(path=path, code="map_mismatch",
                message=f"文字中的 {match.group()} 与引用数据不符；实际地图：{actual}。"))
    return out


def _training_violations(text: str, path: str, cfg: dict, resolver=None) -> list[Violation]:
    from cs2coach.tools.digest import METRIC_LABELS
    slot_errors = render_slots(text, resolver)[1] if resolver is not None else []
    text = SLOT.sub(" ", text)  # 训练文字可以引用比赛数据的占位符，不算英文名称
    allowed_labels = {d["label"].casefold() for d in cfg["training"]["drills"]}
    allowed = allowed_labels | {s.casefold() for s in cfg["training"]["allowed_latin"]}
    allowed.update(s.casefold() for s in WEAPON_LABELS.values())
    allowed.update(v[0].casefold() for v in METRIC_LABELS.values())
    allowed.update(alias.casefold() for names in cfg["checker"]["map_aliases"].values() for alias in names)
    out = [Violation(path=path, code=e.code, message=e.message) for e in slot_errors]
    for match in _BOOK.finditer(text):
        if match[1].casefold() not in allowed_labels:
            out.append(Violation(path=path, code="unknown_drill",
                message=f"《{match[1]}》不在练习图白名单中。"))
    # 练习图的内部编号（如 5e_aim）只能放在 drills 字段里；文字中要写显示名称
    for drill in cfg["training"]["drills"]:
        pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(drill['id'])}(?![A-Za-z0-9_])", re.I)
        if pattern.search(text):
            out.append(Violation(path=path, code="unknown_drill",
                message=f"训练文字中写了练习图的内部编号 {drill['id']}；请写名称「{drill['label']}」。"))
            text = pattern.sub(" ", text)
    text = _PEOPLE.sub(" ", text)  # 3v2 这类人数写法不是英文名称
    for match in _LATIN.finditer(text):
        token = match.group().strip()
        if token.casefold() == "e" and match.start() > 0 and text[match.start() - 1] == "5":
            continue
        if token.casefold() not in allowed and not any(match.start() >= b.start(1) and match.end() <= b.end(1)
                                                      for b in _BOOK.finditer(text)):
            out.append(Violation(path=path, code="unknown_drill",
                message=f"训练文字中的英文名称 {token} 不在允许清单中。"))
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


def _teammate_distances(moments: list[Moment], records: list) -> list[float]:
    """死亡时最近存活队友的距离，加上漏补枪记录中与阵亡队友的距离（游戏单位）。"""
    out = [round(m.trade.nearest_teammate_distance, 1) for m in moments
           if m.trade is not None and m.trade.nearest_teammate_distance is not None]
    return out + [round(r.distance, 1) for r in records]


@dataclass
class _TargetData:
    moments: list[Moment]
    records: list
    rounds: set[int]
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
        return _TargetData(moments, records, rounds,
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


def _check_entries(sub: ReportSubmission, cfg: dict,
                   resolve, disabled: set[str], slot_factory, summary_count: int = 0,
                   match_maps: dict[str, str] | None = None,
                   single_map: str | None = None, *, candidates=None) -> list[Violation]:
    """candidates(problem) 返回 (该问题全部命中 target, finding 可选的前几名)；None 表示不限（020）。"""
    out: list[Violation] = []
    other_paths = []
    seen_findings: set[str] = set()
    aim_count = len({e.problem for e in [*sub.findings, *sub.patterns] if e.problem in AIM_PROBLEMS})
    drills = {d["id"]: d for d in cfg["training"]["drills"]}

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
                ranked, allowed = candidates(pid) if candidates is not None and pid != "other" else ((), None)
                if allowed is not None:
                    for j, target in enumerate(entry.targets):
                        # 没有这个问题命中的 target 由 precondition_failed 报告，这里只管排名靠后的真命中
                        if target in ranked and target not in allowed:
                            add(f"{path}.targets[{j}]", "not_representative",
                                f"{target} 不在“{PROBLEMS[pid].label if pid in PROBLEMS else pid}”的代表候选中；"
                                f"finding 只能从这几个中选：{'、'.join(allowed) or '无'}。其余命中可以放进 patterns。")
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
            allowed_maps = set()
            for j, target in enumerate(entry.targets):
                target_path = f"{path}.targets[{j}]"
                if match_maps:
                    mapped = match_maps.get(target.partition(":")[0])
                    if mapped:
                        allowed_maps.add(mapped)
                data, error = resolve(target, pid)
                if data is None:
                    add(target_path, "unknown_target", error)
                    continue
                moments.extend(data.moments)
                records.extend(data.records)
                rounds.update(data.rounds)
                if known and pid != "other" and pid not in disabled and pid not in data.hit_problems:
                    found = "、".join(sorted(data.hit_problems)) or "没有任何命中"
                    add(target_path, "precondition_failed", f'{target} 不满足“{spec.label}”（{pid}）：该 target 实际检测到的问题为 {found}。')
            text_path = path + ".analysis"
            if not match_maps:
                allowed_maps = {single_map} if single_map else set()
            clean = SLOT.sub(" ", _WEAPONS.sub(" ", entry.analysis))
            out.extend(_map_violations(entry.analysis, text_path, allowed_maps, cfg, match_maps))
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
            out.extend(_slot_violations(entry.analysis, text_path,
                                        slot_factory(pid, entry.targets), cfg))
            if group == "findings":
                for j, drill_id in enumerate(entry.drills):
                    drill = drills.get(drill_id)
                    if (drill is None or pid not in drill["problems"] or
                            aim_count < drill.get("min_aim_problems", 0)):
                        add(f"{path}.drills[{j}]", "drill_not_allowed",
                            f"练习图 {drill_id} 不适用于 {pid} 或本报告枪法问题类别不足。")
                out.extend(_training_violations(entry.training, path + ".training", cfg,
                                                slot_factory(pid, entry.targets)))
            # 用户确认：match 可用整场逐枪数据核对说法，但不扩大其允许数值集合。
            predicate_moments = moments
            if "match" in entry.targets and pid == "spray_control":
                predicate_moments = resolve("match", pid)[0].predicate_moments
            for phrase in cfg["checker"]["phrases"]:
                match = re.search(phrase["pattern"], entry.analysis)
                if match is None:
                    continue
                require = phrase["require"]
                if require in DISTANCE_PREDICATES:
                    distances = _teammate_distances(moments, records)
                    far, near = cfg["checker"]["teammate_far_distance"], cfg["checker"]["teammate_near_distance"]
                    if not any(d >= far if require == "teammate_far" else d <= near for d in distances):
                        limit = f"不少于 {far:g}" if require == "teammate_far" else f"不超过 {near:g}"
                        shown = "、".join(f"{d:g}" for d in distances) or "没有距离数据"
                        add(text_path, "phrase_unsupported",
                            f"“{match.group()}”需要至少一个 target 与队友的距离{limit} 单位，实际为：{shown}。")
                    continue
                if phrase.get("scope", "any") == "all":
                    # 只核对本条 targets 中的瞬间；只有漏补枪记录时（说的是队友阵亡）不适用
                    failing = [m.id for m in moments if not PREDICATES[require](m)]
                    if failing:
                        add(text_path, "phrase_unsupported",
                            f"“{match.group()}”要求所有 target 都满足 {require}，"
                            f"但 {'、'.join(failing)} 不满足（实际类型：{'、'.join(f'{m.id}={m.kind}' for m in moments)}）。")
                    continue
                supported = (any(PREDICATES[require](m) for m in predicate_moments)
                             or (require == "trade" and bool(records))
                             or (require == "planted" and any(r.bomb_planted is True for r in records))
                             or (require == "not_planted" and any(r.bomb_planted is False for r in records)))
                if not supported:
                    actual = _describe(predicate_moments, records)
                    message = ("round_time_s 是已经过去的秒数，请写成\"回合第 N 秒\"。"
                               if require == "never" else
                               f'“{match.group()}”需要至少一个 target 满足 {require}，实际均不满足。{actual}')
                    add(text_path, "phrase_unsupported", message)

    if len(other_paths) > cfg["checker"]["max_other"]:
        add("findings/patterns/highlights", "too_many_other",
            f'other 合计 {len(other_paths)} 条，上限为 {cfg["checker"]["max_other"]}；位置：{"、".join(other_paths)}。')
    for i, item in enumerate(sub.training_plan):
        # 训练计划不针对具体交火，可以引用本场任意瞬间
        out.extend(_training_violations(item, f"training_plan[{i}]", cfg, slot_factory(None, None)))
    return out


def _check_overview(sub: ReportSubmission, digest: str, cfg: dict, slot_factory,
                    id_pattern: re.Pattern, summary_count: int = 0,
                    maps: set[str] | None = None) -> list[Violation]:
    out = _slot_violations(sub.overview, "overview", slot_factory(None, (), True), cfg)
    out.extend(_map_violations(sub.overview, "overview", maps or set(), cfg))
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
                     digest: str, cfg: dict, whole_numbers: list[float] | None = None,
                     score=None, frequency=None, baseline=None) -> list[Violation]:
    """单场检查；训练文字不作数字或敏感说法检查。

    whole_numbers 自 019 起不再使用（数字一律由占位符填写），保留参数只为兼容现有调用；
    score、frequency、baseline 供占位符取值，缺少时相应占位符报 slot_unavailable。
    """
    slots = lambda problem, targets, overview=False: make_slot_resolver(
        mm, report, score, frequency, baseline, problem=problem, targets=targets, overview=overview)
    return (_check_overview(sub, digest, cfg, slots, _MOMENT_ID, maps={mm.map_name})
            + _check_entries(sub, cfg, _single_resolver(mm, report),
                             set(report.disabled) | set(cfg["problems"]["disabled"]),
                             slots, single_map=mm.map_name,
                             candidates=lambda pid: (
                                 candidate_targets(mm, report.hits, pid, len(report.hits)),
                                 candidate_targets(mm, report.hits, pid, cfg["checker"]["finding_candidates"]))))


def _summary_candidates(summary, pid: str, cfg: dict) -> tuple[list[str], list[str]]:
    """多场：该问题的全部跨场命中，以及摘要中列出的代表（finding 只能从代表中选）。"""
    from cs2coach.summary.digest import problem_hits, representative_hits  # 避免循环导入
    every = [ref for ref, _ in problem_hits(summary, pid)]
    chosen = [ref for ref, _ in representative_hits(summary, pid, cfg["summary"]["representatives_per_problem"])]
    return every, chosen


def check_summary_submission(sub: ReportSubmission, summary, digest: str, cfg: dict,
                             whole_numbers: list[float], baseline=None) -> list[Violation]:
    """跨场检查与单场共用前提、占位符、敏感说法规则；whole_numbers 同单场，已不再使用。"""
    disabled = set(cfg["problems"]["disabled"])
    for match in summary.matches:
        disabled.update(summary.detail(match.number)[1].disabled)
    match_maps = {match.number: match.map_name for match in summary.matches}
    slots = lambda problem, targets, overview=False: make_slot_resolver(
        summary=summary, baseline=baseline, problem=problem, targets=targets, overview=overview)
    return (_check_overview(sub, digest, cfg, slots, _SUMMARY_ID, len(summary.matches), set(match_maps.values()))
            + _check_entries(sub, cfg, _summary_resolver(summary), disabled,
                             slots, len(summary.matches), match_maps,
                             candidates=lambda pid: _summary_candidates(summary, pid, cfg)))
