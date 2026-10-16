"""用当前检查器重查一次评测保存下来的提交，不调用模型、不花钱。

用法：python scripts/eval_recheck.py data/output/评测/<某次评测目录> [--set data/eval/eval_set.toml] [--only 关键词]

改了检查规则后，用它确认：旧报告里已知的错误能不能拦住、正确的写法有没有被误伤。
多场汇总会读分析缓存（data/cache/），第二次起只需十几秒。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_batch import DEFAULT_SET, case_dir_name, load_cases, select_cases  # noqa: E402


def recheck_case(case: dict, folder: Path, cfg: dict, baseline: dict | None) -> list | None:
    """返回违规列表；这个 case 没有成功保存的提交时返回 None。"""
    from cs2coach.features.moments import find_moments
    from cs2coach.features.problems import detect_problems, problem_frequency
    from cs2coach.parsing.loader import resolve_demo
    from cs2coach.parsing.match import parse_match
    from cs2coach.summary.aggregate import build_summary
    from cs2coach.summary.digest import build_summary_digest, summary_numbers
    from cs2coach.summary.discover import find_player_demos, inspect_player_demos
    from cs2coach.tools.checker import check_submission, check_summary_submission
    from cs2coach.tools.digest import build_digest, match_score
    from cs2coach.tools.submission import parse_submission

    audits = [f for f in folder.rglob("submission.json") if "历史版本" not in f.parts]
    if not audits:
        return None
    sub = parse_submission(json.loads(max(audits, key=lambda f: f.stat().st_mtime).read_text(encoding="utf-8"))["submission"])
    if case["kind"] == "summary":
        refs = (inspect_player_demos(case["player"], [(ROOT / d, None) for d in case["demos"]])
                if case.get("demos") else find_player_demos(case["player"], ROOT / "data" / "demos"))
        summary = build_summary(case["player"], refs, cfg, baseline)
        digest = build_summary_digest(summary, baseline, cfg)
        return check_summary_submission(sub, summary, digest, cfg, summary_numbers(summary, baseline), baseline)
    match = parse_match(resolve_demo(ROOT / case["demo"], member=case.get("demo_member")), cfg)
    mm = find_moments(match, case["player"], cfg)
    problems = detect_problems(mm, baseline, cfg)
    frequency = problem_frequency(problems, mm, baseline, cfg)
    digest = build_digest(match, mm, baseline, problems, frequency, cfg)
    return check_submission(sub, mm, problems, digest, cfg, None, match_score(match, mm.player), frequency, baseline)


def main(argv: list[str] | None = None) -> int:
    from cs2coach.baseline import load_baseline
    from cs2coach.config import load_config
    from cs2coach.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path, help="某次评测的输出目录")
    ap.add_argument("--set", type=Path, default=DEFAULT_SET)
    ap.add_argument("--only", default=None)
    args = ap.parse_args(argv)
    cfg, baseline = load_config(), load_baseline()
    total = 0
    for case in select_cases(load_cases(args.set), args.only):
        violations = recheck_case(case, args.run / case_dir_name(case["name"]), cfg, baseline)
        if violations is None:
            print(f"== {case['name']}：没有成功保存的提交，跳过")
            continue
        total += len(violations)
        print(f"== {case['name']}：{len(violations)} 项")
        for v in violations:
            print(f"   {v.path} {v.code} {v.message[:200]}")
    print(f"\n合计 {total} 项。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
