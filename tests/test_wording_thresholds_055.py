"""055 页面口径与可信度门槛（Claude 预写，实现方不得修改）。"""

from pathlib import Path

from cs2coach.config import load_config
from cs2coach.features.ladder import LadderPoint, ladder_points, ladder_position
from cs2coach.features.panel import PANEL_METRICS, PanelCounts
from cs2coach.features.problems import ProblemHit, ProblemReport
from cs2coach.report.page_data import build_match_page, build_summary_page
from cs2coach.summary.aggregate import aggregate_matches
from cs2coach.summary.discover import DemoRef
from tests.helpers.moments import make_mm
from tests.helpers.synthetic import A
from tests.test_radar import line
from tests.test_tier_ladder_053 import _aim, _tier


def _points():
    return [LadderPoint(level=i, tier_id=str(i), name=str(i), standard=v, q25=v - 5, q75=v + 5)
            for i, v in enumerate((10, 20, 30), 1)]


def _tiers(a_players=8):
    tiers = {t["id"]: t for t in (_tier("5e_b", 1, 500, 1.4), _tier("5e_a", 2, 400, 1.2), _tier("5e_s", 3, 300, 1.0))}
    tiers["5e_a"]["per_player"]["n_players"] = a_players
    pro = _tier("5e_s", 3, 200, 0.8)
    pro.update(id="pro", name="职业", level=4)
    tiers["pro"] = pro
    return tiers


def _summary(cfg, hits_per_game=0, panel=None):
    games = []
    for index, ttd in enumerate((200, 250, 300)):
        mm = make_mm(player=A, player_name="虚构玩家", rounds=20,
                     lobby=[line(steamid=A, name="虚构玩家", aim=_aim(ttd))],
                     panel=panel or PanelCounts(rounds=20, kills=10))
        hits = [ProblemHit(problem="poor_crosshair_placement", target=f"R{i + 1}-kill1", weapon="ak47", evidence={}, thresholds={})
                for i in range(hits_per_game)]
        ref = DemoRef(path=Path("synthetic.dem"), member=None, match_id=f"s{index}",
                      map_name="de_test", player_name="虚构玩家")
        games.append((ref, mm, ProblemReport(hits=hits, disabled=[], skipped={}, baseline_source=None), (10, 10)))
    return aggregate_matches(A, games, None, cfg), games


# ---------- 1. "达到某档" = 不差于该档标准，与阶梯上的位置一致 ----------

def test_reached_means_at_least_the_tier_standard():
    points = _points()
    assert ladder_position(17, points, True).reached.level == 1  # 位置 1.7：只越过第 1 档的刻度
    assert ladder_position(20, points, True).reached.level == 2
    assert ladder_position(9, points, True).reached is None
    for value in (9, 12, 17, 20, 25, 31):
        result = ladder_position(value, points, True)
        assert (result.reached.level if result.reached else 0) == max([0] + [p.level for p in points if value >= p.standard])
    lower = [p.model_copy(update={"standard": 40 - p.standard}) for p in points]  # 30, 20, 10：越低越好
    assert ladder_position(22, lower, False).reached.level == 1
    assert ladder_position(20, lower, False).reached.level == 2


def test_ladder_config_has_min_players_and_no_reach_quantile():
    ladder = load_config()["ladder"]
    assert ladder["min_players"] == 8
    assert "reach_quantile" not in ladder


# ---------- 2. 每档至少 8 名独立玩家才进入阶梯 ----------

def test_tiers_below_min_players_are_left_off_the_ladder():
    tiers = _tiers(a_players=5)
    assert [p.tier_id for p in ladder_points("time_to_damage_ms", tiers, min_players=8)] == ["5e_b", "5e_s", "pro"]
    assert [p.tier_id for p in ladder_points("time_to_damage_ms", tiers, min_players=5)] == ["5e_b", "5e_a", "5e_s", "pro"]


def test_footer_names_tiers_without_enough_players():
    cfg = load_config()
    summary, _ = _summary(cfg)
    page = build_summary_page(summary, cfg, _tiers(a_players=5))
    assert [item["level"] for item in page.ladder_levels] == [1, 3, 4]
    assert "5E A 段" in page.ladder_note and "不足 8 名" in page.ladder_note


# ---------- 3. 阶梯文字 ----------

def test_ladder_text_wording():
    cfg = load_config()
    summary, _ = _summary(cfg)
    tiers = _tiers()
    tiers["5e_s"]["per_player"]["metrics"]["crosshair_offset_m"]["standard"] = 1.5  # S 段比 A 段还差：无法排序
    view = next(v for v in build_summary_page(summary, cfg, tiers).tiers if v.id == "5e_a")
    # 典型单场 250 毫秒：越过 S 段 300 毫秒的刻度，未到职业 200 毫秒
    assert "已达到5E S 段标准（300 毫秒）" in view.ladder["ttd"].text
    assert view.ladder["crosshair"].text == "现有基准无法稳定分级"


# ---------- 4. 同一指标只有一个数：类别面板的枪法四项用典型单场 ----------

def test_panel_aim_metrics_use_the_typical_single_match_value():
    cfg = load_config()
    summary, _ = _summary(cfg)
    page = build_summary_page(summary, cfg, _tiers())
    aim = next(c for c in page.panel["categories"] if c["id"] == "aim")
    ttd = next(m for m in aim["metrics"] if m["id"] == "time_to_damage_ms")
    axis = next(a for a in page.ladder_axes if a["id"] == "ttd")
    assert ttd["you"] == axis["you"] == 250
    view = next(v for v in page.tiers if v.id == "5e_b")
    assert view.panel_values["time_to_damage_ms"] == 500  # 段位一列也用该段人均标准


# ---------- 5. 小样本显示分子 / 分母 ----------

def test_small_sample_rates_show_counts():
    cfg = load_config()
    assert cfg["panel"]["small_sample_below"] == 20
    summary, _ = _summary(cfg, panel=PanelCounts(rounds=20, kills=10, clutch_1v1=3, clutch_1v1_wins=3))
    page = build_summary_page(summary, cfg, _tiers())
    clutch = next(c for c in page.panel["categories"] if c["id"] == "clutch")
    one = next(m for m in clutch["metrics"] if m["id"] == "clutch_1v1_win_pct")
    assert one["count_text"] == "9/9（样本较少）"  # 三场合并
    kpr = next(m for c in page.panel["categories"] for m in c["metrics"] if m["id"] == "kpr")
    assert kpr.get("count_text") is None  # 不是比例类指标


# ---------- 6. 比赛表现与技能指标分开 ----------

def test_match_performance_metrics_are_separated_from_skill():
    performance = {m.id for m in PANEL_METRICS if m.kind == "表现"}
    assert performance == {"kpr", "kpr_won", "adr", "adr_won", "kill_round_pct", "multikill_round_pct",
                           "clutch_win_pct", "clutch_1v1_win_pct", "opening_round_win_pct"}
    cfg = load_config()
    summary, _ = _summary(cfg)
    page = build_summary_page(summary, cfg, _tiers())
    for category in page.panel["categories"]:
        for metric in category["metrics"]:
            if metric["id"] in performance:
                assert "ladder" not in metric


# ---------- 7. 结论始终写明"相对谁" ----------

def test_verdict_names_the_reference_and_adds_pro_when_target_is_clean():
    cfg = load_config()
    summary, games = _summary(cfg, hits_per_game=4)
    tiers = _tiers()
    for ident in ("5e_b", "5e_a", "5e_s"):
        tiers[ident]["problem_rates"] = {"poor_crosshair_placement": {"hits": 30, "rounds": 100}}
    tiers["pro"]["problem_rates"] = {"poor_crosshair_placement": {"hits": 1, "rounds": 100}}
    page = build_summary_page(summary, cfg, tiers)
    a = next(v for v in page.tiers if v.id == "5e_a")
    pro = next(v for v in page.tiers if v.id == "pro")
    assert a.verdict.startswith("相对5E A 段，")
    assert "相对职业，最该练的是准星没提前放好" in a.verdict
    assert pro.verdict.startswith("相对职业，最该练的是准星没提前放好")
    single = build_match_page(games[0][1], games[0][2], [], cfg, tiers)
    assert all(v.verdict.startswith(f"相对{v.name}，") for v in single.tiers)
