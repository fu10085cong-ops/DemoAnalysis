"""视野计算：某个 tick 一名玩家能不能看到另一名玩家，以及"第一次看见"是什么时候。

- 视线：awpy 的 VisibilityChecker 用地图碰撞网格做射线检测（awpy-data 发布的 .mesh，首次使用时自动下载）。
  从观察者的眼睛到对手身上几个点（头、胸口及两侧、膝、脚），任一条没被挡住就算"看得到"
- 视野范围：对手必须在观察者屏幕内（相对准星的水平 / 垂直角度不超过配置的半角）
- 烟雾：无法精确建模，只用来标记"视线经过烟雾附近"，由调用方决定排除（见 config 的 [visibility] 说明）

不用雷达的 spotted 状态：它约 0.5 秒更新一次，有随机延迟（docs/fields.md §8；Leetify 2020 年也因此弃用）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from cs2coach.features.aim import aim_vector
from cs2coach.parsing.match import MatchData


@dataclass
class FirstSeen:
    tick: int | None  # 这段连续可见的第一个 tick；在回溯范围内一直可见时为 None
    seen_at_limit: bool  # 回溯到上限时仍然可见（"第一次看见"早于回溯范围）
    smoke: bool  # "第一次看见"那一刻视线经过烟雾附近：起点可能偏早，不可信
    # seen_at_limit 时回溯停在哪里：lookback（回溯时长上限）或 floor（调用方给的下限，如上一次击杀、冻结期结束）
    limit: str | None = None


@dataclass(slots=True)
class PlayerState:
    """一名玩家在某个 tick 的状态（只含视野计算需要的字段）。逐 tick 循环中不用 pandas 行，太慢。"""

    x: float
    y: float
    z: float
    pitch: float
    yaw: float
    duck: float
    alive: bool
    team: float


class Sight:
    """checker：可注入实现了 is_visible(a, b) 的对象（测试用）；默认按地图名加载 awpy 的 VisibilityChecker。"""

    def __init__(self, match: MatchData, cfg: dict[str, Any], checker: Any = None):
        v, a = cfg["visibility"], cfg["aim"]
        self.match = match
        self.rate = match.meta.tick_rate
        self.points_s = list(v["body_points_standing"])
        self.points_c = list(v["body_points_crouched"])
        self.side = float(v["body_side_offset"])
        self.fov_h, self.fov_v = float(v["fov_horizontal_half"]), float(v["fov_vertical_half"])
        self.lookback = round(v["first_seen_lookback_seconds"] * self.rate)
        self.gap = int(v["first_seen_gap_ticks"])
        self.smoke_r, self.smoke_h = float(v["smoke_radius"]), float(v["smoke_center_height"])
        self.smoke_from, self.smoke_to = (round(x * self.rate) for x in v["smoke_active_seconds"])
        self.eye_s, self.eye_c = float(a["eye_height_standing"]), float(a["eye_height_crouched"])
        g = match.grenades
        self.smokes = g[g["type"] == "smokegrenade"][["tick", "end_tick", "x", "y", "z"]] if not g.empty else pd.DataFrame()
        self.checker = checker
        self.data_version: str | None = "injected" if checker is not None else None
        self.unavailable_reason: str | None = None
        if checker is not None:
            return
        try:
            from awpy import VisibilityChecker, data

            self.checker = VisibilityChecker(match.meta.map_name)
            self.data_version = data.resolve_version()
        except Exception as e:  # 没有网络、地图不在 awpy-data 中等：视野相关指标留空，不中断分析
            self.unavailable_reason = f"{type(e).__name__}: {e}"

    @property
    def available(self) -> bool:
        return self.checker is not None

    # ---------- 单个 tick ----------

    def eye(self, st: PlayerState) -> np.ndarray:
        return np.array([st.x, st.y, st.z + self.eye_s - (self.eye_s - self.eye_c) * st.duck])

    def body_points(self, st: PlayerState, eye: np.ndarray) -> list[np.ndarray]:
        base = np.array([st.x, st.y, st.z])
        pts = [base + [0.0, 0.0, s - (s - c) * st.duck] for s, c in zip(self.points_s, self.points_c)]
        # 胸口左右两侧：垂直于视线的水平方向
        dx, dy = base[0] - eye[0], base[1] - eye[1]
        n = math.hypot(dx, dy)
        if n > 0:
            side = np.array([-dy, dx, 0.0]) / n * self.side
            chest = pts[1]
            pts += [chest + side, chest - side]
        return pts

    def in_view(self, st: PlayerState, eye: np.ndarray, target: np.ndarray) -> bool:
        """target 是否在 st 这名玩家的屏幕内。"""
        to = target - eye
        dist_h = math.hypot(to[0], to[1])
        yaw_to = math.degrees(math.atan2(to[1], to[0]))
        pitch_to = -math.degrees(math.atan2(to[2], dist_h))  # Source：pitch 为正表示向下看
        dyaw = abs((yaw_to - st.yaw + 180) % 360 - 180)
        return dyaw <= self.fov_h and abs(pitch_to - st.pitch) <= self.fov_v

    def sees(self, viewer: PlayerState | pd.Series | None, target: PlayerState | pd.Series | None,
             need_view: bool = True) -> bool:
        """viewer 能否看到 target（视线 + 屏幕内）。坐标缺失、已死亡时为 False。"""
        v, t = _as_state(viewer), _as_state(target)
        if self.checker is None or v is None or t is None or not v.alive or not t.alive:
            return False
        eye = self.eye(v)
        pts = self.body_points(t, eye)
        if need_view and not self.in_view(v, eye, pts[1]):
            return False
        e = tuple(eye)
        return any(self.checker.is_visible(e, tuple(p)) for p in pts)

    def smoke_between(self, viewer: PlayerState | pd.Series | None, target: PlayerState | pd.Series | None,
                      tick: int) -> bool:
        v, t = _as_state(viewer), _as_state(target)
        if self.smokes.empty or v is None or t is None:
            return False
        sm = self.smokes
        age = tick - sm["tick"]
        active = sm[(age >= self.smoke_from) & (age <= self.smoke_to)
                    & (sm["end_tick"].fillna(np.iinfo(np.int64).max) >= tick)]
        if active.empty:
            return False
        a = self.eye(v)
        b = np.array([t.x, t.y, t.z + self.points_s[1] - (self.points_s[1] - self.points_c[1]) * t.duck])
        centers = active[["x", "y", "z"]].to_numpy(dtype=float) + [0.0, 0.0, self.smoke_h]
        return bool((_segment_distances(a, b, centers) <= self.smoke_r).any())

    # ---------- 时间段 ----------

    def states(self, steamid: int, start: int, end: int) -> dict[int, PlayerState]:
        """steamid 在 [start, end] 每个 tick 的状态（坐标缺失的 tick 不在其中）。"""
        return _states_from_frame(self.match.player_ticks(steamid, start, end))

    def states_at(self, steamid: int, ticks: list[int]) -> dict[int, PlayerState]:
        """只取指定的若干 tick（不连续时比 states 快得多）。"""
        sub = self.match.player_ticks(steamid)
        t = sub["tick"].to_numpy()
        want = np.unique(np.asarray(ticks, dtype=t.dtype))
        idx = np.searchsorted(t, want)
        ok = idx < len(t)
        idx = idx[ok][t[idx[ok]] == want[ok]]
        return _states_from_frame(sub.iloc[idx])

    def first_seen(self, viewer: int, target: int, tick: int, floor: int) -> FirstSeen:
        """viewer 在 tick 时（允许 gap 个 tick 的误差）看得到 target 的话，往前找这段连续可见期的起点。

        - tick 附近就看不到（穿墙、背身被打等）：tick 为 None，seen_at_limit 为 False
        - 一直回溯到上限（或 floor）仍然可见：起点未知，tick 为 None，seen_at_limit 为 True
        """
        lo = max(floor, tick - self.lookback)
        vs = self.states(viewer, lo, tick)
        ts = self.states(target, lo, tick)
        start: int | None = None
        unseen = 0
        for t in range(tick, lo - 1, -1):
            if t not in vs or t not in ts:
                break
            if self.sees(vs[t], ts[t]):
                start, unseen = t, 0
            else:
                unseen += 1
                if unseen > self.gap:
                    break
        at_limit = start is not None and start == lo
        # 只看"第一次看见"那一刻视线是否经过烟雾：如果是，真正看见的时刻可能更晚（烟雾挡住了），起点不可信
        smoke = start is not None and not at_limit and self.smoke_between(vs[start], ts[start], start)
        limit = None
        if at_limit:
            limit = "floor" if lo == floor and floor > tick - self.lookback else "lookback"
        return FirstSeen(tick=None if at_limit else start, seen_at_limit=at_limit, smoke=smoke, limit=limit)

    def view_vector(self, steamid: int, tick: int) -> np.ndarray | None:
        st = _as_state(self.match.state_at(steamid, tick))
        if st is None:
            return None
        return aim_vector(np.array([st.pitch]), np.array([st.yaw]))[0]

    def enemies_seen(self, steamid: int, ticks: list[int]) -> list[bool]:
        """每个 tick，steamid 能否看到任何存活的敌人（用于 Spotted Accuracy 等"看见敌人时"的口径）。"""
        if not ticks:
            return []
        me = self.states_at(steamid, ticks)
        others = [self.states_at(o, ticks) for o in self.match.players if o != steamid]
        out = []
        for t in ticks:
            v = me.get(t)
            seen = False
            if v is not None and v.alive:
                for o in others:
                    st = o.get(t)
                    if st is not None and st.team != v.team and self.sees(v, st):
                        seen = True
                        break
            out.append(seen)
        return out

    def any_enemy_seen(self, steamid: int, tick: int) -> bool:
        return self.enemies_seen(steamid, [tick])[0]


def angle_between(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    return round(math.degrees(math.acos(float(np.clip(np.dot(a, b), -1.0, 1.0)))), 2)


_STATE_COLS = ["X", "Y", "Z", "pitch", "yaw", "duck_amount", "is_alive", "team_num"]


def _states_from_frame(df: pd.DataFrame) -> dict[int, PlayerState]:
    if df.empty:
        return {}
    arr = df[_STATE_COLS].to_numpy(dtype=float, na_value=np.nan)
    out = {}
    for t, (x, y, z, p, yw, d, a, team) in zip(df["tick"].to_numpy(), arr):
        if math.isnan(x) or math.isnan(y) or math.isnan(z) or math.isnan(p) or math.isnan(yw):
            continue
        out[int(t)] = PlayerState(x, y, z, p, yw, 0.0 if math.isnan(d) else d, bool(a), team)
    return out


def _as_state(st: PlayerState | pd.Series | None) -> PlayerState | None:
    if st is None or isinstance(st, PlayerState):
        return st
    vals = [_num(st[c]) for c in ("X", "Y", "Z", "pitch", "yaw")]
    if any(v is None for v in vals):
        return None
    return PlayerState(*vals, _num(st["duck_amount"]) or 0.0, bool(st["is_alive"]), _num(st["team_num"]) or 0.0)


def _segment_distances(a: np.ndarray, b: np.ndarray, centers: np.ndarray) -> np.ndarray:
    ab = b - a
    denom = float(ab @ ab)
    if denom == 0:
        return np.linalg.norm(centers - a, axis=1)
    t = np.clip(((centers - a) @ ab) / denom, 0.0, 1.0)
    closest = a + t[:, None] * ab
    return np.linalg.norm(closest - centers, axis=1)


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f
