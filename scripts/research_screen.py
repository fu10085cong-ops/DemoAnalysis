"""R03 第 2 步：先筛选、再确认，以及回合胜负（描述）。

用法：python scripts/research_screen.py --outcome o-2 --extract st-2 --name s-1
输入：research_outcome.py 的 windows.csv（后果 + 行为之前的状态 + 程序 / AI 标签）
输出：data/research/outcome/<名>/summary.md、subgroups.csv
规则（看数据之前定）：R03 文档"先筛选、再确认：事先定好的规则"；参数 [research.screen]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cs2coach.config import load_config  # noqa: E402
from cs2coach.research.outcome import cluster_bootstrap, mh_risk_difference  # noqa: E402
from cs2coach.research.paths import research_dir  # noqa: E402
from research_outcome import STRATA, add_econ  # noqa: E402

SUBGROUP_VARS = {"role": "位置", "sniper": "武器", "time_bin": "回合时间", "hp_bin": "血量", "known_bin": "已知 T",
                 "direction": "首杀方向", "t_alive": "剩几个 T", "econ": "经济"}
OUTCOMES = {"died": "10 秒内阵亡", "kill_after": "10 秒内又击杀"}


def prepare(frame: pd.DataFrame, extract: str, cfg: dict) -> pd.DataFrame:
    o = cfg["research"]["outcome"]
    f = frame.copy()
    f["hp_bin"] = pd.cut(f["hp_end"], o["hp_bins"], right=False,
                         labels=[f"{a}–{b - 1}" for a, b in zip(o["hp_bins"], o["hp_bins"][1:])]).astype(str)
    f["time_bin"] = pd.cut(f["round_time_s"], o["time_bins"], right=False,
                           labels=[f"{a}–{b} 秒" for a, b in zip(o["time_bins"], o["time_bins"][1:])]).astype(str)
    f["t_alive"] = f["t_alive_end"].map(lambda n: "T 剩 4" if n >= 4 else "T 剩 ≤3")
    f["known_bin"] = f["pre_known_t"].map(lambda n: f"已知 {int(n)} 个 T" if n < 2 else "已知 ≥2 个 T")
    f["sniper"] = f["sniper"].map({True: "狙击枪", False: "非狙击枪", "True": "狙击枪", "False": "非狙击枪"})
    f["direction"] = f["direction"].fillna("未知")
    f = add_econ(f, cfg)
    # 回合胜负（描述用）：存档 rounds.parquet 的 winner（3 = CT）
    ex = research_dir("stance", extract, ROOT)
    wins = []
    for demo in f["demo_id"].unique():
        rounds = pd.read_parquet(ex / "archive" / Path(demo).stem / "rounds.parquet")
        wins.append(pd.DataFrame({"demo_id": demo, "round_id": rounds["number"], "ct_win": rounds["winner"] == 3}))
    return f.merge(pd.concat(wins), on=["demo_id", "round_id"], how="left")


def split(frame: pd.DataFrame, seed: int) -> pd.Series:
    """按比赛随机分两半：True = 筛选集。"""
    matches = np.array(sorted(frame["match_id"].unique()))
    rng = np.random.default_rng(seed)
    screen = set(rng.choice(matches, len(matches) // 2, replace=False))
    return frame["match_id"].isin(screen)


def subgroup_rd(frame: pd.DataFrame, label: str, var: str, value, outcome: str) -> tuple[float | None, int, int]:
    g = frame[(frame[var] == value) & frame[label].isin(["激进", "保守"]) & frame[outcome].notna()]
    strata = [s for s in STRATA if s != var]
    return (mh_risk_difference(g, label, outcome, strata, "激进", "保守"),
            int((g[label] == "激进").sum()), int((g[label] == "保守").sum()))


def screen_table(frame: pd.DataFrame, is_screen: pd.Series, cfg: dict) -> pd.DataFrame:
    s = cfg["research"]["screen"]
    halves = {"筛选": frame[is_screen], "确认": frame[~is_screen]}
    overall = {(h, lab, out): mh_risk_difference(f[f[lab].isin(["激进", "保守"]) & f[out].notna()], lab, out, STRATA, "激进", "保守")
               for h, f in halves.items() for lab in ("program", "ai") for out in OUTCOMES}
    rows = []
    for var, vname in SUBGROUP_VARS.items():
        for value in sorted(frame[var].dropna().unique(), key=str):
            for out, oname in OUTCOMES.items():
                row = {"变量": vname, "子组": value, "后果": oname}
                for h, f in halves.items():
                    for lab in ("program", "ai"):
                        rd, na, nc = subgroup_rd(f, lab, var, value, out)
                        base = overall[(h, lab, out)]
                        row[f"{h}_{lab}_差"] = rd
                        row[f"{h}_{lab}_偏离"] = None if rd is None or base is None else round(rd - base, 4)
                        row[f"{h}_{lab}_n"] = f"{na}/{nc}"
                        row[f"{h}_{lab}_够"] = na >= s["min_group"] and nc >= s["min_group"]
                dev = row["筛选_program_偏离"]
                row["候选"] = bool(row["筛选_program_够"] and dev is not None and abs(dev) >= s["min_deviation"])

                def holds(lab):
                    d = row[f"确认_{lab}_偏离"]
                    return d is not None and dev is not None and np.sign(d) == np.sign(dev) and abs(d) >= s["confirm_share"] * abs(dev)
                row["确认"] = row["候选"] and holds("program") and holds("ai")
                rows.append(row)
    table = pd.DataFrame(rows)
    table.attrs["overall"] = overall
    return table


def summarize(frame: pd.DataFrame, table: pd.DataFrame, cfg: dict, is_screen: pd.Series) -> str:
    s = cfg["research"]["screen"]
    pct = lambda v: "—" if v is None or pd.isna(v) else f"{v:+.0%}"  # noqa: E731
    ov = table.attrs["overall"]
    lines = ["# R03 第 2 步：先筛选、再确认", "",
             f"按比赛随机分两半（种子 {s['seed']}）：筛选集 {frame[is_screen]['match_id'].nunique()} 场、"
             f"确认集 {frame[~is_screen]['match_id'].nunique()} 场。差 = 激进 − 保守（MH 调整），偏离 = 子组的差 − 全体的差。", "",
             "全体（MH 调整）：", ""]
    for h in ("筛选", "确认"):
        for lab, lname in (("program", "程序"), ("ai", "AI")):
            lines.append(f"- {h}集 {lname}：阵亡 {pct(ov[(h, lab, 'died')])}，又击杀 {pct(ov[(h, lab, 'kill_after')])}")
    cand = table[table["候选"]]
    lines += ["", f"## 候选（筛选集程序标签偏离 ≥ {s['min_deviation']:.0%}，两组各 ≥ {s['min_group']}）：{len(cand)} 个", "",
              "| 子组 | 后果 | 筛选：差（偏离） | 确认·程序：差（偏离） | 确认·AI：差（偏离） | 结论 |", "|---|---|---|---|---|---|"]
    for r in cand.itertuples():
        d = r._asdict()
        cell = lambda h, lab: f"{pct(d[f'{h}_{lab}_差'])}（{pct(d[f'{h}_{lab}_偏离'])}）"  # noqa: E731
        lines.append(f"| {r.变量}：{r.子组} | {r.后果} | {cell('筛选', 'program')} | {cell('确认', 'program')} | "
                     f"{cell('确认', 'ai')} | {'**确认**' if r.确认 else '未通过'} |")
    lines += ["", "## 回合胜负（描述，不作主要结论）", "",
              "激进 / 保守所在回合 CT 最终获胜的比例；MH 调整同上（全体数据）。", "",
              "| 标签 | 激进 CT 胜率 | 保守 CT 胜率 | 粗差 | 调整后 | 95% 区间 |", "|---|---:|---:|---:|---:|---|"]
    reps = int(cfg["research"]["outcome"]["bootstrap_reps"])
    for lab, lname in (("program", "程序"), ("ai", "AI")):
        f = frame[frame[lab].isin(["激进", "保守"]) & frame["ct_win"].notna()].copy()
        f["ct_win"] = f["ct_win"].astype(float)
        a, c = f[f[lab] == "激进"]["ct_win"].mean(), f[f[lab] == "保守"]["ct_win"].mean()
        adj = mh_risk_difference(f, lab, "ct_win", STRATA, "激进", "保守")
        ci = cluster_bootstrap(f, lambda x: mh_risk_difference(x, lab, "ct_win", STRATA, "激进", "保守"), reps=reps)
        lines.append(f"| {lname} | {a:.0%} | {c:.0%} | {a - c:+.0%} | {pct(adj)} | {ci[0]:+.0%} ~ {ci[1]:+.0%} |")
    lines += ["", "## 全部子组（含未入选的）见 subgroups.csv", ""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--extract", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    cfg = load_config()
    frame = pd.read_csv(ROOT / "data" / "research" / "outcome" / args.outcome / "windows.csv", dtype={"player_id": str})
    frame = prepare(frame, args.extract, cfg)
    is_screen = split(frame, int(cfg["research"]["screen"]["seed"]))
    table = screen_table(frame, is_screen, cfg)
    out = ROOT / "data" / "research" / "outcome" / args.name
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "subgroups.csv", index=False)
    (out / "summary.md").write_text(summarize(frame, table, cfg, is_screen), encoding="utf-8")
    print(out / "summary.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
