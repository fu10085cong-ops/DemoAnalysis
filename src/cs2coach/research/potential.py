"""R03 第 6 版补丁：可能威胁（docs/tasks/research/R03-ai-post-fight-reposition.md "可能威胁"）。

产品负责人定（2026-10-21）：
- A 为基础：同一回合阶段（冻结结束后第几秒）、同一首杀方向下，历史上 T 通常在哪（其他比赛统计，不含本场）——职业选手的常识
- C 修正：本队最近 clear_seconds 秒内有人能看到的格子视为已清，可能威胁归零
- 已知位置的 T 不重复计算：可能威胁总量按"还剩几名位置不明的 T"缩放
- 某个位置的可能危险度 = potential_weight × Σ 能看到这里的格子的可能 T 数 × 距离权重
地面按 cell 单位划分格子（导航网格覆盖的格子），格子之间的视线预先算一次并缓存。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from cs2coach.research.danger import first_kills

T_TEAM, CT_TEAM = 2, 3


@dataclass
class FloorGrid:
    """导航网格覆盖的地面格子：中心坐标与地面高度（上下两层重叠处取上层）。"""

    x0: float
    y0: float
    cell: float
    shape: tuple[int, int]
    index: np.ndarray        # shape 大小，格子编号，不可走为 -1
    centers: np.ndarray      # N×3

    def locate(self, x, y) -> np.ndarray:
        col = np.floor((np.asarray(x, dtype=float) - self.x0) / self.cell).astype(int)
        row = np.floor((np.asarray(y, dtype=float) - self.y0) / self.cell).astype(int)
        ok = (col >= 0) & (col < self.shape[1]) & (row >= 0) & (row < self.shape[0])
        out = np.full(np.shape(col), -1)
        out[ok] = self.index[row[ok], col[ok]]
        return out

    def nearest(self, x: float, y: float) -> int:
        """所在格子；不在可走格子上（站在箱子边缘等）时取最近的格子。"""
        i = int(self.locate([x], [y])[0])
        if i >= 0:
            return i
        return int(np.argmin((self.centers[:, 0] - x) ** 2 + (self.centers[:, 1] - y) ** 2))


def floor_grid(polygons: list[list[tuple[float, float, float]]], cell: float) -> FloorGrid:
    """把导航区域多边形（世界坐标）落到 cell 大小的格子上（格子中心在多边形内即算可走）。"""
    xs = [c[0] for poly in polygons for c in poly]
    ys = [c[1] for poly in polygons for c in poly]
    x0, y0 = min(xs) - cell, min(ys) - cell
    shape = (int((max(ys) - y0) // cell) + 2, int((max(xs) - x0) // cell) + 2)
    height = np.full(shape, -np.inf)
    for poly in polygons:
        px, py = np.array([c[0] for c in poly]), np.array([c[1] for c in poly])
        c0, c1 = int((px.min() - x0) // cell), int((px.max() - x0) // cell)
        r0, r1 = int((py.min() - y0) // cell), int((py.max() - y0) // cell)
        gx, gy = np.meshgrid(x0 + (np.arange(c0, c1 + 1) + 0.5) * cell, y0 + (np.arange(r0, r1 + 1) + 0.5) * cell)
        inside = np.zeros(gx.shape, dtype=bool)
        for i in range(len(px)):
            xa, ya, xb, yb = px[i], py[i], px[i - 1], py[i - 1]
            inside ^= ((ya > gy) != (yb > gy)) & (gx < (xb - xa) * (gy - ya) / np.where(yb == ya, 1e-9, yb - ya) + xa)
        z = float(np.mean([c[2] for c in poly]))
        block = height[r0:r1 + 1, c0:c1 + 1]
        block[inside] = np.maximum(block[inside], z)
    index = np.full(shape, -1)
    rows, cols = np.nonzero(np.isfinite(height))
    index[rows, cols] = np.arange(len(rows))
    centers = np.column_stack([x0 + (cols + 0.5) * cell, y0 + (rows + 0.5) * cell, height[rows, cols]])
    return FloorGrid(x0=x0, y0=y0, cell=cell, shape=shape, index=index, centers=centers)


def nav_polygons(nav_path: Path) -> list[list[tuple[float, float, float]]]:
    from awpy.map_control import NavMesh

    nav = NavMesh(str(nav_path))
    return [list(nav.area(int(a))["corners"]) for a in nav.areas["area_id"]]


def visibility_matrix(grid: FloorGrid, checker, eye: float, max_distance: float) -> np.ndarray:
    """格子之间能否互相看到（双方眼睛高度），超过 max_distance 记为看不到。对称矩阵。"""
    n = len(grid.centers)
    vis = np.zeros((n, n), dtype=bool)
    pts = grid.centers + [0.0, 0.0, eye]
    for i in range(n):
        vis[i, i] = True
        d2 = ((pts[i + 1:, 0] - pts[i, 0]) ** 2 + (pts[i + 1:, 1] - pts[i, 1]) ** 2)
        for j in np.nonzero(d2 <= max_distance ** 2)[0] + i + 1:
            if checker.is_visible(tuple(pts[i]), tuple(pts[j])):
                vis[i, j] = vis[j, i] = True
    return vis


@dataclass
class TPrior:
    """同一首杀方向、同一回合阶段（bucket 秒一档）里 T 在各格子出现的次数，按比赛分开存，查询时留一场。"""

    bucket: float
    n_cells: int
    counts: dict = field(default_factory=dict)   # (direction, bucket, match) -> np.ndarray[n_cells]（T 出现的样本数）
    moments: dict = field(default_factory=dict)  # (direction, bucket, match) -> 样本时刻数（回合 × 采样时刻）

    sigma: float = 0.0  # 查询时按时间高斯平滑（秒）；0 = 只用 t 所在的那一档

    def expected(self, direction: str | None, t: float, exclude: str | None) -> np.ndarray | None:
        """每个格子平均有几名 T（同方向、t 附近的阶段、不含 exclude 那场）。sigma > 0 时相邻各档按离 t 的远近加权。"""
        if direction is None:
            return None
        if self.sigma > 0:
            lo, hi = int((t - 3 * self.sigma) // self.bucket), int((t + 3 * self.sigma) // self.bucket)
            weights = {b: math.exp(-(((b + 0.5) * self.bucket - t) ** 2) / (2 * self.sigma ** 2)) for b in range(lo, hi + 1)}
        else:
            weights = {int(t // self.bucket): 1.0}
        total, moments = np.zeros(self.n_cells), 0.0
        for (d, bb, match), arr in self.counts.items():
            w = weights.get(bb)
            if d == direction and w and match != exclude:
                total += w * arr
                moments += w * self.moments[(d, bb, match)]
        return total / moments if moments else None


def build_prior(positions: pd.DataFrame, kills: pd.DataFrame, grid: FloorGrid, cfg: dict, map_name: str) -> TPrior:
    """positions：每 sample 秒一行的存活玩家（demo_id、match_id、round、t、team_num、X、Y）；kills 用来确定首杀方向。"""
    p = cfg["research"]["threat"]
    prior = TPrior(bucket=float(p["potential_bucket_seconds"]), n_cells=len(grid.centers),
                   sigma=float(p.get("potential_time_sigma", 0.0)))
    first = first_kills(kills, cfg, map_name)[["demo_id", "round", "direction"]]
    first = first[first["direction"].notna()]
    pos = positions.merge(first, on=["demo_id", "round"])
    pos = pos.assign(bucket=(pos["t"] // prior.bucket).astype(int))
    moments = pos.drop_duplicates(["demo_id", "round", "t"]).groupby(["direction", "bucket", "match_id"]).size()
    t_pos = pos[pos["team_num"] == T_TEAM]
    t_pos = t_pos.assign(cell=grid.locate(t_pos["X"], t_pos["Y"]))
    t_pos = t_pos[t_pos["cell"] >= 0]
    for (d, b, match), g in t_pos.groupby(["direction", "bucket", "match_id"]):
        arr = np.zeros(prior.n_cells)
        np.add.at(arr, g["cell"].to_numpy(), 1.0)
        prior.counts[(d, int(b), match)] = arr
    for (d, b, match), n in moments.items():
        prior.counts.setdefault((d, int(b), match), np.zeros(prior.n_cells))
        prior.moments[(d, int(b), match)] = int(n)
    return prior


def cleared(grid: FloorGrid, vis: np.ndarray, ct_points: list[tuple], fov: float = 360.0) -> np.ndarray:
    """本队看过的格子（已清）：从 CT 位置能看到、且在他视野内（朝向 yaw 左右各 fov/2 度）的格子。
    ct_points：(x, y) 或 (x, y, yaw)；没有朝向时按 360 度。"""
    out = np.zeros(len(grid.centers), dtype=bool)
    for point in ct_points:
        x, y = point[0], point[1]
        row = vis[grid.nearest(x, y)]
        if len(point) > 2 and fov < 360 and point[2] == point[2]:
            ang = np.degrees(np.arctan2(grid.centers[:, 1] - y, grid.centers[:, 0] - x))
            diff = np.abs((ang - float(point[2]) + 180) % 360 - 180)
            near = np.hypot(grid.centers[:, 0] - x, grid.centers[:, 1] - y) <= grid.cell  # 脚下这一格总算看过
            row = row & ((diff <= fov / 2) | near)
        out |= row
    return out


def potential(prior_cells: np.ndarray | None, cleared_cells: np.ndarray, unknown_t: int) -> np.ndarray | None:
    """可能威胁：没清过的格子里的历史 T 分布，总量缩放到位置不明的 T 人数。"""
    if prior_cells is None:
        return None
    mass = prior_cells.sum()
    if mass <= 0 or unknown_t <= 0:
        return np.zeros_like(prior_cells)
    return np.where(cleared_cells, 0.0, prior_cells) * (unknown_t / mass)


def smoke_blocked(point: tuple[float, float, float], targets: np.ndarray, smokes: np.ndarray, radius: float, eye: float) -> np.ndarray:
    """从 point 到每个目标格子（N×3，地面高度）的视线是否穿过生效中的烟（烟心 M×3，已含中心高度）。"""
    blocked = np.zeros(len(targets), dtype=bool)
    if smokes is None or not len(smokes) or not len(targets):
        return blocked
    a = np.array([point[0], point[1], point[2] + eye])
    b = targets + [0.0, 0.0, eye]
    ab = b - a
    length = np.maximum((ab ** 2).sum(1), 1e-9)
    for c in smokes:
        u = np.clip(((c - a) * ab).sum(1) / length, 0, 1)
        blocked |= np.linalg.norm(a + u[:, None] * ab - c, axis=1) <= radius
    return blocked


def potential_danger(point: tuple[float, float, float], pot: np.ndarray | None, grid: FloorGrid, vis: np.ndarray,
                     cfg: dict, smokes: np.ndarray | None = None) -> float:
    """potential_weight × Σ 能看到 point 的格子的可能 T 数 × 距离权重（与已知威胁同一条距离曲线）。
    视线穿过生效中的烟的格子不算（烟后的 T 出不来、也看不到这里；产品负责人看 w07-check c8 后提出）。"""
    if pot is None:
        return 0.0
    p = cfg["research"]["threat"]
    me = grid.nearest(point[0], point[1])
    seen = vis[me] & (pot > 0)
    if seen.any() and smokes is not None and len(smokes):
        idx = np.nonzero(seen)[0]
        seen[idx[smoke_blocked(point, grid.centers[idx], smokes, float(cfg["visibility"]["smoke_radius"]),
                               float(p["eye_height"]))]] = False
    if not seen.any():
        return 0.0
    d = np.hypot(grid.centers[seen, 0] - point[0], grid.centers[seen, 1] - point[1])
    near, far = float(p["full_weight_distance"]), float(p["zero_weight_distance"])
    w = np.where(d <= near, 1.0, np.clip(1 - (d - near) / (far - near), 0, None))
    return float(p["potential_weight"]) * float((pot[seen] * w).sum())



def adjacency(grid: FloorGrid, max_step: float) -> list[list[tuple[int, float]]]:
    """敌人走向格子 i 的来路：out[i] = [(j, 距离)]，表示敌人能从相邻格子 j（上下左右与斜向）走到 i——往上最多爬
    max_step（台阶、坡），往下跳不限高度。emergence() 从他所在格子沿来路反向搜索。"""
    rows, cols = grid.shape
    out: list[list[tuple[int, float]]] = [[] for _ in range(len(grid.centers))]
    for r in range(rows):
        for c in range(cols):
            i = grid.index[r, c]
            if i < 0:
                continue
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if (dr or dc) and 0 <= rr < rows and 0 <= cc < cols and grid.index[rr, cc] >= 0:
                        j = grid.index[rr, cc]
                        if grid.centers[i, 2] - grid.centers[j, 2] <= max_step:  # 从 j 到 i：爬高不超过 max_step，跳下不限
                            out[i].append((int(j), grid.cell * (1.4142 if dr and dc else 1.0)))
    return out


def emergence(start: int, vis: np.ndarray, adj: list[list[tuple[int, float]]], mass: dict[int, float],
              max_path: float, centers: np.ndarray | None = None) -> dict[int, float]:
    """敌人会从哪里出现：从他所在格子 start 沿通道找到每个威胁格子的最短路线；他看得到的威胁，出现点是威胁本身；
    看不到的，出现点是路线上（从威胁往他走）第一个进入他视野的格子。威胁分量按路程线性递减（max_path 以外不算），
    累加到出现点上。返回 {出现点格子: 分量}。"""
    import heapq

    dist = {start: 0.0}
    parent = {start: -1}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf) or d > max_path:
            continue
        for v, step in adj[u]:
            nd = d + step
            if nd < dist.get(v, math.inf):
                dist[v], parent[v] = nd, u
                heapq.heappush(heap, (nd, v))
    out: dict[int, float] = {}
    for cell, weight in mass.items():
        if weight <= 0:
            continue
        if vis[start, cell]:  # 看得到的威胁：出现点就是它自己（路程按直线算，含从高处窗口看下去的情况）
            straight = float(np.hypot(*(centers[cell, :2] - centers[start, :2]))) if centers is not None else dist.get(cell, 0.0)
            out[cell] = out.get(cell, 0.0) + weight * max(0.0, 1.0 - straight / max_path)
            continue
        if cell not in dist:
            continue
        w = weight * max(0.0, 1.0 - dist[cell] / max_path)
        if w <= 0:
            continue
        node = cell
        while node != start and not vis[start, node]:  # 从威胁沿路线往他走，直到第一个他看得到的格子
            node = parent[node]
        out[node] = out.get(node, 0.0) + w
    return out


def corner_facing(track: pd.DataFrame, t0: float, t1: float, mass: dict[int, float], grid: FloorGrid, vis: np.ndarray,
                  adj: list, max_angle: float, max_path: float) -> float:
    """观察期内（t0 到 t1，相对首杀的秒数）他的准星对着敌人出现点的程度：每个时刻按他所在格子算出现点（emergence），
    出现点分量有多少落在视线左右 max_angle 度以内，再对各时刻取平均。没有威胁时为 0。"""
    part = track[(track["t"] >= t0 - 1e-9) & (track["t"] <= t1 + 1e-9) & track["alive"].astype(bool) & track["yaw"].notna()]
    if part.empty or not mass:
        return 0.0
    cache: dict[int, dict[int, float]] = {}
    shares = []
    for x, y, yaw in zip(part["X"], part["Y"], part["yaw"]):
        k = grid.nearest(float(x), float(y))
        if k not in cache:
            cache[k] = emergence(k, vis, adj, mass, max_path, grid.centers)
        em = cache[k]
        total = sum(em.values())
        if total <= 0:
            shares.append(0.0)
            continue
        cells = np.array(list(em.keys()))
        weights = np.array(list(em.values()))
        ang = np.degrees(np.arctan2(grid.centers[cells, 1] - y, grid.centers[cells, 0] - x))
        here = np.hypot(grid.centers[cells, 0] - x, grid.centers[cells, 1] - y) <= grid.cell  # 脚下的格子不算方向
        front = (np.abs((ang - float(yaw) + 180) % 360 - 180) <= max_angle) & ~here
        shares.append(float(weights[front].sum() / weights[~here].sum()) if weights[~here].sum() > 0 else 0.0)
    return round(float(np.mean(shares)), 3)
