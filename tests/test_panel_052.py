"""052 类别面板的计数、合并及页面数据。"""

from pathlib import Path

import pandas as pd
from cs2coach.config import load_config
from cs2coach.features.panel import PanelCounts, panel_counts, panel_values
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.parsing.match import build_match
from cs2coach.parsing.models import Team
from cs2coach.report.page_data import build_summary_page
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.discover import DemoRef
from scripts.build_tier_baseline import build_tier_baseline
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, B, C, D, make_match, make_round, make_ticks


def _cfg():
    cfg = load_config()
    cfg["panel"]["min_samples"] = {key: 1 for key in cfg["panel"]["min_samples"]}
    return cfg


def _match(rounds=None, ticks=None):
    match = make_match(ticks=ticks, rounds=rounds)
    match.kills = pd.DataFrame(columns=["round", "tick", "victim", "in_freeze_time", "after_round_end"])
    match.damages = pd.DataFrame(columns=["round", "tick", "attacker", "victim", "attacker_team_num",
                                          "victim_team_num", "weapon", "dmg_effective"])
    match.shots = pd.DataFrame(columns=["round", "steamid", "is_gun", "controlling_bot"])
    match.blinds = pd.DataFrame(columns=["round", "tick", "thrower", "victim", "thrower_ambiguous",
                                         "victim_team_num", "thrower_team_num", "duration"])
    match.grenades = pd.DataFrame(columns=["round", "tick", "thrower", "type"])
    match.economy = pd.DataFrame(columns=["round", "steamid", "grenades"])
    return match


def test_firepower_opening_and_absent_rounds():
    rounds = [make_round(), make_round(2, start=121, freeze_end=130, end=220, last=240, winner=Team.CT)]
    match = _match(rounds, make_ticks(last=240))
    match.damages = pd.DataFrame([
        dict(round=1, tick=30, attacker=A, victim=C, attacker_team_num=2, victim_team_num=3,
             weapon="ak47", dmg_effective=80),
        dict(round=1, tick=35, attacker=A, victim=D, attacker_team_num=2, victim_team_num=3,
             weapon="ak47", dmg_effective=40),
        dict(round=2, tick=145, attacker=B, victim=C, attacker_team_num=2, victim_team_num=3,
             weapon="ak47", dmg_effective=20),
        dict(round=2, tick=150, attacker=A, victim=C, attacker_team_num=2, victim_team_num=3,
             weapon="ak47", dmg_effective=50),
    ])
    mm = make_mm([
        make_moment("kill", round=1, tick=40, tags=["opening_kill"]),
        make_moment("kill", round=1, tick=60),
        make_moment("death", round=2, tick=160, tags=["opening_death"]),
    ], rounds=2)
    counts = panel_counts(match, A, mm, _cfg())
    values = panel_values([counts], _cfg())
    assert (counts.rounds, counts.rounds_won, counts.kills, counts.damage) == (2, 1, 2, 170)
    assert (values["kpr"], values["kpr_won"], values["adr_won"], values["multikill_round_pct"]) == (1, 2, 120, 50)
    assert (counts.opening_attempts, counts.opening_kills, counts.opening_round_wins,
            counts.first_damage_rounds) == (2, 1, 1, 1)
    assert values["opening_win_pct"] == 50 and values["opening_round_win_pct"] == 100
    mm.absent_rounds = [2]
    absent = panel_counts(match, A, mm, _cfg())
    assert (absent.rounds, absent.kills, absent.damage, absent.opening_attempts) == (1, 2, 120, 1)


def test_flash_utility_and_sniper():
    match = _match()
    match.damages = pd.DataFrame([dict(round=1, tick=25, attacker=B, victim=C,
                                       attacker_team_num=2, victim_team_num=3,
                                       weapon="ak47", dmg_effective=20)])
    match.grenades = pd.DataFrame([
        dict(round=1, tick=30, thrower=A, type="flashbang"),
        dict(round=1, tick=50, thrower=A, type="flashbang"),
        dict(round=1, tick=60, thrower=A, type="hegrenade"),
    ])
    match.blinds = pd.DataFrame([
        dict(round=1, tick=30, thrower=A, victim=C, thrower_ambiguous=False,
             victim_team_num=3, thrower_team_num=2, duration=1.5),
        dict(round=1, tick=50, thrower=A, victim=B, thrower_ambiguous=False,
             victim_team_num=2, thrower_team_num=2, duration=2.0),
    ])
    match.economy = pd.DataFrame([dict(round=1, steamid=A, grenades=["flashbang", "flashbang"])])
    mm = make_mm([make_moment("kill", weapon="awp", tick=70),
                  make_moment("kill", weapon="ssg08", tick=80)])
    counts = panel_counts(match, A, mm, _cfg())
    values = panel_values([counts], _cfg())
    assert counts.effective_flashes == 1 and counts.blind_seconds == 1.5
    assert values["effective_flash_pct"] == 50 and values["blind_time_per_round"] == 1.5
    assert counts.grenades_used == counts.grenades_held == 2
    assert counts.sniper_kills == 2 and counts.sniper_multikill_rounds == 1
    assert counts.gun_hits == 0
    assert counts.sniper_kills < _cfg()["panel"]["sniper_min_kills"]


def test_clutch_entry_is_fixed_at_one_vs_two():
    match = _match()
    extra = [A + n for n in (10, 11, 12)]
    ticks = [match.ticks]
    for sid in extra:
        rows = match.player_ticks(A).copy()
        rows["steamid"] = sid
        ticks.append(rows)
    match = build_match(Path("clutch.dem"), match.meta,
                        {**{sid: str(sid) for sid in (A, B, C, D)}, **{sid: str(sid) for sid in extra}},
                        match.rounds, pd.concat(ticks).sort_values(["steamid", "tick"]).reset_index(drop=True), .95)
    match.kills = pd.DataFrame([dict(round=1, tick=tick, victim=victim, in_freeze_time=False,
                                     after_round_end=False) for tick, victim in
                                    zip((20, 30, 40, 50, 60), (B, *extra, C))])
    match.damages = pd.DataFrame(columns=["round", "tick", "attacker", "victim", "attacker_team_num",
                                          "victim_team_num", "weapon", "dmg_effective"])
    match.shots = pd.DataFrame(columns=["round", "steamid", "is_gun", "controlling_bot"])
    mine = panel_counts(match, A, make_mm(), _cfg())
    enemy = panel_counts(match, D, make_mm(), _cfg())
    assert (mine.clutches, mine.clutch_wins, mine.clutch_1v1) == (1, 1, 0)
    assert (enemy.clutches, enemy.clutch_1v1, enemy.clutch_1v1_wins) == (1, 1, 0)


def test_multi_match_merges_numerators_and_samples():
    cfg = _cfg()
    values = panel_values([PanelCounts(rounds=1, kills=1, reaction_ms=[100]),
                           PanelCounts(rounds=9, kills=3, reaction_ms=[300, 500])], cfg)
    assert values["kpr"] == 0.4
    assert values["reaction_ms"] == 300


def test_radar_four_metrics_use_same_raw_samples():
    cfg = _cfg()
    counts = PanelCounts(time_to_damage_ms=[100, 300], crosshair_offset_m=[.2, .4],
                         counter_strafing_stopped=3, counter_strafing_shots=4,
                         spray_hits=2, spray_shots=5)
    values = panel_values([counts], cfg)
    assert [values[key] for key in ("time_to_damage_ms", "crosshair_offset_m",
                                    "counter_strafing_pct", "spray_accuracy_pct")] == [200, .3, 75, 40]


def test_baseline_and_summary_page_include_panel_without_rebuilding_files(monkeypatch):
    cfg = _cfg()
    mm = make_mm(rounds=20, panel=PanelCounts(rounds=20, kills=12, sniper_kills=2))
    report = ProblemReport(hits=[ProblemHit(problem="slow_reaction", target="R1-death", weapon="ak47",
                                            evidence={}, thresholds={})], disabled=[], skipped={}, baseline_source=None)
    monkeypatch.setattr("scripts.build_tier_baseline.problem_rates", lambda mms, baseline, config: {})
    tier = build_tier_baseline({"id": "5e_a", "name": "5E A 段"}, [mm], 1, {}, cfg)
    assert tier["panel"]["n"] == 1 and tier["panel"]["counts"]["kills"] == 12
    ref = DemoRef(path=Path("synthetic.dem"), member=None, match_id="synthetic", map_name="de_test",
                  player_name="（已匿名）")
    summary = aggregate_matches(A, [(ref, mm, report, (13, 7))], None, cfg)
    page = build_summary_page(summary, cfg, {"5e_a": tier})
    assert summary.panel["counts"]["kills"] == 12
    assert page.panel["categories"][0]["metrics"][0]["you"] == 0.6
    assert len(page.panel["categories"]) == 6  # 狙杀不足，整类隐藏。
    assert page.tiers[0].panel_values["kpr"] == 0.6
    old_tier = {key: value for key, value in tier.items() if key != "panel"}
    old_page = build_summary_page(summary, cfg, {"5e_a": old_tier})
    assert old_page.tiers[0].panel_values == {}


def test_reaction_filters_by_own_weapon_not_killer_weapon():
    # 审查时补：死亡瞬间的 weapon 是对手的武器，白名单要按自己这次交火的武器（engagement_weapon）筛
    rounds = [make_round()]
    match = _match(rounds, make_ticks())
    my_mp9 = make_moment("death", weapon="ak47", tick=60, engagement={"seen_to_first_shot_s": .3})
    my_mp9.engagement.shots = [my_mp9.engagement.shots[0].model_copy(update={"weapon": "mp9"})]
    my_ak = make_moment("death", weapon="mp9", tick=80, engagement={"seen_to_first_shot_s": .4})
    my_ak.engagement.shots = [my_ak.engagement.shots[0].model_copy(update={"weapon": "ak47"})]
    counts = panel_counts(match, A, make_mm([my_mp9, my_ak]), _cfg())
    assert counts.reaction_ms == [400]
