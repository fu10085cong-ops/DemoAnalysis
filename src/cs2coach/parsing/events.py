"""事件表的读取与规范化。

- parse_event 对不存在的事件返回空 list（陷阱 2），统一成空 DataFrame
- 事件里的 steamid 是字符串（陷阱 5），统一转成可空整数 Int64
- 武器名的规范化见 weapons.py
"""

from __future__ import annotations

import numbers

import pandas as pd
from demoparser2 import DemoParser


def event_df(parser: DemoParser, name: str, **kwargs) -> pd.DataFrame:
    df = parser.parse_event(name, **kwargs)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


def to_steamid(s: pd.Series) -> pd.Series:
    """字符串 / 整数 / 缺失值统一转成 Int64；0（世界、炸弹等非玩家）视为缺失。

    不能用 pd.to_numeric：SteamID64 超过 2^53，经过 float 会丢精度。
    """
    return pd.Series([_steamid(v) for v in s], index=s.index, dtype="Int64")


def _steamid(v: object) -> int | None:
    if isinstance(v, str):
        v = v.strip()
        n = int(v) if v.isdigit() else 0
    elif isinstance(v, numbers.Integral):
        n = int(v)
    else:
        n = 0  # NaN / None / 浮点（浮点的 SteamID64 已经丢了精度，不可信）
    return n or None
