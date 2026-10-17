"""从人工标签生成分层加权的准确率报告。用法：python scripts/audit_report.py [--date YYYY-MM-DD]"""

from __future__ import annotations

import argparse
import tomllib
from datetime import date
from pathlib import Path

from cs2coach.audit import precision_report, read_hits

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args(argv)
    out = ROOT / "data/audit" / args.date
    rows = read_hits(out / "hits.jsonl")
    labels = tomllib.loads((out / "labels.toml").read_text(encoding="utf-8")).get("labels", {})
    path = out / "precision.md"
    path.write_text(precision_report(rows, labels), encoding="utf-8")
    print(f"已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
