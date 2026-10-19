"""051：补枪机会分母、漏补回归和报告页共用口径。"""

import pandas as pd
import pytest

from cs2coach.config import ConfigError, load_config
from cs2coach.features.lobby import lobby_line
from cs2coach.features.models import LobbyLine, MatchMoments, MissedTrade, TradeChance
from cs2coach.features.moments import _Params, _missed_trades, find_moments, find_trade_chances
from cs2coach.features.problems import FrequencyRow, ProblemReport, detect_problems
from cs2coach.features.radar import minimum_samples, percentile, player_metrics, sample_count
from cs2coach.features.trades import trade_chance_skip
from cs2coach.features.visibility import Sight
from cs2coach.parsing.match import build_bomb, build_damages, build_kills, build_shots
from cs2coach.report.page_check import check_page
from cs2coach.report.page_data import _strength_moments, build_match_page
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, B, C, D, AlwaysVisible, make_match, make_round, make_ticks
from tests.test_moments import death, hurt, scenario
from tests.test_radar import line, tier


def chance(**changes):
    data = dict(round=1, tick=100, teammate=B, killer=C, distance=20,
                killer_traded_by_other=False, traded_by_me=False, killer_in_view_s=1.0,
                killer_first_in_view_s=0.0, alive_own=2, alive_enemy=1)
    return TradeChance(**(data | changes))


def trade_match(attacker=A, delay=12):
    # D 为另一位存活队友，避免只剩本人；保留击杀后的 C 状态，验证视线明确截断。
    overrides = {(C, t): {"X": 1000.0, "spotted_by_mask": 1} for t in range(1, 601)}
    overrides.update({(D, t): {"team_num": 2, "X": 2000.0} for t in range(1, 601)})
    overrides.update({(B, t): {"is_alive": False} for t in range(100, 601)})
    match = make_match(make_ticks(last=600, overrides=overrides),
                       rounds=[make_round(end=550, last=600)])
    match.kills = build_kills(pd.DataFrame([death(100, C, B), death(100 + delay, attacker, C)]), match)
    match.shots = build_shots(pd.DataFrame(), pd.DataFrame(), match)
    match.damages = build_damages(pd.DataFrame([hurt(100, C, B, 100, 0)]), match)
    match.bomb = build_bomb({}, match)
    return match


@pytest.mark.parametrize("delay,traded", [(12, True), (320, True), (321, False)])
def test_self_trade_window_and_visibility_cutoff(delay, traded):
    cfg = load_config()
    match = trade_match(delay=delay)
    params = _Params(match, cfg)
    params.sight = Sight(match, cfg, checker=AlwaysVisible())
    record, = find_trade_chances(match, params, A)
    assert record.traded_by_me is traded
    assert record.my_trade_tick == (100 + delay if traded else None)
    assert record.killer_traded_by_other is False
    assert record.killer_first_in_view_s == 0
    assert record.killer_in_view_s == round((min(delay, 320) + 1) / 64, 3)
    assert bool(_missed_trades(find_trade_chances(match, params, A))) is not traded
    mm = find_moments(match, A, cfg, sight=params.sight)
    assert mm.trade_chances == [record]
    assert mm.missed_trades == _missed_trades(find_trade_chances(match, params, A))


def test_other_teammate_trade():
    cfg = load_config()
    match = trade_match(attacker=D)
    record, = find_trade_chances(match, _Params(match, cfg), A)
    assert record.killer_traded_by_other and not record.traded_by_me
    assert record.my_trade_tick is None
    assert trade_chance_skip(record, cfg) == "killer_traded_by_other"
    cfg["problems"]["missed_trade_exclude_traded_by_other"] = False
    assert trade_chance_skip(record, cfg) == "missing"


@pytest.mark.parametrize("traded", [False, True])
@pytest.mark.parametrize("changes,reason", [
    ({"killer_traded_by_other": True, "alive_own": 1}, "killer_traded_by_other"),
    ({"alive_own": 1, "killer_in_view_s": None}, "last_alive"),
    ({"killer_in_view_s": None, "reloading_at_death": True}, "missing"),
    ({"killer_first_in_view_s": None}, "killer_first_in_view_s"),
    ({"killer_first_in_view_s": 4.01, "reloading_at_death": True}, "killer_first_in_view_s"),
    ({"reloading_at_death": True, "other_fight_s": 0.1}, "reloading_at_death"),
    ({"other_fight_s": -0.2}, "other_fight_s"),
    ({"alive_own": None, "killer_first_in_view_s": 2.0}, None),
])
def test_shared_conditions_and_order(traded, changes, reason):
    assert trade_chance_skip(chance(traded_by_me=traded, **changes), load_config()) == reason


def test_short_visibility_only_excludes_missed_trade():
    cfg = load_config()
    assert trade_chance_skip(chance(traded_by_me=True, killer_in_view_s=0.2), cfg) is None
    assert trade_chance_skip(chance(killer_in_view_s=0.2), cfg) == "killer_in_view_s"


@pytest.mark.parametrize("disabled", [False, True])
def test_detection_consistency_and_original_skip_counts(disabled):
    cfg = load_config()
    cfg["problems"]["disabled"] = ["missed_trade"] if disabled else []
    changes = [{}, {"killer_traded_by_other": True}, {"alive_own": 1},
               {"killer_in_view_s": None}, {"killer_in_view_s": 0.2},
               {"killer_first_in_view_s": 4.1}, {"reloading_at_death": True},
               {"other_fight_s": 0.1}, {"traded_by_me": True, "my_trade_tick": 112}]
    chances = [chance(tick=100 + i, **data) for i, data in enumerate(changes)]
    missed = [MissedTrade.model_validate(c.model_dump()) for c in chances if not c.traded_by_me]
    report = detect_problems(make_mm(trade_chances=chances, missed_trades=missed), None, cfg)
    assert [h.evidence["tick"] for h in report.hits if h.problem == "missed_trade"] == (
        [] if disabled else [c.tick for c in chances if not c.traded_by_me and trade_chance_skip(c, cfg) is None])
    expected = {f"missed_trade/{key}": 1 for key in (
        "last_alive", "killer_in_view_s", "killer_first_in_view_s", "reloading_at_death", "other_fight_s")}
    if not disabled:
        expected.update({"missed_trade": 1, "missed_trade/killer_traded_by_other": 1})
    assert report.skipped == expected
    if not disabled:
        assert report.hits[0].evidence == {"weapon": None, **missed[0].model_dump()}


@pytest.mark.parametrize("visible", [False, True])
def test_legacy_missed_records_unchanged(visible):
    # 漏补记录（原 find_missed_trades，2026-10-19 删除）在这一合成场景的完整输出，含每个默认字段。
    cfg = load_config()
    overrides = {(A, t): {"X": 3.5, "yaw": 90.0} for t in range(150, 200)} if visible else None
    match = scenario(extra_ticks=overrides)
    params = _Params(match, cfg)
    params.sight = Sight(match, cfg, checker=AlwaysVisible())
    expected = dict(round=1, tick=150, teammate=B, killer=D, distance=3.5,
                    killer_traded_by_other=False, killer_in_view_s=0.781 if visible else 0.0,
                    killer_first_in_view_s=0.0 if visible else None, reloading_at_death=False,
                    other_fight_s=None, side="T", alive_own=1, alive_enemy=1,
                    round_time_s=2.19, bomb_planted=False)
    assert [r.model_dump() for r in _missed_trades(find_trade_chances(match, params, A))] == [expected]
    assert [r.model_dump() for r in find_moments(match, A, cfg, sight=params.sight).missed_trades] == [expected]


def test_lobby_denominators_and_radar_boundaries():
    cfg = load_config()
    chances = [chance(tick=100 + i, traded_by_me=i < 3, killer_in_view_s=0.2 if i < 3 else 1.0)
               for i in range(5)]
    chances += [chance(reloading_at_death=True), chance(alive_own=1), chance(killer_traded_by_other=True)]
    result = lobby_line(scenario(), A, cfg, make_mm(trade_chances=chances))
    assert (result.trade_chances, result.missed_trade_chances) == (5, 2)
    assert player_metrics(result, cfg)["missed_trade_pct"] == 40
    assert sample_count(result, "missed_trade_pct") == 5
    assert minimum_samples("missed_trade_pct", cfg) == 5
    for n in (0, 4):
        assert player_metrics(result.model_copy(update={"trade_chances": n}), cfg)["missed_trade_pct"] is None
    reference = tier(missed_trade_pct=[20, 40, 60])
    assert percentile("missed_trade_pct", 20, reference, cfg) > percentile("missed_trade_pct", 60, reference, cfg)


def test_old_cache_defaults():
    assert MatchMoments.model_validate(make_mm().model_dump(exclude={"trade_chances"})).trade_chances == []
    old = LobbyLine.model_validate(line().model_dump(exclude={"trade_chances", "missed_trade_chances"}))
    assert old.trade_chances == old.missed_trade_chances == 0


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_minimum_chances_config_validation(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[radar.min_samples]\ntrade_chances = {str(value).lower()}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="radar.min_samples.trade_chances"):
        load_config(path)


def test_strength_examples_match_eligible_self_trades_and_problem_axis():
    # 补枪已移出雷达（2026-10-18）；这里加回补枪轴，检验漏补率作为优点时的例子与维度
    cfg = load_config()
    cfg["radar"]["axes"].append({"id": "trade", "name": "补枪", "metrics": ["missed_trade_pct"]})
    early = make_moment("kill", tick=112, opponent=C, jump_tick=92)
    late = make_moment("kill", tick=212, opponent=D, jump_tick=192)
    wrong_killer = make_moment("kill", tick=112, opponent=D)
    wrong_tick = make_moment("kill", tick=113, opponent=C)
    wrong_round = make_moment("kill", round=2, tick=112, opponent=C)
    death_moment = make_moment("death", tick=112, opponent=C)
    excluded = make_moment("kill", tick=312, opponent=C)
    chances = [chance(traded_by_me=True, my_trade_tick=112, killer_in_view_s=0.2),
               chance(traded_by_me=True, my_trade_tick=212, killer=D),
               chance(traded_by_me=True, my_trade_tick=312, reloading_at_death=True),
               chance(my_trade_tick=113)]
    # 同一击杀可能补回多位队友，例子只列一次。
    chances.append(chances[0].model_copy(update={"teammate": D}))
    mm = make_mm([late, wrong_killer, excluded, early, wrong_tick, wrong_round, death_moment],
                 trade_chances=chances, lobby=[line(trade_chances=5, missed_trade_chances=0)])
    assert _strength_moments(mm, "missed_trade_pct", 5, cfg) == [early, late]
    assert _strength_moments(mm, "missed_trade_pct", 1, cfg) == [early]
    data = tier(missed_trade_pct=[20, 40, 60])
    data["problem_rates"] = {"missed_trade": {"hits": 1, "rounds": 100}}
    tiers = {data["id"]: data}
    report = ProblemReport(hits=[], disabled=[], skipped={}, baseline_source=None)
    page = build_match_page(mm, report, [], cfg, tiers)
    row, = page.tiers[0].strengths
    assert row.id == "missed_trade_pct" and row.axis == "trade" and row.name == "漏补率"
    assert row.samples == 5 and row.unit == "%" and row.you == 0
    assert [e.tick for e in row.examples] == [92, 192]
    assert page.axes[-1] == {"id": "trade", "name": "补枪"}
    assert check_page(page, cfg) == []
    frequency = [FrequencyRow(problem="missed_trade", label="漏补枪", hits=3,
                              per_10_rounds=30, pro_per_10_rounds=None, ratio=None, prominent=False)]
    page = build_match_page(mm, report, frequency, cfg, tiers)
    assert page.tiers[0].problems[0].axis == "trade"
    assert not page.tiers[0].strengths
