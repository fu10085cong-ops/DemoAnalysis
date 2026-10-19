"""R03 全量：CT 方拿下首杀的选手交火后怎么做，以及 5 秒内是否被补掉。

只用规则程序判行为（定义见 docs/tasks/research/R03-ai-post-fight-reposition.md 第 3 版），不调用 AI。
按拿首杀时的位置角色（A 主防 / B 主防 / 中路 CT 侧 / 后方 / 接触区）和武器（狙击枪 / 其他）分组。
比较按比赛分组：置信区间用按比赛重抽样的 bootstrap。描述性结果，不宣称因果。

用法：python scripts/research_firstkill.py --source r01-v6 --name fk-1 [--jobs 2]
输出：data/research/firstkill/<名>/fights.csv、summary.md
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cs2coach.config import load_config  # noqa: E402
from cs2coach.parallel import default_jobs, map_ordered  # noqa: E402
from cs2coach.research.fight import LABELS  # noqa: E402

BEHAVIOURS = ["原位再架", "换位", "继续压"]  # R03 第 4 版：去掉撤离
ROLES = ["A 主防", "B 主防", "中路 CT 侧", "后方", "接触区", "未知"]


def role_of(area: dict) -> str:
    """拿首杀时的位置角色。Connector 同属 A 区组和中路组，算中路 CT 侧。"""
    zones = area["zones"]
    if "Mid" in zones:
        return "中路 CT 侧"
    if "A" in zones:
        return "A 主防"
    if "B" in zones:
        return "B 主防"
    return {0: "后方", 2: "接触区"}.get(area["layer"], "未知")


def fight_row(match, row: dict, sight, cfg: dict, nav=None) -> dict:
    from cs2coach.research.fight import _death_tick, area_of, reposition

    rate = match.meta.tick_rate
    limits = cfg["research"]["fight"]
    sid, round_id, trigger = int(row["player_id"]), int(row["round_id"]), int(row["start_tick"])
    lead = round(cfg["research"]["timeline"]["lead_seconds"] * rate)
    result = reposition(match, sid, round_id, trigger, cfg, sight=sight, lead_ticks=lead, nav=nav)
    fight = result.fight
    kills = match.kills
    first = kills[(kills["round"] == round_id) & (kills["tick"] == trigger) & (kills["attacker"] == sid)]
    weapon = str(first["weapon"].iloc[0]) if len(first) else ""
    state = match.state_at(sid, trigger)
    out = {"demo_id": row["demo_id"], "match_id": row["match_id"], "round_id": round_id, "player_id": row["player_id"],
           "player_name": row["player_name"], "start_tick": trigger, "round_time_s": row["round_time_s"],
           "start_place": row["start_place"],
           "role": role_of(area_of(state, cfg, match.meta.map_name)),
           "weapon": weapon, "sniper": weapon in limits["sniper_weapons"],
           "behaviour": LABELS[result.label], "move": result.move, "height": result.height, "closer_to_t": result.closer,
           "fight_end_s": None, "hp_at_fight_end": None, "death_after_fight_s": None,
           "traded": None, "advantage_held": row.get("advantage_held"), "ct_won": row.get("ct_won")}
    if fight is None:
        return out
    end_state = match.state_at(sid, fight.end_tick)
    death = _death_tick(match, sid, round_id, fight.start_tick)  # 死在交火结束前也算（之后的 0 秒被补）
    out["fight_end_s"] = round((fight.end_tick - trigger) / rate, 2)
    out["hp_at_fight_end"] = int(end_state["health"]) if end_state is not None else None
    if death is not None:
        out["death_after_fight_s"] = round((death - fight.end_tick) / rate, 2)
    rnd = match.round(round_id)
    horizon = fight.end_tick + round(limits["trade_seconds"] * rate)
    # 回合在 5 秒内结束且没死：算没被补（观察不满 5 秒，汇总里单独计数）
    out["traded"] = death is not None and death <= horizon
    out["round_over_before_horizon"] = rnd is not None and rnd.end_tick < horizon and death is None
    return out


def _work(path: Path, member: str | None, rows: list[dict], cfg: dict) -> list[dict]:
    from cs2coach.features.visibility import Sight
    from cs2coach.parsing.loader import resolve_demo
    from cs2coach.parsing.match import parse_match

    from awpy.map_control import NavMesh

    match = parse_match(resolve_demo(path, member=member), cfg)
    sight = Sight(match, cfg)
    nav = NavMesh(str(Path.home() / ".awpy" / "2000930" / "navs" / f"{match.meta.map_name}.nav"))
    return [fight_row(match, row, sight, cfg, nav) for row in rows]


def cluster_ci(frame: pd.DataFrame, value: str, draws: int, seed: int) -> tuple[float, float]:
    """按比赛重抽样的比例 95% 区间。"""
    if frame.empty:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    groups = [g[value].astype(float).tolist() for _, g in frame.groupby("match_id")]
    stats = []
    for _ in range(draws):
        sample = [x for _ in groups for x in rng.choice(groups)]
        stats.append(sum(sample) / len(sample))
    stats.sort()
    return stats[int(0.025 * draws)], stats[int(0.975 * draws) - 1]


def _rate_table(frame: pd.DataFrame, by: str, keys: list[str], draws: int, seed: int) -> list[str]:
    lines = [f"| {by} | 窗口 | 比赛 | 5 秒内被补 | 95% 区间（按比赛重抽样） | 人数优势保住 | 回合赢 |",
             "|---|---:|---:|---:|---|---:|---:|"]
    for key in keys:
        part = frame[frame[by] == key]
        if part.empty:
            continue
        lo, hi = cluster_ci(part, "traded", draws, seed)
        held = part["advantage_held"].astype(float).mean()
        won = part["ct_won"].astype(float).mean()
        lines.append(f"| {key} | {len(part)} | {part['match_id'].nunique()} | {part['traded'].mean():.0%} | "
                     f"{lo:.0%}–{hi:.0%} | {held:.0%} | {won:.0%} |")
    return lines


def summarize(fights: pd.DataFrame, cfg: dict) -> str:
    limits = cfg["research"]["fight"]
    draws, seed = limits["bootstrap_draws"], 20261020
    total = len(fights)
    no_fight = fights["fight_end_s"].isna().sum()
    quick = fights[fights["behaviour"] == "看不出"]
    usable = fights[fights["behaviour"].isin(BEHAVIOURS)].copy()
    usable["traded"] = usable["traded"].astype(bool)
    lines = ["# R03 全量：CT 首杀选手交火后的行为与被补", "",
             f"首杀回合 {total} 个；找不到交火段 {no_fight} 个；交火后 {limits['min_observe_seconds']:g} 秒内阵亡或回合结束"
             f"（行为看不出）{len(quick)} 个，其中阵亡 {int(quick['death_after_fight_s'].notna().sum())} 个；"
             f"可比较 {len(usable)} 个，来自 {usable['match_id'].nunique()} 场比赛。",
             f"被补 = 交火结束后 {limits['trade_seconds']:g} 秒内被打死；5 秒内回合结束且没死记为没被补"
             f"（{int(usable['round_over_before_horizon'].fillna(False).sum())} 个）。",
             "**描述性结果，不是因果结论**：没有控制血量、对手位置与人数、道具等差别；行为定义尚待人工复核。", "",
             "## 按行为", ""]
    lines += _rate_table(usable, "behaviour", BEHAVIOURS, draws, seed)
    lines += ["", "## 按位置角色 × 行为（被补比例，括号内为窗口数）", "",
              "| 角色 | " + " | ".join(BEHAVIOURS) + " |", "|---|" + "---:|" * len(BEHAVIOURS)]
    for role in ROLES:
        part = usable[usable["role"] == role]
        if part.empty:
            continue
        cells = []
        for b in BEHAVIOURS:
            cell = part[part["behaviour"] == b]
            cells.append(f"{cell['traded'].mean():.0%}（{len(cell)}）" if len(cell) else "—")
        lines.append(f"| {role} | " + " | ".join(cells) + " |")
    for sniper, name in ((True, "狙击枪"), (False, "其他武器")):
        lines += ["", f"## {name}", ""]
        lines += _rate_table(usable[usable["sniper"] == sniper], "behaviour", BEHAVIOURS, draws, seed)
    lines += ["", "## 行为分布（按位置角色）", "", "| 角色 | 窗口 | " + " | ".join(BEHAVIOURS) + " |",
              "|---|---:|" + "---:|" * len(BEHAVIOURS)]
    for role in ROLES:
        part = usable[usable["role"] == role]
        if len(part):
            lines.append(f"| {role} | {len(part)} | " + " | ".join(f"{(part['behaviour'] == b).mean():.0%}" for b in BEHAVIOURS) + " |")
    return "\n".join(lines) + "\n"


def run(source: str, name: str, jobs: int | None, root: Path = ROOT) -> Path:
    from research_export import sources

    cfg = load_config()
    windows = pd.read_csv(root / "data" / "research" / source / "windows.csv", dtype={"player_id": str})
    rows = windows[windows["first_kill_by"] == "self"]
    paths = {(member or path.name): (path, member) for path, member in sources("de_mirage")}
    by_demo: dict[str, list[dict]] = {}
    for row in rows.to_dict("records"):
        by_demo.setdefault(row["demo_id"], []).append(row)
    demos = sorted(by_demo)
    jobs = jobs or default_jobs(cfg)
    print(f"解析 {len(demos)} 份 demo，{len(rows)} 个首杀窗口，{jobs} 个进程", flush=True)
    outcomes = map_ordered(_work, [(*paths[d], by_demo[d]) for d in demos], jobs, args=(cfg,),
                           on_done=lambda i, o: print(f"[{i + 1}/{len(demos)}] {demos[i]}"
                                                      + ("" if o.error is None else f" 失败：{o.error}"), flush=True))
    fights = pd.DataFrame([r for o in outcomes if o.error is None for r in o.value])
    out = root / "data" / "research" / "firstkill" / name
    out.mkdir(parents=True, exist_ok=True)
    fights.to_csv(out / "fights.csv", index=False)
    (out / "summary.md").write_text(summarize(fights, cfg), encoding="utf-8")
    print(f"已写入：{out / 'summary.md'}")
    return out


def review_pack(name: str, pack_name: str, per_behaviour: int, seed: int, source: str = "r01-v6",
                root: Path = ROOT, exclude: list[str] = ()) -> Path:
    """从全量结果按行为分层抽首杀选手的窗口做盲审复核包；尽量每场比赛只取一条。"""
    from research_ai import NAMES
    from research_review import _write_review

    cfg = load_config()
    fights = pd.read_csv(root / "data" / "research" / "firstkill" / name / "fights.csv", dtype={"player_id": str})
    seen = set()
    for old in exclude:  # 已经复核过的回合不再抽（检验定义要用没看过的样本）
        import tomllib

        data = tomllib.loads((root / "data" / "research" / "review" / old / "review.toml").read_text(encoding="utf-8"))
        seen |= {(i["demo_id"], int(i["round_id"])) for i in data["items"]}
    fights = fights[[(d, int(r)) not in seen for d, r in zip(fights["demo_id"], fights["round_id"])]]
    rng = random.Random(seed)
    picked, used = [], set()
    for behaviour in BEHAVIOURS:
        rows = fights[fights["behaviour"] == behaviour].to_dict("records")
        rng.shuffle(rows)
        for _ in range(min(per_behaviour, len(rows))):
            row = next((r for r in rows if r["match_id"] not in used), rows[0])  # 先取还没用过的比赛
            rows.remove(row)
            used.add(row["match_id"])
            picked.append(row)
    rng.shuffle(picked)
    observe = cfg["research"]["fight"]["observe_seconds"]
    items = []
    for i, row in enumerate(picked, 1):
        end = float(row["fight_end_s"])
        items.append({"id": f"k{i}", "demo_id": row["demo_id"], "match_id": row["match_id"], "round_id": int(row["round_id"]),
                      "player_id": str(row["player_id"]), "start_tick": int(row["start_tick"]),
                      "replay_until_s": max(11.0, end + observe + 1), "focus": [end, end + observe],
                      "focus_note": f"他拿下首杀后，交火在 {end:+.1f} 秒结束（程序判断），看之后 {observe:g} 秒",
                      "result": "", "comment": ""})
    pack = root / "data" / "research" / "review" / pack_name
    pack.mkdir(parents=True, exist_ok=True)
    header = {"source": source, "labels": NAMES,
              "note": "看拿下首杀的这名 CT：交火结束后约 3 秒内他做了什么（盲审：不显示程序的答案）"}
    _write_review(pack, header, items)
    print(f"已生成 {len(items)} 条：{pack / 'review.toml'}（{len({r['match_id'] for r in picked})} 场比赛）")
    return pack


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="r01-v6")
    parser.add_argument("--name", default="fk-1")
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--review", metavar="复核包名", help="不重新分析，从已有结果抽首杀选手的复核包")
    parser.add_argument("--per-behaviour", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261020)
    parser.add_argument("--exclude", nargs="*", default=[], help="已复核过的复核包名，其中的回合不再抽")
    args = parser.parse_args(argv)
    if args.review:
        review_pack(args.name, args.review, args.per_behaviour, args.seed, args.source, exclude=args.exclude)
    else:
        run(args.source, args.name, args.jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
