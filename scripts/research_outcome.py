"""R03 第 2 步：首杀选手交战之后的后果，按行为（程序标签 / AI 标签）比较。

用法：python scripts/research_outcome.py --source st-6d --extract st-2 --ai full-6d/text/m1 --name o-1
输出：data/research/outcome/<名>/windows.csv（每个窗口的后果）、summary.md
参数：config/research.toml 的 [research.outcome]
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cs2coach.config import load_config  # noqa: E402
from cs2coach.research.outcome import (cluster_bootstrap, econ_matchup, econ_type, km_death_by,  # noqa: E402
                                       mh_risk_difference, outcome_row)
from cs2coach.research.paths import research_dir  # noqa: E402

BIG = {"激进·原位架枪": "激进", "激进·前压": "激进", "激进·主动寻求对枪": "激进", "激进·被动寻求对枪": "激进", "激进·被动": "激进",
       "保守": "保守", "看不出": "看不出"}


_CHECKER = None


def _one_match(job: tuple[str, list[dict], str]) -> list[dict]:
    """一场比赛：后果 + 行为之前的状态（交战结束时的血量、还活着的 T、已知威胁）。"""
    global _CHECKER
    demo, windows, extract = job
    from cs2coach.research.threat import MatchArchive, pre_danger

    cfg = load_config()
    o = cfg["research"]["outcome"]
    ex = research_dir("stance", extract, ROOT)
    stem = Path(demo).stem
    kills = pd.read_parquet(ex / "raw" / "kills" / f"{stem}.parquet")
    tracks = pd.read_parquet(ex / "tracks" / f"{stem}.parquet")
    tracks["player_id"] = tracks["player_id"].astype(str)
    events = pd.read_parquet(ex / "events" / f"{stem}.parquet")
    events["player_id"] = events["player_id"].astype(str)
    arch = MatchArchive(ex / "archive" / stem) if (ex / "archive" / stem).exists() else None
    econ = {}
    if (ex / "archive" / stem / "economy.parquet").exists():  # 每队人均：回合开始时带的装备 + 本回合花的钱
        e = pd.read_parquet(ex / "archive" / stem / "economy.parquet")
        e["bought"] = e["round_start_equip_value"] + e["cash_spent_this_round"]
        econ = e.groupby(["round", "team_num"])["bought"].mean().to_dict()
    if arch is not None and _CHECKER is None:
        from awpy import VisibilityChecker

        _CHECKER = VisibilityChecker("de_mirage")
    rows = []
    for r in windows:
        out = outcome_row(r, kills, float(o["horizon_seconds"]))
        t = tracks[(tracks["round_id"] == r["round_id"]) & (tracks["player_id"] == r["player_id"])]
        at = t.iloc[(t["t"] - float(r["engagement_end_s"])).abs().argsort()[:1]] if len(t) else t
        hp = float(at["health"].iloc[0]) if len(at) and pd.notna(at["health"].iloc[0]) else None
        end_tick = int(r["first_kill_tick"]) + round(float(r["engagement_end_s"]) * float(r["tick_rate"]))
        rk = kills[(kills["round"] == r["round_id"]) & (kills["victim_team_num"] == 2) & (kills["tick"] <= end_tick)]
        pre, known = None, None
        if arch is not None and len(at):
            own = events[(events["round_id"] == r["round_id"]) & (events["player_id"] == r["player_id"])]
            p0 = (float(at["X"].iloc[0]), float(at["Y"].iloc[0]), float(at["Z"].iloc[0]))
            pre, known = pre_danger(arch, own, p0, end_tick, _CHECKER, cfg)
        rows.append({**r, **out, "hp_end": hp, "t_alive_end": 5 - len(rk), "pre_danger": pre, "pre_known_t": known,
                     "ct_equip": econ.get((r["round_id"], 3)), "t_equip": econ.get((r["round_id"], 2))})
    return rows


def build(source: str, extract: str, ai: str | None, cfg: dict, jobs: int = 1) -> pd.DataFrame:
    from concurrent.futures import ProcessPoolExecutor

    w = pd.read_csv(research_dir("stance", source, ROOT) / "windows.csv", dtype={"player_id": str})
    todo = [(demo, g.to_dict("records"), extract) for demo, g in w.groupby("demo_id")]
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as ex:
            parts = list(ex.map(_one_match, todo))
    else:
        parts = [_one_match(job) for job in todo]
    frame = pd.DataFrame([row for part in parts for row in part])
    frame["program"] = frame["behaviour"].map(BIG)
    if ai:
        pack, sub, name = ai.split("/")
        folder = research_dir("review", pack, ROOT)
        items = pd.DataFrame(tomllib.loads((folder / "review.toml").read_text(encoding="utf-8"))["items"])
        items["player_id"] = items["player_id"].astype(str)
        labels = pd.DataFrame([json.loads(l) for l in (folder / sub / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()])
        items = items[["id", "demo_id", "round_id", "player_id"]].merge(labels[["id", "label"]], on="id")
        frame = frame.merge(items.drop(columns="id").rename(columns={"label": "ai_label"}),
                            on=["demo_id", "round_id", "player_id"], how="left")
        frame["ai"] = frame["ai_label"].map(BIG)
    return frame


def _stats(f: pd.DataFrame, horizon: float) -> dict:
    ok = f[f["time_s"].notna()]
    died = km_death_by(ok["time_s"].to_numpy(float), ok["event"].astype(bool).to_numpy(), horizon)
    known = f[f["kill_after"].notna()]
    return {"n": len(f), "matches": f["match_id"].nunique(), "died": died,
            "kill_after": round(float(known["kill_after"].astype(bool).mean()), 4) if len(known) else None}


def _compare(frame: pd.DataFrame, by: str, cfg: dict, title: str) -> list[str]:
    o = cfg["research"]["outcome"]
    horizon, reps = float(o["horizon_seconds"]), int(o["bootstrap_reps"])
    lines = [f"### {title}", "", f"| 分组 | 窗口 | 比赛 | {horizon:g} 秒内阵亡（KM） | 95% 区间 | {horizon:g} 秒内又击杀 | 95% 区间 |",
             "|---|---:|---:|---:|---|---:|---|"]
    for key, g in frame.groupby(by, dropna=True, observed=True):
        s = _stats(g, horizon)
        d_ci = cluster_bootstrap(g, lambda f: _stats(f, horizon)["died"], reps=reps)
        k_ci = cluster_bootstrap(g, lambda f: _stats(f, horizon)["kill_after"], reps=reps)
        lines.append(f"| {key} | {s['n']} | {s['matches']} | {s['died']:.0%} | {d_ci[0]:.0%}–{d_ci[1]:.0%} | "
                     f"{s['kill_after']:.0%} | {k_ci[0]:.0%}–{k_ci[1]:.0%} |")
    return lines + [""]


def _difference(frame: pd.DataFrame, by: str, cfg: dict) -> str:
    """激进 − 保守 的差及按比赛重抽的区间。"""
    o = cfg["research"]["outcome"]
    horizon, reps = float(o["horizon_seconds"]), int(o["bootstrap_reps"])
    f = frame[frame[by].isin(["激进", "保守"])]

    def diff(key):
        def stat(x):
            a, b = _stats(x[x[by] == "激进"], horizon)[key], _stats(x[x[by] == "保守"], horizon)[key]
            return None if a is None or b is None else a - b
        return stat

    out = []
    for key, name in (("died", "阵亡"), ("kill_after", "又击杀")):
        point = diff(key)(f)
        ci = cluster_bootstrap(f, diff(key), reps=reps)
        out.append(f"{name} {point:+.0%}（{ci[0]:+.0%} ~ {ci[1]:+.0%}）")
    return "激进 − 保守：" + "；".join(out)


STRATA = ["role", "sniper", "hp_bin", "time_bin", "t_alive", "known_bin", "econ"]


def add_econ(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    o = cfg["research"]["outcome"]
    ct = frame["ct_equip"].map(lambda v: econ_type(v, o["full_min_ct"], o["eco_max"]))
    t = frame["t_equip"].map(lambda v: econ_type(v, o["full_min_t"], o["eco_max"]))
    frame["econ"] = [econ_matchup(a, b) for a, b in zip(ct, t)]
    return frame


def _adjusted(frame: pd.DataFrame, cfg: dict) -> list[str]:
    """激进 − 保守，粗差与按行为之前的状态分层调整（Mantel–Haenszel）后的差；后果用二值（10 秒内是否阵亡 / 又击杀），
    回合在 10 秒内结束、不知道结果的窗口不算。"""
    reps = int(cfg["research"]["outcome"]["bootstrap_reps"])
    lines = ["## 按行为之前的状态调整", "",
             "在位置、武器、血量、回合时间、剩几个 T、已知 T 人数、经济都相同的格子里比较激进与保守，再按格子加权合并"
             "（Mantel–Haenszel 风险差）。二值后果：10 秒内是否阵亡 / 又击杀；回合在 10 秒内结束、结果未知的窗口不算。"
             "区间按比赛整场重抽。", "",
             "| 标签 | 范围 | 后果 | 粗差 | 调整后 | 95% 区间 | 能比较的窗口 |", "|---|---|---|---:|---:|---|---:|"]
    for by, name in (("program", "程序"), ("ai", "AI")):
        if by not in frame:
            continue
        for scope, f in [("全部", frame), *[(f"位置：{k}", g) for k, g in frame.groupby("role")]]:
            for outcome, oname in (("died", "10 秒内阵亡"), ("kill_after", "10 秒内又击杀")):
                ff = f[f[by].isin(["激进", "保守"]) & f[outcome].notna()]
                crude = round(ff[ff[by] == "激进"][outcome].astype(float).mean() - ff[ff[by] == "保守"][outcome].astype(float).mean(), 4)
                adj = mh_risk_difference(ff, by, outcome, STRATA, "激进", "保守")
                ci = cluster_bootstrap(ff, lambda x: mh_risk_difference(x, by, outcome, STRATA, "激进", "保守"), reps=reps)
                sizes = ff.groupby(STRATA, observed=True)[by].transform("nunique")
                lines.append(f"| {name} | {scope} | {oname} | {crude:+.0%} | {adj:+.0%} | {ci[0]:+.0%} ~ {ci[1]:+.0%} | "
                             f"{int((sizes == 2).sum())}/{len(ff)} |")
    return lines + [""]


def summarize(frame: pd.DataFrame, cfg: dict, source: str) -> str:
    o = cfg["research"]["outcome"]
    frame = frame.copy()
    frame["hp_bin"] = pd.cut(frame["hp_end"], o["hp_bins"], right=False,
                             labels=[f"{a}–{b - 1}" for a, b in zip(o["hp_bins"], o["hp_bins"][1:])])
    frame["t_alive"] = frame["t_alive_end"].map(lambda n: f"T 剩 {n}" if n >= 4 else "T 剩 ≤3")
    frame["time_bin"] = pd.cut(frame["round_time_s"], o["time_bins"], right=False,
                               labels=[f"{a}–{b} 秒" for a, b in zip(o["time_bins"], o["time_bins"][1:])])
    frame = add_econ(frame, cfg)
    frame["known_bin"] = frame["pre_known_t"].map(lambda n: "未知" if pd.isna(n) else f"已知 {int(n)} 个 T" if n < 2 else "已知 ≥2 个 T")
    lines = [f"# R03 第 2 步：交战后的后果（{source}）", "",
             f"计时从观察截止时刻开始；回合结束时还活着的在回合结束处截断（Kaplan–Meier）。区间按比赛整场重抽 {o['bootstrap_reps']} 次。"
             "观察期内阵亡的窗口截止时刻就是阵亡，记 0 秒阵亡。", "",
             "## 正对照（行为之前的状态，结果应当符合常识）", ""]
    lines += _compare(frame, "hp_bin", cfg, "交战结束时的血量")
    lines += _compare(frame, "t_alive", cfg, "交战结束时还活着的 T")
    lines += _compare(frame, "known_bin", cfg, "交战结束时 CT 方知道位置的 T（之前 3 秒内雷达、声音、枪声）")
    lines += _compare(frame, "econ", cfg, "经济（购买时间结束时，双方是否全起）")
    lines += _adjusted(frame, cfg)
    for by, name in (("program", "程序标签"), ("ai", "AI 标签（MiMo 文字版）")):
        if by not in frame:
            continue
        lines += [f"## 激进 / 保守（{name}）", "", _difference(frame, by, cfg), ""]
        lines += _compare(frame[frame[by].isin(["激进", "保守"])], by, cfg, "全部")
        for role, g in frame.groupby("role"):
            lines += _compare(g[g[by].isin(["激进", "保守"])], by, cfg, f"位置：{role}")
            lines += [_difference(g, by, cfg), ""]
        for sniper, g in frame.groupby("sniper"):
            lines += _compare(g[g[by].isin(["激进", "保守"])], by, cfg, "狙击枪" if sniper else "非狙击枪")
            lines += [_difference(g, by, cfg), ""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True)
    parser.add_argument("--extract", required=True)
    parser.add_argument("--ai")
    parser.add_argument("--name", required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args(argv)
    cfg = load_config()
    frame = build(args.source, args.extract, args.ai, cfg, args.jobs)
    out = ROOT / "data" / "research" / "outcome" / args.name
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "windows.csv", index=False)
    (out / "summary.md").write_text(summarize(frame, cfg, args.source), encoding="utf-8")
    print(out / "summary.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
