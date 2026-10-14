"""回合切分。不依赖 round_start / round_end 事件（ESL、完美、5E 中是 demoparser2 合成的，字段类型也不同），
改用比赛规则的 tick 字段（docs/fields.md §3）：

- 回合结束：m_iRoundWinStatus 由 0 变为非 0 的 tick，该 tick 上的值就是胜方，m_eRoundWinReason 是原因
- 回合号：回合结束那一 tick 的 total_rounds_played（已经加 1，等于从 1 开始的序号）
- 冻结期结束：round_freeze_end 事件（所有来源都是原生的）
- 冻结期开始：is_freeze_period 由 False 变为 True 的 tick
- 热身：is_warmup_period 为 True 的回合结束 / 冻结期结束一律丢弃（5E 的热身回合混在事件里，陷阱 14）
"""

from __future__ import annotations

import bisect

import pandas as pd

from cs2coach.parsing.models import Round, Team

# rules 表需要的列（每个 tick 一行，按 tick 升序）
RULES_COLUMNS = ["tick", "total_rounds_played", "is_freeze_period", "is_warmup_period", "win_status", "win_reason"]


def segment_rounds(rules: pd.DataFrame, freeze_end_ticks: list[int]) -> list[Round]:
    if rules.empty:
        return []
    rules = rules.sort_values("tick").reset_index(drop=True)
    first_tick, last_tick = int(rules["tick"].iloc[0]), int(rules["tick"].iloc[-1])

    status = rules["win_status"].astype(int)
    ends = rules[(status != 0) & (status.shift(fill_value=0) == 0) & ~rules["is_warmup_period"].astype(bool)]

    freeze = rules["is_freeze_period"].astype(bool)
    freeze_starts = rules.loc[freeze & ~freeze.shift(fill_value=False), "tick"].astype(int).tolist()

    warmup_at = rules.set_index("tick")["is_warmup_period"].astype(bool)
    freeze_ends = sorted(
        t for t in {int(t) for t in freeze_end_ticks}
        if first_tick <= t <= last_tick and not warmup_at.asof(t)
    )

    rounds: list[Round] = []
    prev_end = first_tick - 1
    for row in ends.itertuples(index=False):
        end, winner = int(row.tick), int(row.win_status)
        lo, hi = prev_end, end
        prev_end = end
        if winner not in (Team.T, Team.CT):
            continue  # 平局等异常结果（如重开比赛）不算回合

        freeze_end = _last_in(freeze_ends, lo, hi)
        start = _last_in(freeze_starts, lo, freeze_end if freeze_end is not None else hi)
        if start is None:
            start = lo + 1 if lo >= first_tick else first_tick
        nxt = _first_after(freeze_starts, end)
        number = int(row.total_rounds_played)

        # 比赛重开（如 mp_restartgame、回档）后回合号会倒退：丢弃被重打的旧回合
        rounds = [r for r in rounds if r.number < number]
        rounds.append(Round(
            number=number,
            start_tick=start,
            freeze_end_tick=freeze_end,
            end_tick=end,
            last_tick=nxt - 1 if nxt is not None else last_tick,
            winner=Team(winner),
            reason=int(row.win_reason),
        ))
    return rounds


def _last_in(sorted_ticks: list[int], lo: int, hi: int) -> int | None:
    """sorted_ticks 中落在 (lo, hi] 的最大值。"""
    i = bisect.bisect_right(sorted_ticks, hi)
    if i and sorted_ticks[i - 1] > lo:
        return sorted_ticks[i - 1]
    return None


def _first_after(sorted_ticks: list[int], tick: int) -> int | None:
    i = bisect.bisect_right(sorted_ticks, tick)
    return sorted_ticks[i] if i < len(sorted_ticks) else None
