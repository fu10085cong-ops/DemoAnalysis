"""R03 AI 判定试点：交战后换位（docs/tasks/research/R03-ai-post-fight-reposition.md）。

  sample     从研究表抽适用窗口（自己拿第一个人头 / 队友拿 各半，同一回合最多一个）
  timelines  解析涉及的 demo，生成选手视角时间线与规则基线（占 CPU，先通知产品负责人）
  run        把时间线交给 AI 判断，每条判 repeats 遍；不加 --yes 只估算，不调用（真实调用会产生费用）
  compare    规则 vs AI、AI 两遍之间的一致率；生成人工复核包（不一致的窗口 + 一致中随机 5 条）

输出在 data/research/ai/<名>/；复核包在 data/research/review/<名>/，回放页用 research_replay.py 生成。
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cs2coach.config import load_config  # noqa: E402
from cs2coach.parallel import default_jobs, map_ordered  # noqa: E402
from cs2coach.research.fight import LABELS  # noqa: E402

NAMES = list(LABELS.values())
KEY = ["demo_id", "match_id", "round_id", "player_id", "start_tick"]

SYSTEM = """你是 CS2 职业比赛的分析员。用户会给你一名 CT 选手的"选手视角时间线"：只包含他当时能看到、听到、从雷达和队友那里知道的信息。

任务：判断他在【交火结束】之后约 3 秒内做了什么，从下面五个标签中选一个。时间线每行给出你所在的报点和区域层级
（后方 < 防守区 < 接触区，防守区还分 A / B / 中路等组）、离交火结束时位置的距离、离交火对手的距离：
- 原位再架：离交火结束时位置挪动不到约 100 单位（转头看别处、原地调整都算）
- 换位：挪动超过约 100 单位，但仍在同一个报点或同一组防守区内，换到另一个点或角度
- 撤离：往后退了一层（接触区→防守区、防守区→后方）；或离开了所在报点 / 防守区组，并且离交火对手明显更远（约 250 以上）
- 继续压：往前进了一层（例如防守区→接触区）；或离交火对手明显更近（约 150 以上）
- 看不出：交火后很快阵亡、回合结束，或信息不足以判断
按观察期结束时（交火结束后约 3 秒）相对交火结束时的位置判断；中途走出去又回来，以结束时为准，并在依据里说明。

要求：
1. 交火结束默认以时间线中"程序判断的交火结束"为准；如果你认为交火在别的时刻结束，用你的判断，并写进 fight_end_s
2. 只根据时间线里的信息判断，不要猜测语音沟通或时间线以外的事
3. 依据要写明第几秒发生了什么（例如"+1.5s 到 +3.0s 从 A 包点走到 Jungle，离交火结束时位置 260"）

只输出一个 JSON 对象，不要输出其他文字：
{"label": "换位|原位再架|撤离|继续压|看不出", "fight_end_s": 数字或 null, "evidence": "依据", "confidence": "高|中|低"}"""


def _dir(root: Path, name: str) -> Path:
    return root / "data" / "research" / "ai" / name


# ---------- sample ----------

def sample(frame: pd.DataFrame, per_group: int, seed: int) -> pd.DataFrame:
    """自己拿第一个人头 / 队友拿 各 per_group 个；队友组只取窗口内开过枪或有伤害的；同一回合最多一个，按比赛轮流取。"""
    rng = random.Random(seed)
    engaged = (frame["shots"] > 0) | (frame["damage_dealt"] > 0) | (frame["damage_taken"] > 0)
    groups = {"self": frame[frame["first_kill_by"] == "self"],
              "teammate": frame[(frame["first_kill_by"] == "teammate") & engaged]}
    used: set[tuple[str, int]] = set()
    picked = []
    for group, pool in groups.items():
        by_match: dict[str, list[dict]] = {}
        for row in pool.to_dict("records"):
            by_match.setdefault(row["match_id"], []).append(row)
        matches = sorted(by_match)
        rng.shuffle(matches)
        for rows in by_match.values():
            rng.shuffle(rows)
        taken = 0
        while taken < per_group and any(by_match[m] for m in matches):
            for match in matches:
                while by_match[match]:
                    row = by_match[match].pop()
                    if (row["match_id"], row["round_id"]) not in used:
                        used.add((row["match_id"], row["round_id"]))
                        picked.append(row)
                        taken += 1
                        break
                if taken >= per_group:
                    break
    out = pd.DataFrame(picked)
    out = out.sample(frac=1, random_state=seed).reset_index(drop=True)
    out.insert(0, "id", [f"f{i + 1}" for i in range(len(out))])
    return out


# ---------- timelines ----------

def _events(demo: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    from demoparser2 import DemoParser

    parser = DemoParser(str(demo))
    raw = parser.parse_event("player_sound", player=["X", "Y", "Z"])
    sounds = pd.DataFrame(columns=["tick", "steamid", "x", "y", "z", "radius", "step"])
    if len(raw):
        sounds = pd.DataFrame({"tick": raw["tick"].astype("int64"), "steamid": pd.to_numeric(raw["user_steamid"]),
                               "x": raw["user_X"], "y": raw["user_Y"], "z": raw["user_Z"],
                               "radius": raw["radius"].astype(float), "step": raw["step"].astype(bool)}).dropna()
    raw = parser.parse_event("player_ping")
    pings = pd.DataFrame(columns=["tick", "steamid", "x", "y", "z"])
    if len(raw):
        pings = pd.DataFrame({"tick": raw["tick"].astype("int64"), "steamid": pd.to_numeric(raw["user_steamid"]),
                              "x": raw["x"], "y": raw["y"], "z": raw["z"]}).dropna()
    return sounds, pings


def _work(path: Path, member: str | None, rows: list[dict], cfg: dict) -> list[dict]:
    from cs2coach.features.visibility import Sight
    from cs2coach.parsing.loader import resolve_demo
    from cs2coach.parsing.match import parse_match
    from cs2coach.research.fight import reposition
    from cs2coach.research.timeline import build_timeline

    demo = resolve_demo(path, member=member)
    match = parse_match(demo, cfg)
    sight = Sight(match, cfg)
    sounds, pings = _events(demo)
    return [timeline_record(match, row, sight, sounds, pings, cfg, reposition, build_timeline) for row in rows]


def timeline_record(match, row: dict, sight, sounds, pings, cfg, reposition, build_timeline) -> dict:
    rate = match.meta.tick_rate
    lead = round(cfg["research"]["timeline"]["lead_seconds"] * rate)
    result = reposition(match, int(row["player_id"]), int(row["round_id"]), int(row["start_tick"]), cfg,
                        sight=sight, lead_ticks=lead)
    text = build_timeline(match, row, result, sight, sounds, pings, cfg)
    fight = result.fight
    rel = lambda tick: None if tick is None else round((tick - int(row["start_tick"])) / rate, 2)  # noqa: E731
    return {"id": row["id"], **{k: row[k] for k in KEY}, "player_name": row["player_name"],
            "first_kill_by": row["first_kill_by"], "rule": LABELS[result.label],
            "fight_start_s": rel(fight.start_tick if fight else None), "fight_end_s": rel(fight.end_tick if fight else None),
            "observe_end_s": rel(result.observe_end), "move": result.move, "closer": result.closer,
            "location_source": result.location_source, "text": text}


def timelines(name: str, jobs: int | None, root: Path = ROOT) -> Path:
    from research_export import sources

    cfg = load_config()
    out = _dir(root, name)
    frame = pd.read_csv(out / "sample.csv", dtype={"player_id": str})
    paths = {(member or path.name): (path, member) for path, member in sources("de_mirage")}
    by_demo: dict[str, list[dict]] = {}
    for row in frame.to_dict("records"):
        by_demo.setdefault(row["demo_id"], []).append(row)
    demos = sorted(by_demo)
    jobs = jobs or default_jobs(cfg)
    print(f"解析 {len(demos)} 份 demo，{len(frame)} 个窗口，{jobs} 个进程", flush=True)
    outcomes = map_ordered(_work, [(*paths[d], by_demo[d]) for d in demos], jobs, args=(cfg,),
                           on_done=lambda i, o: print(f"[{i + 1}/{len(demos)}] {demos[i]}"
                                                      + ("" if o.error is None else f" 失败：{o.error}"), flush=True))
    records = [r for o in outcomes if o.error is None for r in o.value]
    order = {row["id"]: i for i, row in enumerate(frame.to_dict("records"))}
    records.sort(key=lambda r: order[r["id"]])
    with (out / "timelines.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    counts = pd.Series([r["rule"] for r in records]).value_counts().to_dict()
    print(f"已生成 {len(records)}/{len(frame)} 条时间线：{out / 'timelines.jsonl'}；规则基线 {counts}")
    return out


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- run ----------

def parse_answer(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"label": None, "error": "没有 JSON"}
    try:
        answer = json.loads(match.group())
    except json.JSONDecodeError as exc:
        return {"label": None, "error": f"JSON 无法解析：{exc}"}
    if answer.get("label") not in NAMES:
        answer["error"] = f"标签不在 {NAMES} 中"
    return answer


def run(name: str, model: str | None, repeats: int | None, yes: bool, root: Path = ROOT, client=None,
        provider: str | None = None) -> Path:
    from cs2coach.ai.providers import make_provider
    from cs2coach.env import load_env_file

    cfg = load_config()
    ai = cfg["research"]["ai"]
    provider = provider or cfg["ai"]["provider"]
    model = model or (ai["model"] if provider == cfg["ai"]["provider"] else cfg["ai"][provider]["model"])
    repeats = repeats or ai["repeats"]
    out = _dir(root, name)
    records = _load(out / "timelines.jsonl")
    chars = sum(len(SYSTEM) + len(r["text"]) for r in records) * repeats
    print(f"{len(records)} 条 × {repeats} 遍，输入约 {chars} 字（模型 {model}）")
    if not yes:
        print("未调用：真实调用会产生费用，确认后加 --yes")
        return out
    if (root / ".env").exists():
        load_env_file(root / ".env")  # API key 放在 .env（不进仓库），与 CLI 相同
    run_cfg = copy.deepcopy(cfg)
    run_cfg["ai"]["provider"] = provider
    run_cfg["ai"][provider]["model"] = model
    results = out / "results"
    results.mkdir(exist_ok=True)
    for k in range(1, repeats + 1):
        path = results / f"{model}-{ai['prompt_version']}-r{k}.jsonl"
        done = {r["id"] for r in _load(path)} if path.exists() else set()
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            for record in records:
                if record["id"] in done:
                    continue
                service = make_provider(run_cfg, client)
                service.start(SYSTEM, [record["text"]], [])
                turn = service.send()
                answer = parse_answer(turn.text)
                stream.write(json.dumps({"id": record["id"], "model": turn.model or model,
                                         "prompt_version": ai["prompt_version"], **answer, "raw": turn.text,
                                         "input_tokens": turn.usage.input_tokens,
                                         "output_tokens": turn.usage.output_tokens}, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"[第 {k} 遍] {record['id']} {answer.get('label')}", flush=True)
    return out


# ---------- compare ----------

def _matrix(pairs: list[tuple[str, str]], rows_name: str, cols_name: str) -> list[str]:
    lines = [f"| {rows_name} / {cols_name} | " + " | ".join(NAMES) + " |", "|---|" + "---:|" * len(NAMES)]
    for a in NAMES:
        counts = [sum(1 for x, y in pairs if x == a and y == b) for b in NAMES]
        if sum(counts):
            lines.append(f"| {a} | " + " | ".join(str(c) for c in counts) + " |")
    return lines


def compare(name: str, model: str | None = None, seed: int = 20261019, root: Path = ROOT) -> Path:
    cfg = load_config()
    ai = cfg["research"]["ai"]
    model = model or ai["model"]
    out = _dir(root, name)
    records = {r["id"]: r for r in _load(out / "timelines.jsonl")}
    runs = sorted((out / "results").glob(f"{model}-{ai['prompt_version']}-r*.jsonl"))
    answers = [{r["id"]: r for r in _load(path)} for path in runs]
    if not answers:
        raise SystemExit(f"没有 {model} 的结果：{out / 'results'}")
    first = answers[0]
    ids = [i for i in records if i in first]
    lines = [f"# R03 交战后换位：规则 vs AI（{name}）", "", f"模型 {model}，提示词 {ai['prompt_version']}，{len(answers)} 遍，{len(ids)} 条", ""]
    agree = sum(records[i]["rule"] == first[i].get("label") for i in ids)
    lines += [f"- 规则与 AI 第 1 遍一致：{agree}/{len(ids)}"]
    if len(answers) > 1:
        both = [i for i in ids if i in answers[1]]
        stable = sum(first[i].get("label") == answers[1][i].get("label") for i in both)
        lines += [f"- AI 第 1 遍与第 2 遍一致：{stable}/{len(both)}"]
    for group, label in (("self", "自己拿第一个人头"), ("teammate", "队友拿")):
        part = [i for i in ids if records[i]["first_kill_by"] == group]
        lines.append(f"- {label}：规则与 AI 一致 {sum(records[i]['rule'] == first[i].get('label') for i in part)}/{len(part)}")
    lines += ["", "## 混淆矩阵（行 = 规则，列 = AI 第 1 遍）", ""]
    lines += _matrix([(records[i]["rule"], first[i].get("label")) for i in ids], "规则", "AI")
    review_ids = [i for i in ids if records[i]["rule"] != first[i].get("label")
                  or any(i in a and a[i].get("label") != first[i].get("label") for a in answers[1:])]
    agreed = [i for i in ids if i not in review_ids]
    review_ids += random.Random(seed).sample(agreed, min(5, len(agreed)))
    human = _human(root, name)
    if human:
        lines += ["", "## 人工复核", ""]
        scored = [i for i in review_ids if human.get(i)]
        lines.append(f"已复核 {len(scored)}/{len(review_ids)} 条：规则对 {sum(records[i]['rule'] == human[i] for i in scored)}，"
                     f"AI 对 {sum(first[i].get('label') == human[i] for i in scored)}")
    lines += ["", "## 需要人工复核的窗口", "", "| 编号 | 选手 | 第一个人头 | 规则 | AI | AI 第 2 遍 | 交火结束 | 依据（AI） |", "|---|---|---|---|---|---|---:|---|"]
    for i in review_ids:
        second = answers[1].get(i, {}).get("label", "") if len(answers) > 1 else ""
        lines.append(f"| {i} | {records[i]['player_name']} | {'自己' if records[i]['first_kill_by'] == 'self' else '队友'} | "
                     f"{records[i]['rule']} | {first[i].get('label')} | {second} | {records[i]['fight_end_s']} | "
                     f"{str(first[i].get('evidence', '')).replace('|', '/')} |")
    (out / "compare.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_pack(root, name, [records[i] for i in review_ids], cfg)
    print("\n".join(lines[:12]))
    print(f"已写入：{out / 'compare.md'}；复核包：data/research/review/{name}/（{len(review_ids)} 条）")
    return out


def _human(root: Path, name: str) -> dict[str, str]:
    import tomllib

    path = root / "data" / "research" / "review" / name / "review.toml"
    if not path.exists():
        return {}
    return {item["id"]: item["result"] for item in tomllib.loads(path.read_text(encoding="utf-8"))["items"] if item.get("result")}


def _write_pack(root: Path, name: str, records: list[dict], cfg: dict) -> None:
    from research_review import _write_review

    pack = root / "data" / "research" / "review" / name
    pack.mkdir(parents=True, exist_ok=True)
    if (pack / "review.toml").exists():
        return  # 不覆盖已作答的复核包
    source = json.loads((_dir(root, name) / "meta.json").read_text(encoding="utf-8"))["source"]
    header = {"source": source, "labels": NAMES,
              "note": "R03 交战后换位：判断交火结束后约 3 秒内他做了什么（盲审：不显示规则和 AI 的答案）"}
    items = [{"id": r["id"], **{k: r[k] for k in KEY}, "replay_until_s": max(11.0, (r["observe_end_s"] or 0) + 1),
              "result": "", "comment": ""} for r in records]
    _write_review(pack, header, items)


def main(argv=None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--source", default="r01-v6")
    s.add_argument("--name", required=True)
    s.add_argument("--per-group", type=int, default=20)
    s.add_argument("--seed", type=int, default=20261019)
    t = sub.add_parser("timelines")
    t.add_argument("name")
    t.add_argument("--jobs", type=int, default=None)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--provider", default=None, help="deepseek / mimo / claude / local，默认 [ai] provider")
    r.add_argument("--model", default=None)
    r.add_argument("--repeats", type=int, default=None)
    r.add_argument("--yes", action="store_true", help="确认真实调用（会产生费用）")
    c = sub.add_parser("compare")
    c.add_argument("name")
    c.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "sample":
        frame = pd.read_csv(root / "data" / "research" / args.source / "windows.csv", dtype={"player_id": str})
        picked = sample(frame, args.per_group, args.seed)
        out = _dir(root, args.name)
        out.mkdir(parents=True, exist_ok=True)
        picked.to_csv(out / "sample.csv", index=False)
        (out / "meta.json").write_text(json.dumps({"source": args.source, "seed": args.seed}, ensure_ascii=False), encoding="utf-8")
        print(f"已抽 {len(picked)} 条：{out / 'sample.csv'}（{picked['first_kill_by'].value_counts().to_dict()}，"
              f"{picked['match_id'].nunique()} 场比赛）")
    elif args.cmd == "timelines":
        timelines(args.name, args.jobs, root)
    elif args.cmd == "run":
        run(args.name, args.model, args.repeats, args.yes, root, provider=args.provider)
    else:
        compare(args.name, args.model, root=root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
