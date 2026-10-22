"""研究复核的 2D 回放页：在 Mirage 小地图上动画回放每个行为窗口（R02）。

旧 demo 在新版 CS2 里无法播放（版本不兼容），改用 demo 数据生成不依赖游戏版本的回放页。
用法：python scripts/research_replay.py r02-1 [--jobs 2]
输出：data/research/review/<名>/replay.html（单个文件，直接用浏览器打开）。
页面是盲审：不显示程序标签；答案保存在浏览器里，"导出结果"下载 JSON，再用
python scripts/research_review.py import <名> <JSON 路径> 写回 review.toml。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tomllib
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cs2coach.config import load_config  # noqa: E402
from cs2coach.parallel import default_jobs, map_ordered  # noqa: E402
from cs2coach.parsing.loader import resolve_demo  # noqa: E402
from cs2coach.parsing.match import MatchData, parse_match  # noqa: E402
from cs2coach.research.paths import research_dir  # noqa: E402

AWPY = Path.home() / ".awpy" / "2000930"
GRENADE_TYPES = {"smokegrenade": "smoke", "flashbang": "flash", "hegrenade": "he", "inferno": "fire", "decoy": "decoy"}


def extract(match: MatchData, items: list[dict], cfg: dict) -> dict[str, dict]:
    """每个窗口：T 阵亡前 lead 秒到之后 window + 1 秒，按 fps 抽帧的全部玩家位置与朝向，以及击杀、开枪、投掷物。"""
    rate = match.meta.tick_rate
    review = cfg["research"]["review"]
    step = max(1, round(rate / review["replay_fps"]))
    names = {int(sid): player.name.strip() for sid, player in match.players.items()}
    ticks = match.ticks
    out = {}
    for item in items:
        t0 = int(item["start_tick"])
        lo = t0 - round(review["lead_seconds"] * rate)
        until = max(cfg["research"]["window_seconds"] + review["replay_tail_seconds"], item.get("replay_until_s", 0))
        hi = t0 + round(until * rate)
        part = ticks[(ticks["tick"] >= lo) & (ticks["tick"] <= hi) & ticks["team_num"].isin((2, 3))]
        if not getattr(match, "sampled", False):  # 存档已经按固定间隔抽过帧
            part = part[(part["tick"] - lo) % step == 0]
        frames = []
        for tick, group in part.groupby("tick", sort=True):
            frames.append([round((int(tick) - t0) / rate, 3), [
                [str(int(r.steamid)), round(float(r.X), 1), round(float(r.Y), 1),
                 round(float(r.yaw), 1) if pd.notna(r.yaw) else 0.0, int(bool(r.is_alive)), int(r.team_num)]
                for r in group.itertuples() if pd.notna(r.X) and pd.notna(r.Y)]])
        k = match.kills
        kills = k[(k["tick"] >= lo) & (k["tick"] <= hi)]
        target = int(item["player_id"])
        shots = match.shots
        shots = shots[(shots["tick"] >= lo) & (shots["tick"] <= hi) & (shots["steamid"] == target)
                      & shots["is_gun"]] if not shots.empty else shots
        nades = match.grenades
        nades = nades[(nades["tick"] >= lo) & (nades["tick"] <= hi)] if not nades.empty else nades
        out[item["id"]] = {
            "rate": rate, "target": str(target),
            "players": {str(sid): names.get(sid, str(sid)) for sid in {int(r[0]) for f in frames for r in f[1]}},
            "frames": frames,
            "kills": [[round((int(r.tick) - t0) / rate, 3), str(int(r.attacker)) if pd.notna(r.attacker) else "",
                       str(int(r.victim)) if pd.notna(r.victim) else ""] for r in kills.itertuples()],
            "shots": [round((int(t) - t0) / rate, 3) for t in shots["tick"]],
            "grenades": [[round((int(r.tick) - t0) / rate, 3), GRENADE_TYPES.get(r.type, r.type),
                          round(float(r.x), 1), round(float(r.y), 1)] for r in nades.itertuples()
                         if pd.notna(r.x) and pd.notna(r.y)],
        }
    return out


def _png(rgba) -> bytes:
    """最简 PNG 编码（RGBA，8 位），不依赖图像库。"""
    import struct
    import zlib

    height, width = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))
    chunk = lambda kind, body: struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def floor_mask(map_cfg: dict, size: int = 1024):
    """小地图像素上的可走区域：把导航网格的每个区域多边形画到 size×size 的格子上（偶奇规则）。"""
    return floor_map(map_cfg, size)[0]


def floor_map(map_cfg: dict, size: int = 1024):
    """(可走区域, 地面高度)：高度取覆盖该像素的导航区域中最高的一层（上下两层重叠处显示上层）。"""
    import numpy as np
    from awpy.map_control import NavMesh

    nav = NavMesh(str(AWPY / "navs" / "de_mirage.nav"))
    mask = np.zeros((size, size), dtype=bool)
    height = np.full((size, size), -np.inf)
    for area_id in nav.areas["area_id"]:
        corners = nav.area(int(area_id))["corners"]
        xs = np.array([(c[0] - map_cfg["pos_x"]) / map_cfg["scale"] for c in corners])
        ys = np.array([(map_cfg["pos_y"] - c[1]) / map_cfg["scale"] for c in corners])
        x0, x1 = max(0, int(np.floor(xs.min()))), min(size - 1, int(np.ceil(xs.max())))
        y0, y1 = max(0, int(np.floor(ys.min()))), min(size - 1, int(np.ceil(ys.max())))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        inside = np.zeros(gx.shape, dtype=bool)
        for i in range(len(xs)):
            xa, ya, xb, yb = xs[i], ys[i], xs[i - 1], ys[i - 1]
            cross = ((ya > gy) != (yb > gy)) & (gx < (xb - xa) * (gy - ya) / np.where(yb == ya, 1e-9, yb - ya) + xa)
            inside ^= cross
        mask[y0:y1 + 1, x0:x1 + 1] |= inside
        z = float(np.mean([c[2] for c in corners]))
        block = height[y0:y1 + 1, x0:x1 + 1]
        block[inside] = np.maximum(block[inside], z)
    return mask, height


def _heat_full(grid, maps, map_cfg: dict, mask) -> bytes:
    """把危险度格子（世界坐标）双线性插值到小地图的每个像素，只画在可走区域内。"""
    import numpy as np

    size = mask.shape[0]
    px = np.arange(size) + 0.5
    wx = map_cfg["pos_x"] + px * map_cfg["scale"]                        # 列 → 世界 X
    wy = map_cfg["pos_y"] - px * map_cfg["scale"]                        # 行 → 世界 Y
    fx = (wx - maps.x0) / maps.cell - 0.5
    fy = (wy - maps.y0) / maps.cell - 0.5
    filled = np.nan_to_num(grid, nan=1.0)
    rows, cols = filled.shape
    c0 = np.clip(np.floor(fx).astype(int), 0, cols - 2)
    r0 = np.clip(np.floor(fy).astype(int), 0, rows - 2)
    tx, ty = np.clip(fx - c0, 0, 1), np.clip(fy - r0, 0, 1)
    R0, C0 = np.meshgrid(r0, c0, indexing="ij")
    TY, TX = np.meshgrid(ty, tx, indexing="ij")
    value = (filled[R0, C0] * (1 - TX) * (1 - TY) + filled[R0, C0 + 1] * TX * (1 - TY)
             + filled[R0 + 1, C0] * (1 - TX) * TY + filled[R0 + 1, C0 + 1] * TX * TY)
    t = np.clip(np.log(value) / np.log(3), -1, 1)
    mid, hot, cold = np.array([255, 232, 150]), np.array([235, 55, 55]), np.array([55, 140, 255])
    w = np.abs(t)[..., None]
    rgb = np.where(t[..., None] >= 0, mid * (1 - w) + hot * w, mid * (1 - w) + cold * w)
    alpha = np.where(mask, 110 + 110 * np.abs(t), 0)
    return _png(np.ascontiguousarray(np.dstack([rgb, alpha]).astype("uint8")))


def _archive_dir(root: Path, archive: str) -> Path:
    """按归档条目中的原始相对路径定位 stance 包。"""
    parts = Path(archive).parts
    return research_dir("stance", parts[1], root) / Path(*parts[2:])


def danger_layers(root: Path, data: dict, cfg: dict, map_cfg: dict) -> dict[str, dict]:
    """research_stance 的复盘包：每个窗口叠加程序判断那一回合时用的危险度地图（首杀方向，不含本场）。"""
    from cs2coach.research.danger import build_danger

    source = data.get("source", "")
    items = data["items"]
    if not source.startswith("stance/") or not all("archive" in i for i in items):
        return {}
    windows = pd.read_csv(research_dir("stance", source.removeprefix("stance/"), root) / "windows.csv", dtype={"player_id": str})
    direction = {(r.demo_id, int(r.round_id), str(r.player_id)): r.direction for r in windows.itertuples()}
    extract = research_dir("stance", Path(items[0]["archive"]).parts[1], root)
    read = lambda sub: pd.concat([pd.read_parquet(f) for f in sorted((extract / "raw" / sub).glob("*.parquet"))], ignore_index=True)  # noqa: E731
    maps = build_danger(read("positions"), read("kills"), cfg, "de_mirage")
    # 每个首杀方向一张（全部比赛）：判定某场时程序去掉了这一场，189 场里少一场，图上看不出差别；逐条画会让页面大几十 MB
    mask = floor_mask(map_cfg)
    images = {}
    for d in ("A", "B", "Mid"):
        grid = maps.ratio_grid(d)
        if grid is not None:
            images[d] = base64.b64encode(_heat_full(grid, maps, map_cfg, mask)).decode()
    out = {"_images": images}
    for item in items:
        d = direction.get((item["demo_id"], int(item["round_id"]), str(item["player_id"])))
        out[item["id"]] = {"direction": d if isinstance(d, str) else None}
    threat_layers(root, items, windows, extract, cfg, map_cfg, out)
    return out


def threat_layers(root: Path, items: list[dict], windows: pd.DataFrame, extract: Path, cfg: dict, map_cfg: dict,
                  out: dict, block: int = 8, size: int = 512) -> None:
    """每个窗口这一回合的威胁热力图与威胁位置（与判定程序同一组威胁：交战结束前 memory 秒到观察结束 CT 方知道的）。"""
    import numpy as np
    from awpy import VisibilityChecker

    from cs2coach.research.potential import potential_danger
    from cs2coach.research.threat import MatchArchive, contact_threats, danger_at, known_threats, potential_at, window_smokes

    if not all((_archive_dir(root, i["archive"]) / "sounds.parquet").exists() for i in items):
        return
    pot_model = None
    if cfg["research"]["threat"].get("use_potential"):
        from research_stance import potential_model

        pot_model = potential_model(extract, cfg)
    mask, height = floor_map(map_cfg)
    full = mask.shape[0]
    rows = cols = full // block
    centers = []
    for r in range(rows):
        for c in range(cols):
            sub = mask[r * block:(r + 1) * block, c * block:(c + 1) * block]
            if sub.any():
                z = height[r * block:(r + 1) * block, c * block:(c + 1) * block][sub].max()
                centers.append((r, c, map_cfg["pos_x"] + (c + 0.5) * block * map_cfg["scale"],
                                map_cfg["pos_y"] - (r + 0.5) * block * map_cfg["scale"], float(z)))
    checker = VisibilityChecker("de_mirage")
    small = mask[::full // size, ::full // size]
    memory = cfg["research"]["threat"]["memory_seconds"]
    archives: dict[str, MatchArchive] = {}
    events_cache: dict[str, pd.DataFrame] = {}
    for item in items:
        key = (item["demo_id"], int(item["round_id"]), str(item["player_id"]))
        row = windows[(windows["demo_id"] == key[0]) & (windows["round_id"] == key[1]) & (windows["player_id"] == key[2])]
        if row.empty:
            continue
        row = row.iloc[0]
        folder = _archive_dir(root, item["archive"])
        arch = archives.setdefault(item["archive"], MatchArchive(folder))
        stem = folder.name
        if stem not in events_cache:
            ev = pd.read_parquet(extract / "events" / f"{stem}.parquet")
            ev["player_id"] = ev["player_id"].astype(str)
            events_cache[stem] = ev
        ev = events_cache[stem]
        ev = ev[(ev["round_id"] == key[1]) & (ev["player_id"] == key[2])]
        rate = int(row["tick_rate"])
        start = int(row["first_kill_tick"]) + round(float(row["engagement_end_s"]) * rate)
        stop = int(row["observe_end_tick"]) if pd.notna(row["observe_end_tick"]) else start
        lo = start - round(memory * rate)
        threats = known_threats(arch, lo, stop, cfg) + contact_threats(ev, lo, stop)
        smokes = window_smokes(arch, start, stop, cfg)
        ctx = None
        if pot_model is not None:
            d = row.get("direction")
            ctx = {**pot_model, "direction": d if isinstance(d, str) else None, "match": item["match_id"],
                   "t_start": float(row["round_time_s"]), "start_tick": int(row["first_kill_tick"])}
        pot = potential_at(arch, threats, start, cfg, ctx)
        grid = np.full((rows, cols), np.nan)
        for r, c, x, y, z in centers:
            grid[r, c] = danger_at((x, y, z), threats, checker, smokes, cfg) if threats else 0.0
            if pot is not None:
                grid[r, c] += potential_danger((x, y, z), pot, ctx["grid"], ctx["vis"], cfg, smokes)
        out[item["id"]]["threat_png"] = base64.b64encode(_threat_heat(grid, small)).decode()
        out[item["id"]]["threats"] = [[th.source, round((th.x - map_cfg["pos_x"]) / map_cfg["scale"], 1),
                                       round((map_cfg["pos_y"] - th.y) / map_cfg["scale"], 1)] for th in threats]
        out[item["id"]]["live"] = _live_threats(arch, ev, item, rate, cfg, centers, checker, map_cfg, ctx)
    out["_cells"] = {"block": block, "rows": rows,
                     "cells": [[r, c, round(x, 1), round(y, 1), round(z, 1)] for r, c, x, y, z in centers]}
    if pot_model is not None:
        g = pot_model["grid"]
        out["_potential"] = {"centers": [[round(float(x), 1), round(float(y), 1), round(float(z), 1)] for x, y, z in g.centers],
                             "vis": base64.b64encode(np.packbits(pot_model["vis"])).decode(),
                             "map": [g.nearest(x, y) for _, _, x, y, _ in centers],
                             "weight": cfg["research"]["threat"]["potential_weight"]}
    floor = np.zeros((size, size, 4), dtype="uint8")
    floor[small] = 255
    out["_floor"] = base64.b64encode(_png(floor)).decode()
    q = cfg["research"]["threat"]
    out["_threat_cfg"] = {"near": q["full_weight_distance"], "far": q["zero_weight_distance"], "hidden": q["hidden_weight"],
                          "eye": q["eye_height"], "smoke_r": cfg["visibility"]["smoke_radius"],
                          "facing_angle": cfg["research"]["stance"]["facing_max_angle"]}


class _Span:
    """存档中某个时间段的切片（加快逐帧查询）。"""

    def __init__(self, arch, lo: int, hi: int):
        cut = lambda f: f[(f["tick"] >= lo) & (f["tick"] <= hi)] if not f.empty else f  # noqa: E731
        self.rate, self.bits = arch.rate, arch.bits
        self.ticks, self.sounds, self.shots, self.grenades = cut(arch.ticks), cut(arch.sounds), cut(arch.shots), arch.grenades


def _live_threats(arch, ev: pd.DataFrame, item: dict, rate: int, cfg: dict, centers: list, checker, map_cfg: dict,
                  ctx: dict | None = None) -> dict:
    """随时间变化的威胁：每 0.25 秒一帧，列出那一刻 CT 方知道的威胁（memory 秒内最新的）；每个威胁位置能看到哪些地面格子
    （位图，不含烟——烟在页面里按时间实时扣除）。"""
    import numpy as np

    from cs2coach.research.threat import contact_threats, known_threats, potential_at

    review = cfg["research"]["review"]
    memory = round(cfg["research"]["threat"]["memory_seconds"] * rate)
    eye = float(cfg["research"]["threat"]["eye_height"])
    t0 = int(item["start_tick"])
    lead, until = float(review["lead_seconds"]), float(item.get("replay_until_s", 10.0))
    span = _Span(arch, t0 - round(lead * rate) - memory, t0 + round(until * rate))
    points: dict[tuple, tuple[int, object]] = {}
    frames, pot_frames, corners = [], [], []
    target = int(item["player_id"])
    for tau in np.arange(-lead, until + 1e-9, 0.25):
        tick = t0 + round(tau * rate)
        found = known_threats(span, tick - memory, tick, cfg) + contact_threats(ev, tick - memory, tick)
        refs = []
        for th in found:
            key = (round(th.x / 40), round(th.y / 40), round(th.z / 40))
            if key not in points:
                points[key] = (len(points), th)
            refs.append([points[key][0], th.source])
        frames.append([round(float(tau), 2), refs])
        if ctx is not None:  # 可能威胁与已知威胁同步，每 0.25 秒一帧（每格可能有几名 T，量化成 0–255）
            vec = potential_at(span, found, tick, cfg, ctx)
            if vec is not None:
                top = float(vec.max()) if vec.max() > 0 else 1.0
                pot_frames.append([round(float(tau), 2), round(top, 5),
                                   base64.b64encode(np.round(vec / top * 255).astype("uint8").tobytes()).decode()])
            if ctx.get("adj") is not None:  # 第 7 版：这一刻敌人最可能出现的拐角 / 门口（按他此刻所在格子算）
                me = span.ticks[(span.ticks["steamid"] == target) & (span.ticks["tick"] <= tick)].tail(1)
                if not me.empty and bool(me["is_alive"].iloc[0]):
                    from cs2coach.research.potential import emergence

                    grid = ctx["grid"]
                    mass: dict[int, float] = {}
                    for th in found:
                        k = grid.nearest(th.x, th.y)
                        mass[k] = mass.get(k, 0.0) + 1.0
                    if vec is not None:
                        for j in np.nonzero(vec > 0)[0]:
                            mass[int(j)] = mass.get(int(j), 0.0) + float(cfg["research"]["threat"]["potential_weight"]) * float(vec[j])
                    em = emergence(grid.nearest(float(me["X"].iloc[0]), float(me["Y"].iloc[0])), ctx["vis"], ctx["adj"], mass,
                                   float(cfg["research"]["threat"]["corner_max_path"]), grid.centers)
                    total = sum(em.values()) or 1.0
                    top_em = sorted(em.items(), key=lambda kv: -kv[1])[:6]
                    corners.append([round(float(tau), 2), [[round((grid.centers[c][0] - map_cfg["pos_x"]) / map_cfg["scale"], 1),
                                                            round((map_cfg["pos_y"] - grid.centers[c][1]) / map_cfg["scale"], 1),
                                                            round(w / total, 3)] for c, w in top_em if w / total >= 0.05]])
    vis, coords = [], []
    for _, th in sorted(points.values(), key=lambda v: v[0]):
        bits = np.zeros(len(centers), dtype=bool)
        source = (th.x, th.y, th.z + eye)
        for i, (_, _, x, y, z) in enumerate(centers):
            if (x - th.x) ** 2 + (y - th.y) ** 2 <= cfg["research"]["threat"]["zero_weight_distance"] ** 2:
                bits[i] = checker.is_visible(source, (x, y, z + eye))
        vis.append(base64.b64encode(np.packbits(bits).tobytes()).decode())
        coords.append([round(th.x, 1), round(th.y, 1), round(th.z, 1),
                       round((th.x - map_cfg["pos_x"]) / map_cfg["scale"], 1), round((map_cfg["pos_y"] - th.y) / map_cfg["scale"], 1)])
    g = arch.grenades
    smokes = []
    if not g.empty:
        v = cfg["visibility"]
        on, off = (round(s * rate) for s in v["smoke_active_seconds"])
        for r in g[g["type"] == "smokegrenade"].itertuples():
            begin, end = int(r.tick) + on, int(r.tick) + off
            if pd.notna(getattr(r, "end_tick", None)):
                end = min(end, int(r.end_tick))
            if end >= t0 - round(lead * rate) and begin <= t0 + round(until * rate):
                smokes.append([round((begin - t0) / rate, 2), round((end - t0) / rate, 2), round(float(r.x), 1), round(float(r.y), 1),
                               round(float(r.z) + float(v["smoke_center_height"]), 1)])
    return {"frames": frames, "points": coords, "vis": vis, "smokes": smokes, "potential": pot_frames, "corners": corners}


def _threat_heat(grid, mask) -> bytes:
    """威胁危险度（加权个数）→ 颜色：0 淡蓝 → 0.75 淡黄 → 1.5 以上红；双线性放大到 mask 的尺寸，只画在可走区域。"""
    import numpy as np

    size = mask.shape[0]
    rows, cols = grid.shape
    filled = np.nan_to_num(grid, nan=0.0)
    f = (np.arange(size) + 0.5) * rows / size - 0.5
    i0 = np.clip(np.floor(f).astype(int), 0, rows - 2)
    w = np.clip(f - i0, 0, 1)
    R0, C0 = np.meshgrid(i0, i0, indexing="ij")
    WY, WX = np.meshgrid(w, w, indexing="ij")
    value = (filled[R0, C0] * (1 - WX) * (1 - WY) + filled[R0, C0 + 1] * WX * (1 - WY)
             + filled[R0 + 1, C0] * (1 - WX) * WY + filled[R0 + 1, C0 + 1] * WX * WY)
    t = np.clip(value / 1.5, 0, 1)[..., None]
    cold, mid, hot = np.array([90, 150, 255]), np.array([255, 232, 150]), np.array([235, 55, 55])
    rgb = np.where(t < 0.5, cold * (1 - 2 * t) + mid * 2 * t, mid * (2 - 2 * t) + hot * (2 * t - 1))
    alpha = np.where(mask, 25 + 205 * t[..., 0], 0)  # 没有威胁看得到的地方淡一些，不挡小地图
    return _png(np.ascontiguousarray(np.dstack([rgb, alpha]).astype("uint8")))


class _Archived:
    """research_stance.py extract 保存的整场数据（archive/<比赛>/），按 extract() 需要的样子读出来，不必解析 demo。"""

    def __init__(self, folder: Path):
        columns = {"kills": ["tick", "attacker", "victim"], "shots": ["tick", "steamid", "is_gun"],
                   "grenades": ["tick", "type", "x", "y"]}  # 这场没有该事件时存档里没有文件
        read = lambda name: (pd.read_parquet(folder / f"{name}.parquet") if (folder / f"{name}.parquet").exists()  # noqa: E731
                             else pd.DataFrame(columns=columns.get(name, [])))
        from types import SimpleNamespace

        self.meta = SimpleNamespace(**json.loads((folder / "meta.json").read_text(encoding="utf-8")))
        players = read("players")
        self.players = {int(r.steamid): SimpleNamespace(name=str(r.name)) for r in players.itertuples()}
        self.ticks, self.kills, self.shots, self.grenades = read("ticks"), read("kills"), read("shots"), read("grenades")
        self.sampled = True


def _work(path: Path, member: str | None, items: list[dict], cfg: dict) -> dict:
    return extract(parse_match(resolve_demo(path, member=member), cfg), items, cfg)


def build(name: str, jobs: int | None, root: Path = ROOT) -> Path:
    from research_export import sources
    from research_review import _pack_dir, _windows

    cfg = load_config()
    pack = _pack_dir(root, name)
    data = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))
    by_demo: dict[str, list[dict]] = {}
    for item in data["items"]:
        by_demo.setdefault(item["demo_id"], []).append(item)
    demos = sorted(by_demo)
    replays = {}
    if all("archive" in item for item in data["items"]):  # 有整场数据存档：直接读，不解析 demo
        for demo in demos:
            folder = _archive_dir(root, by_demo[demo][0]["archive"])
            replays.update(extract(_Archived(folder), by_demo[demo], cfg))
        print(f"从存档读取 {len(demos)} 场比赛、{len(data['items'])} 个窗口（不解析 demo）", flush=True)
    else:
        paths = {(member or path.name): (path, member) for path, member in sources("de_mirage")}
        print(f"解析 {len(demos)} 场 demo，{len(data['items'])} 个窗口，{jobs or default_jobs(cfg)} 个进程", flush=True)
        outcomes = map_ordered(_work, [(*paths[d], by_demo[d]) for d in demos], jobs or default_jobs(cfg), args=(cfg,),
                               on_done=lambda i, o: print(f"[{i + 1}/{len(demos)}] {demos[i]}" + ("" if o.error is None else f" 失败：{o.error}"), flush=True))
        for outcome in outcomes:
            if outcome.error is None:
                replays.update(outcome.value)
    windows = None
    meta = []
    for item in data["items"]:
        if "player_name" in item:  # 复盘包自带展示信息
            row = {"player_name": item["player_name"], "start_place": item.get("start_place", ""),
                   "round_time_s": item.get("round_time_s", 0)}
        else:
            windows = _windows(root, data["source"]).astype({"player_id": str}) if windows is None else windows
            row = windows[(windows["demo_id"] == item["demo_id"]) & (windows["round_id"] == item["round_id"])
                          & (windows["player_id"] == str(item["player_id"])) & (windows["start_tick"] == item["start_tick"])].iloc[0]
        meta.append({"id": item["id"], "match": item["match_id"], "round": int(item["round_id"]),
                     "player": row["player_name"], "place": row["start_place"] if isinstance(row["start_place"], str) else "",
                     "round_time": round(float(row["round_time_s"]), 0), **({"reveal": item["reveal"]} if "reveal" in item else {}),
                     **({"focus": item["focus"], "focus_note": item.get("focus_note", "要判断的时段")} if "focus" in item else {})})
    map_cfg = json.loads((AWPY / "map_data.json").read_text(encoding="utf-8"))["de_mirage"]
    radar = base64.b64encode((AWPY / "radars" / "de_mirage.png").read_bytes()).decode()
    layers = danger_layers(root, data, cfg, map_cfg)
    for entry in meta:
        if entry["id"] in layers:
            layer = layers[entry["id"]]
            entry["direction"] = layer["direction"]
            if "threat_png" in layer:
                entry["threat_png"], entry["threats"], entry["live"] = layer["threat_png"], layer["threats"], layer.get("live")
    payload = {"pack": name, "map": map_cfg, "window": cfg["research"]["window_seconds"], "items": meta, "replays": replays,
               "labels": data.get("labels"), "note": data.get("note"), "danger": layers.get("_images", {}),
               "cells": layers.get("_cells"), "floor": layers.get("_floor"), "threat_cfg": layers.get("_threat_cfg"),
               "potential": layers.get("_potential")}
    if layers:
        from research_stance import criteria

        payload["criteria"] = criteria(cfg)
    template = (root / "scripts" / "research_replay.html").read_text(encoding="utf-8")
    html = template.replace("__RADAR__", radar).replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    out = pack / "replay.html"
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"已生成：{out}（{out.stat().st_size / 1e6:.1f} MB，{len(replays)}/{len(meta)} 个窗口有数据）")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="复盘包名（data/research/review/<名>）")
    parser.add_argument("--jobs", type=int, default=None)
    args = parser.parse_args(argv)
    build(args.name, args.jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
