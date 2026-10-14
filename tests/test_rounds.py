import pandas as pd

from cs2coach.parsing.models import Team
from cs2coach.parsing.rounds import segment_rounds


def rules(last: int, freeze: list[tuple[int, int]], ends: list[tuple[int, int, int]], warmup_until: int = 0,
          first: int = 1) -> pd.DataFrame:
    """合成比赛规则表。

    freeze: [(冻结期开始, 冻结期结束)]，冻结期结束那一 tick 已不在冻结期
    ends: [(胜负判定 tick, 胜方, 判定后的回合号)]；胜方一直保持到下一次冻结期开始
    """
    t = pd.Series(range(first, last + 1))
    df = pd.DataFrame({"tick": t, "total_rounds_played": 0, "is_freeze_period": False,
                       "is_warmup_period": t <= warmup_until, "win_status": 0, "win_reason": 0})
    for a, b in freeze:
        df.loc[(t >= a) & (t < b), "is_freeze_period"] = True
    starts = sorted(a for a, _ in freeze)
    for tick, winner, number in ends:
        nxt = next((a for a in starts if a > tick), last + 1)
        sel = (t >= tick) & (t < nxt)
        df.loc[sel, "win_status"] = winner
        df.loc[sel, "win_reason"] = 9 if winner == Team.T else 8
        df.loc[t >= tick, "total_rounds_played"] = number
    return df


def test_two_rounds():
    df = rules(300, freeze=[(1, 20), (150, 170)], ends=[(100, 2, 1), (250, 3, 2)])
    r1, r2 = segment_rounds(df, [20, 170])
    assert (r1.number, r1.start_tick, r1.freeze_end_tick, r1.end_tick, r1.last_tick) == (1, 1, 20, 100, 149)
    assert r1.winner == Team.T and r1.reason == 9
    assert (r2.number, r2.start_tick, r2.freeze_end_tick, r2.end_tick, r2.last_tick) == (2, 150, 170, 250, 300)
    assert r2.winner == Team.CT
    assert r1.contains(149) and not r1.contains(150)
    assert r1.is_live(20) and r1.is_live(100) and not r1.is_live(19) and not r1.is_live(101)


def test_warmup_round_is_dropped():
    # 5E：热身期间也有回合结束和冻结期结束（陷阱 14）
    df = rules(400, freeze=[(1, 20), (150, 170)], ends=[(60, 2, 0), (250, 3, 1)], warmup_until=120)
    rounds = segment_rounds(df, [20, 170])
    assert len(rounds) == 1
    assert (rounds[0].number, rounds[0].start_tick, rounds[0].freeze_end_tick) == (1, 150, 170)


def test_demo_starting_mid_round():
    # Valve 匹配 demo 可能在第一回合冻结期结束后才开始录制
    df = rules(300, freeze=[(150, 170)], ends=[(100, 3, 1), (250, 2, 2)], first=40)
    r1, r2 = segment_rounds(df, [170])
    assert (r1.start_tick, r1.freeze_end_tick, r1.end_tick, r1.last_tick) == (40, None, 100, 149)
    assert r1.is_live(40)
    assert (r2.number, r2.freeze_end_tick) == (2, 170)


def test_restart_drops_replayed_rounds():
    # 打了两回合后比赛重开，回合号从 1 重新计数
    df = rules(600, freeze=[(1, 20), (150, 170), (300, 320), (450, 470)],
               ends=[(100, 2, 1), (250, 2, 2), (400, 3, 1), (550, 3, 2)])
    rounds = segment_rounds(df, [20, 170, 320, 470])
    assert [(r.number, r.end_tick) for r in rounds] == [(1, 400), (2, 550)]
    assert rounds[0].start_tick == 300 and rounds[0].last_tick == 449


def test_non_team_winner_is_skipped():
    df = rules(300, freeze=[(1, 20), (150, 170)], ends=[(100, 1, 1), (250, 3, 1)])
    rounds = segment_rounds(df, [20, 170])
    assert [(r.number, r.end_tick) for r in rounds] == [(1, 250)]


def test_freeze_end_list_may_be_unsorted_or_duplicated():
    df = rules(300, freeze=[(1, 20), (150, 170)], ends=[(100, 2, 1), (250, 3, 2)])
    rounds = segment_rounds(df, [170, 20, 170, 9999])
    assert [r.freeze_end_tick for r in rounds] == [20, 170]


def test_round_without_end_is_ignored():
    # demo 在回合中途结束
    df = rules(300, freeze=[(1, 20), (150, 170)], ends=[(100, 2, 1)])
    rounds = segment_rounds(df, [20, 170])
    assert len(rounds) == 1 and rounds[0].last_tick == 149


def test_empty_rules():
    assert segment_rounds(rules(0, [], []), []) == []
