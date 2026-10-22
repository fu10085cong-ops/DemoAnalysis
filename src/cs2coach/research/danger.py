"""R03 第 5 版：首杀方向与危险度地图（docs/tasks/research/R03-ai-post-fight-reposition.md "第 5 版方向"）。

危险度 = CT 在某位置每停留 1 分钟被打死的次数，相对本组（同一首杀方向）平均的倍数；只看首杀之后
after_seconds 秒内的停留与阵亡。停留少的格子向本组平均收缩：(阵亡 + K × 平均) / (停留 + K)。
判定某场比赛时只用其他比赛的数据（留一场），避免用这场的结果定义这场的行为。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DIRECTIONS = ("A", "B", "Mid")
T_TEAM, CT_TEAM = 2, 3


def direction_of(place: str | None, x: float, y: float, cfg: dict, map_name: str) -> str | None:
    """位置属于哪个方向（A / B / Mid）；不归类为 None。大报点按 direction_splits 的坐标规则拆分。"""
    research = cfg["research"]
    splits = research.get("direction_splits", {}).get(map_name, {})
    if place in splits:
        for rule in splits[place]:
            if (x >= rule.get("x_min", -math.inf) and x <= rule.get("x_max", math.inf)
                    and y >= rule.get("y_min", -math.inf) and y <= rule.get("y_max", math.inf)):
                return rule["direction"]
        return None
    for name, places in research.get("directions", {}).get(map_name, {}).items():
        if place in places:
            return name
    return None


KILL_COLUMNS = ["round", "tick", "t", "attacker", "victim", "victim_team_num", "victim_X", "victim_Y", "victim_Z",
                "victim_last_place_name", "attacker_X", "attacker_Y", "attacker_Z"]


def samples(match, sample_seconds: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """危险度地图的原始数据：冻结结束到回合结束，每 sample_seconds 秒一行的存活玩家位置；同一时段的敌对击杀。
    t = 距冻结结束的秒数。"""
    rate = match.meta.tick_rate
    step = max(1, round(sample_seconds * rate))
    cols = ["tick", "steamid", "team_num", "X", "Y", "Z", "last_place_name", "is_alive"]
    ticks = match.ticks[cols]
    ticks = ticks[(ticks["tick"] % step == 0) & ticks["is_alive"].astype(bool)]
    pos, kills = [], []
    for rnd in match.rounds:
        if rnd.freeze_end_tick is None:
            continue
        seg = ticks[(ticks["tick"] >= rnd.freeze_end_tick) & (ticks["tick"] <= rnd.end_tick)].drop(columns="is_alive")
        pos.append(seg.assign(round=rnd.number, t=(seg["tick"] - rnd.freeze_end_tick) / rate))
        k = match.kills
        if k.empty:
            continue
        k = k[(k["round"] == rnd.number) & (k["tick"] >= rnd.freeze_end_tick) & (k["tick"] <= rnd.end_tick)
              & k["is_enemy_kill"]]
        kills.append(k.assign(t=(k["tick"] - rnd.freeze_end_tick) / rate).reindex(columns=KILL_COLUMNS))
    pos_df = pd.concat(pos, ignore_index=True) if pos else pd.DataFrame(columns=[*cols[:-1], "round", "t"])
    kill_df = pd.concat(kills, ignore_index=True) if kills else pd.DataFrame(columns=KILL_COLUMNS)
    return pos_df, kill_df


def first_kills(kills: pd.DataFrame, cfg: dict, map_name: str) -> pd.DataFrame:
    """每回合第一个敌对击杀（不论哪方阵亡），加上首杀方向 direction（按死者位置）。kills 需有 match_id、demo_id。"""
    if kills.empty:
        return kills.assign(direction=pd.Series(dtype=object))
    first = kills.sort_values("tick", kind="stable").groupby(["demo_id", "round"], sort=False).head(1).copy()
    first["direction"] = [direction_of(p if isinstance(p, str) else None, x, y, cfg, map_name)
                          for p, x, y in zip(first["victim_last_place_name"], first["victim_X"], first["victim_Y"])]
    return first.reset_index(drop=True)


def _gaussian(sigma_cells: float) -> np.ndarray:
    radius = max(1, int(math.ceil(4 * sigma_cells)))
    k = np.exp(-np.arange(-radius, radius + 1) ** 2 / (2 * sigma_cells ** 2))
    return k / k.sum()


def _blur(grid: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    out = np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="same"), 0, grid)
    return np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="same"), 1, out)


@dataclass
class DangerMaps:
    """按方向、按比赛保存的停留与阵亡计数；查询时减去被判定的那场比赛再平滑。"""

    x0: float
    y0: float
    cell: float
    shape: tuple[int, int]
    kernel: np.ndarray
    shrink: float
    walkable: np.ndarray | None = None  # 任何玩家走过的格子（行号随 Y 增大）；只用于显示
    layers: np.ndarray | None = None  # 每个格子所属区域层级（0 深处 / 1 防守位 / 2 接触区，-1 未知）：收缩的目标
    # (direction, match_id) -> (exposure 秒, deaths 次) 的稀疏计数：flat index -> value
    counts: dict[tuple[str, str], tuple[pd.Series, pd.Series]] = field(default_factory=dict)
    _cache: dict = field(default_factory=dict, repr=False)

    def _flat(self, x, y) -> np.ndarray:
        col = np.floor((np.asarray(x, dtype=float) - self.x0) / self.cell).astype(int)
        row = np.floor((np.asarray(y, dtype=float) - self.y0) / self.cell).astype(int)
        ok = (col >= 0) & (col < self.shape[1]) & (row >= 0) & (row < self.shape[0])
        return np.where(ok, row * self.shape[1] + col, -1)

    def _grid(self, direction: str, exclude: str | None, which: int) -> np.ndarray:
        """本方向全部比赛的计数，减去 exclude 那场。"""
        key = ("total", direction, which)
        if key not in self._cache:
            total = np.zeros(self.shape[0] * self.shape[1])
            for (d, _), pair in self.counts.items():
                if d == direction:
                    np.add.at(total, pair[which].index.to_numpy(dtype=int), pair[which].to_numpy(dtype=float))
            self._cache[key] = total
        grid = self._cache[key].copy()
        own = self.counts.get((direction, exclude))
        if own is not None:
            np.subtract.at(grid, own[which].index.to_numpy(dtype=int), own[which].to_numpy(dtype=float))
        return np.clip(grid, 0, None).reshape(self.shape)

    def surface(self, direction: str, exclude: str | None = None) -> tuple[np.ndarray, np.ndarray, float, np.ndarray] | None:
        """(平滑后的停留秒, 平滑后的阵亡次数, 本组平均每秒阵亡, 每个格子的收缩目标)；缓存最近一次查询的比赛。
        收缩目标：格子所属区域层级在本组的平均（停留少的位置向同一层级的平均靠拢，而不是全图平均）；层级未知时用本组平均。"""
        key = (direction, exclude)
        if key not in self._cache:
            surfaces = [k for k in self._cache if k[0] != "total"]
            if len(surfaces) > 8:
                for k in surfaces:
                    del self._cache[k]
            exposure, deaths = self._grid(direction, exclude, 0), self._grid(direction, exclude, 1)
            total = exposure.sum()
            if total <= 0:
                self._cache[key] = None
            else:
                base = deaths.sum() / total
                prior = np.full(self.shape, base)
                if self.layers is not None:
                    for layer in np.unique(self.layers[self.layers >= 0]):
                        mask = self.layers == layer
                        e = exposure[mask].sum()
                        if e > 0:
                            prior[mask] = deaths[mask].sum() / e
                self._cache[key] = (_blur(exposure, self.kernel), _blur(deaths, self.kernel), base, prior)
        return self._cache[key]

    def ratio_grid(self, direction: str, exclude: str | None = None, min_exposure: float = 0.0) -> np.ndarray | None:
        """整张地图每个格子的危险度（与 ratio() 相同的算法）；可走区域之外、平滑后停留不足 min_exposure 秒的格子为 NaN。
        行号随 Y 增大，列号随 X 增大。"""
        found = self.surface(direction, exclude)
        if found is None or found[2] <= 0:
            return None
        exposure, deaths, base, prior = found
        grid = (deaths + self.shrink * prior) / (exposure + self.shrink) / base
        keep = exposure >= min_exposure
        if self.walkable is not None:
            keep &= self.walkable
        return np.where(keep, grid, np.nan)

    def ratio(self, direction: str | None, x: float, y: float, exclude: str | None = None) -> float | None:
        """该位置的危险度：相对本组平均的倍数（1 = 平均）。方向不归类、超出地图或没有数据时为 None。"""
        if direction is None:
            return None
        found = self.surface(direction, exclude)
        flat = int(self._flat([x], [y])[0])
        if found is None or flat < 0:
            return None
        exposure, deaths, base, prior = found
        if base <= 0:
            return None
        r, c = divmod(flat, self.shape[1])
        return float((deaths[r, c] + self.shrink * prior[r, c]) / (exposure[r, c] + self.shrink) / base)


LAYERS = {"deep": 0, "hold": 1, "contact": 2}


def _cell_layers(positions: pd.DataFrame, shape: tuple[int, int], cfg: dict, map_name: str) -> np.ndarray:
    """每个格子的区域层级：取在这里出现最多的报点（Palace 等按高度拆分），查区域表（research.regions）。"""
    from cs2coach.research.labels import _place

    research = cfg["research"]
    regions = research.get("regions", {}).get(map_name, {})
    floors = research.get("floors", {}).get(map_name, {})
    grid = np.full(shape[0] * shape[1], -1, dtype=int)
    if "last_place_name" not in positions:
        return grid.reshape(shape)
    named = positions[(positions["flat"] >= 0) & positions["last_place_name"].fillna("").astype(str).ne("")]
    if named.empty:
        return grid.reshape(shape)
    top = named.groupby(["flat", "last_place_name"]).size().reset_index(name="n").sort_values("n").drop_duplicates("flat", keep="last")
    height = named.groupby("flat")["Z"].median() if "Z" in named else pd.Series(dtype=float)
    for flat, name in zip(top["flat"], top["last_place_name"]):
        place = _place(str(name), (0.0, 0.0, float(height.get(flat, 0.0))), floors)
        layer = regions.get(place, regions.get(str(name), ""))
        grid[int(flat)] = LAYERS.get(layer, -1)
    return grid.reshape(shape)


def build_danger(positions: pd.DataFrame, kills: pd.DataFrame, cfg: dict, map_name: str) -> DangerMaps:
    """positions：每 sample_seconds 秒一行的存活玩家（demo_id、match_id、round、t、team_num、X、Y）；
    kills：敌对击杀（demo_id、match_id、round、t、tick、victim_team_num、victim_X/Y、victim_last_place_name）。"""
    p = cfg["research"]["danger"]
    cell = float(p["cell_size"])
    margin = 4 * float(p["smooth_radius"])
    x0, y0 = float(positions["X"].min()) - margin, float(positions["Y"].min()) - margin
    shape = (int((positions["Y"].max() + margin - y0) // cell) + 1, int((positions["X"].max() + margin - x0) // cell) + 1)
    maps = DangerMaps(x0=x0, y0=y0, cell=cell, shape=shape, kernel=_gaussian(float(p["smooth_radius"]) / cell),
                      shrink=float(p["shrink_seconds"]))
    first = first_kills(kills, cfg, map_name)[["demo_id", "round", "t", "direction"]].rename(columns={"t": "t_fk"})
    first = first[first["direction"].notna()]
    after = float(p["after_seconds"])
    flat_all = maps._flat(positions["X"], positions["Y"])
    seen = np.bincount(flat_all.clip(min=0), minlength=shape[0] * shape[1])
    maps.walkable = (seen >= p.get("walkable_min_samples", 1)).reshape(shape)
    if p.get("shrink_toward", "layer") == "layer":
        maps.layers = _cell_layers(positions.assign(flat=flat_all), shape, cfg, map_name)
    ct = positions[positions["team_num"] == CT_TEAM].merge(first, on=["demo_id", "round"])
    ct = ct[(ct["t"] >= ct["t_fk"]) & (ct["t"] < ct["t_fk"] + after)]
    dead = kills[kills["victim_team_num"] == CT_TEAM].merge(first, on=["demo_id", "round"])
    dead = dead[(dead["t"] > dead["t_fk"]) & (dead["t"] < dead["t_fk"] + after)]
    ct = ct.assign(flat=maps._flat(ct["X"], ct["Y"]))
    dead = dead.assign(flat=maps._flat(dead["victim_X"], dead["victim_Y"]))
    sample = float(p["sample_seconds"])
    for (direction, match), g in ct[ct["flat"] >= 0].groupby(["direction", "match_id"]):
        maps.counts[(direction, match)] = (g.groupby("flat").size() * sample, pd.Series(dtype=float))
    for (direction, match), g in dead[dead["flat"] >= 0].groupby(["direction", "match_id"]):
        exposure = maps.counts.get((direction, match), (pd.Series(dtype=float), None))[0]
        maps.counts[(direction, match)] = (exposure, g.groupby("flat").size().astype(float))
    return maps
