"""039 新问题"看见对手却没开枪"（Claude 预写，实现方不得修改）。"""

import copy

import pytest

from cs2coach.config import load_config
from cs2coach.features.problems import PROBLEMS, detect_problems
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A
from tests.test_moments import CFG, death, find_moments, fire, hurt, moment, scenario


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def baseline():
    stats = {"time_to_damage_ms": {"p75": 500.0}, "seen_to_first_shot_ms": {"p75": 400.0},
             "crosshair_placement_deg": {"p75": 10.0}, "crosshair_initial_offset_m": {"p75": 2.0},
             "distance_m": {"p90": 30.0}}
    return {"source": "手工职业样本", "weapons": {"ak47": copy.deepcopy(stats)}, "spray": {}}


def _death(seen=0.7, recent=0, **changes):
    eng = {"shots": [], "shots_fired": 0, "player_first_seen_tick": 55, "seen_to_death_s": seen}
    eng |= changes.pop("engagement", {})
    return make_moment(engagement=eng, death={"unused_grenades": [], "shots_last_2s": recent} | changes.pop("death", {}),
                       **changes)


def found(m, baseline, cfg):
    return {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


def test_registered_and_enabled(cfg):
    assert PROBLEMS["saw_but_no_shot"].label == "看见对手却没开枪"
    assert "saw_but_no_shot" not in cfg["problems"]["disabled"]
    assert cfg["problems"]["no_shot_min_seen_seconds"] == 0.6


def test_threshold_and_conditions(cfg, baseline):
    assert "saw_but_no_shot" in found(_death(0.6), baseline, cfg)
    assert "saw_but_no_shot" not in found(_death(0.59), baseline, cfg)
    assert "saw_but_no_shot" not in found(_death(0.8, recent=1), baseline, cfg)      # 在朝别人开枪
    assert "saw_but_no_shot" not in found(_death(0.8, engagement={"visibility_note": "smoke"}), baseline, cfg)
    assert "saw_but_no_shot" not in found(_death(0.8, engagement={"seen_to_death_s": None}), baseline, cfg)


def test_exclusions(cfg, baseline):
    assert "saw_but_no_shot" not in found(_death(0.8, through_smoke=True), baseline, cfg)
    assert "saw_but_no_shot" not in found(_death(0.8, wallbang=True), baseline, cfg)
    blind = dict(blinded=True, blind_remaining_s=1.0)
    assert "saw_but_no_shot" not in found(_death(0.8, death=blind), baseline, cfg)
    assert "saw_but_no_shot" not in found(_death(0.8, weapon="molotov"), baseline, cfg)


def test_evidence(cfg, baseline):
    hit = next(h for h in detect_problems(make_mm([_death(0.75)]), baseline, cfg).hits if h.problem == "saw_but_no_shot")
    assert hit.evidence["seen_to_death_s"] == 0.75
    assert hit.thresholds["min_seen_seconds"] == 0.6
    from cs2coach.report.evidence import evidence_sentence
    assert "0.75" in evidence_sentence(hit) or "750" in evidence_sentence(hit)


def test_features_seen_to_death_and_recent_shots():
    # scenario：A 在 90/95/100 tick 开枪击杀 C，200 tick 被 D 击杀；64 tick 下 2 秒 = 128 tick，三枪都在窗口内
    d = moment(find_moments(scenario(), A, CFG), "R1-death")
    assert d.death.shots_last_2s == 3
    # A 不开枪、160 tick 起转向 D（避开 150 tick 上一次击杀的回看下限）（D 在 +Y 方向）：看见到阵亡约 0.5–1.0 秒，期间没开枪
    from tests.helpers.synthetic import C, D
    quiet = scenario(fires=[fire(85, C), fire(150, D), fire(198, D), fire(200, D)],
                     extra_ticks={(A, t): {"X": 3.5, "yaw": 90.0} for t in range(160, 201)})
    d = moment(find_moments(quiet, A, CFG), "R1-death")
    assert d.death.shots_last_2s == 0
    assert d.engagement.seen_to_death_s is not None and 0.5 <= d.engagement.seen_to_death_s <= 1.0


def test_holding_utility_is_not_hesitation(cfg, baseline):
    """040：死时手上拿的是道具（没切枪），不算"看见却没开枪"。"""
    smoke = _death(0.8, weapon="smokegrenade", context={"own_weapon": "Smoke Grenade"})
    assert "saw_but_no_shot" not in found(smoke, baseline, cfg)
