"""从人工标签生成分层加权的准确率报告。用法：python scripts/audit_report.py [--date YYYY-MM-DD]"""

from __future__ import annotations

import argparse
import re
import tomllib
from datetime import date
from pathlib import Path

from cs2coach.audit import comparison_report, precision_report, read_hits

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--name", help="审计输出目录名，覆盖 --date")
    parser.add_argument("--labels-from", help="沿用该审计目录中的标签并比较前后命中")
    args = parser.parse_args(argv)
    name = args.name or args.date
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        parser.error("--name 必须是安全的单层目录名")
    if args.labels_from and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.labels_from):
        parser.error("--labels-from 必须是安全的单层目录名")
    out = ROOT / "data/audit" / name
    rows = read_hits(out / "hits.jsonl")
    label_dir = ROOT / "data/audit" / (args.labels_from or name)
    labels = tomllib.loads((label_dir / "labels.toml").read_text(encoding="utf-8")).get("labels", {})
    path = out / "precision.md"
    report = precision_report(rows, labels)
    if args.labels_from:
        before = read_hits(label_dir / "hits.jsonl")
        report += "\n" + comparison_report(before, rows, labels)
    path.write_text(report, encoding="utf-8")
    print(f"已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
