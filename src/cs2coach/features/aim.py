"""判断每一发枪械子弹打向哪个敌人（近似）。

demo 里没有"这发子弹瞄的是谁"。fire_bullets 的 angles 是子弹的实际方向（已叠加后坐力，docs/fields.md §9），
取开枪前一 tick 所有存活敌人，计算子弹方向与"开枪者眼睛 → 敌人身体中心"的夹角，最小的那个就是目标。
取前一 tick 是因为被这一枪打死的人在开枪那一 tick 已经是死亡状态。

用命中的子弹（有对应的 player_hurt）核对：5 个样本 demo 中 96%–99% 判对，判错的多是两个敌人在视线上重叠。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cs2coach.parsing.match import MatchData


def scoped_before_shot(states: pd.DataFrame, tick: int) -> bool:
    """开镜状态取开枪前一 tick，缺数据时退回开枪 tick（docs/fields.md 陷阱 23）。"""
    for t in (tick - 1, tick):
        if t in states.index and pd.notna(states.loc[t, "is_scoped"]):
            return bool(states.loc[t, "is_scoped"])
    return False


def shot_targets(match: MatchData, cfg: dict[str, Any]) -> pd.DataFrame:
    """所有枪械射击（不含接管 bot 时）的目标：列 tick、steamid、target（可空）、target_angle（度，可空）。"""
    a = cfg["aim"]
    s = match.shots
    s = s[s["is_gun"] & ~s["controlling_bot"] & s["aim_yaw"].notna()][["tick", "steamid", "aim_pitch", "aim_yaw"]]
    out = s[["tick", "steamid"]].copy()
    out["target"] = pd.array([None] * len(out), dtype="Int64")
    out["target_angle"] = np.nan
    if s.empty:
        return out

    state = match.ticks[["tick", "steamid", "X", "Y", "Z", "duck_amount", "team_num", "is_alive"]]
    prev = s.assign(prev_tick=s["tick"] - 1, steamid=s["steamid"].astype("int64")).reset_index()
    me = prev.merge(state.rename(columns={"tick": "prev_tick"}), on=["prev_tick", "steamid"], how="left")
    cand = state[state["is_alive"]].rename(columns={"tick": "prev_tick", "steamid": "cand"})
    j = me.merge(cand, on="prev_tick", suffixes=("", "_c"))
    j = j[j["team_num_c"] != j["team_num"]]
    if j.empty:
        return out

    duck = j["duck_amount"].fillna(0).to_numpy()
    duck_c = j["duck_amount_c"].fillna(0).to_numpy()
    eye = np.stack([j["X"], j["Y"], j["Z"] + _lerp(a["eye_height_standing"], a["eye_height_crouched"], duck)], -1)
    body = np.stack([j["X_c"], j["Y_c"], j["Z_c"] + _lerp(a["body_height_standing"], a["body_height_crouched"], duck_c)], -1)
    to_target = body - eye
    norm = np.linalg.norm(to_target, axis=1)
    ok = norm > 0
    to_target[ok] /= norm[ok, None]
    aim = aim_vector(j["aim_pitch"].to_numpy(), j["aim_yaw"].to_numpy())
    j = j.assign(angle=np.degrees(np.arccos(np.clip((to_target * aim).sum(1), -1.0, 1.0))))
    j = j[ok & np.isfinite(j["angle"].to_numpy())]

    best = j.loc[j.groupby("index")["angle"].idxmin(), ["index", "cand", "angle"]].set_index("index")
    best = best[best["angle"] <= a["max_target_angle"]]
    out.loc[best.index, "target"] = best["cand"].astype("int64").to_numpy()
    out.loc[best.index, "target_angle"] = best["angle"].round(2).to_numpy()
    return out


def aim_vector(pitch_deg: np.ndarray, yaw_deg: np.ndarray) -> np.ndarray:
    """Source 引擎的视角 → 单位方向向量。pitch 为正表示向下看。"""
    p, y = np.radians(pitch_deg), np.radians(yaw_deg)
    return np.stack([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), -np.sin(p)], -1)


def _lerp(standing: float, crouched: float, duck: np.ndarray) -> np.ndarray:
    return standing - (standing - crouched) * duck
