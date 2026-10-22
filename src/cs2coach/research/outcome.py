"""R03 第 2 步：首杀选手交战之后的后果（行为之后）与比较。

计时从观察截止时刻（`observe_end_tick`）开始：行为在前、后果在后，两段不重叠。
- 资源：截止后多久阵亡；回合结束时还活着的记为在回合结束时截断（不当作"没被补"）
- 局势优势：截止后到阵亡 / 回合结束之前，他是否又击杀了 T
窗口之间不独立（同一场比赛、同一支战队），置信区间按比赛整场重抽（cluster bootstrap）。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

T_TEAM = 2


def outcome_row(window: dict, kills: pd.DataFrame, horizon_s: float) -> dict:
    """一个窗口的后果。kills：这场比赛的击杀表（round、tick、attacker、victim、victim_team_num）。
    返回 time_s（截止 → 阵亡或截断的秒数）、died（是否在 horizon 内、回合结束前阵亡；截断前不到 horizon 的记 None）、
    event（这段时间是以阵亡结束）、kill_after（horizon 内又击杀 T；回合在 horizon 内结束且之前没击杀的同样记 None 以外的值）。"""
    rate = float(window["tick_rate"])
    stop = window.get("observe_end_tick")
    if stop is None or pd.isna(stop):
        return {"time_s": None, "event": None, "died": None, "kill_after": None}
    stop = int(stop)
    sid = str(window["player_id"])
    rnd = kills[kills["round"] == int(window["round_id"])]
    deaths = rnd[(rnd["victim"].astype(str) == sid) & (rnd["tick"] >= stop)]["tick"]
    death = int(deaths.min()) if len(deaths) else None
    end = window.get("round_end_tick")
    end = int(end) if end is not None and pd.notna(end) else None
    if death is not None:
        last, event = death, True
    elif end is not None:
        last, event = end, False
    else:
        return {"time_s": None, "event": None, "died": None, "kill_after": None}
    time_s = max(0.0, (last - stop) / rate)
    horizon = round(horizon_s * rate)
    if event and time_s <= horizon_s:
        died = True
    elif time_s >= horizon_s:
        died = False
    else:
        died = None  # 回合在 horizon 内结束、他还活着：不知道再过几秒会不会被补
    mine = rnd[(rnd["attacker"].astype(str) == sid) & (rnd["victim_team_num"] == T_TEAM)
               & (rnd["tick"] > stop) & (rnd["tick"] <= min(last, stop + horizon))]
    if len(mine):
        kill_after = True
    elif time_s >= horizon_s or event:
        kill_after = False  # 活满 horizon，或者在 horizon 内阵亡（阵亡前没再击杀）
    else:
        kill_after = None
    return {"time_s": round(time_s, 3), "event": event, "died": died, "kill_after": kill_after}


def km_death_by(times: np.ndarray, events: np.ndarray, horizon_s: float) -> float | None:
    """Kaplan–Meier：horizon 秒内阵亡的比例（1 − 存活率）；回合结束的截断样本不算没被补。"""
    if len(times) == 0:
        return None
    times, events = np.asarray(times, float), np.asarray(events, bool)
    uniq, inverse = np.unique(times, return_inverse=True)
    leaving = np.bincount(inverse, minlength=len(uniq))           # 每个时刻离开风险集的人数（阵亡 + 截断）
    deaths = np.bincount(inverse, weights=events, minlength=len(uniq))
    at_risk = len(times) - np.concatenate([[0], np.cumsum(leaving)[:-1]])
    keep = uniq <= horizon_s
    surv = float(np.prod(1 - deaths[keep] / at_risk[keep]))
    return round(1 - surv, 4)


def mh_risk_difference(frame: pd.DataFrame, group: str, outcome: str, strata: list[str], a: str, b: str) -> float | None:
    """Mantel–Haenszel 风险差：每个分层格子里 a 组与 b 组的比例差，按 n_a·n_b / (n_a + n_b) 加权平均。
    只有一组的格子不参与（没有可比对象）；outcome 为 None 的行不算。"""
    f = frame[frame[group].isin([a, b]) & frame[outcome].notna()]
    if f.empty:
        return None
    codes = f.groupby(strata, observed=True).ngroup().to_numpy()
    is_a = (f[group] == a).to_numpy()
    y = f[outcome].astype(float).to_numpy()
    k = codes.max() + 1
    na, nb = np.bincount(codes[is_a], minlength=k), np.bincount(codes[~is_a], minlength=k)
    ya, yb = np.bincount(codes[is_a], y[is_a], minlength=k), np.bincount(codes[~is_a], y[~is_a], minlength=k)
    both = (na > 0) & (nb > 0)
    if not both.any():
        return None
    w = na[both] * nb[both] / (na[both] + nb[both])
    diff = ya[both] / na[both] - yb[both] / nb[both]
    return round(float((w * diff).sum() / w.sum()), 4)


def cluster_bootstrap(frame: pd.DataFrame, stat, cluster: str = "match_id", reps: int = 1000, seed: int = 0) -> tuple[float, float]:
    """按比赛整场重抽的 95% 区间；stat(frame) → 数值（None 的重抽样本跳过）。"""
    rng = np.random.default_rng(seed)
    groups = [np.asarray(ix) for ix in frame.groupby(cluster).indices.values()]
    values = []
    for _ in range(reps):
        pick = rng.choice(len(groups), len(groups), replace=True)
        value = stat(frame.iloc[np.concatenate([groups[j] for j in pick])])
        if value is not None and not (isinstance(value, float) and math.isnan(value)):
            values.append(value)
    if not values:
        return (float("nan"), float("nan"))
    return (round(float(np.percentile(values, 2.5)), 4), round(float(np.percentile(values, 97.5)), 4))


def econ_type(avg: float | None, full_min: float, eco_max: float) -> str:
    """一队人均装备（回合开始时带的 + 本回合花的）→ 全起 / 半起 / 经济局；缺数据记"未知"。"""
    if avg is None or pd.isna(avg):
        return "未知"
    return "全起" if avg >= full_min else "经济局" if avg < eco_max else "半起"


def econ_matchup(ct: str, t: str) -> str:
    """分层用的经济对比（4 类，避免格子太碎）：双方全起 / 只有 CT 全起 / 只有 T 全起 / 都没全起。"""
    if "未知" in (ct, t):
        return "未知"
    if ct == "全起":
        return "双方全起" if t == "全起" else "只有 CT 全起"
    return "只有 T 全起" if t == "全起" else "都没全起"
