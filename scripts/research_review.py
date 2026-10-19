"""研究行为窗口的人工复核：分层抽样、游戏内复盘包、回填与混淆矩阵（R02）。

用法（以下参数接在同一条命令后）：
    python scripts/research_review.py build --source r01-v3 --name r02-1 --seed 20261019
    python scripts/research_review.py install r02-1 --csgo "D:/SteamLibrary/steamapps/common/Counter-Strike Global Offensive/game/csgo"
    python scripts/research_review.py score r02-1

清单是盲审：不显示程序给的标签。产品负责人在 review.toml 的 result 填"保持 / 前压 / 后撤 / 横向调整 / 看不出"。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
import tomllib
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cs2coach.config import load_config  # noqa: E402
from cs2coach.parsing.loader import resolve_demo  # noqa: E402

LABELS = {"保持": "hold", "前压": "advance", "后撤": "retreat", "横向调整": "lateral", "看不出": "unknown"}
NAMES = {value: key for key, value in LABELS.items()}
KEY = ["demo_id", "round_id", "player_id", "start_tick"]


def _name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError(f"目录名无效：{value}")
    return value


def _pack_dir(root: Path, name: str) -> Path:
    return root / "data" / "research" / "review" / _name(name)


def _windows(root: Path, source: str) -> pd.DataFrame:
    frame = pd.read_csv(root / "data" / "research" / _name(source) / "windows.csv", dtype={"player_id": str})
    frame["window_s"] = (frame["end_tick"] - frame["start_tick"]) / frame["tick_rate"]
    return frame


def _tier(demo_id: str, top_events: set[str], manifest: dict[str, str]) -> str:
    """不在 pro_mirage 清单里的是早期职业样本（ESL Pro League 等顶级赛事）。"""
    event = manifest.get(demo_id)
    return "顶级" if event is None or event in top_events else "二三线"


def _manifest(root: Path) -> dict[str, str]:
    path = root / "data" / "demos" / "pro_mirage" / "manifest.json"
    if not path.exists():
        return {}
    return {item["file"]: item.get("event") for item in json.loads(path.read_text(encoding="utf-8"))["matches"]}


def sample(frame: pd.DataFrame, counts: dict[str, int], short_unknown: int, seed: int,
           top_events: set[str], manifest: dict[str, str]) -> pd.DataFrame:
    """按主标签分层；每层顶级与二三线各半；同一回合最多一个窗口。unknown 中 short_unknown 个取 1 秒内结束的窗口。"""
    rng = random.Random(seed)
    frame = frame.assign(tier=[_tier(d, top_events, manifest) for d in frame["demo_id"]])
    picked, used_rounds = [], set()

    def take(pool: pd.DataFrame, n: int) -> None:
        rows = pool.to_dict("records")
        rng.shuffle(rows)
        by_tier = {"顶级": [r for r in rows if r["tier"] == "顶级"], "二三线": [r for r in rows if r["tier"] == "二三线"]}
        order = ["顶级", "二三线"] if rng.random() < .5 else ["二三线", "顶级"]
        got, turn = 0, 0
        while got < n and any(by_tier.values()):
            bucket = by_tier[order[turn % 2]]
            turn += 1
            while bucket:
                row = bucket.pop()
                round_key = (row["match_id"], row["round_id"])
                if round_key not in used_rounds:
                    used_rounds.add(round_key)
                    picked.append(row)
                    got += 1
                    break

    for label, n in counts.items():
        pool = frame[frame["main_label"] == label]
        if label == "unknown" and short_unknown:
            take(pool[pool["window_s"] < 1], short_unknown)
            take(pool[pool["window_s"] >= 1], n - short_unknown)
        else:
            take(pool, n)
    result = pd.DataFrame(picked)
    rng.shuffle(order := list(range(len(result))))  # 打乱顺序，避免按标签排队暴露答案
    return result.iloc[order].reset_index(drop=True)


def _stem(demo_id: str) -> str:
    stem = demo_id[:-4] if demo_id.lower().endswith(".dem") else demo_id
    if not re.fullmatch(r"[A-Za-z0-9_-]+", stem):
        raise ValueError(f"demo 文件名无法用于 CS2 控制台：{demo_id}")
    return stem


def build(args, root: Path, cfg: dict) -> Path:
    review = cfg["research"]["review"]
    frame = _windows(root, args.source)
    counts = dict(review["counts"])
    picked = sample(frame, counts, review["short_unknown"], args.seed, set(review["top_events"]), _manifest(root))
    pack = _pack_dir(root, args.name)
    pack.mkdir(parents=True, exist_ok=True)
    subdir = review["demo_subdir"]
    lead = review["lead_seconds"]
    items, checklist, aliases = [], [], ["// 研究复核：先 exec cs2coach_research，再按清单输入 p1 加载、r1 跳转"]
    checklist += ["# 研究复核清单（盲审）", "",
                  "控制台先输入 `exec cs2coach_research` 一次。每条先输入 `p编号` 加载 demo，加载完成后输入 `r编号`，",
                  f"画面会停在 T 方第一个人阵亡前 {lead:g} 秒。切到指定 CT 选手的视角，看 T 阵亡后的 {review_window(cfg):g} 秒他做了什么，",
                  "在 review.toml 的 result 填：保持 / 前压 / 后撤 / 横向调整 / 看不出；有特别情况写在 comment。", "",
                  "- 保持：基本留在原来的位置", "- 前压：往 T 的方向、往接触区推进", "- 后撤：往自己的后方、纵深撤",
                  "- 横向调整：换了位置，但没有明显往前或往后（如换到另一侧、去补另一个点）", "- 看不出：太短、太乱或无法判断", ""]
    for index, row in enumerate(picked.to_dict("records"), 1):
        jump = max(0, int(row["start_tick"] - lead * row["tick_rate"]))
        stem = _stem(row["demo_id"])
        items.append({"id": f"w{index}", **{k: str(row[k]) if k == "player_id" else row[k] for k in KEY},
                      "match_id": row["match_id"], "result": "", "comment": ""})
        aliases += [f'alias p{index} "playdemo {subdir}/{stem}"', f'alias r{index} "demo_gototick {jump}"']
        checklist += [f"## w{index}", "",
                      f"- 比赛：`{row['match_id']}`（{row['tier']}）；回合 {row['round_id']}，冻结期结束后第 {row['round_time_s']:.0f} 秒",
                      f"- 看谁：**{row['player_name']}**（CT），T 阵亡时他在 {row['start_place'] or '未知地点'}",
                      f"- 加载：`p{index}`；跳转：`r{index}`（demo_gototick {jump}）", ""]
    toml = [f'source = "{args.source}"', f"seed = {args.seed}", f'demo_subdir = "{subdir}"', ""]
    for item in items:
        toml.append("[[items]]")
        for key, value in item.items():
            toml.append(f"{key} = {json.dumps(value, ensure_ascii=False) if isinstance(value, str) else value}")
        toml.append("")
    (pack / "review.toml").write_text("\n".join(toml), encoding="utf-8", newline="\n")
    (pack / "checklist.md").write_text("\n".join(checklist), encoding="utf-8", newline="\n")
    (pack / "cs2coach_research.cfg").write_text("\n".join(aliases) + "\n", encoding="utf-8", newline="\n")
    print(f"已生成 {len(items)} 条：{pack}")
    return pack


def review_window(cfg: dict) -> float:
    return cfg["research"]["window_seconds"]


def _demo_paths(root: Path) -> dict[str, tuple[Path, str | None]]:
    sys.path.insert(0, str(root / "scripts"))
    from research_export import sources  # 与导出时同一套 demo 来源
    return {(member or path.name): (path, member) for path, member in sources("de_mirage")}


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, i


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def game_protocol(csgo: Path) -> int | None:
    """CS2 安装目录 steam.inf 的 PatchVersion（如 1.41.9.1）去掉点即 demo 文件头的网络协议版本（14191）。"""
    inf = csgo / "steam.inf"
    if not inf.exists():
        return None
    for line in inf.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("PatchVersion="):
            digits = line.split("=", 1)[1].strip().replace(".", "")
            return int(digits) if digits.isdigit() else None
    return None


def patch_protocol(path: Path, protocol: int) -> int | None:
    """把 demo 副本文件头（第一条 DEM_FileHeader，未压缩）的 network_protocol（字段 2）改成 protocol。

    只在新旧版本号编码长度相同时原地改写，文件其余字节不动；返回原版本号，无需修改或无法修改时返回 None。
    游戏更新后旧 demo 只是被版本检查拒绝、网络格式未变时，这样即可播放（2026-10-19 实机：14188 → 14191 正常）。
    """
    with path.open("r+b") as f:
        head = f.read(4096)
        if head[:8] != b"PBDEMS2\x00":
            return None
        cmd, i = _varint(head, 16)
        _, i = _varint(head, i)
        size, i = _varint(head, i)
        if cmd != 1:  # 必须是未压缩的文件头
            return None
        j, end = i, i + size
        while j < end:
            key, j = _varint(head, j)
            field, wire = key >> 3, key & 7
            if wire == 0:
                start = j
                value, j = _varint(head, j)
                if field == 2:
                    new = _encode_varint(protocol)
                    if value == protocol or len(new) != j - start:
                        return None
                    f.seek(start)
                    f.write(new)
                    return value
            elif wire == 2:
                length, j = _varint(head, j)
                j += length
            else:
                return None
    return None


def install(args, root: Path, cfg: dict) -> None:
    pack = _pack_dir(root, args.name)
    data = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))
    csgo = Path(args.csgo or cfg["review"]["csgo_dir"])
    if not csgo.is_dir():
        raise ValueError(f"CS2 game/csgo 目录不存在：{csgo}")
    paths = _demo_paths(root)
    copies = {}
    for item in data["items"]:
        demo_id = item["demo_id"]
        if demo_id in copies:
            continue
        path, member = paths[demo_id]
        copies[demo_id] = (resolve_demo(path, member=member), csgo / data["demo_subdir"] / (_stem(demo_id) + ".dem"))
    copies["cfg"] = (pack / "cs2coach_research.cfg", csgo / "cfg" / "cs2coach_research.cfg")
    for source, target in copies.values():
        if target.exists() and source.suffix != ".cfg" and source.stat().st_size == target.stat().st_size:
            print(f"已存在，跳过：{target.name}")  # 副本的版本号可能已改过，不按内容比较
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print(f"已复制：{target}")
    protocol = game_protocol(csgo)
    if protocol is not None:
        for demo_id, (_, target) in copies.items():
            if demo_id != "cfg" and (before := patch_protocol(target, protocol)) is not None:
                print(f"已把副本版本号 {before} 改为 {protocol}：{target.name}")
    print("CS2 控制台先输入：exec cs2coach_research")


def _matrix(done: pd.DataFrame) -> list[str]:
    """混淆矩阵的 Markdown 表；行 = 程序标签，列 = 人工判断。"""
    order = list(NAMES)
    lines = ["| 程序 / 人工 | " + " | ".join(NAMES[c] for c in order) + " |", "|---|" + "---:|" * len(order)]
    for program in order:
        counts = [int(((done["main_label"] == program) & (done["human"] == human)).sum()) for human in order]
        if sum(counts):
            lines.append(f"| {NAMES[program]} | " + " | ".join(str(c) for c in counts) + " |")
    return lines


def score(args, root: Path, cfg: dict) -> Path:
    pack = _pack_dir(root, args.name)
    data = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))
    frame = _windows(root, data["source"]).astype({"player_id": str})
    rows = []
    for item in data["items"]:
        if item["result"] not in LABELS:
            continue
        match = frame[(frame["demo_id"] == item["demo_id"]) & (frame["round_id"] == item["round_id"])
                      & (frame["player_id"] == str(item["player_id"])) & (frame["start_tick"] == item["start_tick"])]
        if len(match) != 1:
            raise ValueError(f"{item['id']} 在研究表中找不到唯一对应的窗口")
        rows.append({**match.iloc[0].to_dict(), "human": LABELS[item["result"]], "id": item["id"], "comment": item["comment"]})
    done = pd.DataFrame(rows)
    lines = [f"# 复核结果：{args.name}", "", f"已填 {len(done)} / {len(data['items'])} 条。", ""]
    if not done.empty:
        agree = (done["human"] == done["main_label"]).mean()
        judged = done[done["human"] != "unknown"]
        lines += [f"- 程序与人工一致：{agree:.0%}（{(done['human'] == done['main_label']).sum()}/{len(done)}）",
                  f"- 人工能判断的窗口中一致：{(judged['human'] == judged['main_label']).mean():.0%}（{len(judged)} 条）" if len(judged) else "", "",
                  "## 混淆矩阵（行 = 程序，列 = 人工）", "",
                  *_matrix(done), "",
                  "## 不一致的窗口", "",
                  "| 编号 | 程序 | 人工 | 位移 | 路径距离变化 | 窗口秒数 | 备注 |", "|---|---|---|---:|---:|---:|---|"]
        for row in done[done["human"] != done["main_label"]].to_dict("records"):
            lines.append(f"| {row['id']} | {NAMES[row['main_label']]} | {NAMES[row['human']]} | {row['displacement']:.0f} | "
                         f"{row['nav_delta']:.0f} | {row['window_s']:.1f} | {row['comment']} |")
    (pack / "score.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return pack / "score.md"


def main(argv=None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--source", required=True, help="data/research/ 下的导出目录名")
    b.add_argument("--name", required=True)
    b.add_argument("--seed", type=int, default=0)
    i = sub.add_parser("install")
    i.add_argument("name")
    i.add_argument("--csgo", default=None)
    s = sub.add_parser("score")
    s.add_argument("name")
    args = parser.parse_args(argv)
    cfg = load_config()
    {"build": build, "install": install, "score": score}[args.command](args, root, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
