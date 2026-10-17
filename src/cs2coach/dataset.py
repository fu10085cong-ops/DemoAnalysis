"""按当前解析与规则代码版本读取交火数据集。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from cs2coach.cache import code_hash

DATASET_ROOT = Path(__file__).resolve().parents[2] / "data" / "dataset"
KINDS = {"moments", "missed_trades", "players"}


def load(kind: str = "moments", group: str | None = None) -> pd.DataFrame:
    """读取当前代码版本的全部 parquet，按行类型和来源筛选。"""
    if kind not in KINDS:
        raise ValueError(f"未知数据类型：{kind}")
    if group not in (None, "pro", "amateur"):
        raise ValueError(f"未知来源：{group}")
    folder = DATASET_ROOT / code_hash()[:12]
    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"数据集不存在：{folder}；请先运行 python scripts/dataset_export.py")
    frames = []
    for path in files:
        frame = pd.read_parquet(path)
        frame = frame.loc[frame["kind"].eq(kind)]
        if group is not None:
            frame = frame.loc[frame["group"].eq(group)]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
