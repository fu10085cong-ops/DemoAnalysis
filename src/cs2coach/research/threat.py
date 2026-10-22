"""R03 第 6 版：这一回合的威胁热力图（docs/tasks/research/R03-ai-post-fight-reposition.md "第 6 版"）。

威胁来源只用 CT 当时能知道的信息（研究原则第 3 条）：
- 雷达：本队任一人发现（spotted_by_mask）的 T 的位置；
- 声音：T 的脚步 / 其他声音（player_sound），在任一活着的 CT 的可听范围（游戏给的 radius）内；队友听到也算（会报点）；
- 枪声：T 开枪的位置，在任一活着的 CT 的枪声范围内（消音武器更短）；
- 交战中被他打死的 T 的位置（接火点）。
每名 T 只保留 memory_seconds 内最新的一条。某个位置的危险度 = 能直接看到这里的威胁的加权个数
（双方眼睛高度、awpy 几何视线、扣除生效中的烟；距离越远权重越低）。
数据来自 research_stance.py extract 的整场存档（archive/<比赛>/），不解析 demo。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from cs2coach.features.visibility import _segment_distances
from cs2coach.parsing.match import spotted_bits

T_TEAM, CT_TEAM = 2, 3


@dataclass
class Threat:
    enemy: int | None
    x: float
    y: float
    z: float
    tick: int
    source: str  # radar / sound / gunshot / contact


class MatchArchive:
    """一场比赛的存档：每 0.125 秒全员状态、声音、开枪、投掷物。"""

    def __init__(self, folder: Path):
        read = lambda name: pd.read_parquet(folder / f"{name}.parquet") if (folder / f"{name}.parquet").exists() else pd.DataFrame()  # noqa: E731
        self.rate = int(json.loads((folder / "meta.json").read_text(encoding="utf-8"))["tick_rate"])
        self.ticks = read("ticks")
        self.sounds, self.shots, self.grenades = read("sounds"), read("shots"), read("grenades")
        self.bits = spotted_bits(self.ticks) if not self.ticks.empty else {}



def known_threats(arch: MatchArchive, start: int, end: int, cfg: dict) -> list[Threat]:
    """[start, end] 内 CT 方知道的每名 T 的最新位置（雷达、声音、枪声）。"""
    t = arch.ticks
    part = t[(t["tick"] >= start) & (t["tick"] <= end)]
    if part.empty:
        return []
    ct_ids = part.loc[part["team_num"] == CT_TEAM, "steamid"].unique()
    mask = 0
    for sid in ct_ids:
        if int(sid) in arch.bits:
            mask |= 1 << arch.bits[int(sid)]
    enemies = part[part["team_num"] == T_TEAM]
    found: dict[int, Threat] = {}

    def keep(th: Threat) -> None:
        if th.enemy not in found or found[th.enemy].tick < th.tick:
            found[th.enemy] = th

    seen = enemies[enemies["is_alive"].astype(bool) & ((enemies["spotted_by_mask"].fillna(0).astype("int64") & mask) != 0)]
    for r in seen.itertuples():
        keep(Threat(int(r.steamid), float(r.X), float(r.Y), float(r.Z), int(r.tick), "radar"))
    t_ids = set(int(s) for s in enemies["steamid"].unique())
    ct_alive = part[(part["team_num"] == CT_TEAM) & part["is_alive"].astype(bool)][["tick", "X", "Y", "Z"]]
    ct_ticks = np.sort(ct_alive["tick"].unique())

    def heard(tick: int, x: float, y: float, z: float, radius: float) -> bool:
        if not len(ct_ticks):
            return False
        i = np.searchsorted(ct_ticks, tick, side="right") - 1
        frame = ct_alive[ct_alive["tick"] == ct_ticks[max(i, 0)]]
        d = np.sqrt((frame["X"] - x) ** 2 + (frame["Y"] - y) ** 2 + (frame["Z"] - z) ** 2)
        return bool((d <= radius).any())

    s = arch.sounds
    if not s.empty:
        s = s[(s["tick"] >= start) & (s["tick"] <= end) & s["steamid"].isin(t_ids)]
        for r in s.itertuples():
            if heard(int(r.tick), float(r.x), float(r.y), float(r.z), float(r.radius)):
                keep(Threat(int(r.steamid), float(r.x), float(r.y), float(r.z), int(r.tick), "sound"))
    shots = arch.shots
    if not shots.empty:
        shots = shots[(shots["tick"] >= start) & (shots["tick"] <= end) & shots["steamid"].isin(t_ids) & shots["is_gun"].astype(bool)]
        timeline = cfg["research"]["timeline"]
        for r in shots.itertuples():
            pos = enemies[(enemies["steamid"] == r.steamid) & (enemies["tick"] <= r.tick)].tail(1)
            if pos.empty:
                continue
            x, y, z = float(pos["X"].iloc[0]), float(pos["Y"].iloc[0]), float(pos["Z"].iloc[0])
            radius = timeline["silenced_gunshot_radius"] if r.weapon in timeline["silenced_weapons"] else timeline["gunshot_radius"]
            if heard(int(r.tick), x, y, z, float(radius)):
                keep(Threat(int(r.steamid), x, y, z, int(r.tick), "gunshot"))
    # 之后阵亡的 T 不再是威胁（击杀信息全队可见）
    alive_end = set(int(s) for s in part[(part["tick"] == part["tick"].max()) & (part["team_num"] == T_TEAM)
                                         & part["is_alive"].astype(bool)]["steamid"])
    return [th for th in found.values() if th.enemy in alive_end]


def _weight(dist: float, cfg: dict) -> float:
    p = cfg["research"]["threat"]
    near, far = float(p["full_weight_distance"]), float(p["zero_weight_distance"])
    return 1.0 if dist <= near else max(0.0, 1.0 - (dist - near) / (far - near))


def danger_at(point: tuple[float, float, float], threats: list[Threat], checker, smokes: np.ndarray, cfg: dict) -> float:
    """能看到 point 的威胁的加权个数。smokes：生效中的烟的中心（N×3，已加上中心高度）。"""
    p = cfg["research"]["threat"]
    eye = float(p["eye_height"])
    radius = float(cfg["visibility"]["smoke_radius"])
    target = np.array([point[0], point[1], point[2] + eye])
    total = 0.0
    for th in threats:
        source = np.array([th.x, th.y, th.z + eye])
        w = _weight(float(np.linalg.norm(source - target)), cfg)
        if w <= 0:
            continue
        blocked = not checker.is_visible(tuple(source), tuple(target)) or (
            len(smokes) and (_segment_distances(source, target, smokes) <= radius).any())
        total += w * float(p.get("hidden_weight", 0.0)) if blocked else w  # 看不到的近处威胁（拐角后）只算一部分
    return total


def active_smoke_rows(arch: MatchArchive, tick: int, cfg: dict) -> pd.DataFrame:
    """这一刻生效中的烟（道具表的行，含扔烟的人 thrower 与阵营 thrower_team_num）。"""
    g = arch.grenades
    if g.empty:
        return g
    v = cfg["visibility"]
    lo, hi = (round(x * arch.rate) for x in v["smoke_active_seconds"])
    sm = g[(g["type"] == "smokegrenade") & (tick - g["tick"] >= lo) & (tick - g["tick"] <= hi)]
    return sm[sm["end_tick"].isna() | (sm["end_tick"] >= tick)] if "end_tick" in sm else sm


def active_smokes(arch: MatchArchive, tick: int, cfg: dict) -> np.ndarray:
    sm = active_smoke_rows(arch, tick, cfg)
    if sm.empty:
        return np.zeros((0, 3))
    return sm[["x", "y", "z"]].to_numpy(dtype=float) + [0.0, 0.0, float(cfg["visibility"]["smoke_center_height"])]


def window_smokes(arch: MatchArchive, start: int, stop: int, cfg: dict) -> np.ndarray:
    """观察期内生效过的烟（交战结束时或观察结束时生效）：起爆要 1 秒才展开，只看交战结束那一刻会漏掉刚扔的烟。"""
    both = np.vstack([active_smokes(arch, start, cfg), active_smokes(arch, stop, cfg)])
    return np.unique(both.round(1), axis=0) if len(both) else both


def contact_threats(events: pd.DataFrame, start: int, end: int) -> list[Threat]:
    """交战中被他打死的 T 的位置：接火点，提示那个方向还有人。"""
    if events.empty:
        return []
    kills = events[(events["kind"] == "kill") & (events["tick"] >= start) & (events["tick"] <= end) & events["enemy_x"].notna()]
    return [Threat(None, float(r.enemy_x), float(r.enemy_y), float(r.enemy_z if pd.notna(r.enemy_z) else 0.0), int(r.tick), "contact")
            for r in kills.itertuples()]


def sources_at(point: tuple[float, float, float], threats: list[Threat], checker, smokes: np.ndarray, cfg: dict,
               pot: np.ndarray | None = None, potential_ctx: dict | None = None) -> list[tuple[float, float, float]]:
    """他周围的威胁来源 (x, y, 权重)：已知威胁与可能威胁（有可能 T 的地面格子），按距离加权，**不要求此刻能直接看到**
    ——架枪架的是敌人会绕出来的拐角、门口；被烟挡住的不算（烟后的敌人出不来）。第 7 版用来判断他的视线是不是朝着
    这些方向（被动寻求对枪）。checker 不再使用，保留参数以便调用方不变。"""
    p = cfg["research"]["threat"]
    eye = float(p["eye_height"])
    radius = float(cfg["visibility"]["smoke_radius"])
    target = np.array([point[0], point[1], point[2] + eye])
    out = []
    for th in threats:
        source = np.array([th.x, th.y, th.z + eye])
        w = _weight(float(np.linalg.norm(source - target)), cfg)
        if w <= 0:
            continue
        if len(smokes) and (_segment_distances(source, target, smokes) <= radius).any():
            continue
        out.append((th.x, th.y, w))
    if pot is not None and potential_ctx is not None:
        from cs2coach.research.potential import smoke_blocked

        grid = potential_ctx["grid"]
        idx = np.nonzero(pot > 0)[0]
        if len(idx):
            idx = idx[~smoke_blocked(point, grid.centers[idx], smokes, radius, eye)]
            for j in idx:
                d = math.dist(point[:2], grid.centers[j][:2])
                w = _weight(d, cfg) * float(pot[j]) * float(p["potential_weight"])
                if w > 0:
                    out.append((float(grid.centers[j][0]), float(grid.centers[j][1]), w))
    return out


def window_danger(arch: MatchArchive, events: pd.DataFrame, p0: tuple, p1: tuple, start: int, stop: int, checker,
                  cfg: dict, potential_ctx: dict | None = None, detail: dict | None = None) -> tuple[float, float, int, list[Threat]]:
    """交战结束（start）与观察结束（stop）两个位置的危险度，用同一组威胁：从 start 前 memory 秒到 stop 之间 CT 方知道的。
    potential_ctx（可选）：{"prior", "grid", "vis", "direction", "match", "t_start"}，加上可能威胁（research/potential.py），
    按交战结束那一刻算（同一组可能威胁比较起点和终点）。"""
    memory = round(cfg["research"]["threat"]["memory_seconds"] * arch.rate)
    threats = known_threats(arch, start - memory, stop, cfg) + contact_threats(events, start - memory, stop)
    smokes = window_smokes(arch, start, stop, cfg)
    d0, d1 = danger_at(p0, threats, checker, smokes, cfg), danger_at(p1, threats, checker, smokes, cfg)
    pot = potential_at(arch, threats, start, cfg, potential_ctx)
    if pot is not None:
        from cs2coach.research.potential import potential_danger

        d0 += potential_danger(p0, pot, potential_ctx["grid"], potential_ctx["vis"], cfg, smokes)
        d1 += potential_danger(p1, pot, potential_ctx["grid"], potential_ctx["vis"], cfg, smokes)
    if detail is not None:  # 第 7 版判断视线朝向用；烟雾补丁用
        detail["sources"] = sources_at(p1, threats, checker, smokes, cfg, pot, potential_ctx)
        detail["smoke_share"] = smoke_share(p1, threats, smokes, cfg, pot, potential_ctx)
        if potential_ctx is not None and potential_ctx.get("adj") is not None:
            detail["facing"] = _corner_facing_fn(threats, pot, potential_ctx, cfg)
    return d0, d1, len(threats), threats


def pre_danger(arch: MatchArchive, events: pd.DataFrame, point: tuple, start: int, checker, cfg: dict) -> tuple[float, int]:
    """行为之前的状态（分层用）：交战结束那一刻，只用这之前 memory 秒内 CT 方知道的威胁，
    返回 (这个位置的危险度, 知道位置的活着的 T 人数)。不含可能威胁、不看观察期内才知道的信息。"""
    memory = round(cfg["research"]["threat"]["memory_seconds"] * arch.rate)
    threats = known_threats(arch, start - memory, start, cfg) + contact_threats(events, start - memory, start)
    smokes = active_smokes(arch, start, cfg)
    alive = {t.enemy for t in threats if t.source != "contact" and t.enemy is not None}
    return danger_at(point, threats, checker, smokes, cfg), len(alive)


def potential_at(arch: MatchArchive, threats: list[Threat], tick: int, cfg: dict, ctx: dict | None):
    """tick 时的可能威胁（每个格子可能有几名 T）：历史分布（同方向、同阶段、不含本场）× 本队最近没看过 ×
    缩放到位置不明的 T 人数。没有配置或不可用时为 None。"""
    from cs2coach.research.potential import cleared, potential

    p = cfg["research"]["threat"]
    if ctx is None or not p.get("use_potential"):
        return None
    t = ctx["t_start"] + (tick - ctx["start_tick"]) / arch.rate
    prior = ctx["prior"].expected(ctx["direction"], t, ctx["match"])
    if prior is None:
        return None
    clear = round(p["clear_seconds"] * arch.rate)
    part = arch.ticks[(arch.ticks["tick"] >= tick - clear) & (arch.ticks["tick"] <= tick)]
    step = max(1, round(arch.rate / 2))
    ct = part[(part["team_num"] == CT_TEAM) & part["is_alive"].astype(bool) & (part["tick"] % step < 8)]
    seen = cleared(ctx["grid"], ctx["vis"], list(zip(ct["X"], ct["Y"], ct["yaw"])), float(p.get("clear_fov", 360)))
    now = part[part["tick"] == part["tick"].max()]
    alive_t = int(((now["team_num"] == T_TEAM) & now["is_alive"].astype(bool)).sum())
    known = len({th.enemy for th in threats if th.enemy is not None})
    return potential(prior, seen, alive_t - known)


def danger_grid(threats: list[Threat], checker, smokes: np.ndarray, cfg: dict, points: np.ndarray) -> np.ndarray:
    """回放页的这一回合威胁热力图：在给定的地面点（N×3）上逐个计算危险度。"""
    return np.array([danger_at((float(x), float(y), float(z)), threats, checker, smokes, cfg) for x, y, z in points])


def _corner_facing_fn(threats: list[Threat], pot, ctx: dict, cfg: dict):
    """返回 facing(track, t0, t1)：准星对着敌人出现点（拐角、门口）的程度。威胁分量：已知的每名 T（含接火点）记 1，
    可能威胁按 potential_weight × 可能人数。"""
    from cs2coach.research.potential import corner_facing

    grid = ctx["grid"]
    mass: dict[int, float] = {}
    for th in threats:
        k = grid.nearest(th.x, th.y)
        mass[k] = mass.get(k, 0.0) + 1.0
    if pot is not None:
        w = float(cfg["research"]["threat"]["potential_weight"])
        for j in np.nonzero(pot > 0)[0]:
            mass[int(j)] = mass.get(int(j), 0.0) + w * float(pot[j])
    s, q = cfg["research"]["stance"], cfg["research"]["threat"]
    return lambda track, t0, t1: corner_facing(track, t0, t1, mass, grid, ctx["vis"], ctx["adj"],
                                               float(s["facing_max_angle"]), float(q["corner_max_path"]))


def smoke_share(point: tuple[float, float, float], threats: list[Threat], smokes: np.ndarray, cfg: dict,
                pot: np.ndarray | None = None, potential_ctx: dict | None = None) -> float:
    """他和周围威胁（已知的每名 T 记 1、可能威胁按 potential_weight × 可能人数，按距离加权，不管墙）之间的直线，
    有多少比例（按权重）穿过生效中的烟。烟雾补丁：原地不动且大部分威胁被烟隔开，算保守（gold-1 g35、g46）。"""
    p = cfg["research"]["threat"]
    eye = float(p["eye_height"])
    radius = float(cfg["visibility"]["smoke_radius"])
    pts, weights = [], []
    for th in threats:
        w = _weight(math.dist(point[:2], (th.x, th.y)), cfg)
        if w > 0:
            pts.append((th.x, th.y, th.z))
            weights.append(w)
    if pot is not None and potential_ctx is not None:
        grid = potential_ctx["grid"]
        for j in np.nonzero(pot > 0)[0]:
            c = grid.centers[j]
            w = _weight(math.dist(point[:2], c[:2]), cfg) * float(pot[j]) * float(p["potential_weight"])
            if w > 0:
                pts.append(tuple(c))
                weights.append(w)
    total = float(sum(weights))
    if total <= 0 or not len(smokes):
        return 0.0
    from cs2coach.research.potential import smoke_blocked

    blocked = smoke_blocked(point, np.array(pts, dtype=float), smokes, radius, eye)
    return round(float(np.array(weights)[blocked].sum() / total), 3)
