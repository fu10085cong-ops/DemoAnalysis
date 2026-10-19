"""R03 全量首杀研究：位置角色、被补判定、按比赛重抽样与汇总。"""

from __future__ import annotations

import pandas as pd

from scripts import research_firstkill as fk
from tests.test_research_fight import A_SITE, CFG, CT, T, fight_match


def test_role_of_prefers_mid_for_connector():
    assert fk.role_of({"zones": {"A", "Mid"}, "layer": 1}) == "中路 CT 侧"
    assert fk.role_of({"zones": {"B"}, "layer": 1}) == "B 主防"
    assert fk.role_of({"zones": set(), "layer": 0}) == "后方"
    assert fk.role_of({"zones": set(), "layer": 2}) == "接触区"


def _row():
    return dict(demo_id="example.dem", match_id="example", round_id=1, player_id=str(CT[0]), player_name="p",
                start_tick=100, round_time_s=1.4, start_place="BombsiteA", advantage_held=True, ct_won=True)


def test_fight_row_traded_within_five_seconds():
    traded = fight_match({CT[0]: A_SITE}, extra_kills=[(100 + 4 * 64, T[1], CT[0])])
    row = fk.fight_row(traded, _row(), None, CFG)
    assert row["role"] == "A 主防" and row["weapon"] == "ak47" and not row["sniper"]
    assert row["behaviour"] == "原位再架" and row["traded"] and row["death_after_fight_s"] == 4.0
    late = fight_match({CT[0]: A_SITE}, extra_kills=[(100 + 6 * 64, T[1], CT[0])])
    assert fk.fight_row(late, _row(), None, CFG)["traded"] is False


def test_cluster_ci_and_summary():
    rows = []
    for m in range(6):
        for b, traded in (("原位再架", True), ("原位再架", m % 2 == 0), ("换位", False), ("换位", m == 0)):
            rows.append(dict(match_id=f"m{m}", behaviour=b, traded=traded, role="A 主防", sniper=False,
                             fight_end_s=0.5, death_after_fight_s=1.0 if traded else None,
                             round_over_before_horizon=False, advantage_held=not traded, ct_won=True))
    frame = pd.DataFrame(rows)
    lo, hi = fk.cluster_ci(frame[frame["behaviour"] == "原位再架"], "traded", 500, 1)
    assert 0.5 <= lo <= 0.75 <= hi <= 1.0
    text = fk.summarize(frame, CFG)
    assert "| 原位再架 | 12 | 6 | 75% |" in text and "| 换位 | 12 | 6 | 8% |" in text
    assert "描述性结果" in text


def test_review_pack_samples_each_behaviour_blind(tmp_path):
    import tomllib

    rows = []
    for i in range(40):
        rows.append(dict(demo_id=f"m{i % 10}.dem", match_id=f"m{i % 10}", round_id=i, player_id=str(1000 + i),
                         start_tick=100 * i, behaviour=fk.BEHAVIOURS[i % len(fk.BEHAVIOURS)], fight_end_s=0.5))
    out = tmp_path / "data" / "research" / "firstkill" / "x"
    out.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(out / "fights.csv", index=False)
    pack = fk.review_pack("x", "kr", 2, 1, root=tmp_path)
    items = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))["items"]
    assert len(items) == 2 * len(fk.BEHAVIOURS) and len({i["match_id"] for i in items}) == len(items)
    assert items[0]["focus"] == [0.5, 3.5] and "behaviour" not in items[0]


def test_review_pack_excludes_reviewed_rounds(tmp_path):
    import tomllib

    rows = [dict(demo_id="m.dem", match_id="m", round_id=i, player_id=str(i), start_tick=i,
                 behaviour=fk.BEHAVIOURS[i % len(fk.BEHAVIOURS)], fight_end_s=0.5) for i in range(12)]
    out = tmp_path / "data" / "research" / "firstkill" / "x"
    out.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(out / "fights.csv", index=False)
    first = fk.review_pack("x", "a", 2, 1, root=tmp_path)
    second = fk.review_pack("x", "b", 2, 2, root=tmp_path, exclude=["a"])
    rounds = lambda pack: {i["round_id"] for i in tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))["items"]}  # noqa: E731
    assert not rounds(first) & rounds(second)
