"""纯文字版 AI 判定：把回放里这名 CT 当时能知道的信息写成时间线，交给文字模型判断第 7 版的行为。

  python scripts/research_text_ai.py build gold-1-v7dev          # 生成时间线 data/research/review/<包>/text/<编号>.txt
  python scripts/research_text_ai.py judge gold-1-v7dev [--model deepseek-flash] [--concurrency 10] [--name t1]   # 会产生费用
  python scripts/research_text_ai.py compare gold-1-v7dev --source st-6b [--name t1]

只写他能知道的：自己的状态、本队雷达发现 / 任一 CT 听得到的 T（research/threat.known_threats，3 秒内最新）、
生效中的烟、队友、他自己的交火与击杀信息。不给上帝视角，不给程序的阈值和规则。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
REVIEW = ROOT / "data" / "research" / "review"

from cs2coach.config import load_config  # noqa: E402
from cs2coach.research.paths import research_dir  # noqa: E402
from cs2coach.research.threat import CT_TEAM, T_TEAM, MatchArchive, active_smoke_rows, known_threats  # noqa: E402
from research_vision import LABELS, parse  # noqa: E402

PROMPT = """你是 CS2 职业比赛的复盘分析员。下面是 Mirage 一个回合里一名 CT 的视角时间线（只有他和队友当时能知道的信息，没有上帝视角；语音不可知）。
0 秒 = 他拿下本回合首杀的时刻。

{timeline}

请判断：这名 CT 在这一波交战结束后约 3 秒内（{focus}）的行为属于哪一类——
- 激进·主动寻求对枪：主动去找人打——往敌人那边走、去拉新的枪线；
- 激进·被动寻求对枪：被动等人打——准星架着敌人会出来的具体拐角 / 门口，人暴露在那条枪线上（挪没挪动都算）；
- 保守：避免对枪——往后撤、躲进掩体、没在看敌人会来的方向、或者和敌人之间被烟挡住还停在原地；
- 看不出：信息不足以判断（如很快阵亡）。

只输出一个 JSON：{{"label": "四个选项之一", "confidence": "高|中|低", "evidence": "第几秒发生了什么"}}"""


def glossary(cfg: dict) -> str:
    """Mirage 报点按区域与纵深（前沿 / 防守位 / 后方，CT 视角）分组，帮模型理解方向。"""
    regions = cfg["research"]["regions"]["de_mirage"]
    groups: dict[str, list[str]] = {}
    for place, layer in regions.items():
        if "/" not in place:
            groups.setdefault(LAYER_NAMES.get(layer, layer), []).append(place)
    return "；".join(f"{k}：{'、'.join(sorted(v))}" for k, v in groups.items())


PROMPT_V2 = """你是 CS2 职业比赛的复盘分析员，熟悉 Mirage。下面是一个回合里一名 CT 的视角时间线（只有他和队友当时能知道的信息，没有上帝视角；语音不可知）。0 秒 = 他拿下本回合首杀的时刻。

Mirage 报点（CT 视角的纵深）：{glossary}。CT 从后方 → 防守位 → 前沿是往敌人那边推进，反过来是后撤。
时间线里"往 T 方推进 / 往 CT 方后撤 X"是他沿可走路线离 T 出生点近了 / 远了多少，比报点名更可靠。

要判断的三类（产品负责人的定义）：
- 激进·主动寻求对枪：主动去找人打——往敌人那边走（往 T 方推进）、去拉新的枪线；
- 激进·被动寻求对枪：被动等人打——停在 / 换到一个能架到敌人来路的位置，准星对着敌人会出来的具体拐角 / 门口，人暴露在那条枪线上；
- 保守：避免对枪——往 CT 方后撤（如压出去拿完人头回到包点、退到第二枪位）、躲进掩体、准星不看敌人来的方向、或和敌人之间被烟隔开还停在原地；包点主防在自己或队友扔的防守烟里 / 烟后拖时间、等队友回防，也算保守（目的是拖延，不是找人打）。
  注意：身边有烟本身不等于保守——借烟往前走、穿烟或绕烟去找人、在烟边上继续对枪，仍是寻求对枪；
- 看不出：信息不足（如很快阵亡）。

请按步骤想：① 交战结束后 3 秒内他整体是往 T 方推进、往 CT 方后撤，还是基本没动？② 他停下的位置是否暴露在敌人来路上？③ 准星是否对着敌人可能出来的方向？然后给结论。

{examples}

现在判断这一条，看 {focus} 这段：
{timeline}

只输出一个 JSON：{{"label": "四个选项之一", "confidence": "高|中|低", "evidence": "第几秒发生了什么、按步骤的判断"}}"""

EXAMPLES = [("w07-check", "c2", "保守", "交战结束后往 CT 方后撤，离开对枪位"),
            ("w07-check", "c9", "激进·主动寻求对枪", "拿完人头继续往 T 方推进"),
            ("w07-check", "c12", "激进·被动寻求对枪", "留在 B 包点附近，准星架着敌人来的方向")]


def examples_text() -> str:
    """产品负责人复核过的例子（不来自开发集、留出集），时间线由 build 时生成到 <包>/text/。"""
    out = ["产品负责人复核过的例子："]
    for k, (pack, gid, label, why) in enumerate(EXAMPLES, 1):
        path = research_dir("review", pack, ROOT) / "text" / f"{gid}.txt"
        if path.exists():
            out.append(f"【例 {k}】\n{path.read_text(encoding='utf-8')}\n答案：{label}（{why}）")
    return "\n\n".join(out)


def _place_centers(ticks: pd.DataFrame) -> dict[str, tuple[float, float]]:
    named = ticks[ticks["last_place_name"].fillna("") != ""]
    return {p: (float(g["X"].median()), float(g["Y"].median())) for p, g in named.groupby("last_place_name")}


def _place_of(x: float, y: float, centers: dict) -> str:
    return min(centers, key=lambda p: (centers[p][0] - x) ** 2 + (centers[p][1] - y) ** 2) if centers else "?"


def _look(x: float, y: float, yaw: float, centers: dict, here: str) -> str:
    """准星朝向的报点：视线左右 25 度内、3000 单位内最近的报点（不含脚下这个）。"""
    best, best_d = None, math.inf
    for p, (px, py) in centers.items():
        if p == here:
            continue
        d = math.hypot(px - x, py - y)
        ang = math.degrees(math.atan2(py - y, px - x))
        if d <= 3000 and abs((ang - yaw + 180) % 360 - 180) <= 25 and d < best_d:
            best, best_d = p, d
    return best or "（前方 3000 内没有报点，可能对着墙或角落）"


LAYER_NAMES = {"contact": "前沿", "hold": "防守位", "deep": "后方"}


def _layer(place: str, cfg: dict) -> str:
    return LAYER_NAMES.get(cfg["research"]["regions"].get("de_mirage", {}).get(place, ""), "")


def timeline(item: dict, arch: MatchArchive, events: pd.DataFrame, track: pd.DataFrame, cfg: dict, centers: dict,
             nav=None) -> str:
    rate = arch.rate
    from cs2coach.research.windows import _nav_distance

    goal = tuple(cfg["research"]["fight"]["t_spawn"]["de_mirage"])
    snap = cfg["research"]["nav_snap_max_distance"]
    first = int(item["start_tick"])
    end, stop = float(item["focus"][0]), float(item["focus"][1])
    memory = round(cfg["research"]["threat"]["memory_seconds"] * rate)
    me = int(item["player_id"])
    ticks = arch.ticks
    lines = [f"背景：他拿首杀时在 {item.get('start_place', '未知')}；冻结期结束后第 {float(item.get('round_time_s', 0)):.0f} 秒拿到首杀；"
             f"程序判断这一波交战在 {end:+.1f} 秒结束。"]
    base = track[(track["t"] - end).abs() < 0.07]
    bx, by, bz = (float(base["X"].iloc[0]), float(base["Y"].iloc[0]), float(base["Z"].iloc[0])) if len(base) else (None, None, None)
    base_nav = _nav_distance(nav, (bx, by, bz), goal, snap) if nav is not None and bx is not None else None
    prev = None
    taus = list(np.arange(end - 1.5, stop + 1e-6, 0.5))
    if stop - taus[-1] > 0.05:
        taus.append(stop)
    for tau in taus:
        tick = first + round(tau * rate)
        row = track.iloc[(track["t"] - tau).abs().argsort()[:1]]
        if row.empty or not bool(row["alive"].iloc[0]):
            lines.append(f"[{tau:+.1f}s] 你已阵亡。")
            break
        r = row.iloc[0]
        here = str(r["place"]) if isinstance(r["place"], str) and r["place"] else _place_of(r["X"], r["Y"], centers)
        speed = float(r["speed"]) if pd.notna(r.get("speed")) else 0.0
        move = "静止" if speed < 10 else "走 / 静步" if speed < 150 else "跑"
        rel = f"离交战结束时的位置 {math.hypot(r['X'] - bx, r['Y'] - by):.0f}，高度 {r['Z'] - bz:+.0f}" if bx is not None else ""
        if base_nav is not None:  # 沿可走路线到 T 出生点的距离变化：正 = 往 T 方（敌人那边）推进，负 = 往 CT 方后撤
            d = _nav_distance(nav, (float(r["X"]), float(r["Y"]), float(r["Z"])), goal, snap)
            if d is not None:
                change = base_nav - d
                rel += f"，{'往 T 方推进' if change >= 0 else '往 CT 方后撤'} {abs(change):.0f}"
        layer = _layer(here, cfg)
        look = _look(r['X'], r['Y'], float(r['yaw']), centers, here)
        look_layer = _layer(look, cfg)
        parts = [f"你：{here}{'（' + layer + '）' if layer else ''}（{rel}），{move}，准星朝 {look}{'（' + look_layer + '）' if look_layer else ''}，"
                 f"血 {int(r['health']) if pd.notna(r['health']) else '?'}，{r['weapon']}" + ("，开镜" if bool(r.get("scoped")) else "")]
        found = known_threats(arch, tick - memory, tick, cfg)
        if found:
            src = {"radar": "雷达", "sound": "听到声音", "gunshot": "听到枪声"}
            parts.append("已知敌人：" + "；".join(
                f"{src.get(th.source, th.source)} {_place_of(th.x, th.y, centers)} 约 {math.hypot(th.x - r['X'], th.y - r['Y']):.0f}"
                f"（{(tick - th.tick) / rate:.1f} 秒前）" for th in found))
        smokes = active_smoke_rows(arch, tick, cfg)
        if len(smokes):
            desc = []
            for s in smokes.sort_values(["x", "y"]).itertuples():
                who = "你扔的" if str(s.thrower) == str(me) else "T 扔的" if s.thrower_team_num == T_TEAM else "队友扔的"
                near = math.hypot(s.x - r["X"], s.y - r["Y"])
                desc.append(f"{_place_of(s.x, s.y, centers)}（{who}" + (f"，离你 {near:.0f}" if near <= 500 else "") + "）")
            parts.append("烟：" + "、".join(desc))
        now = ticks[ticks["tick"] == ticks.loc[ticks["tick"] <= tick, "tick"].max()]
        mates = now[(now["team_num"] == CT_TEAM) & now["is_alive"].astype(bool) & (now["steamid"] != me)]
        if len(mates):
            parts.append("队友：" + "、".join(_place_of(x, y, centers) for x, y in zip(mates["X"], mates["Y"])))
        alive_t = int(((now["team_num"] == T_TEAM) & now["is_alive"].astype(bool)).sum())
        parts.append(f"T 剩 {alive_t} 人")
        lo = first + round((prev if prev is not None else tau - 0.5) * rate)
        ev = events[(events["tick"] > lo) & (events["tick"] <= tick) & events["kind"].isin(["kill", "hurt", "hit", "death"])]
        names = {"kill": "你击杀 1 名 T", "hurt": "你被 T 打中", "hit": "你打中 T", "death": "你被击杀"}
        if len(ev):
            parts.append("事件：" + "、".join(names[k] for k in ev["kind"]))
        lines.append(f"[{tau:+.1f}s] " + "；".join(parts))
        prev = tau
    reason = item.get("stop_reason", "observe")
    if reason != "observe":
        names = {"death": "阵亡", "round_end": "回合结束", "new_fight": "又一次交战"}
        lines.append(f"[{stop:+.1f}s] 观察到此截止（{names.get(reason, reason)}）：之后发生的事不算，只按截止前的表现判断。")
    return "\n".join(lines)


_NAV = None


def _build_match(job: tuple[list[dict], str]) -> int:
    """一场比赛（同一个存档）的所有条目：存档、轨迹、交火事件只读一次。"""
    global _NAV
    items, out = job
    cfg = load_config()
    if _NAV is None:
        from awpy.map_control import NavMesh

        _NAV = NavMesh(str(Path.home() / ".awpy" / "2000930" / "navs" / "de_mirage.nav"))
    first = items[0]
    stem = Path(first["demo_id"]).stem
    extract = ROOT / "data" / "research" / Path(first["archive"]).parent.parent
    arch = MatchArchive(ROOT / "data" / "research" / first["archive"])
    centers = _place_centers(arch.ticks)
    tracks = pd.read_parquet(extract / "tracks" / f"{stem}.parquet")
    events = pd.read_parquet(extract / "events" / f"{stem}.parquet")
    for item in items:
        track = tracks[(tracks["round_id"] == int(item["round_id"])) & (tracks["player_id"].astype(str) == str(item["player_id"]))].sort_values("t")
        own = events[(events["round_id"] == int(item["round_id"])) & (events["player_id"].astype(str) == str(item["player_id"]))]
        (Path(out) / f"{item['id']}.txt").write_text(timeline(item, arch, own, track, cfg, centers, _NAV), encoding="utf-8")
    return len(items)


def build(pack: str, jobs: int = 1) -> None:
    """按比赛分组，多进程生成时间线；已经生成过的条目跳过。"""
    from concurrent.futures import ProcessPoolExecutor

    items = tomllib.loads((research_dir("review", pack, ROOT) / "review.toml").read_text(encoding="utf-8"))["items"]
    out = research_dir("review", pack, ROOT) / "text"
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[dict]] = {}
    for item in items:
        if not (out / f"{item['id']}.txt").exists():
            groups.setdefault(item["archive"], []).append(item)
    todo = [(g, str(out)) for g in groups.values()]
    start, done = time.time(), 0
    print(f"{sum(len(g) for g, _ in todo)} 条、{len(todo)} 场比赛，{jobs} 个进程", flush=True)
    if jobs <= 1:
        results = map(_build_match, todo)
    else:
        ex = ProcessPoolExecutor(jobs)
        results = ex.map(_build_match, todo)
    for n, count in enumerate(results, 1):
        done += count
        if n % 10 == 0:
            print(f"[{n}/{len(todo)} 场] {done} 条 {time.time() - start:.0f}s", flush=True)
    print(f"已生成 {len(items)} 条时间线：{out}")


def _clients(model: str) -> list:
    """mimo-* 用 .env 里所有 MiMo 账号（轮流分配，见 research_vision._clients）；其他模型走 DeepSeek。"""
    from openai import OpenAI

    if model.startswith("mimo"):
        import research_vision

        return research_vision._clients()
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DEEPSEEK_API_KEY=") and "DEEPSEEK_API_KEY" not in os.environ:
            os.environ["DEEPSEEK_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
    return [OpenAI(base_url="https://api.deepseek.com", api_key=os.environ["DEEPSEEK_API_KEY"])]


def judge(pack: str, model: str, concurrency: int, name: str, prompt: str = "v1") -> Path:
    """concurrency 是每个账号的并发数。"""
    clients = _clients(model)
    cfg = load_config()
    items = tomllib.loads((research_dir("review", pack, ROOT) / "review.toml").read_text(encoding="utf-8"))["items"]
    out = research_dir("review", pack, ROOT) / "text" / f"{name}.jsonl"
    done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines()} if out.exists() else set()

    def one(job: tuple[int, dict]) -> dict:
        n, item = job
        client = clients[n % len(clients)]
        end, stop = float(item["focus"][0]), float(item["focus"][1])
        tl = (research_dir("review", pack, ROOT) / "text" / f"{item['id']}.txt").read_text(encoding="utf-8")
        focus = f"{end:+.1f} 到 {stop:+.1f} 秒"
        text = (PROMPT_V2.format(glossary=glossary(cfg), examples=examples_text(), timeline=tl, focus=focus) if prompt == "v2"
                else PROMPT.format(timeline=tl, focus=focus))
        err = "失败"
        for attempt in range(4):
            try:
                t = time.time()
                r = client.chat.completions.create(model=model, max_tokens=8000, temperature=0,
                                                   messages=[{"role": "user", "content": text}])
                answer = parse(r.choices[0].message.content or "")
                if answer.get("label") is None and attempt < 3:
                    continue
                return {"id": item["id"], "model": model, **answer, "seconds": round(time.time() - t, 1),
                        "tokens_in": r.usage.prompt_tokens, "tokens_out": r.usage.completion_tokens}
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                time.sleep(5 * 2 ** attempt)
        return {"id": item["id"], "model": model, "label": None, "error": err}

    todo = [i for i in items if i["id"] not in done]
    start = time.time()
    print(f"{len(todo)} 条，{model}，{len(clients)} 个账号 × {concurrency} 路并发", flush=True)
    with ThreadPoolExecutor(concurrency * len(clients)) as ex, out.open("a", encoding="utf-8", newline="\n") as f:
        for n, res in enumerate(ex.map(one, enumerate(todo)), 1):
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{n}/{len(todo)}] {res['id']} {res.get('label')} {time.time() - start:.0f}s", flush=True)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("pack")
    b.add_argument("--jobs", type=int, default=1)
    j = sub.add_parser("judge")
    j.add_argument("pack")
    j.add_argument("--model", default="deepseek-flash")
    j.add_argument("--concurrency", type=int, default=10)
    j.add_argument("--name", default="t1")
    j.add_argument("--prompt", choices=["v1", "v2"], default="v2")
    c = sub.add_parser("compare")
    c.add_argument("pack")
    c.add_argument("--source", required=True)
    c.add_argument("--name", default="t1")
    args = parser.parse_args(argv)
    if args.command == "build":
        build(args.pack, args.jobs)
    elif args.command == "judge":
        print(judge(args.pack, args.model, args.concurrency, args.name, args.prompt))
    else:
        import research_vision as rv

        jsonl = research_dir("review", args.pack, ROOT) / "text" / f"{args.name}.jsonl"
        target = research_dir("review", args.pack, ROOT) / "vision" / f"text-{args.name}.jsonl"  # 复用看图版的三方对照
        target.write_text(jsonl.read_text(encoding="utf-8"), encoding="utf-8")
        text = rv.compare(args.pack, args.source, f"text-{args.name}")
        (research_dir("review", args.pack, ROOT) / "text" / f"compare-{args.name}.md").write_text(text, encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
