"""用真实 demo 做端到端检查。默认跳过；设置环境变量 CS2COACH_TEST_DEMO=<.dem 路径> 后运行。

更完整的逐项对照见 scripts/check_parsing.py。
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from cs2coach.config import load_config

DEMO = os.environ.get("CS2COACH_TEST_DEMO")
pytestmark = pytest.mark.skipif(not DEMO, reason="未设置 CS2COACH_TEST_DEMO")


@pytest.fixture(scope="module")
def match():
    from cs2coach.parsing.match import parse_match

    return parse_match(Path(DEMO), load_config())


def test_rounds_are_contiguous(match):
    assert match.rounds
    assert [r.number for r in match.rounds] == list(range(1, len(match.rounds) + 1))
    for a, b in zip(match.rounds, match.rounds[1:]):
        assert a.last_tick < b.start_tick
        assert a.start_tick <= (a.freeze_end_tick or a.start_tick) <= a.end_tick <= a.last_tick


def test_kills_match_scoreboard(match):
    from demoparser2 import DemoParser

    sb = DemoParser(str(DEMO)).parse_ticks(["kills_total", "deaths_total"], ticks=[match.meta.last_tick])
    sb = sb.assign(steamid=sb["steamid"].astype("int64")).set_index("steamid")
    k = match.kills
    kills = k[k["is_enemy_kill"] & ~k["by_bot_control"]].groupby("attacker").size()
    deaths = k[~k["in_freeze_time"]].groupby("victim").size()
    for sid in match.players:
        assert kills.get(sid, 0) == sb.loc[sid, "kills_total"]
        assert deaths.get(sid, 0) == sb.loc[sid, "deaths_total"]


def test_all_players_have_data(match):
    assert all(p.coverage > 0.95 for p in match.players.values())
    guns = match.shots[match.shots["is_gun"]]
    assert guns["aim_yaw"].notna().mean() > 0.99
    assert pd.api.types.is_integer_dtype(match.ticks["steamid"])
