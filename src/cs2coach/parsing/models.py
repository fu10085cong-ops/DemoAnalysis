"""解析层的结构化记录。批量数据（逐 tick、逐事件）用 DataFrame，见 match.py。"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel


class Team(IntEnum):
    """team_num 的取值（docs/fields.md 陷阱 10：中场换边，必须按 tick 取值）。"""

    T = 2
    CT = 3


class MatchMeta(BaseModel):
    map_name: str
    server_name: str
    patch_version: str
    tick_rate: int
    last_tick: int


class PlayerInfo(BaseModel):
    steamid: int
    name: str
    # 在场回合中，回合进行中坐标非空的 tick 占比；过低说明 demoparser2 没读到该玩家
    coverage: float
    # 不在场的回合（中途掉线、重连前；docs/fields.md 陷阱 24），不计入覆盖率和问题频率
    absent_rounds: list[int] = []


class Round(BaseModel):
    number: int  # 从 1 开始
    start_tick: int  # 冻结期开始（demo 从回合中途开始录制时为 demo 第一个 tick）
    freeze_end_tick: int | None  # 冻结期结束；demo 从回合中途开始录制时可能缺失
    end_tick: int  # 胜负判定的 tick（m_iRoundWinStatus 由 0 变为非 0）
    last_tick: int  # 属于本回合的最后一个 tick（下一回合 start_tick - 1）；包含回合结束后的自由时间
    winner: Team
    reason: int  # m_eRoundWinReason，见 docs/fields.md §3
    # 购买时间结束：有 buytime_ended 事件时取事件，否则按 freeze_end + mp_buytime 推算；不晚于 end_tick
    buy_end_tick: int | None = None

    def contains(self, tick: int) -> bool:
        return self.start_tick <= tick <= self.last_tick

    def is_live(self, tick: int) -> bool:
        """冻结期结束到胜负判定之间（不含回合结束后的自由时间）。"""
        start = self.freeze_end_tick if self.freeze_end_tick is not None else self.start_tick
        return start <= tick <= self.end_tick
