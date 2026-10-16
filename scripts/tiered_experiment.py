"""分级报告实验：对评测清单中的单场 case，生成 L0 程序报告，并用模型做 L1 逐个交火的两三句话。

用法：python scripts/tiered_experiment.py [--set data/eval/eval_set.toml] [--provider local] [--model ...]
输出：data/output/评测/<日期时间>_分级L0L1_<provider>/<case 名>/report.md、stats.json，以及 summary.md。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_batch import case_dir_name, load_cases  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    from cs2coach.ai.providers import make_provider
    from cs2coach.baseline import load_baseline
    from cs2coach.config import load_config
    from cs2coach.console import ensure_utf8_stdio
    from cs2coach.features.moments import find_moments
    from cs2coach.features.problems import detect_problems, problem_frequency
    from cs2coach.parsing.loader import resolve_demo
    from cs2coach.parsing.match import parse_match
    from cs2coach.report.tiered import build_l0, render_tiered, write_l1
    from cs2coach.tools.digest import match_score

    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", type=Path, default=ROOT / "data" / "eval" / "eval_set.toml")
    ap.add_argument("--provider", default="local", choices=("local", "deepseek", "claude"))
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)
    cfg = load_config()
    cfg["ai"]["provider"] = args.provider
    if args.model:
        cfg["ai"][args.provider]["model"] = args.model
    if args.provider != "local":
        from cs2coach.env import load_env_file
        load_env_file(ROOT / ".env")
    baseline = load_baseline()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = ROOT / "data" / "output" / "评测" / f"{stamp}_分级L0L1_{args.provider}"
    rows = []
    for case in load_cases(args.set):
        if case["kind"] != "analyze":
            continue
        print(f"\n=== {case['name']} ===", flush=True)
        started = perf_counter()
        match = parse_match(resolve_demo(ROOT / case["demo"], member=case.get("demo_member")), cfg)
        mm = find_moments(match, case["player"], cfg)
        problems = detect_problems(mm, baseline, cfg)
        frequency = problem_frequency(problems, mm, baseline, cfg)
        score = match_score(match, mm.player)
        names = {sid: p.name for sid, p in match.players.items()}
        sections = build_l0(mm, problems, frequency, cfg, names)
        model = None
        for s in sections:
            for e in s.examples:
                provider = None

                def factory():
                    nonlocal provider
                    provider = make_provider(copy.deepcopy(cfg))
                    return provider
                write_l1(s, e, mm, problems, frequency, baseline, score, cfg, factory)
                model = getattr(provider, "model", model)
                print(f"  {s.problem} {e.target}: {'通过' if e.text else '未写成'}（{e.attempts} 次，{e.seconds:.0f} 秒）", flush=True)
        folder = out_root / case_dir_name(case["name"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.md").write_text(render_tiered(mm, sections, frequency, score, model), encoding="utf-8")
        (folder / "stats.json").write_text(json.dumps([asdict(s) for s in sections], ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        examples = [e for s in sections for e in s.examples]
        rows.append({"name": case["name"], "sections": len(sections), "examples": len(examples),
                     "ok": sum(bool(e.text) for e in examples), "first": sum(bool(e.text) and e.attempts == 1 for e in examples),
                     "seconds": round(perf_counter() - started)})
    lines = ["# 分级报告实验汇总", "", f"模型：{args.provider} / {cfg['ai'][args.provider]['model']}", "",
             "| case | 问题数 | 交火段落 | L1 写成 | 一次通过 | 耗时（秒） | 人工核对：错误数 |", "|---|---:|---:|---:|---:|---:|---|"]
    lines += [f"| {r['name']} | {r['sections']} | {r['examples']} | {r['ok']} | {r['first']} | {r['seconds']} |  |" for r in rows]
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n汇总已写入 {out_root / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
