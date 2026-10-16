"""从多场 demo 聚合问题频率和每场枪法中位数。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median

from pydantic import BaseModel, PrivateAttr

from cs2coach.cache import cache_key, cached
from cs2coach.features.models import AimStats, MatchMoments
from cs2coach.features.problems import (FrequencyRow, ProblemReport, detect_problems,
                                         frequency_from_counts, problem_frequency)
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.summary.discover import DemoRef, SkippedDemo
from cs2coach.tools.digest import match_score


_AIM_FIELDS = ("time_to_damage_ms", "crosshair_placement_deg", "counter_strafing_pct",
               "spray_accuracy_pct", "spotted_accuracy_pct", "headshot_accuracy_pct")


class MatchSummary(BaseModel):
    number: str
    match_id: str
    map_name: str
    rounds: int
    rounds_played: int
    score: tuple[int, int]
    kills: int
    deaths: int
    assists: int
    aim_stats: AimStats | None
    prominent_problems: list[str]


class SummaryFrequencyRow(FrequencyRow):
    matches_with_hits: int
    matches_above_pro: int
    by_map: dict[str, int]


class AimMedian(BaseModel):
    median: float | None
    matches: int


class PlayerSummary(BaseModel):
    steamid: int
    names: list[str]
    matches: list[MatchSummary]
    skipped: list[SkippedDemo]
    rounds_played_total: int
    frequency: list[SummaryFrequencyRow]
    aim_overall: dict[str, AimMedian]

    # 013 用来回查 M3:R9-death 等引用，不序列化到 summary.json。
    _details: dict[str, tuple[MatchMoments, ProblemReport]] = PrivateAttr(default_factory=dict)

    def detail(self, number: str) -> tuple[MatchMoments, ProblemReport]:
        return self._details[number]

    @property
    def primary_name(self) -> str:
        """按出现场数选名字，平票沿用编号最小场次的名字。"""
        counts = Counter(self._details[match.number][0].player_name for match in self.matches
                         if match.number in self._details)
        if counts:
            return max(counts, key=counts.get)
        return self.names[0] if self.names else str(self.steamid)


def aggregate_matches(steamid: int, games: list[tuple[DemoRef, MatchMoments, ProblemReport,
                                                    tuple[int, int]]], baseline: dict | None,
                      cfg: dict, skipped: list[SkippedDemo] | None = None) -> PlayerSummary:
    """聚合已解析的比赛；测试可用手工 MatchMoments/ProblemReport 验证口径。"""
    games = sorted(games, key=lambda item: item[0].match_id)
    counts: Counter[str] = Counter()
    by_map: dict[str, Counter[str]] = {}
    with_hits: Counter[str] = Counter()
    above_pro: Counter[str] = Counter()
    disabled = set(cfg["problems"]["disabled"])
    names: list[str] = []
    matches: list[MatchSummary] = []
    details: dict[str, tuple[MatchMoments, ProblemReport]] = {}
    rounds_total = 0
    for ref, mm, report, score in games:
        number = f"M{len(matches) + 1}"
        details[number] = (mm, report)
        if mm.player_name not in names:
            names.append(mm.player_name)
        rounds_total += mm.rounds_played
        disabled.update(report.disabled)
        local = Counter(hit.problem for hit in report.hits if hit.target != "match")
        counts.update(local)
        by_map.setdefault(mm.map_name, Counter()).update(local)
        with_hits.update(pid for pid, count in local.items() if count > 0)
        local_frequency = problem_frequency(report, mm, baseline, cfg)
        above_pro.update(row.problem for row in local_frequency
                         if row.excess_hits is not None and row.excess_hits > 0)
        matches.append(MatchSummary(number=number, match_id=ref.match_id, map_name=mm.map_name,
            rounds=mm.rounds, rounds_played=mm.rounds_played, score=score,
            kills=sum(m.kind == "kill" for m in mm.moments),
            deaths=sum(m.kind == "death" for m in mm.moments),
            assists=sum(m.kind == "assist" for m in mm.moments), aim_stats=mm.aim_stats,
            prominent_problems=[row.problem for row in local_frequency if row.prominent]))
    base_rows = frequency_from_counts(counts, rounds_total, disabled, baseline, cfg)
    frequency = [SummaryFrequencyRow(**row.model_dump(), matches_with_hits=with_hits[row.problem],
        matches_above_pro=above_pro[row.problem],
        by_map={name: group[row.problem] for name, group in sorted(by_map.items())
                if group[row.problem] > 0}) for row in base_rows]
    aim_overall = {}
    for field in _AIM_FIELDS:
        values = [getattr(mm.aim_stats, field) for _, mm, _, _ in games
                  if mm.aim_stats is not None and getattr(mm.aim_stats, field) is not None]
        aim_overall[field] = AimMedian(median=median(values) if values else None, matches=len(values))
    summary = PlayerSummary(steamid=steamid, names=names, matches=matches,
        skipped=list(skipped or []), rounds_played_total=rounds_total,
        frequency=frequency, aim_overall=aim_overall)
    summary._details = details
    return summary


def build_summary(steamid: int, refs: list[DemoRef], cfg: dict,
                  baseline: dict | None, use_cache: bool = True) -> PlayerSummary:
    """逐场解析与检测；失败的比赛记录原因，其余比赛继续。"""
    from cs2coach.features.moments import find_moments

    games = []
    skipped = list(getattr(refs, "skipped", []))
    seen = set()
    for ref in sorted(refs, key=lambda item: item.match_id):
        if ref.match_id in seen:
            continue
        seen.add(ref.match_id)
        print(f"正在处理 {ref.match_id} …", flush=True)
        try:
            def analyse():
                demo = resolve_demo(ref.path, member=ref.member)
                match = parse_match(demo, cfg)
                player = match.require_player(steamid, cfg["parsing"]["min_target_coverage"])
                mm = find_moments(match, player.steamid, cfg)
                return mm, detect_problems(mm, baseline, cfg), match_score(match, steamid)

            # 同一份 demo、配置和代码不变时直接读缓存（汇总 21 场从约 5 分钟降到十几秒）；
            # 视野计算失败（如地图网格下载不完整）的结果不写入，下次重新计算
            key = cache_key(Path(ref.path), ref.member, steamid, cfg)
            mm, report, score = cached(key, analyse, enabled=use_cache,
                                       store=lambda value: value[0].visibility_unavailable is None)
        except Exception as exc:  # 单场坏数据不能中断其余比赛
            skipped.append(SkippedDemo(path=ref.path, member=ref.member,
                                       match_id=ref.match_id, reason=str(exc)))
            print(f"跳过 {ref.match_id}：{exc}", flush=True)
            continue
        games.append((ref, mm, report, score))
        print(f"完成 {ref.match_id}：{mm.map_name}，在场 {mm.rounds_played} 回合", flush=True)
    return aggregate_matches(steamid, games, baseline, cfg, skipped)
