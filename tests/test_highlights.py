"""构造高光正例、优先级与排除条件。"""

import pytest

from cs2coach.features.highlights import find_highlights
from cs2coach.parsing.models import Team
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import B, C, make_round


def kills(count, **changes):
    return [make_moment("kill", id=f"R1-kill{i}", tick=100+i, jump_tick=80+i, **changes) for i in range(count)]


@pytest.mark.parametrize("kind,moments", [
    ("multikill", kills(3)),
    ("clutch", kills(2, tags=["clutch_1v3"])),
    ("opening_win", kills(1, tags=["opening_kill"])),
    ("flash_assist", [make_moment("assist", tags=["flash_assist"], assist={"killer": B})]),
])
def test_four_highlights(kind, moments):
    mm = make_mm(moments, player_names={B: "队友甲", C: "对手甲"})
    highlight, = find_highlights(mm, [make_round()])
    assert highlight.kind == kind and highlight.time == "0:10"
    assert highlight.tick == moments[0].jump_tick
    assert highlight.title.startswith("R1 ")
    if kind == "flash_assist":
        assert "队友甲" in highlight.what
    if kind == "multikill":
        assert highlight.title == "R1 三杀" and "AK-47" in highlight.what


def test_priority_and_first_clutch_value():
    moments = kills(3, tags=["clutch_1v3", "opening_kill"])
    moments[1].tags = ["clutch_1v2"]
    moments.append(make_moment("assist", tags=["flash_assist"]))
    highlight, = find_highlights(make_mm(list(reversed(moments))), [make_round()])
    assert highlight.kind == "clutch" and "1v3" in highlight.title
    moments[0].tags = ["opening_kill"]
    moments[1].tags = []
    moments[2].tags = []
    assert find_highlights(make_mm(moments), [make_round()])[0].kind == "multikill"


@pytest.mark.parametrize("tag", ["clutch_1v3", "opening_kill"])
def test_lost_round_is_not_winning_highlight(tag):
    assert find_highlights(make_mm(kills(1, tags=[tag])), [make_round(winner=Team.CT)]) == []


@pytest.mark.parametrize("tag", ["teamkill", "after_round_end", "victim_is_bot"])
def test_invalid_kill_does_not_make_multikill(tag):
    moments = kills(3)
    moments[-1].tags = [tag]
    assert find_highlights(make_mm(moments), [make_round()]) == []


def test_ct_side_after_switch_and_flash_fallback():
    mm = make_mm(kills(1, tags=["opening_kill"], context={"side": "CT"}))
    assert find_highlights(mm, [make_round(winner=Team.CT)])[0].kind == "opening_win"
    mm.moments.append(make_moment("assist", tags=["flash_assist"], tick=200))
    assert find_highlights(mm, [make_round(winner=Team.T)])[0].kind == "flash_assist"
