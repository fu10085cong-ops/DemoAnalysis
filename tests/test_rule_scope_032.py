"""032 规则口径修正（Claude 预写，实现方不得修改）。每项改动一对样本，只改这一个条件。"""

import copy

import pandas as pd
import pytest

from cs2coach.config import load_config
from cs2coach.features.problems import detect_problems
from tests.helpers.moments import make_mm, make_moment, make_shot
from tests.helpers.synthetic import A
from tests.test_moments import CFG, find_moments, moment, scenario


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def baseline():
    stats = {"time_to_damage_ms": {"p75": 500.0}, "seen_to_first_shot_ms": {"p75": 300.0},
             "crosshair_placement_deg": {"p75": 10.0}, "crosshair_initial_offset_m": {"p75": 2.0},
             "distance_m": {"p90": 30.0}}
    return {"source": "手工职业样本", "weapons": {w: copy.deepcopy(stats) for w in
            ("ak47", "m4a1", "m4a1_silencer", "awp", "deagle", "glock")}, "spray": {}}


def problems(m, baseline, cfg):
    return {h.problem for h in detect_problems(make_mm([m]), baseline, cfg).hits}


# 1. 出枪慢：只比较"看见 → 开枪"
def test_slow_reaction_uses_seen_to_first_shot(cfg, baseline):
    slow = make_moment(engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .35, "first_shot_to_damage_s": .25})
    assert "slow_reaction" in problems(slow, baseline, cfg)
    # TTD 超过阈值，但慢在"开枪 → 命中"：不判
    missed_shots = make_moment(engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .2, "first_shot_to_damage_s": .4})
    assert "slow_reaction" not in problems(missed_shots, baseline, cfg)


def test_slow_reaction_evidence_has_all_segments(cfg, baseline):
    m = make_moment(engagement={"time_to_damage_s": .6, "seen_to_first_shot_s": .35, "first_shot_to_damage_s": .25})
    hit = next(h for h in detect_problems(make_mm([m]), baseline, cfg).hits if h.problem == "slow_reaction")
    assert hit.evidence["seen_to_first_shot_ms"] == pytest.approx(350)
    assert hit.evidence["first_shot_to_damage_ms"] == pytest.approx(250)
    assert hit.evidence["ttd_ms"] == pytest.approx(600)
    assert hit.thresholds["seen_to_first_shot_ms"] == 300.0 and hit.thresholds["source"] == "pro_p75"


# 2. 拉出撞架点：默认停用，两条加重版照常
def test_peeked_into_held_angle_disabled_but_aggravated_variants_kept(cfg, baseline):
    assert "peeked_into_held_angle" in cfg["problems"]["disabled"]
    dry = make_moment(engagement={"contact_type": "peek"}, death={"unused_grenades": ["flashbang"]})
    over = make_moment(tags=["man_advantage"], engagement={"contact_type": "peek"}, context={"round_time_s": 30})
    found = problems(dry, baseline, cfg) | problems(over, baseline, cfg)
    assert "peeked_into_held_angle" not in found
    assert {"dry_peek_with_utility", "overpeek_in_advantage"} <= found


# 3. 有道具拉出：只算闪光
def test_dry_peek_counts_only_flashbang(cfg, baseline):
    smoke = make_moment(engagement={"contact_type": "peek"}, death={"unused_grenades": ["smokegrenade"]})
    flash = make_moment(engagement={"contact_type": "peek"}, death={"unused_grenades": ["flashbang"]})
    assert "dry_peek_with_utility" not in problems(smoke, baseline, cfg)
    assert "dry_peek_with_utility" in problems(flash, baseline, cfg)
    assert cfg["problems"]["dry_peek_grenades"] == ["flashbang"]


# 4. 优势冒进：CT 已下包时不判
def test_overpeek_skips_ct_after_plant(cfg, baseline):
    base = dict(tags=["man_advantage"], engagement={"contact_type": "peek"})
    ct = make_moment(**base, context={"side": "CT", "bomb_planted": True})
    t = make_moment(**base, context={"side": "T", "bomb_planted": True})
    ct_before = make_moment(**base, context={"side": "CT", "bomb_planted": False})
    assert "overpeek_in_advantage" not in problems(ct, baseline, cfg)
    assert "overpeek_in_advantage" in problems(t, baseline, cfg)
    assert "overpeek_in_advantage" in problems(ct_before, baseline, cfg)


# 5. 下包带火：下包后至少存活 10 秒
@pytest.mark.parametrize("seconds,expected", [(9.9, False), (10.0, True), (None, False)])
def test_post_plant_requires_time_alive(cfg, baseline, seconds, expected):
    m = make_moment(context={"bomb_planted": True, "seconds_since_plant": seconds},
                    death={"unused_grenades": ["molotov"]})
    assert ("post_plant_utility_unused" in problems(m, baseline, cfg)) is expected


def test_post_plant_evidence_has_seconds(cfg, baseline):
    m = make_moment(context={"bomb_planted": True, "seconds_since_plant": 12.5}, death={"unused_grenades": ["molotov"]})
    hit = next(h for h in detect_problems(make_mm([m]), baseline, cfg).hits if h.problem == "post_plant_utility_unused")
    assert hit.evidence["seconds_since_plant"] == 12.5


def test_seconds_since_plant_from_bomb_events():
    planted = scenario()
    planted.bomb = pd.DataFrame([{"tick": 136, "round": 1, "event": "planted"}])
    death = moment(find_moments(planted, A, CFG), "R1-death")
    assert death.context.bomb_planted is True
    assert death.context.seconds_since_plant == pytest.approx((200 - 136) / 64, abs=0.02)
    later = scenario()
    later.bomb = pd.DataFrame([{"tick": 260, "round": 1, "event": "planted"}])
    assert moment(find_moments(later, A, CFG), "R1-death").context.seconds_since_plant is None


# 6. Glock 远距离：只算被步枪或狙打死
def test_glock_long_range_requires_rifle_or_sniper_opponent(cfg, baseline):
    def glock_death(killer_weapon):
        m = make_moment(weapon="glock", engagement={"shots": [make_shot(weapon="glock")]}, context={"distance": 31})
        return m.model_copy(update={"weapon": killer_weapon})
    assert "glock_long_range" not in problems(glock_death("glock"), baseline, cfg)
    assert "glock_long_range" in problems(glock_death("ak47"), baseline, cfg)
    assert "glock_long_range" in problems(glock_death("awp"), baseline, cfg)
    assert "awp" in cfg["problems"]["glock_long_range_opponent_weapons"]
