"""按问题分层抽样，生成核对清单和待填写标签。用法：python scripts/audit_sample.py [--date YYYY-MM-DD]"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from cs2coach.audit import read_hits, review_page, sample_hits, update_labels
from cs2coach.config import load_config
from cs2coach.features.problems import DETECTED_PROBLEMS

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--per-problem", type=int, default=20)
    parser.add_argument("--per-demo", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261017)
    args = parser.parse_args(argv)
    out = ROOT / "data/audit" / args.date
    rows = read_hits(out / "hits.jsonl")
    samples = sample_hits(rows, args.per_problem, args.per_demo, args.seed)
    by_problem = defaultdict(list)
    for row, selection in samples:
        by_problem[row["problem"]].append((row, selection))
    review = out / "review"
    review.mkdir(parents=True, exist_ok=True)
    for problem in DETECTED_PROBLEMS:
        (review / f"{problem}.md").write_text(review_page(problem, by_problem[problem]), encoding="utf-8")
    cfg = load_config()
    update_labels(out / "labels.toml", samples, set(cfg["audit"]["visibility_problems"]))
    print(f"抽样 {len(samples)} 条：{dict(sorted(Counter(row['problem'] for row, _ in samples).items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
