"""评测集：用固定的一批比赛重新生成报告，汇总违规、用量和耗时，供人工核对错误率（任务 021）。

用法：python scripts/eval_batch.py [--set data/eval/eval_set.toml] [--provider ...] [--model ...]
                                   [--label 说明] [--only 玩家02,ZywOo] [--jobs 5] [--yes]

每个 case 在独立进程里运行，输出写到该 case 目录的 run.log；API 模型默认 5 个同时跑，本地模型固定 1 个。

清单格式见 config/eval_set.example.toml。真实调用会产生费用：不加 --yes 时先确认。
输出在 data/output/评测/<日期时间>_<label>/<case 名>/，不覆盖各玩家文件夹中的正式报告。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SET = ROOT / "data" / "eval" / "eval_set.toml"
OUTPUT_ROOT = ROOT / "data" / "output" / "评测"
KINDS = ("analyze", "summary")


class EvalSetError(ValueError):
    pass


def load_cases(path: Path) -> list[dict[str, Any]]:
    """读取并校验清单；每个 case 需有 name、kind、player，analyze 还需 demo。"""
    if not path.is_file():
        raise EvalSetError(f"评测清单不存在：{path}（格式见 config/eval_set.example.toml）")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cases = data.get("case")
    if not isinstance(cases, list) or not cases:
        raise EvalSetError("评测清单中没有 [[case]]")
    names = set()
    for i, case in enumerate(cases):
        where = f"case[{i}]"
        if not isinstance(case.get("name"), str) or not case["name"].strip():
            raise EvalSetError(f"{where} 缺少 name")
        if case["name"] in names:
            raise EvalSetError(f"{where} 的 name 重复：{case['name']}")
        names.add(case["name"])
        if case.get("kind") not in KINDS:
            raise EvalSetError(f"{where} 的 kind 应为 analyze 或 summary")
        if type(case.get("player")) is not int:
            raise EvalSetError(f"{where} 的 player 应为 SteamID64 整数")
        if case["kind"] == "analyze" and not isinstance(case.get("demo"), str):
            raise EvalSetError(f"{where} 是 analyze，需要 demo 路径")
    return cases


def case_dir_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "case"


def build_argv(case: dict[str, Any], out: Path, provider: str | None, model: str | None) -> list[str]:
    """与手动运行 CLI 完全相同的参数。"""
    if case["kind"] == "analyze":
        argv = ["analyze", case["demo"]]
        if case.get("demo_member"):
            argv += ["--demo-member", case["demo_member"]]
    else:
        argv = ["summary", *case.get("demos", [])]
    argv += ["--player", str(case["player"]), "--ai", "--out", str(out)]
    if provider:
        argv += ["--provider", provider]
    if model:
        argv += ["--model", model]
    return argv


def _latest_audit(folder: Path) -> tuple[Path | None, dict[str, Any] | None]:
    files = sorted(folder.rglob("submission*.json"), key=lambda f: f.stat().st_mtime) if folder.exists() else []
    files = [f for f in files if "历史版本" not in f.parts]
    if not files:
        return None, None
    latest = files[-1]
    return latest, json.loads(latest.read_text(encoding="utf-8"))


def _short(path: Path) -> str:
    """仓库内的路径写成相对路径，汇总表更好读。"""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def case_row(case: dict[str, Any], folder: Path, exit_code: int | None, seconds: float,
             error: str | None = None) -> dict[str, Any]:
    """从这个 case 的输出目录读审计，整理成汇总表的一行。"""
    audit_path, audit = _latest_audit(folder)
    history = (audit or {}).get("violations_history", [])
    codes = Counter(v["code"] for group in history for v in group)
    dropped = (audit or {}).get("dropped", [])
    run = (audit or {}).get("run", {})
    report = next(iter(sorted(folder.rglob("report.md"))), None) if folder.exists() else None
    failed = audit_path is not None and audit_path.name == "submission_failed.json"
    reason = error or (audit or {}).get("error") or f"退出码 {exit_code}"
    # 失败原因可能带换行和很长的违规列表；汇总表里只放第一行，完整内容看审计文件
    reason = reason.splitlines()[0].rstrip("：:")[:80]
    status = "成功" if exit_code == 0 and report is not None else "失败：" + reason
    return {
        "name": case["name"], "kind": case["kind"], "status": status,
        "model": f"{run.get('provider', '?')} / {run.get('model', '?')}" if run else "?",
        "rounds": "→".join(str(len(group)) for group in history) or "-",
        "codes": "、".join(f"{c} {n}" for c, n in codes.most_common()) or "-",
        "removed": sum(d.get("action", "removed") == "removed" for d in dropped),
        "replaced": sum(d.get("action") == "replaced" for d in dropped),
        "tool_calls": run.get("tool_calls", "-"),
        "tokens": (f"{run['input_tokens']}（{run['cache_read_tokens']}）/ {run['output_tokens']}"
                   if "input_tokens" in run else "-"),  # 失败的审计只有模型名，没有用量
        "turns": run.get("turns", len((audit or {}).get("usage_turns", [])) or "-"),
        "cache_rate": (f"{run['cache_read_tokens'] / run['input_tokens']:.1%}"
                       if run.get("input_tokens", 0) else "-"),
        "reasoning_rate": (f"{run.get('reasoning_tokens', 0) / run['output_tokens']:.1%}"
                           if run.get("output_tokens", 0) else "-"),
        "seconds": round(seconds, 1),
        "report": _short(report) if report and not failed else (_short(audit_path) if audit_path else "-"),
    }


def subprocess_runner(argv: list[str]) -> int:
    """每个 case 一个独立进程，输出写到该 case 目录的 run.log；并行时互不干扰。"""
    out = Path(argv[argv.index("--out") + 1])
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "run.log", "w", encoding="utf-8") as log:
        return subprocess.run([sys.executable, "-m", "cs2coach", *argv], cwd=ROOT, stdout=log,
                              stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8"}).returncode


def run_batch(cases: list[dict[str, Any]], out_root: Path, provider: str | None, model: str | None,
              runner: Callable[[list[str]], int], clock: Callable[[], float] = perf_counter,
              jobs: int = 1) -> list[dict[str, Any]]:
    """运行全部 case（jobs > 1 时并行）；单个 case 出错只记录原因，其余继续。结果按清单顺序。"""
    def one(case: dict[str, Any]) -> dict[str, Any]:
        folder = out_root / case_dir_name(case["name"])
        folder.mkdir(parents=True, exist_ok=True)
        print(f"=== 开始 {case['name']}（{case['kind']}）", flush=True)
        started = clock()
        error, code = None, None
        try:
            code = runner(build_argv(case, folder, provider, model))
        except Exception as exc:  # 单个 case 的任何异常都不中断其余
            error = f"{type(exc).__name__}: {exc}"
        row = case_row(case, folder, code, clock() - started, error)
        print(f"=== 完成 {case['name']}：{row['status']}（{row['seconds']} 秒）", flush=True)
        return row

    if jobs <= 1:
        return [one(case) for case in cases]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(one, cases))


def select_cases(cases: list[dict[str, Any]], only: str | None) -> list[dict[str, Any]]:
    """--only 用逗号分隔的关键词筛选 case 名，例如 --only 玩家02,ZywOo。"""
    if not only:
        return cases
    keys = [k.strip() for k in only.split(",") if k.strip()]
    return [c for c in cases if any(k in c["name"] for k in keys)]


COLUMNS = [("name", "case"), ("kind", "类型"), ("status", "结果"), ("model", "模型"), ("rounds", "各轮违规数"),
           ("codes", "违规代码分布"), ("removed", "删除"), ("replaced", "替换"), ("tool_calls", "工具调用"),
           ("tokens", "输入（缓存）/ 输出 token"), ("turns", "请求轮数"),
           ("cache_rate", "缓存命中率"), ("reasoning_rate", "思考/输出"),
           ("seconds", "耗时（秒）"), ("report", "报告"),
           ("manual", "人工核对：错误数")]


def render_summary(rows: list[dict[str, Any]], label: str, started_at: str) -> str:
    lines = [f"# 评测汇总：{label}", "", f"开始时间：{started_at}。人工核对一列由 Claude 填写，记录方法见 docs/eval_log.md。", "",
             "| " + " | ".join(title for _, title in COLUMNS) + " |",
             "|" + "---|" * len(COLUMNS)]
    for row in rows:
        cells = [str(row.get(key, "")).replace("|", "\\|") for key, _ in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def prefetch_map_data() -> str | None:
    """并行前先下载、解压地图碰撞网格（第一次使用时）。

    awpy 在解压完成后才写"已完成"标记；多个进程同时第一次下载时，后解压的进程会覆盖
    其他进程正在读的 .mesh，读到不完整的文件后视野计算失败。返回失败原因，成功返回 None。
    """
    try:
        from awpy import data

        maps = data.available_maps()
        if maps:
            data.mesh_path(maps[0])  # 解压整个 geometry 包，之后各进程只读
        return None
    except Exception as exc:  # 没有网络等：各 case 自己再试，视野不可用时报告里会注明
        return f"{type(exc).__name__}: {exc}"


def confirm(cases: list[dict[str, Any]], provider: str | None, model: str | None,
            ask: Callable[[str], str] = input) -> bool:
    who = f"{provider or '配置默认的服务'} / {model or '配置默认的模型'}"
    print(f"将运行 {len(cases)} 个 case，模型：{who}。真实调用会产生费用。")
    return ask("确认运行？输入 y 继续：").strip().lower() == "y"


def main(argv: list[str] | None = None, runner: Callable[[list[str]], int] | None = None,
         ask: Callable[[str], str] = input, prefetch: Callable[[], str | None] = prefetch_map_data) -> int:
    from cs2coach.console import ensure_utf8_stdio
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", type=Path, default=DEFAULT_SET, help="评测清单（TOML）")
    ap.add_argument("--provider", choices=("deepseek", "mimo", "claude", "local"), default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--label", default="eval", help="这次评测的简短说明，用在输出目录名里")
    ap.add_argument("--out", type=Path, default=OUTPUT_ROOT, help="评测输出根目录")
    ap.add_argument("--yes", action="store_true", help="不再确认，直接运行")
    ap.add_argument("--only", default=None, help="只跑名字包含这些关键词的 case，逗号分隔")
    ap.add_argument("--jobs", type=int, default=5, help="同时运行的 case 数；本地模型固定为 1（显卡同一时间只能跑一个）")
    args = ap.parse_args(argv)
    try:
        cases = select_cases(load_cases(args.set), args.only)
    except EvalSetError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if not cases:
        print(f"错误: 没有名字包含 {args.only!r} 的 case", file=sys.stderr)
        return 1
    jobs = 1 if args.provider == "local" else max(1, args.jobs)
    if not args.yes and not confirm(cases, args.provider, args.model, ask):
        print("已取消。")
        return 1
    error = prefetch()
    if error:
        print(f"警告: 地图数据预下载失败（{error}），各 case 会各自重试", file=sys.stderr)
    started_at = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = args.out / f"{started_at}_{case_dir_name(args.label)}"
    rows = run_batch(cases, out_root, args.provider, args.model, runner or subprocess_runner, jobs=jobs)
    summary = out_root / "summary.md"
    summary.write_text(render_summary(rows, args.label, started_at), encoding="utf-8")
    print(f"\n汇总已写入 {summary}")
    return 0 if all(row["status"] == "成功" for row in rows) else 2


if __name__ == "__main__":
    sys.exit(main())
