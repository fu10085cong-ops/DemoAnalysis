"""让看图模型像产品负责人一样看回放页，独立判断第 7 版的行为（主动 / 被动寻求对枪、保守）。

  python scripts/research_vision.py frames gold-1-v7dev [--jobs 6]
      用无界面 Edge 截回放页（只截地图），每条在交战结束前 1 秒到之后 3 秒取 6 帧，拼成一张图：
      data/research/review/<包>/vision/<编号>.png
  python scripts/research_vision.py judge gold-1-v7dev [--model mimo-v2.6-flash] [--concurrency 10（每个账号）] [--name v1]
      把拼图和基本信息发给看图模型（**会产生费用**），结果写入 vision/<名>.jsonl
  python scripts/research_vision.py compare gold-1-v7dev --source st-6b [--name v1]
      AI、程序、人工三方对照

给模型的只有画面和与人工复盘相同的定义说明，不给程序的阈值和规则（R03 的教训：给了规则 AI 只会重算）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from cs2coach.research.paths import research_dir
REVIEW = ROOT / "data" / "research" / "review"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
def offsets(item: dict) -> list[float]:
    """6 帧，相对交战结束的秒数：结束前 1 秒、结束时，再把程序的观察时段（到截止时刻）均分 4 帧。"""
    length = float(item["focus"][1]) - float(item["focus"][0])
    return [-1.0, 0.0] + [round(length * k / 4, 3) for k in range(1, 5)]
BOARD = (235, 12, 1275, 932)                  # 1600×1000 截图里地图区域的位置（左、上、右、下）
LABELS = ["激进·主动寻求对枪", "激进·被动寻求对枪", "保守", "看不出"]

PROMPT = """你是 CS2 职业比赛的复盘分析员。下面是一个回合的 2D 小地图回放，按时间顺序截了 6 帧（每帧左上角标了时间，0 秒 = 这名 CT 拿下本回合首杀的时刻）。

画面说明：蓝点 = CT，橙点 = T，带白圈和白色视线的大蓝点 = 要看的这名 CT；他前方的白色扇形是他准星正前方左右 20 度；
✕ = 阵亡；灰色大圆 = 烟；地图上的红黄色热力表示"这一刻 CT 方知道的敌人能看到这里"的危险程度（红 = 危险）；
◇ = 本队雷达发现的 T，虚线圈 = 听到的声音，+ = 听到的枪声，红 ✕ = 刚被他打死的 T；青色圈 = 敌人可能从这里出现的拐角（仅供参考，可能不准）。

背景：{info}

请判断：这名 CT 在这一波交战结束后约 3 秒内（{focus}）的行为属于哪一类——
- 激进·主动寻求对枪：主动去找人打——往敌人那边走、去拉新的枪线；
- 激进·被动寻求对枪：被动等人打——准星架着敌人会出来的具体拐角 / 门口，人暴露在那条枪线上（挪没挪动都算）；
- 保守：避免对枪——往后撤、躲进掩体、没在看敌人会来的方向、或者和敌人之间被烟挡住还停在原地；
- 看不出：画面不足以判断（如很快阵亡）。

只根据画面判断，不要假设看不到的信息。只输出一个 JSON：{{"label": "四个选项之一", "confidence": "高|中|低", "evidence": "第几帧看到了什么"}}"""


def _items(pack: str) -> list[dict]:
    return tomllib.loads((research_dir("review", pack, ROOT) / "review.toml").read_text(encoding="utf-8"))["items"]


def _shot(args: tuple) -> str:
    """一次无界面 Edge 截图；每个进程用自己的浏览器配置目录，互不干扰。"""
    page, index, t, out, profile = args
    url = f"file:///{page.as_posix()}#i={index}&t={t:.2f}&h=live"
    subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--window-size=1600,1000", "--virtual-time-budget=8000",
                    f"--user-data-dir={profile}", f"--screenshot={out}", url], capture_output=True, timeout=120)
    return str(out)


def frames(pack: str, jobs: int) -> None:
    from matplotlib import pyplot as plt  # 由带 matplotlib 的环境运行拼图那一步

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]

    items = _items(pack)
    folder = research_dir("review", pack, ROOT) / "vision"
    (folder / "raw").mkdir(parents=True, exist_ok=True)
    page = research_dir("review", pack, ROOT) / "replay.html"
    tasks = []
    for index, item in enumerate(items):
        end = float(item["focus"][0])
        for k, off in enumerate(offsets(item)):
            out = folder / "raw" / f"{item['id']}_{k}.png"
            if not out.exists():
                tasks.append((page, index, end + off, out, folder / f"profile{len(tasks) % jobs}"))
    start = time.time()
    print(f"截图 {len(tasks)} 张，{jobs} 个进程", flush=True)
    with ThreadPoolExecutor(jobs) as ex:
        for n, _ in enumerate(ex.map(_shot, tasks), 1):
            if n % 30 == 0:
                print(f"[{n}/{len(tasks)}] {time.time() - start:.0f}s", flush=True)
    import pandas as pd

    map_cfg = json.loads((Path.home() / ".awpy" / "2000930" / "map_data.json").read_text(encoding="utf-8"))["de_mirage"]
    left, top, right, bottom = BOARD
    fit = min(right - left, bottom - top) / 1024                       # 回放页把 1024 的小地图等比放进地图区域
    off_x, off_y = left + (right - left - 1024 * fit) / 2, top + (bottom - top - 1024 * fit) / 2
    for item in items:
        end = float(item["focus"][0])
        extract = Path(item["archive"]).parent.parent.name
        track = pd.read_parquet(research_dir("stance", extract, ROOT) / "tracks" / f"{Path(item['demo_id']).stem}.parquet")
        track = track[(track["round_id"] == int(item["round_id"])) & (track["player_id"].astype(str) == str(item["player_id"]))]
        fig, axes = plt.subplots(2, 3, figsize=(15, 10), dpi=80)
        for k, (ax, off) in enumerate(zip(axes.flat, offsets(item))):
            img = plt.imread(folder / "raw" / f"{item['id']}_{k}.png")
            # 以他这一刻的位置为中心裁一个正方形（约 3200 游戏单位见方），看清周围
            row = track.iloc[(track["t"] - (end + off)).abs().argsort()[:1]]
            cx = off_x + (float(row["X"].iloc[0]) - map_cfg["pos_x"]) / map_cfg["scale"] * fit if len(row) else (left + right) / 2
            cy = off_y + (map_cfg["pos_y"] - float(row["Y"].iloc[0])) / map_cfg["scale"] * fit if len(row) else (top + bottom) / 2
            half = 320
            x0 = int(min(max(cx - half, left), right - 2 * half))
            y0 = int(min(max(cy - half, top), bottom - 2 * half))
            ax.imshow(img[y0:y0 + 2 * half, x0:x0 + 2 * half])
            ax.set_title(f"第 {k + 1} 帧 · {end + off:+.2f} 秒" + ("（交战结束）" if off == 0 else ""), fontsize=13)
            ax.axis("off")
        fig.tight_layout()
        fig.savefig(folder / f"{item['id']}.png")
        plt.close(fig)
    print(f"完成 {time.time() - start:.0f}s：{folder}", flush=True)


def _clients() -> list:
    """`.env` / 环境变量里的 MIMO_API_KEY、MIMO_API_KEY_2、…：每个账号一个客户端，请求轮流分配。"""
    from openai import OpenAI

    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        key, _, value = line.partition("=")
        if key.startswith("MIMO_API_KEY") and key not in os.environ:
            os.environ[key] = value.strip().strip('"')
    keys = [os.environ[k] for k in sorted(os.environ) if re.fullmatch(r"MIMO_API_KEY(_\d+)?", k)]
    if not keys:
        raise SystemExit("没有 MIMO_API_KEY")
    return [OpenAI(base_url="https://api.xiaomimimo.com/v1", api_key=k) for k in keys]


def parse(text: str) -> dict:
    """取回复里第一个 JSON 对象；标签不在四个选项里记为无效。"""
    match = re.search(r"\{.*?\}", text or "", re.S)
    if not match:
        return {"label": None, "error": "没有 JSON"}
    try:
        answer = json.loads(match.group())
    except json.JSONDecodeError:
        return {"label": None, "error": "JSON 无效"}
    if answer.get("label") not in LABELS:
        answer["error"] = f"标签不在选项中：{answer.get('label')}"
    return answer


def info_text(item: dict) -> tuple[str, str]:
    end = float(item["focus"][0])
    info = (f"比赛 {item['match_id']} 第 {item['round_id']} 回合；要看的 CT 是 {item.get('player_name', '')}，"
            f"拿首杀时在 {item.get('start_place', '未知')}；冻结期结束后第 {float(item.get('round_time_s', 0)):.0f} 秒拿到首杀；"
            f"这一波交战在 {end:+.1f} 秒结束。")
    return info, f"{end:+.1f} 到 {float(item['focus'][1]):+.1f} 秒"


LEGEND = ("另附 6 帧小地图截图：蓝点 = CT，橙点 = T，带白圈的大蓝点 = 他，白色扇形 = 他准星前方左右 20 度；灰色大圆 = 烟；"
          "红黄热力 = 这一刻 CT 方知道的敌人能看到的地方；◇ 雷达、虚线圈 声音、+ 枪声、红 ✕ 刚被他打死的 T；"
          "青色圈 = 程序估计的敌人出现点（可能不准，仅供参考）。图和文字矛盾时以文字的移动方向为准。")


def judge(pack: str, model: str, concurrency: int, name: str, prompt: str = "v1") -> Path:
    clients = _clients()
    if prompt == "v2":
        sys.path.insert(0, str(ROOT / "scripts"))
        from cs2coach.config import load_config
        from research_text_ai import PROMPT_V2, examples_text, glossary

        cfg = load_config()
    items = _items(pack)
    out = research_dir("review", pack, ROOT) / "vision" / f"{name}.jsonl"
    done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines()} if out.exists() else set()

    def one(job: tuple[int, dict]) -> dict:
        n, item = job
        client = clients[n % len(clients)]
        info, focus = info_text(item)
        image = (research_dir("review", pack, ROOT) / "vision" / f"{item['id']}.png").read_bytes()
        if prompt == "v2":
            tl = (research_dir("review", pack, ROOT) / "text" / f"{item['id']}.txt").read_text(encoding="utf-8")
            text = PROMPT_V2.format(glossary=glossary(cfg), examples=examples_text(), timeline=tl, focus=focus) + "\n\n" + LEGEND
        else:
            text = PROMPT.format(info=info, focus=focus)
        content = [{"type": "text", "text": text},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}}]
        for attempt in range(4):
            try:
                t = time.time()
                r = client.chat.completions.create(model=model, max_tokens=4000, temperature=0,
                                                   messages=[{"role": "user", "content": content}])
                text = r.choices[0].message.content or ""
                answer = parse(text)
                if answer.get("label") is None and attempt < 3:
                    continue
                return {"id": item["id"], "model": model, **answer, "seconds": round(time.time() - t, 1),
                        "tokens_in": r.usage.prompt_tokens, "tokens_out": r.usage.completion_tokens, "raw": text}
            except Exception as e:  # 网络 / 限流：退避重试
                err = f"{type(e).__name__}: {e}"
                time.sleep(5 * 2 ** attempt)
        return {"id": item["id"], "model": model, "label": None, "error": err if "err" in dir() else "失败"}

    todo = [i for i in items if i["id"] not in done]
    start = time.time()
    print(f"{len(todo)} 条，{model}，{len(clients)} 个账号 × {concurrency} 路并发", flush=True)
    with ThreadPoolExecutor(concurrency * len(clients)) as ex, out.open("a", encoding="utf-8", newline="\n") as f:
        for n, res in enumerate(ex.map(one, enumerate(todo)), 1):
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{n}/{len(todo)}] {res['id']} {res.get('label')} {time.time() - start:.0f}s", flush=True)
    return out


def compare(pack: str, source: str, name: str) -> str:
    import pandas as pd

    big = {"激进·原位架枪": "激进", "激进·前压": "激进", "激进·主动寻求对枪": "激进", "激进·被动寻求对枪": "激进",
           "保守": "保守", "看不出": "看不出"}
    items = pd.DataFrame(_items(pack))
    from research_stance import adjudicated

    final, _ = adjudicated(pack, ROOT)
    items["result"] = [final.get(i, r) for i, r in zip(items["id"], items["result"])]
    ai = pd.DataFrame([json.loads(l) for l in (research_dir("review", pack, ROOT) / "vision" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()])
    windows = pd.read_csv(research_dir("stance", source, ROOT) / "windows.csv", dtype={"player_id": str})
    items["player_id"] = items["player_id"].astype(str)
    m = items.merge(windows[["demo_id", "round_id", "player_id", "behaviour"]], on=["demo_id", "round_id", "player_id"])
    m = m.merge(ai[["id", "label", "confidence", "evidence"]], on="id", how="left")
    h, p, a = m["result"].map(big), m["behaviour"].map(big), m["label"].map(big)
    lines = [f"# {pack}：AI（{name}）/ 程序（{source}）/ 人工 三方对照（{len(m)} 条，大类）", "",
             f"- AI 与人工一致：{int((a == h).sum())}/{len(m)}",
             f"- 程序与人工一致：{int((p == h).sum())}/{len(m)}",
             f"- AI 与程序一致：{int((a == p).sum())}/{len(m)}", "", "## AI 与程序不一致的条目", ""]
    for r, hh, pp, aa in zip(m.itertuples(), h, p, a):
        if aa != pp:
            lines.append(f"- {r.id}：AI {r.label}（{r.confidence}）｜程序 {r.behaviour}｜人工 {r.result}"
                         f"｜{'人工同 AI' if aa == hh else '人工同程序' if pp == hh else '三方都不同'}｜AI 依据：{r.evidence}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("frames")
    f.add_argument("pack")
    f.add_argument("--jobs", type=int, default=6)
    j = sub.add_parser("judge")
    j.add_argument("pack")
    j.add_argument("--model", default="mimo-v2.6-flash")
    j.add_argument("--concurrency", type=int, default=10)
    j.add_argument("--name", default="v1")
    j.add_argument("--prompt", choices=["v1", "v2"], default="v1")
    c = sub.add_parser("compare")
    c.add_argument("pack")
    c.add_argument("--source", required=True)
    c.add_argument("--name", default="v1")
    args = parser.parse_args(argv)
    if args.command == "frames":
        frames(args.pack, args.jobs)
    elif args.command == "judge":
        print(judge(args.pack, args.model, args.concurrency, args.name, args.prompt))
    else:
        text = compare(args.pack, args.source, args.name)
        (research_dir("review", args.pack, ROOT) / "vision" / f"compare-{args.name}.md").write_text(text, encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
