"""重建基准、全量收集命中，并沿用历史判定估算规则变更效果。"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import tomllib
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from pathlib import Path

from cs2coach.audit import read_hits
from cs2coach.baseline import BASELINE_FILE

try:
    from scripts import audit_collect, build_baseline
except ModuleNotFoundError:  # python scripts/rule_regression.py 时 scripts/ 在 sys.path
    import audit_collect
    import build_baseline

ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = ROOT / "data/audit"


def label_dirs(compare: Path, audit_root: Path = AUDIT_ROOT) -> list[Path]:
    """默认沿用比较目录及同日更早目录的判定。"""
    day = compare.name[:10]
    return sorted((p for p in audit_root.iterdir() if p.is_dir() and p.name[:10] == day
                   and p.name <= compare.name and (p / "labels.toml").exists()), key=lambda p: p.name)


def read_labels(directories: list[Path]) -> dict:
    """后面的目录覆盖前面的相同 hit_key。"""
    labels = {}
    for directory in sorted(directories, key=lambda p: p.name):
        path = directory / "labels.toml"
        if path.exists():
            labels.update(tomllib.loads(path.read_text(encoding="utf-8")).get("labels", {}))
    return labels


def compare_hits(old: list[dict], new: list[dict], labels: dict) -> list[dict]:
    """按问题计算命中变化及沿用判定；移除比例分母是移除的已判定条目。"""
    old_by_key = {r["hit_key"]: r for r in old}
    new_by_key = {r["hit_key"]: r for r in new}
    old_counts = Counter(r["problem"] for r in old)
    new_counts = Counter(r["problem"] for r in new)
    result = []
    for problem in sorted(old_counts.keys() | new_counts.keys()):
        retained = Counter()
        removed = Counter()
        for key, entry in labels.items():
            row = new_by_key.get(key) or old_by_key.get(key)
            if row is None or row["problem"] != problem:
                continue
            verdict = entry.get("verdict", "")
            worth = entry.get("worth", "")
            if verdict not in ("correct", "wrong"):
                continue
            if verdict == "wrong" and worth:
                raise ValueError(f"错误判定不得填写 worth：{key}")
            if worth not in ("", "yes", "no"):
                raise ValueError(f"无效 worth：{key}")
            counts = retained if key in new_by_key else removed
            counts["judged"] += 1
            counts[verdict] += 1
            if worth:
                counts[f"worth_{worth}"] += 1
        n = retained["correct"] + retained["wrong"]
        useful_n = retained["worth_yes"] + retained["worth_no"] + retained["wrong"]
        result.append({"problem": problem, "old": old_counts[problem], "new": new_counts[problem],
                       "delta": new_counts[problem] - old_counts[problem], "retained": dict(retained),
                       "removed": dict(removed), "precision": retained["correct"] / n if n else None,
                       "usefulness": retained["worth_yes"] / useful_n if useful_n else None,
                       "removed_wrong_share": removed["wrong"] / removed["judged"] if removed["judged"] else None,
                       "removed_worth_no_share": removed["worth_no"] / removed["judged"] if removed["judged"] else None})
    return result


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def render_report(name: str, compare: str, rates_old: dict | None, rates_new: dict | None,
                  rows: list[dict], label_sources: list[Path]) -> str:
    lines = [f"# 规则回归：{name}", "", f"比较：{compare}",
             f"判定来源：{', '.join(path.name for path in label_sources) or '无'}", "",
             "准确率和有用率仅基于旧命中中仍命中的已判定样本，不能视为新规则的正式抽样估计。", ""]
    if rates_old is not None and rates_new is not None:
        lines += ["## 职业基准 problem_rates", "", "| 问题 | 旧值 | 新值 |", "|---|---:|---:|"]
        for key in sorted(rates_old.keys() | rates_new.keys()):
            lines.append(f"| {key} | {rates_old.get(key, '—')} | {rates_new.get(key, '—')} |")
        lines.append("")
    lines += ["## 命中与沿用判定", "",
              "| 问题 | 旧命中 | 新命中 | 变化 | 仍命中已判定 | correct | wrong | worth=yes | worth=no | 估算准确率 | 估算有用率 | 移除已判定 | 其中 wrong | 其中 worth=no | 移除 wrong 占比 | 移除 worth=no 占比 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        kept, gone = Counter(row["retained"]), Counter(row["removed"])
        lines.append(f"| {row['problem']} | {row['old']} | {row['new']} | {row['delta']:+d} | "
                     f"{kept['judged']} | {kept['correct']} | {kept['wrong']} | {kept['worth_yes']} | {kept['worth_no']} | "
                     f"{_percent(row['precision'])} | {_percent(row['usefulness'])} | {gone['judged']} | "
                     f"{gone['wrong']} | {gone['worth_no']} | {_percent(row['removed_wrong_share'])} | "
                     f"{_percent(row['removed_worth_no_share'])} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--compare-to", required=True)
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--labels-from", type=Path, action="append", default=[])
    args = ap.parse_args(argv)
    for value in (args.name, args.compare_to):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
            ap.error("目录名必须是安全的单层名称")
    compare = AUDIT_ROOT / args.compare_to
    old_path = compare / "hits.jsonl"
    if not old_path.exists():
        ap.error(f"比较目录缺少 hits.jsonl：{old_path}")
    out = AUDIT_ROOT / args.name
    if out == compare:
        ap.error("新目录与比较目录不能相同")
    if out.exists():
        ap.error(f"输出目录已存在：{out}")
    old = read_hits(old_path)
    sources = args.labels_from or label_dirs(compare)
    labels = read_labels(sources)
    out.mkdir(parents=True)
    old_rates = new_rates = None
    if not args.skip_baseline:
        backup = out / "pro_sample.before.json"
        shutil.copy2(BASELINE_FILE, backup)
        old_rates = json.loads(backup.read_text(encoding="utf-8")).get("problem_rates", {})
        # 基准脚本会打印玩家名；只保留退出状态，避免在终端日志泄露姓名。
        with redirect_stdout(io.StringIO()):
            status = build_baseline.main([])
        if status:
            raise RuntimeError("职业基准重建失败；备份保存在审计目录")
        new_rates = json.loads(BASELINE_FILE.read_text(encoding="utf-8")).get("problem_rates", {})
    status = audit_collect.main(["--name", args.name])
    if status:
        raise RuntimeError("业余全量收集有失败项，请检查 collect_stats.json")
    new = read_hits(out / "hits.jsonl")
    rows = compare_hits(old, new, labels)
    report = render_report(args.name, args.compare_to, old_rates, new_rates, rows, sources)
    (out / "regression.md").write_text(report, encoding="utf-8")
    print(f"回归完成：旧 {len(old)} 条，新 {len(new)} 条，变化 {len(new)-len(old):+d}；报告 {out / 'regression.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
