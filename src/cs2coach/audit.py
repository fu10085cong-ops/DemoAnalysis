"""S01 检测审计的纯数据操作；不改变正式报告和检测规则。"""

from __future__ import annotations

import hashlib
import json
import math
import random
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

from cs2coach.features.problems import DETECTED_PROBLEMS, ProblemHit
from cs2coach.report.evidence import evidence_sentence, match_fact
from cs2coach.report.facts import missed_trade_facts, render_moment_card


def hit_key(demo: str, member: str | None, steamid: int, hit: ProblemHit) -> str:
    """路径、成员、玩家、问题、目标及整场分段组成稳定编号。"""
    evidence = hit.evidence
    extra = ([hit.weapon, evidence.get("segment"), evidence.get("tick")]
             if hit.target in ("match",) or hit.problem == "missed_trade" else [])
    parts = [demo, member or "", str(steamid), hit.problem, hit.target, *extra]
    digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    return f"{hit.problem}-{digest}"


def _near(value: float | None, threshold: float, ratio: float) -> bool:
    return value is not None and math.isfinite(value) and abs(value - threshold) <= threshold * ratio


def scan_hit(hit: ProblemHit, moment, cfg: dict) -> list[dict[str, str]]:
    """逐条配置判定可疑组合，缺失字段不当作触发。"""
    e = moment.engagement if moment is not None else None
    ratio = cfg["audit"]["borderline_ratio"]
    found = []
    for rule in cfg["audit"]["rules"]:
        if hit.problem not in rule["problems"]:
            continue
        condition = rule["condition"]
        detail = None
        if condition == "shots_before_seen_positive" and e is not None and e.shots_before_seen is not None and e.shots_before_seen > 0:
            detail = f"看见前开枪 {e.shots_before_seen} 发"
        elif condition == "negative_seen_to_shot" and e is not None and e.seen_to_first_shot_s is not None and e.seen_to_first_shot_s < 0:
            detail = f"看见到第一枪 {e.seen_to_first_shot_s:.3f} 秒"
        elif condition == "blind_at_seen" and e is not None and e.player_seen_while_blind is True:
            detail = rule["description"]
        elif condition == "visibility_note_set" and e is not None and e.visibility_note in cfg["audit"]["incomplete_visibility_notes"]:
            detail = f"视野说明：{e.visibility_note}"
        elif condition == "through_smoke" and moment is not None and getattr(moment, "kind", None) in ("kill", "death") and getattr(moment, "through_smoke", False) is True:
            detail = rule["description"]
        elif condition == "contact_speed_borderline" and e is not None:
            limits = (cfg["moments"]["contact_still_speed"], cfg["moments"]["contact_moving_speed"])
            speeds = (e.player_speed_at_contact, e.opponent_speed_at_contact)
            if any(_near(speed, limit, ratio) for speed in speeds for limit in limits):
                detail = f"接触速度：本人 {speeds[0]}、对手 {speeds[1]} u/s；阈值 {limits[0]}/{limits[1]} u/s"
        elif condition == "stop_borderline" and e is not None and e.shots:
            first = min(e.shots, key=lambda shot: shot.tick)
            limit = cfg["counter_strafe"]["accurate_speed_ratio"]
            if _near(first.speed_ratio, limit, ratio):
                detail = f"第一枪速度比 {first.speed_ratio:.3f}；阈值 {limit}"
        elif condition == "spray_mostly_unseen":
            shots = hit.evidence.get("shots")
            excluded = hit.evidence.get("excluded_unseen_shots")
            if isinstance(shots, (int, float)) and shots > 0 and isinstance(excluded, (int, float)) and excluded >= shots:
                detail = f"未见目标排除 {excluded} 发，参与评价 {shots} 发"
        elif condition == "crosshair_extreme":
            angle = hit.evidence.get("crosshair_placement_deg")
            if isinstance(angle, (int, float)) and angle > rule["angle_deg"]:
                detail = f"准星角度 {angle:.1f}°，超过 {rule['angle_deg']}°"
        if detail is not None:
            found.append({"id": rule["id"], "description": rule["description"], "detail": detail})
    return found


def make_record(demo: str, member: str | None, mm, hit: ProblemHit, cfg: dict,
                names: dict[int, str], disabled: set[str]) -> dict:
    moments = {m.id: m for m in mm.moments}
    moment = moments.get(hit.target)
    if moment is not None:
        card = render_moment_card(moment, names)
        jump = moment.jump_command
    elif hit.problem == "missed_trade":
        mt = next((item for item in mm.missed_trades if item.round == hit.evidence["round"]
                   and item.tick == hit.evidence["tick"]), None)
        if mt is None:
            raise ValueError(f"漏补枪记录不存在：{hit.target} / {hit.evidence.get('tick')}")
        card = f"### 〔{hit.target}〕漏补枪\n\n" + "\n".join(f"- {line}" for line in missed_trade_facts(mt, names))
        # 漏补枪不是 Moment；以同回合首个瞬间的命令为格式参照，目标 tick 前退三秒。
        lead = cfg["moments"]["jump_lead_seconds"]
        jump = f"demo_gototick {max(0, mt.tick - round(lead * mm.tick_rate))}"
    elif hit.target == "match":
        card = match_fact(hit)
        jump = None
    else:
        raise ValueError(f"未知命中目标：{hit.target}")
    return {"hit_key": hit_key(demo, member, mm.player, hit), "demo": demo, "member": member,
            "map": mm.map_name, "steamid": mm.player, "player_name": mm.player_name,
            "problem": hit.problem, "disabled": hit.problem in disabled, "target": hit.target,
            "weapon": hit.weapon, "evidence": hit.evidence, "thresholds": hit.thresholds,
            "fact_card": card, "basis": evidence_sentence(hit), "jump_command": jump,
            "suspicious": scan_hit(hit, moment, cfg)}


def read_hits(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = [row["hit_key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("hits.jsonl 中 hit_key 重复")
    return rows


def write_hits(path: Path, rows: list[dict]) -> None:
    keys = [row["hit_key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("收集结果 hit_key 重复，未写入文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def suspicious_summary(rows: list[dict], cfg: dict) -> str:
    totals = Counter(row["problem"] for row in rows)
    lines = ["# 可疑组合扫描", "", "| 规则 | 问题 | 可疑 / 全部 | 比例 |", "|---|---|---:|---:|"]
    for rule in cfg["audit"]["rules"]:
        for problem in rule["problems"]:
            matches = [row for row in rows if row["problem"] == problem and any(s["id"] == rule["id"] for s in row["suspicious"])]
            total = totals[problem]
            lines.append(f"| {rule['id']} | {problem} | {len(matches)} / {total} | {len(matches) / total:.1%} |" if total else
                         f"| {rule['id']} | {problem} | 0 / 0 | — |")
    lines.append("")
    for rule in cfg["audit"]["rules"]:
        lines += ["", f"## {rule['id']}：{rule['description']}", ""]
        matches = [row for row in rows if any(s["id"] == rule["id"] for s in row["suspicious"])]
        lines += [f"- `{row['hit_key']}`：{next(s['detail'] for s in row['suspicious'] if s['id'] == rule['id'])}"
                  for row in matches[:10]] or ["无命中。"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def sample_hits(rows: list[dict], per_problem: int = 20, per_demo: int = 3,
                seed: int = 20261017) -> list[tuple[dict, str]]:
    """按问题抽样；每类问题中同一场比赛最多取 per_demo 条。"""
    if per_problem < 1 or per_demo < 1:
        raise ValueError("per_problem 和 per_demo 必须为正整数")
    rng = random.Random(seed)
    selected = []
    by_problem = defaultdict(list)
    for row in rows:
        by_problem[row["problem"]].append(row)
    for problem in DETECTED_PROBLEMS:
        counts: Counter = Counter()
        candidates = sorted(by_problem[problem], key=lambda row: row["hit_key"])
        suspicious = [row for row in candidates if row["suspicious"]]
        ordinary = [row for row in candidates if not row["suspicious"]]
        rng.shuffle(suspicious)
        rng.shuffle(ordinary)
        possible = min(per_problem, sum(min(per_demo, count) for count in Counter(
            (row["demo"], row.get("member")) for row in candidates).values()))
        chosen_s, chosen_r = [], []
        # 只有可疑层时单独抽样；混合样本里可疑优先项不超过一半。
        priority_limit = possible if not ordinary else possible // 2
        for row in suspicious:
            demo = (row["demo"], row.get("member"))
            if len(chosen_s) >= priority_limit:
                break
            if counts[demo] < per_demo:
                chosen_s.append(row)
                counts[demo] += 1
        for row in ordinary:
            demo = (row["demo"], row.get("member"))
            if len(chosen_s) + len(chosen_r) >= per_problem:
                break
            if counts[demo] < per_demo:
                chosen_r.append(row)
                counts[demo] += 1
        if ordinary:
            while len(chosen_s) > len(chosen_r):
                chosen_s.pop()
        selected.extend((row, "suspicious_priority") for row in chosen_s)
        selected.extend((row, "random") for row in chosen_r)
    return selected


def review_page(problem: str, samples: list[tuple[dict, str]]) -> str:
    lines = [f"# {problem} 核对清单", "", f"共 {len(samples)} 条。判定请填写同目录上级的 labels.toml。", ""]
    for row, selection in samples:
        suspicious = "；".join(f"{s['id']}：{s['detail']}" for s in row["suspicious"]) or "无"
        lines += [f"## {row['hit_key']}", "", f"- 来源：{'可疑优先' if selection == 'suspicious_priority' else '随机'}",
                  f"- Demo：`{row['demo']}`；成员：`{row.get('member') or '无'}`；地图：{row['map']}",
                  f"- 玩家：{row['player_name']}（{row['steamid']}）；目标：{row['target']}",
                  f"- 当前停用：{'是' if row['disabled'] else '否'}", f"- 可疑组合：{suspicious}", "",
                  row["fact_card"], "", f"程序依据：{row['basis']}", "", "证据 JSON：", "```json",
                  json.dumps({"evidence": row["evidence"], "thresholds": row["thresholds"]}, ensure_ascii=False, indent=2),
                  "```", "", f"跳转命令：`{row.get('jump_command') or '无（整场统计）'}`", "",
                  "判定：________（correct / wrong / unsure）；原因：________；游戏内核对备注：________", ""]
    return "\n".join(lines).rstrip() + "\n"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def update_labels(path: Path, samples: list[tuple[dict, str]], visibility: set[str]) -> None:
    """保留现有文件所有字节，只在文件末尾追加新条目。"""
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    old = tomllib.loads(original).get("labels", {}) if original else {}
    additions = []
    for row, selection in samples:
        key = row["hit_key"]
        if key in old:
            continue
        additions += [f"[labels.{_toml_string(key)}]", f"verdict = {_toml_string('')}",
                      f"reason = {_toml_string('')}", f"needs_ingame = {str(row['problem'] in visibility).lower()}",
                      f"note = {_toml_string('')}", f"selection = {_toml_string(selection)}", ""]
        old[key] = True
    if additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        separator = "" if not original or original.endswith("\n\n") else "\n" if original.endswith("\n") else "\n\n"
        path.write_text(original + separator + "\n".join(additions), encoding="utf-8")


def wilson(correct: int, wrong: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    n = correct + wrong
    if not n:
        return None
    p = correct / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0, center - margin), min(1, center + margin)


def precision_report(rows: list[dict], labels: dict) -> str:
    hits = {row["hit_key"]: row for row in rows}
    lines = ["# 检测规则抽样准确率", "",
             "可疑命中优先抽样，故按可疑与不可疑两层分别统计，再按两层在全部命中中的比例加权得到整体准确率。"
             "两层使用 Wilson 95% 区间；整体使用分层方差的正态近似区间。未填写与不确定判定不计入准确率。", "",
             "| 问题 | 层 | 总命中 | 权重 | 已判定 | 正确 | 错误 | 不确定 | 准确率 | 95% 区间 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    reasons = defaultdict(Counter)
    for problem in DETECTED_PROBLEMS:
        population = {True: sum(row["problem"] == problem and bool(row["suspicious"]) for row in rows),
                      False: sum(row["problem"] == problem and not row["suspicious"] for row in rows)}
        total = population[True] + population[False]
        strata = {}
        for suspicious, label in ((True, "可疑层"), (False, "不可疑层")):
            verdicts = Counter()
            for key, entry in labels.items():
                row = hits.get(key)
                if row is None or row["problem"] != problem or bool(row["suspicious"]) != suspicious:
                    continue
                verdict = entry.get("verdict", "")
                if verdict not in ("", "correct", "wrong", "unsure"):
                    raise ValueError(f"无效判定 {key}: {verdict}")
                if verdict:
                    verdicts[verdict] += 1
                if verdict == "wrong":
                    reasons[(problem, suspicious)][entry.get("reason") or "未填写原因"] += 1
            correct, wrong, unsure = (verdicts[v] for v in ("correct", "wrong", "unsure"))
            n = correct + wrong
            strata[suspicious] = {"correct": correct, "wrong": wrong, "unsure": unsure,
                                  "p": correct / n if n else None, "n": n}
            interval = wilson(correct, wrong)
            precision = f"{correct / n:.1%}" if n else "—"
            ci = f"{interval[0]:.1%}–{interval[1]:.1%}" if interval else "—"
            weight = f"{population[suspicious] / total:.1%}" if total else "—"
            lines.append(f"| {problem} | {label} | {population[suspicious]} | {weight} | {sum(verdicts.values())} | {correct} | {wrong} | {unsure} | {precision} | {ci} |")
        judged = sum(stratum["n"] + stratum["unsure"] for stratum in strata.values())
        correct = sum(stratum["correct"] for stratum in strata.values())
        wrong = sum(stratum["wrong"] for stratum in strata.values())
        unsure = sum(stratum["unsure"] for stratum in strata.values())
        if total and all(population[s] == 0 or strata[s]["n"] > 0 for s in (True, False)):
            weighted = sum(population[s] / total * strata[s]["p"] for s in (True, False) if population[s])
            variance = sum((population[s] / total) ** 2 * strata[s]["p"] * (1 - strata[s]["p"]) / strata[s]["n"]
                           for s in (True, False) if population[s])
            margin = 1.959963984540054 * math.sqrt(variance)
            precision = f"{weighted:.1%}"
            ci = f"{max(0, weighted - margin):.1%}–{min(1, weighted + margin):.1%}"
        else:
            precision = ci = "不足"
        lines.append(f"| {problem} | 整体（加权） | {total} | {'100.0%' if total else '—'} | {judged} | {correct} | {wrong} | {unsure} | {precision} | {ci} |")
    lines += ["", "## 错误原因", ""]
    for (problem, suspicious), counts in sorted(reasons.items()):
        lines.append(f"### {problem}（{'可疑层' if suspicious else '不可疑层'}）")
        lines += [f"- {reason}：{count}" for reason, count in counts.most_common()]
        lines.append("")
    if not reasons:
        lines.append("暂无已判定错误。")
    return "\n".join(lines).rstrip() + "\n"
