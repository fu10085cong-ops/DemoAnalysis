"""R03 第 5 版：CT 首杀选手拿完人头后是激进还是保守（docs/tasks/research/R03-ai-post-fight-reposition.md "第 5 版定义"）。

两步：
  python scripts/research_stance.py extract --name st-1 [--jobs 6]
      解析全部 Mirage demo（CPU 任务），保存判定事实 facts.csv、首杀选手轨迹 tracks/ 与交火事件 events/、
      危险度原始数据 raw/，以及留作以后研究的整场数据 archive/<比赛>/（事件表 + 每 0.125 秒全员状态）
  python scripts/research_stance.py label --source st-1 --name st-1a
      按当前 config/research.toml 判定，不解析 demo；危险度留一场计算。只输出标签分布，**不算结果**
      （定义校准期间不看被补率，见 docs/status.md 工作流程改进）
  python scripts/research_stance.py review --source st-1a --extract st-1 --name gold-1
      抽标准答案集（盲审复盘包，一半开发集一半留出集）；再 python scripts/research_replay.py gold-1 生成回放页
  python scripts/research_stance.py score --pack gold-1 --source st-1a [--split dev|holdout|all]
      人工答案对照程序标签：一致率、混淆矩阵、不一致条目
输出：data/research/stance/<名>/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cs2coach.config import load_config  # noqa: E402
from cs2coach.research.paths import research_dir  # noqa: E402
from cs2coach.parallel import default_jobs, map_ordered  # noqa: E402
from cs2coach.research import stance  # noqa: E402

MAP = "de_mirage"
OUT = ROOT / "data" / "research" / "stance"


def archive(match, dest: Path, step_seconds: float) -> int:
    """留作以后研究用的整场数据（不必再解析 demo）：回合、玩家、全部事件表，以及回合进行中每 step_seconds 秒
    所有玩家的完整状态。返回逐 tick 表的行数。"""
    dest.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([r.model_dump() for r in match.rounds]).to_parquet(dest / "rounds.parquet")
    pd.DataFrame([{"steamid": str(s), **p.model_dump(exclude={"steamid"})} if hasattr(p, "model_dump")
                  else {"steamid": str(s), "name": match.name_of(s)} for s, p in match.players.items()]
                 ).to_parquet(dest / "players.parquet")
    for name in ("kills", "damages", "shots", "blinds", "bomb", "grenades", "economy"):
        frame = getattr(match, name)
        if frame is not None and not frame.empty:
            frame.to_parquet(dest / f"{name}.parquet")
    step = max(1, round(step_seconds * match.meta.tick_rate))
    ticks = match.ticks[match.ticks["tick"] % step == 0]
    live = pd.Series(False, index=ticks.index)
    for r in match.rounds:
        live |= ticks["tick"].between(r.freeze_end_tick if r.freeze_end_tick is not None else r.start_tick, r.end_tick)
    ticks = ticks[live]
    ticks.to_parquet(dest / "ticks.parquet")
    (dest / "meta.json").write_text(match.meta.model_dump_json(), encoding="utf-8")
    return len(ticks)


def _work(path: Path, member: str | None, cfg: dict, out: Path) -> tuple[list[dict], int, Counter]:
    from cs2coach.parsing.loader import resolve_demo
    from cs2coach.parsing.match import parse_match
    from cs2coach.research.danger import samples
    from cs2coach.research.triggers import find_situations

    match = parse_match(resolve_demo(path, member=member), cfg)
    stem = Path(member or path.name).stem
    situations, skipped = find_situations(match)
    rows, tracks, events = [], [], []
    for situation in situations:
        found = stance.facts_for(match, situation, cfg)
        if found is None:
            skipped["first_killer_missing"] += 1
            continue
        rows.append(found[0])
        tracks.append(found[1])
        events.append(found[2])
    if tracks:
        pd.concat(tracks, ignore_index=True).to_parquet(out / "tracks" / f"{stem}.parquet")
        pd.concat(events, ignore_index=True).to_parquet(out / "events" / f"{stem}.parquet")
    archive(match, out / "archive" / stem, cfg["research"]["archive"]["tick_step_seconds"])
    pos, kills = samples(match, cfg["research"]["danger"]["sample_seconds"])
    from research_export import _match_id

    for frame, sub in ((pos, "positions"), (kills, "kills")):
        frame.assign(demo_id=match.demo_path.name, match_id=_match_id(member or path.name)).to_parquet(
            out / "raw" / sub / f"{stem}.parquet")
    return rows, len(pos), skipped


def _sounds(path: Path, member: str | None, out: Path) -> int:
    """只读 player_sound 一种事件（不完整解析），存进 archive/<比赛>/sounds.parquet。radius 是游戏给的可听范围。"""
    from demoparser2 import DemoParser

    from cs2coach.parsing.loader import resolve_demo

    raw = DemoParser(str(resolve_demo(path, member=member))).parse_event("player_sound", player=["X", "Y", "Z"])
    frame = pd.DataFrame(columns=["tick", "steamid", "x", "y", "z", "radius", "step", "duration"])
    if len(raw):
        frame = pd.DataFrame({"tick": raw["tick"].astype("int64"), "steamid": pd.to_numeric(raw["user_steamid"], errors="coerce"),
                              "x": raw["user_X"], "y": raw["user_Y"], "z": raw["user_Z"], "radius": raw["radius"].astype(float),
                              "step": raw["step"].astype(bool), "duration": raw["duration"].astype(float)}).dropna()
        frame["steamid"] = frame["steamid"].astype("int64")
    folder = out / "archive" / Path(member or path.name).stem
    folder.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(folder / "sounds.parquet")
    return len(frame)


def add_sounds(name: str, jobs: int) -> None:
    """给已有的提取结果补上声音事件（威胁热力图用），不重新完整解析。"""
    from research_export import sources

    out = research_dir("stance", name, ROOT)
    entries = sources(MAP)
    start = time.time()
    print(f"读取 {len(entries)} 份 demo 的声音事件，{jobs} 个进程", flush=True)
    results = map_ordered(_sounds, entries, jobs, args=(out,),
                          on_done=lambda i, o: print(f"[{i + 1}/{len(entries)}] {time.time() - start:.0f}s"
                                                     + ("" if o.error is None else f" 失败：{o.error}"), flush=True))
    failed = [e[1] or e[0].name for e, r in zip(entries, results) if r.error is not None]
    print(f"完成 {time.time() - start:.0f}s，失败 {len(failed)}：{failed}", flush=True)


def extract(name: str, jobs: int, only: list[str] | None = None) -> Path:
    """only：只重新解析这些 demo（文件名），结果并入已有的 facts.csv（修复个别失败时用）。"""
    from research_export import sources

    cfg = load_config()
    out = research_dir("stance", name, ROOT)
    for sub in ("tracks", "events", "archive", "raw/positions", "raw/kills"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    entries = sources(MAP)
    if only:
        entries = [e for e in entries if (e[1] or e[0].name) in set(only)]
        missing = set(only) - {e[1] or e[0].name for e in entries}
        if missing:
            raise SystemExit(f"找不到 demo：{sorted(missing)}")
    start = time.time()
    print(f"解析 {len(entries)} 份 demo，{jobs} 个进程", flush=True)
    results = map_ordered(_work, entries, jobs, args=(cfg, out),
                          on_done=lambda i, o: print(f"[{i + 1}/{len(entries)}] {time.time() - start:.0f}s"
                                                     + ("" if o.error is None else f" 失败：{o.error}"), flush=True))
    rows, skipped, failed = [], Counter(), []
    for (path, member), result in zip(entries, results):
        if result.error is not None:
            failed.append(f"{member or path.name}: {result.error}")
            continue
        rows += result.value[0]
        skipped.update(result.value[2])
    facts = pd.DataFrame(rows)
    if only and (out / "facts.csv").exists():
        old = pd.read_csv(out / "facts.csv", dtype={"player_id": str})
        facts = pd.concat([old[~old["demo_id"].isin(only)], facts], ignore_index=True)
    facts.to_csv(out / "facts.csv", index=False)
    title = f"# R03 第 5 版：提取（只重新解析 {len(entries)} 份，并入已有结果）" if only else "# R03 第 5 版：提取"
    lines = [title, "", f"Demo：{len(entries) - len(failed)} 份成功，{len(failed)} 份失败",
             f"首杀选手窗口：本次 {len(rows)}，facts.csv 合计 {len(facts)}", "", "## 跳过", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(skipped.items())]
    lines += ["", "## 失败", ""] + [f"- {f}" for f in failed] if failed else []
    (out / ("extract-only.md" if only else "extract.md")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"完成 {time.time() - start:.0f}s：{out}", flush=True)
    return out


def _read_dir(path: Path) -> pd.DataFrame:
    files = sorted(path.glob("*.parquet"))
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else pd.DataFrame()


CACHE = ROOT / "data" / "research" / "threat_cache"


def potential_model(src: Path, cfg: dict, cache: Path = CACHE) -> dict | None:
    """可能威胁需要的地面格子、格子间视线（缓存）与历史 T 分布（按比赛分开存，查询时留一场）。"""
    import numpy as np

    from cs2coach.research.potential import build_prior, floor_grid, nav_polygons, visibility_matrix

    nav = Path.home() / ".awpy" / "2000930" / "navs" / f"{MAP}.nav"
    if not nav.exists() or not (src / "raw" / "positions").exists():
        return None
    p = cfg["research"]["threat"]
    grid = floor_grid(nav_polygons(nav), float(p["potential_cell"]))
    key = f"vis_{MAP}_{p['potential_cell']}_{p['eye_height']}_{p['zero_weight_distance']}.npy"
    cache.mkdir(parents=True, exist_ok=True)
    if (cache / key).exists():
        vis = np.unpackbits(np.load(cache / key))[: len(grid.centers) ** 2].reshape(len(grid.centers), -1).astype(bool)
    else:
        from awpy import VisibilityChecker

        print(f"计算 {len(grid.centers)} 个地面格子之间的视线（只算一次，缓存到 {cache / key}）", flush=True)
        vis = visibility_matrix(grid, VisibilityChecker(MAP), float(p["eye_height"]), float(p["zero_weight_distance"]))
        np.save(cache / key, np.packbits(vis))
    prior = build_prior(_read_dir(src / "raw" / "positions"), _read_dir(src / "raw" / "kills"), grid, cfg, MAP)
    from cs2coach.research.potential import adjacency

    return {"grid": grid, "vis": vis, "prior": prior, "adj": adjacency(grid, float(p["adjacency_max_step"]))}


def _progress_fn(cfg: dict):
    """横移补丁：p0 → p1 沿导航路线到 T 出生点缩短的距离（正 = 往 T 方向）；没有导航网格时为 None。"""
    nav_path = Path.home() / ".awpy" / "2000930" / "navs" / f"{MAP}.nav"
    if not nav_path.exists():
        return None
    from awpy.map_control import NavMesh

    from cs2coach.research.windows import _nav_distance

    nav = NavMesh(str(nav_path))
    goal = tuple(cfg["research"]["fight"]["t_spawn"][MAP])
    snap = cfg["research"]["nav_snap_max_distance"]

    def progress(p0, p1):
        d0, d1 = (_nav_distance(nav, tuple(map(float, q)), goal, snap) for q in (p0, p1))
        return None if d0 is None or d1 is None else d0 - d1

    return progress


def label_all(source: str, name: str, root: Path = OUT) -> Path:
    from cs2coach.research.danger import build_danger
    from cs2coach.research.fight import area_of
    from research_firstkill import role_of

    cfg = load_config()
    src = research_dir("stance", source, ROOT) if root == OUT else root / source
    facts = pd.read_csv(src / "facts.csv", dtype={"player_id": str})
    tracks = _read_dir(src / "tracks")
    tracks["player_id"] = tracks["player_id"].astype(str)
    events = _read_dir(src / "events")
    events["player_id"] = events["player_id"].astype(str)
    events_by = {k: g for k, g in events.groupby(["demo_id", "round_id", "player_id"])}
    maps = build_danger(_read_dir(src / "raw" / "positions"), _read_dir(src / "raw" / "kills"), cfg, MAP)
    by_window = {k: g.sort_values("t") for k, g in tracks.groupby(["demo_id", "round_id", "player_id"])}
    checker, archive = None, (None, None)
    pot = potential_model(src, cfg) if cfg["research"]["threat"].get("use_potential") else None
    progress = _progress_fn(cfg)
    rows = []
    for fact in facts.to_dict("records"):
        track = by_window.get((fact["demo_id"], fact["round_id"], fact["player_id"]), tracks.iloc[0:0])
        key = (fact["demo_id"], fact["round_id"], fact["player_id"])
        own_events = events_by.get(key, events.iloc[0:0])
        threat = smokes_at = None
        folder = src / "archive" / Path(fact["demo_id"]).stem
        if (folder / "sounds.parquet").exists():  # 有声音数据才算这一回合的威胁热力图
            from cs2coach.research.threat import MatchArchive, window_danger

            if checker is None:
                from awpy import VisibilityChecker

                checker = VisibilityChecker(MAP)
            if archive[0] != folder:
                archive = (folder, MatchArchive(folder))
            arch = archive[1]

            ctx = None
            if pot is not None:
                from cs2coach.research.danger import direction_of

                direction = (direction_of(fact.get("fk_victim_place") if isinstance(fact.get("fk_victim_place"), str) else None,
                                          fact["fk_victim_x"], fact["fk_victim_y"], cfg, MAP)
                             if pd.notna(fact.get("fk_victim_x")) else None)
                ctx = {**pot, "direction": direction, "match": fact["match_id"], "t_start": float(fact["round_time_s"]),
                       "start_tick": int(fact["first_kill_tick"])}

            def threat(p0, p1, start, stop, arch=arch, own_events=own_events, ctx=ctx):
                detail: dict = {}
                d0, d1, count, _ = window_danger(arch, own_events, p0, p1, start, stop, checker, cfg, ctx, detail)
                return d0, d1, count, detail

            def smokes_at(t, arch=arch, first=int(fact["first_kill_tick"])):
                from cs2coach.research.threat import active_smokes

                return active_smokes(arch, first + round(t * arch.rate), cfg)
        result = stance.label(fact, track, own_events, maps, cfg, MAP, threat=threat, progress=progress, smokes_at=smokes_at)
        role = "未知"
        first = track[track["t"] >= 0].head(1)
        if not first.empty:
            first = first.iloc[0]
            role = role_of(area_of({"last_place_name": first["place"], "X": first["X"], "Y": first["Y"], "Z": first["Z"]},
                                   cfg, MAP))
        rows.append({**fact, **result, "role": role,
                     "sniper": fact.get("weapon") in cfg["research"]["fight"]["sniper_weapons"]})
    windows = pd.DataFrame(rows)
    out = research_dir("stance", name, ROOT) if root == OUT else root / name
    out.mkdir(parents=True, exist_ok=True)
    windows.to_csv(out / "windows.csv", index=False)
    (out / "summary.md").write_text(summarize(windows, source), encoding="utf-8")
    return out


def _table(frame: pd.DataFrame, by: str, title: str) -> list[str]:
    seen = set(frame["behaviour"])
    cols = [c for c in ("激进·主动寻求对枪", "激进·被动寻求对枪", "激进·原位架枪", "激进·前压", "保守", "看不出")
            if c in seen or c in ("保守", "看不出")]
    lines = [f"## {title}", "", f"| {title} | " + " | ".join(cols) + " | 合计 | 激进占可判 |", "|---|" + "---:|" * (len(cols) + 2)]
    for key, g in frame.groupby(by, dropna=False):
        counts = g["behaviour"].value_counts()
        judged = len(g) - counts.get("看不出", 0)
        aggressive = sum(v for k, v in counts.items() if k.startswith("激进"))
        share = f"{aggressive / judged:.0%}" if judged else "—"
        lines.append(f"| {key if pd.notna(key) else '不归类'} | " + " | ".join(str(counts.get(c, 0)) for c in cols)
                     + f" | {len(g)} | {share} |")
    return lines + [""]


def summarize(windows: pd.DataFrame, source: str) -> str:
    detail = windows[windows["label"] == "unknown"]["detail"].value_counts()
    sub = windows[windows["label"] == "conservative"]["detail"].value_counts()
    lines = ["# R03 第 5 版：激进 / 保守的分布", "",
             f"来源：data/research/stance/{source}；{windows['match_id'].nunique()} 场、{len(windows)} 个首杀选手窗口。",
             "**只有标签分布，不含任何结果（被补、胜负）**：定义经标准答案集校准之前不看结果。", "",
             *_table(windows.assign(all="全部"), "all", "全部"),
             "看不出的原因：" + "、".join(f"{k} {v}" for k, v in detail.items()) if len(detail) else "看不出：0",
             "", "保守的小类：" + "、".join(f"{k} {v}" for k, v in sub.items()) if len(sub) else "",
             "", *_table(windows, "direction", "首杀方向"), *_table(windows, "role", "位置角色"),
             *_table(windows.assign(weapon_kind=windows["sniper"].map({True: "狙击枪", False: "其他"})), "weapon_kind", "武器"),
             *_table(windows.assign(streak=windows["engagement_kills"].clip(upper=3).map({0: "0（首杀不是枪械）", 1: "1", 2: "2", 3: "3+"})), "streak", "这次交战的击杀数"),
             f"交战（首杀起间隔 ≤ 1 秒的交火并作一次）时长中位数 {windows['engagement_end_s'].median():.2f} 秒，"
             f"交战中又拿人头的 {int((windows['engagement_kills'] > 1).sum())} 个；观察期内又一次交战：{int(windows['refight'].sum())} 个；"
             f"视线仍朝交战中最后一个死者方向（可判的窗口中）："
             f"{windows['view_toward_kill'].mean():.0%}" if windows["view_toward_kill"].notna().any() else ""]
    return "\n".join(lines) + "\n"


REVIEW_LABELS = ["激进·原位架枪", "激进·前压", "保守", "看不出"]
SEEK_LABELS = ["激进·主动寻求对枪", "激进·被动寻求对枪", "保守", "看不出"]


def review_labels(cfg: dict) -> list[str]:
    """复盘包的选项跟随当前判定方案（第 7 版：主动 / 被动寻求对枪）。"""
    return SEEK_LABELS if cfg["research"]["stance"].get("scheme", "position") == "seek" else REVIEW_LABELS


def criteria(cfg: dict) -> list[str]:
    """程序的判断依据（写进回放页），数值全部取自当前配置，避免页面与程序不一致。"""
    s, d = cfg["research"]["stance"], cfg["research"]["danger"]
    lines = [
        "对象：本回合拿下首杀（CT 首次形成 5 打 4）的 CT 选手。",
        f"一次交战：从首杀开始，他与 T 之间的枪械伤害或击杀（不论谁打谁）相邻间隔不超过 {s['fight_gap_seconds']:g} 秒的并作一次；"
        "只开枪不算。页面绿色时段从交战结束开始。",
        f"观察：交战结束后看 {s['observe_seconds']:g} 秒；中途阵亡、回合结束或又一次交战就截止，用截止那一刻的位置判断。",
        f"① 看不出：交战中阵亡；或观察不足 {s['min_observe_seconds']:g} 秒；或数据缺失；首杀前已经下包。",
    ]
    if s.get("scheme", "position") == "seek":
        q = cfg["research"]["threat"]
        lines += [
            "（第 7 版：看他在不在找人打。主动、被动寻求对枪都算激进）",
            f"② 激进·主动寻求对枪：移动了（水平 ≥ {s['stay_max_move']:g} 或高度变化 ≥ {s['stay_max_height']:g}），且所到位置这一回合的"
            f"威胁危险度比起点高 {q['push_min_delta']:g} 以上（走近敌人、去拉新的枪线）。从首杀起一直对枪打到回合结束的也算。",
            f"③ 激进·被动寻求对枪：观察结束时暴露在威胁下（危险度 ≥ {s['passive_min_danger']:g}），并且观察期内平均至少 "
            f"{s['facing_min_share']:.0%} 的威胁在他视线前方（左右 {s['facing_max_angle']:g}° 以内；威胁含已知与可能的，按距离加权，"
            "不要求此刻看得到——架的是敌人会绕出来的拐角）——架着敌人会来的方向等人；挪没挪动都算。",
            "④ 保守：其余——往后撤（危险度明显下降）、躲进掩体看不到威胁、或者没在看威胁的方向。按 ①→④ 顺序，先满足的为准。",
            f"这一回合的威胁：交战结束前 {q['memory_seconds']:g} 秒到观察结束，CT 方知道的每名 T 的最新位置——本队雷达发现的、"
            "任一活着的 CT 听得到的脚步 / 声音与枪声（队友听到也算）、交战中被他打死的 T 的位置。之后阵亡的 T 不再算。",
            f"可能威胁：同一首杀方向、同一回合阶段，历史上 T 通常在哪（其他比赛统计）；本队最近 {q['clear_seconds']:g} 秒视野里看过的格子算已清；"
            f"总量按位置不明的 T 人数缩放，按已知威胁的 {q['potential_weight']:g} 倍计入；烟后面的不算。",
            f"威胁危险度：能直接看到这个位置的威胁按距离加权求和（{q['zero_weight_distance']:g} 以外为 0），墙或烟挡住的已知威胁按 "
            f"{q['hidden_weight']:g} 计入。观察期内生效过的烟都算。",
            "热力图：「实时威胁」= 播放到的这一刻；「程序用的威胁」就是程序比较起点、终点用的那张。",
        ]
        return lines
    lines.append(
        f"② 激进·原位架枪：观察结束时离交战结束时的位置水平不到 {s['stay_max_move']:g} 单位（约跑 1 秒），"
        f"且高度变化不到 {s['stay_max_height']:g} 单位。只看位置，转身、退半步躲掩体也算原位（视线另行记录，不参与判断）。")
    if s.get("patch_own_smoke"):
        lines.insert(-1, f"①½ 保守（身边有烟）：观察期内至少 {s['own_smoke_min_share']:.0%} 的时间，他身边 {s['own_smoke_radius']:g} 单位以内"
                         "有生效中的烟——躲在烟里 / 烟后，不论移动多少，先于 ② 判断。")
    if s.get("danger_method", "map") == "threat":
        q = cfg["research"]["threat"]
        lines += [
            f"③ 激进·前压：移动了，且所到位置这一回合的威胁危险度比起点高 {q['push_min_delta']:g} 以上（走近敌人、暴露给更多敌人）。",
            f"④ 保守：其余（同等危险的横移也算）；危险度降低 {-q['retreat_max_delta']:g} 以上另记为后撤。按 ①→④ 顺序，先满足的为准。"
            + (f"横移补丁：危险度变化在前压与后撤之间的横移，如果沿导航路线往 T 出生点缩短了至少 {s['sideways_min_progress']:g} 单位，"
               "改算激进·前压（去找下一个对枪位）。" if s.get("patch_sideways_toward_t") else ""),
            f"这一回合的威胁：交战结束前 {q['memory_seconds']:g} 秒到观察结束，CT 方知道的每名 T 的最新位置——本队雷达发现的、"
            "任一活着的 CT 听得到的脚步 / 声音与枪声（队友听到也算）、交战中被他打死的 T 的位置。之后阵亡的 T 不再算。",
            f"威胁危险度：能直接看到这个位置的威胁（眼睛高度、地图几何视线，生效中的烟会挡）按距离加权求和，距离 {q['full_weight_distance']:g} 起"
            f"线性递减、{q['zero_weight_distance']:g} 以外为 0；墙或烟挡住的威胁按 {q['hidden_weight']:g} 计入（拐角后的敌人也有危险）。"
            "起点和终点用同一组威胁比较。",
            "首杀前已经下包的回合不算（判看不出）。",
            "热力图（危险度按钮切换）：「这一回合威胁」就是程序用的那张，标记是 CT 方当时知道的威胁位置；「统计地图」是第 5 版的历史平均风险，仅供对照。",
        ]
    else:
        lines += [
        f"③ 激进·前压：移动了，且所到位置的危险度 ≥ 起点的 {s['push_danger_ratio']:g} 倍。",
            f"④ 保守：其余（同等危险的横移也算）；危险度降到起点的 {s['retreat_danger_ratio']:g} 倍以下另记为后撤。按 ①→④ 顺序，先满足的为准。",
            f"危险度：按本回合首杀方向（A / B / 中路，看第一个死者的位置）分组，统计其他所有比赛中首杀之后 {d['after_seconds']:g} 秒内，"
            "CT 在每个位置每停留 1 分钟被打死的次数，除以本组平均；1 = 平均，2 = 两倍危险。计算时不含本场比赛。"
            f"停留很少的位置向它所在区域层级（深处 / 防守位 / 接触区）的平均收缩（{d['shrink_seconds']:g} 人·秒），"
            "所以 T 方深处这类 CT 很少去的位置按接触区的平均风险算。",
            "首杀方向不归类（如首杀在 T 出生点）时没有危险度：原地不动仍判原位架枪，移动了判看不出。",
            "热力图：红 = 比本组平均危险，蓝 = 比平均安全，只画在玩家走得到的地方。数值是相对本组平均的倍数（1 = 平均，2 = 两倍）。页面按本回合首杀方向显示用全部比赛画的那张图；程序判断时去掉了本场比赛，189 场少一场，图上看不出差别。",
        ]
    return lines


STOP_NAMES = {"observe": "观察满", "death": "阵亡", "round_end": "回合结束", "new_fight": "又一次交战"}


def focus_fields(row: dict, observe: float) -> dict:
    """复盘包条目的观察时段：与程序一致，从交战结束到程序的截止时刻（阵亡、回合结束、又一次交战会提前截止）。"""
    end = float(row["engagement_end_s"])
    seen = row.get("observed_s")
    reason = row.get("stop_reason") if isinstance(row.get("stop_reason"), str) else None
    length = float(seen) if seen is not None and pd.notna(seen) else float(observe)
    note = f"他拿下首杀后，这一波交战在 {end:+.1f} 秒结束（程序判断），看之后 {length:.1f} 秒"
    if reason and reason != "observe":
        note += f"（{STOP_NAMES.get(reason, reason)}，到 {end + length:+.1f} 秒截止，之后的不算）"
    return {"focus": [end, round(end + length, 3)], "focus_note": note, "stop_reason": reason or "observe"}


def review(source: str, pack_name: str, seed: int, extract_name: str, root: Path = ROOT) -> Path:
    """标准答案集：按程序标签分层抽样的盲审复盘包，一半开发集、一半留出集。
    程序的答案和分组另存（answer_key.csv），复盘包和回放页里都不出现。"""
    import random
    import tomllib

    from research_review import _write_review

    cfg = load_config()
    p = cfg["research"]["stance_review"]
    windows = pd.read_csv(research_dir("stance", source, root) / "windows.csv", dtype={"player_id": str})
    windows = windows[windows["detail"].fillna("") != "died_in_fight"]  # 交战中阵亡：没有可判断的时段
    seen = set()
    for old in p["exclude_packs"]:  # 已经复核过的回合不再抽（检验定义要用没看过的样本）
        path = research_dir("review", old, root) / "review.toml"
        if path.exists():
            seen |= {(i["demo_id"], int(i["round_id"])) for i in tomllib.loads(path.read_text(encoding="utf-8"))["items"]}
    windows = windows[[(d, int(r)) not in seen for d, r in zip(windows["demo_id"], windows["round_id"])]]
    rng = random.Random(seed)
    picked, used = [], set()
    for label, count in p["counts"].items():
        rows = windows[windows["label"] == label].to_dict("records")
        rng.shuffle(rows)
        for _ in range(min(count, len(rows))):
            row = next((r for r in rows if r["match_id"] not in used), rows[0])  # 先取还没用过的比赛
            rows.remove(row)
            used.add(row["match_id"])
            picked.append(row)
    rng.shuffle(picked)
    holdout = set(rng.sample(range(len(picked)), round(len(picked) * p["holdout_share"])))
    observe = cfg["research"]["stance"]["observe_seconds"]
    items, key = [], []
    for i, row in enumerate(picked, 1):
        end = float(row["engagement_end_s"])
        until = end + observe + p["tail_seconds"]
        items.append({"id": f"g{i}", "demo_id": row["demo_id"], "match_id": row["match_id"], "round_id": int(row["round_id"]),
                      "player_id": str(row["player_id"]), "start_tick": int(row["first_kill_tick"]),
                      "player_name": row["player_name"], "start_place": row["start_place"] if isinstance(row["start_place"], str) else "",
                      "round_time_s": float(row["round_time_s"]),
                      "replay_until_s": max(8.0, until), **focus_fields(row, observe),
                      "archive": f"stance/{extract_name}/archive/{Path(row['demo_id']).stem}",
                      "jump_command": row["jump_command"], "result": "", "comment": ""})
        key.append({"id": f"g{i}", "split": "holdout" if i - 1 in holdout else "dev", "label": row["label"],
                    "behaviour": row["behaviour"], "detail": row["detail"], "move": row["move"], "height": row["height"],
                    "danger_start": row["danger_start"], "danger_end": row["danger_end"], "observed_s": row["observed_s"]})
    pack = research_dir("review", pack_name, root)
    pack.mkdir(parents=True, exist_ok=True)
    header = {"source": f"stance/{source}", "labels": review_labels(cfg),
              "note": "看拿下首杀的这名 CT：这一波交战结束后约 3 秒内，他是原地继续架枪、往更危险的地方压，还是换到更安全的位置"
                      "（盲审：不显示程序的答案）。说不清就选看不出，并在备注写原因"}
    _write_review(pack, header, items)
    pd.DataFrame(key).to_csv(pack / "answer_key.csv", index=False)
    print(f"已生成 {len(items)} 条：{pack / 'review.toml'}（{len(used)} 场比赛；开发集 {len(items) - len(holdout)}、留出集 {len(holdout)}）")
    return pack


def _markdown(table: pd.DataFrame) -> str:
    """混淆矩阵：行 = 人工，列 = 程序。"""
    head = "| 人工 \\ 程序 | " + " | ".join(map(str, table.columns)) + " |"
    rows = [f"| {idx} | " + " | ".join(str(v) for v in row) + " |" for idx, row in zip(table.index, table.to_numpy())]
    return "\n".join([head, "|---|" + "---:|" * len(table.columns), *rows])


BIG = {"激进·原位架枪": "激进", "激进·前压": "激进", "激进·主动寻求对枪": "激进", "激进·被动寻求对枪": "激进",
       "保守": "保守", "看不出": "看不出"}
# 旧选项（第 5/6 版标注）对应到第 7 版细类，只用于细类对照：前压 ≈ 主动寻求对枪，原位架枪 ≈ 被动寻求对枪
OLD_TO_SEEK = {"激进·前压": "激进·主动寻求对枪", "激进·原位架枪": "激进·被动寻求对枪"}


ADJUDICATION_SUFFIXES = ("-adj", "-ai")  # 裁定包：人工 vs 程序、AI vs 程序；按顺序覆盖，后面的更新


def adjudicated(pack_name: str, root: Path = ROOT) -> tuple[dict[str, str], list[str]]:
    """标准答案集的裁定结果：{条目 id: 最终答案}，以及用到的裁定包名。"""
    import tomllib

    final, used = {}, []
    for suffix in ADJUDICATION_SUFFIXES:
        path = research_dir("review", f"{pack_name}{suffix}", root) / "review.toml"
        if path.exists():
            answers = {i["id"]: i["result"] for i in tomllib.loads(path.read_text(encoding="utf-8"))["items"] if i.get("result")}
            if answers:
                final.update(answers)
                used.append(f"{pack_name}{suffix}")
    return final, used


def score(pack_name: str, source: str, split: str = "dev", root: Path = ROOT) -> str:
    """人工答案（review.toml 的 result）对照某次 label 的程序标签：一致率与混淆矩阵。
    默认只看开发集；留出集只在定稿时看一次（split="holdout"）。"""
    import tomllib

    pack = research_dir("review", pack_name, root)
    items = pd.DataFrame(tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))["items"])
    items = items.merge(pd.read_csv(pack / "answer_key.csv")[["id", "split"]], on="id")
    final, used = adjudicated(pack_name, root)
    overridden = int(items["id"].isin(final).sum())
    items["result"] = [final.get(i, r) for i, r in zip(items["id"], items["result"])]
    items = items[(items["result"].fillna("") != "") & ((items["split"] == split) | (split == "all"))]
    windows = pd.read_csv(research_dir("stance", source, root) / "windows.csv", dtype={"player_id": str})
    items["player_id"] = items["player_id"].astype(str)
    merged = items.merge(windows[["demo_id", "round_id", "player_id", "behaviour"]], on=["demo_id", "round_id", "player_id"])
    if merged.empty:
        return f"{pack_name}（{split}）还没有人工答案"
    lines = [f"# {pack_name} 对照 stance/{source}（{split}，{len(merged)} 条）", "",
             *([f"已用 {'、'.join(used)} 的裁定结果覆盖 {overridden} 条人工答案", ""] if overridden else [])]
    fine, program_fine = merged["result"], merged["behaviour"]
    if (merged["behaviour"].str.contains("寻求").any() or merged["result"].str.contains("寻求").any()):
        # 新旧选项混用时统一换成第 7 版的名称再比细类
        fine = fine.map(lambda r: OLD_TO_SEEK.get(r, r))
        program_fine = program_fine.map(lambda r: OLD_TO_SEEK.get(r, r))
        lines += ["细类对照时，旧选项按 前压 ≈ 主动寻求对枪、原位架枪 ≈ 被动寻求对枪 对应（只是近似）", ""]
    for name, human, program in (("细类", fine, program_fine),
                                 ("激进 / 保守", merged["result"].map(BIG), merged["behaviour"].map(BIG))):
        lines += [f"## {name}：一致 {int((human == program).sum())}/{len(merged)}（{(human == program).mean():.0%}）", "",
                  _markdown(pd.crosstab(human.rename("人工"), program.rename("程序"))), ""]
    wrong = merged[merged["result"].map(BIG) != merged["behaviour"].map(BIG)]
    lines += ["## 大类不一致的条目", ""] + [f"- {r.id}：人工 {r.result}，程序 {r.behaviour}" + (f"；备注：{r.comment}" if r.comment else "")
                                         for r in wrong.itertuples()]
    return "\n".join(lines) + "\n"


def compare_pack(source_a: str, source_b: str, pack_name: str, extract_name: str, exclude_pack: str = "gold-1",
                 root: Path = ROOT) -> Path:
    """两次 label 结果（如两个参数）判断不同的窗口做成盲审复盘包，看哪一版更对；两版答案另存 answer_key.csv。
    exclude_pack 里的回合（标准答案集，含留出集）不抽，避免以后检验时用到看过的样本。"""
    import tomllib

    from research_review import _write_review

    cfg = load_config()
    a = pd.read_csv(research_dir("stance", source_a, root) / "windows.csv", dtype={"player_id": str})
    b = pd.read_csv(research_dir("stance", source_b, root) / "windows.csv", dtype={"player_id": str})
    keys = ["demo_id", "round_id", "player_id"]
    m = a.merge(b[keys + ["behaviour"]], on=keys, suffixes=("_a", "_b"))
    m = m[m["behaviour_a"] != m["behaviour_b"]]
    seen = set()
    old = research_dir("review", exclude_pack, root) / "review.toml"
    if old.exists():
        seen = {(i["demo_id"], int(i["round_id"])) for i in tomllib.loads(old.read_text(encoding="utf-8"))["items"]}
    m = m[[(d, int(r)) not in seen for d, r in zip(m["demo_id"], m["round_id"])]].sample(frac=1, random_state=1)
    observe = cfg["research"]["stance"]["observe_seconds"]
    tail = cfg["research"]["stance_review"]["tail_seconds"]
    items, key = [], []
    for i, row in enumerate(m.itertuples(), 1):
        end = float(row.engagement_end_s)
        items.append({"id": f"c{i}", "demo_id": row.demo_id, "match_id": row.match_id, "round_id": int(row.round_id),
                      "player_id": str(row.player_id), "start_tick": int(row.first_kill_tick), "player_name": row.player_name,
                      "start_place": row.start_place if isinstance(row.start_place, str) else "", "round_time_s": float(row.round_time_s),
                      "replay_until_s": max(8.0, end + observe + tail), **focus_fields(row._asdict(), observe),
                      "archive": f"stance/{extract_name}/archive/{Path(row.demo_id).stem}", "jump_command": row.jump_command,
                      "result": "", "comment": ""})
        key.append({"id": f"c{i}", source_a: row.behaviour_a, source_b: row.behaviour_b})
    pack = research_dir("review", pack_name, root)
    pack.mkdir(parents=True, exist_ok=True)
    header = {"source": f"stance/{source_b}", "labels": review_labels(cfg),
              "note": "看拿下首杀的这名 CT：这一波交战结束后约 3 秒内，他是原地继续架枪、往更危险的地方压，还是换到更安全的位置"
                      "（盲审：不显示程序的答案）。说不清就选看不出，并在备注写原因"}
    _write_review(pack, header, items)
    pd.DataFrame(key).to_csv(pack / "answer_key.csv", index=False)
    print(f"已生成 {len(items)} 条：{pack / 'review.toml'}")
    return pack


def relabel_pack(pack_name: str, new_pack: str, split: str = "dev", root: Path = ROOT) -> Path:
    """定义改了以后重标：同一批条目（默认只取开发集）、当前方案的选项、盲审（不显示以前的人工答案和程序答案）。"""
    import tomllib

    from research_review import _write_review

    cfg = load_config()
    pack = research_dir("review", pack_name, root)
    data = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))
    key = pd.read_csv(pack / "answer_key.csv")
    keep = set(key.loc[(key["split"] == split) | (split == "all"), "id"])
    items = [{**{k: v for k, v in i.items() if k not in ("result", "comment")}, "result": "", "comment": ""}
             for i in data["items"] if i["id"] in keep]
    dest = research_dir("review", new_pack, root)
    dest.mkdir(parents=True, exist_ok=True)
    header = {"source": data["source"], "labels": review_labels(cfg),
              "note": "按新定义重标：看拿下首杀的这名 CT 在这一波交战结束后约 3 秒内——主动找人打（往敌人那边走、去拉新枪线）、"
                      "被动等人打（准星架着敌人会出来的拐角 / 门口，人暴露在那条枪线上），还是避免对枪（后撤、躲进掩体、不看敌人方向）。"
                      "盲审：不显示以前的答案和程序的答案。说不清就选看不出，并在备注写原因"}
    _write_review(dest, header, items)
    key[key["id"].isin(keep)].to_csv(dest / "answer_key.csv", index=False)
    print(f"已生成 {len(items)} 条：{dest / 'review.toml'}")
    return dest


def _program_reveal(r, cfg: dict, labels: list[str]) -> list[str]:
    """裁定包共用的程序判断依据。"""
    q = cfg["research"]["threat"]
    stops = {"observe": "观察满", "death": "阵亡", "round_end": "回合结束", "new_fight": "又一次交战"}
    fmt = lambda v, d=0: "—" if pd.isna(v) else f"{v:.{d}f}"  # noqa: E731
    delta = r.threat_end - r.threat_start if pd.notna(r.threat_end) and pd.notna(r.threat_start) else float("nan")
    program = r.behaviour + (f"（≈ {OLD_TO_SEEK[r.behaviour][3:]}）" if r.behaviour in OLD_TO_SEEK
                             and "寻求" in "".join(labels) else "")
    return [f"程序的判断：{program}" + (f"（{r.detail}）" if isinstance(r.detail, str) else ""),
            f"交战在首杀后 {fmt(r.engagement_end_s, 1)} 秒结束；观察 {fmt(r.observed_s, 1)} 秒，{stops.get(r.stop_reason, r.stop_reason)}截止",
            f"移动 {fmt(r.move)} 单位，高度变化 {fmt(r.height)}（原位界线 {cfg['research']['stance']['stay_max_move']:g} / "
            f"{cfg['research']['stance']['stay_max_height']:g}）",
            f"威胁危险度 {fmt(r.threat_start, 2)} → {fmt(r.threat_end, 2)}（变化 {fmt(delta, 2)}；≥ {q['push_min_delta']:g} 为前压），"
            f"已知威胁 {fmt(r.threat_count)} 个",
            "看页面「程序用的威胁」：起点和终点就是在这张图上比较的"]


def adjudicate(pack_name: str, source: str, new_pack: str, split: str = "dev", root: Path = ROOT) -> Path:
    """裁定包：人工答案与程序大类不一致的条目，这次公开程序的判断和依据（不是盲审），由产品负责人给最终答案。"""
    import tomllib

    from research_review import _write_review

    cfg = load_config()
    pack = research_dir("review", pack_name, root)
    data = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))
    items = pd.DataFrame(data["items"]).merge(pd.read_csv(pack / "answer_key.csv")[["id", "split"]], on="id")
    items = items[(items["split"] == split) | (split == "all")]
    windows = pd.read_csv(research_dir("stance", source, root) / "windows.csv", dtype={"player_id": str})
    items["player_id"] = items["player_id"].astype(str)
    merged = items.merge(windows, on=["demo_id", "round_id", "player_id"], suffixes=("", "_w"))
    wrong = merged[merged["result"].map(BIG) != merged["behaviour"].map(BIG)]
    out = []
    for r in wrong.itertuples():
        reveal = [f"你之前的判断：{r.result}" + (f"（{r.comment}）" if r.comment else ""),
                  *_program_reveal(r, cfg, data.get("labels", []))]
        item = {k: getattr(r, k) for k in ("id", "demo_id", "match_id", "round_id", "player_id", "start_tick", "player_name",
                                          "start_place", "round_time_s", "replay_until_s", "focus", "focus_note", "archive",
                                          "jump_command")}
        out.append({**item, "reveal": reveal, "previous": r.result, "program": r.behaviour, "result": "", "comment": ""})
    dest = research_dir("review", new_pack, root)
    dest.mkdir(parents=True, exist_ok=True)
    header = {"source": f"stance/{source}", "labels": data.get("labels", review_labels(cfg)),
              "note": "裁定：这几条你和程序的大类不一致。这次公开程序的判断和依据，请重新看回放给出最终答案——"
                      "可能是你看漏了，也可能是程序错了；备注里写清楚理由"}
    _write_review(dest, header, out)
    print(f"已生成 {len(out)} 条裁定：{dest / 'review.toml'}")
    return dest


def ai_adjudicate(pack_name: str, source: str, new_pack: str, ai_names: list[str], split: str = "all",
                  root: Path = ROOT, include_all: bool = False) -> Path:
    """把 AI 与程序大类不一致的条目做成公开依据的裁定包；include_all 时 AI 与程序一致的条目也放进来（排在最后）。"""
    import tomllib

    from research_review import _write_review

    cfg = load_config()
    pack = research_dir("review", pack_name, root)
    data = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))
    items = pd.DataFrame(data["items"])
    key_path = pack / "answer_key.csv"
    if key_path.exists() and split != "all":
        key = pd.read_csv(key_path)[["id", "split"]]
        items = items.merge(key[key["split"] == split][["id"]], on="id")
    windows = pd.read_csv(research_dir("stance", source, root) / "windows.csv", dtype={"player_id": str})
    items["player_id"] = items["player_id"].astype(str)
    merged = items.merge(windows, on=["demo_id", "round_id", "player_id"], suffixes=("", "_w"))
    ai_results = []
    for spec in ai_names:
        subdir, name = spec.split("/", 1)
        path = pack / subdir / f"{name}.jsonl"
        answers = {row["id"]: row for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip() for row in [json.loads(line)]}
        display = f"{'文字' if subdir == 'text' else '看图' if subdir == 'vision' else subdir} {name}"
        ai_results.append((display, answers))

    selected = []
    for r in merged.itertuples():
        responses = [(display, answers.get(r.id, {})) for display, answers in ai_results]
        given = [(display, answer) for display, answer in responses if answer.get("label") is not None]
        dissent = [display for display, answer in given if BIG.get(answer["label"]) != BIG.get(r.behaviour)]
        if not dissent and not include_all:
            continue
        unanimous = len(dissent) == len(given) and len({BIG.get(answer["label"]) for _, answer in given}) == 1
        selected.append((r, responses, dissent, unanimous))
    selected.sort(key=lambda entry: (0 if entry[3] else 1 if entry[2] else 2, int(re.search(r"\d+", entry[0].id).group())))

    out = []
    for r, responses, dissent, _ in selected:
        if not dissent:
            kind = "AI 与程序一致"
        elif len(dissent) == len(ai_names) == 2:
            kind = "两个 AI 都反对程序"
        elif len(dissent) == 1:
            kind = f"只有 {dissent[0]} 反对程序"
        else:
            kind = f"{'、'.join(dissent)} 反对程序"
        reveal = [f"分歧类型：{kind}", *_program_reveal(r, cfg, data.get("labels", []))]
        for display, answer in responses:
            if answer.get("label") is None:
                reveal.append(f"{display}：失败——{answer.get('error') or '未给出答案'}")
            else:
                reveal.append(f"{display}：{answer['label']}（{answer.get('confidence') or '—'}）——{answer.get('evidence') or '—'}")
        item = {k: getattr(r, k) for k in ("id", "demo_id", "match_id", "round_id", "player_id", "start_tick", "player_name",
                                          "start_place", "round_time_s", "replay_until_s", "focus", "focus_note", "archive",
                                          "jump_command")}
        out.append({**item, "reveal": reveal, "program": r.behaviour, "result": "", "comment": ""})
    dest = research_dir("review", new_pack, root)
    dest.mkdir(parents=True, exist_ok=True)
    header = {"source": f"stance/{source}", "labels": data.get("labels", review_labels(cfg)),
              "note": "AI 与程序的判断不一致。页面公开了程序的依据和每个 AI 的理由；请看回放给出最终答案，备注写清楚谁对、为什么"}
    _write_review(dest, header, out)
    print(f"已生成 {len(out)} 条裁定：{dest / 'review.toml'}")
    return dest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sc = sub.add_parser("score", help="人工答案对照程序标签（默认只看开发集）")
    sc.add_argument("--pack", required=True)
    sc.add_argument("--source", required=True, help="label 的输出名")
    sc.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    cp = sub.add_parser("compare", help="两次 label 判断不同的窗口做成盲审复盘包（比较参数）")
    cp.add_argument("--a", required=True)
    cp.add_argument("--b", required=True)
    cp.add_argument("--extract", required=True)
    cp.add_argument("--name", required=True)
    ad = sub.add_parser("adjudicate", help="人工与程序不一致的条目做成裁定包（公开程序的判断和依据）")
    ad.add_argument("--pack", required=True)
    ad.add_argument("--source", required=True)
    ad.add_argument("--name", required=True)
    ad.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    aa = sub.add_parser("ai-adjudicate", help="AI 与程序大类不一致的条目做成裁定包")
    aa.add_argument("--pack", required=True)
    aa.add_argument("--source", required=True)
    aa.add_argument("--ai", action="append", required=True, help="子目录/结果名，可重复")
    aa.add_argument("--name", required=True)
    aa.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    aa.add_argument("--all", action="store_true", help="AI 与程序一致的条目也放进来（排在最后）")
    so = sub.add_parser("sounds", help="给已有的提取结果补上声音事件（只读这一种事件）")
    so.add_argument("--name", required=True)
    so.add_argument("--jobs", type=int)
    rv = sub.add_parser("review", help="抽标准答案集（盲审复盘包），再用 research_replay.py 生成回放页")
    rv.add_argument("--source", required=True, help="label 的输出名")
    rv.add_argument("--extract", required=True, help="extract 的输出名（回放从它的 archive 读取）")
    rv.add_argument("--name", required=True, help="复盘包名")
    rv.add_argument("--seed", type=int, default=1)
    ex = sub.add_parser("extract")
    ex.add_argument("--name", required=True)
    ex.add_argument("--jobs", type=int)
    ex.add_argument("--only", nargs="+", metavar="DEMO", help="只重新解析这些 demo，并入已有结果")
    lb = sub.add_parser("label")
    lb.add_argument("--source", required=True)
    lb.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    if args.command == "score":
        print(score(args.pack, args.source, args.split))
        return 0
    for value in (args.name, getattr(args, "source", "ok")):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            parser.error("名字只能含字母、数字、下划线和连字符")
    if args.command == "compare":
        compare_pack(args.a, args.b, args.name, args.extract)
        return 0
    if args.command == "adjudicate":
        adjudicate(args.pack, args.source, args.name, args.split)
        return 0
    if args.command == "ai-adjudicate":
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", spec) for spec in args.ai):
            parser.error("--ai 必须是 子目录/结果名")
        ai_adjudicate(args.pack, args.source, args.name, args.ai, args.split, include_all=args.all)
        return 0
    if args.command == "sounds":
        add_sounds(args.name, args.jobs or default_jobs(load_config()))
        return 0
    if args.command == "extract":
        print(extract(args.name, args.jobs or default_jobs(load_config()), args.only))
    elif args.command == "review":
        review(args.source, args.name, args.seed, args.extract)
    else:
        print(label_all(args.source, args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
