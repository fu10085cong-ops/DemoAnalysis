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
        part = ticks[(ticks["tick"] >= lo) & (ticks["tick"] <= hi) & ((ticks["tick"] - lo) % step == 0)
                     & ticks["team_num"].isin((2, 3))]
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


def _work(path: Path, member: str | None, items: list[dict], cfg: dict) -> dict:
    return extract(parse_match(resolve_demo(path, member=member), cfg), items, cfg)


def build(name: str, jobs: int | None, root: Path = ROOT) -> Path:
    from research_export import sources
    from research_review import _pack_dir, _windows

    cfg = load_config()
    pack = _pack_dir(root, name)
    data = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))
    paths = {(member or path.name): (path, member) for path, member in sources("de_mirage")}
    windows = _windows(root, data["source"]).astype({"player_id": str})
    by_demo: dict[str, list[dict]] = {}
    for item in data["items"]:
        by_demo.setdefault(item["demo_id"], []).append(item)
    demos = sorted(by_demo)
    print(f"解析 {len(demos)} 场 demo，{len(data['items'])} 个窗口，{jobs or default_jobs(cfg)} 个进程", flush=True)
    outcomes = map_ordered(_work, [(*paths[d], by_demo[d]) for d in demos], jobs or default_jobs(cfg), args=(cfg,),
                           on_done=lambda i, o: print(f"[{i + 1}/{len(demos)}] {demos[i]}" + ("" if o.error is None else f" 失败：{o.error}"), flush=True))
    replays = {}
    for outcome in outcomes:
        if outcome.error is None:
            replays.update(outcome.value)
    meta = []
    for item in data["items"]:
        row = windows[(windows["demo_id"] == item["demo_id"]) & (windows["round_id"] == item["round_id"])
                      & (windows["player_id"] == str(item["player_id"])) & (windows["start_tick"] == item["start_tick"])].iloc[0]
        meta.append({"id": item["id"], "match": item["match_id"], "round": int(item["round_id"]),
                     "player": row["player_name"], "place": row["start_place"] if isinstance(row["start_place"], str) else "",
                     "round_time": round(float(row["round_time_s"]), 0)})
    map_cfg = json.loads((AWPY / "map_data.json").read_text(encoding="utf-8"))["de_mirage"]
    radar = base64.b64encode((AWPY / "radars" / "de_mirage.png").read_bytes()).decode()
    payload = {"pack": name, "map": map_cfg, "window": cfg["research"]["window_seconds"], "items": meta, "replays": replays,
               "labels": data.get("labels"), "note": data.get("note")}
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
