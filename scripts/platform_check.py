"""对照平台录入数据与同场统计。用法：python scripts/platform_check.py [平台 TOML ...]。"""

from __future__ import annotations

import argparse
import math
import tomllib
from pathlib import Path

import pandas as pd

from cs2coach.config import load_config
from cs2coach.features.lobby import lobby_lines
from cs2coach.features.models import LobbyLine
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_DIR = ROOT / "data/eval/platform"


def _value(line: LobbyLine, field: str) -> float | int | None:
    """只允许 LobbyLine 的顶层字段或 AimStats 字段。"""
    parts = field.split(".")
    if len(parts) == 1:
        if field not in LobbyLine.model_fields:
            raise ValueError(f"未知同场字段：{field}")
        return getattr(line, field)
    if len(parts) == 2 and parts[0] == "aim":
        from cs2coach.features.models import AimStats

        if parts[1] not in AimStats.model_fields:
            raise ValueError(f"未知枪法字段：{field}")
        return getattr(line.aim, parts[1]) if line.aim else None
    raise ValueError(f"未知同场字段：{field}")


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def compare(data: dict, lines: list[LobbyLine]) -> tuple[list[str], bool]:
    """逐指标比较；返回 Markdown 行与整体是否通过。"""
    players = {int(row["steamid"]): row for row in data["player"]}
    ours = {line.steamid: line for line in lines}
    if len(players) != len(data["player"]) or len(ours) != len(lines):
        raise ValueError("平台或同场数据有重复 steamid")
    missing_ours = sorted(players.keys() - ours.keys())
    missing_platform = sorted(ours.keys() - players.keys())
    rows = ["| 指标 | 对上/有值 | 结果 | 最大偏差 / Spearman |",
            "|---|---:|---|---:|"]
    passed = not (missing_ours or missing_platform)
    shared = sorted(players.keys() & ours.keys())
    for spec in data["compare"]:
        field, own_field, kind = spec["platform_field"], spec["ours"], spec["kind"]
        if kind not in {"exact", "tolerance", "info"}:
            raise ValueError(f"未知比较类型：{kind}")
        if kind == "tolerance" and (not _number(spec.get("tolerance")) or spec["tolerance"] < 0):
            raise ValueError(f"{field} 的 tolerance 无效")
        share = spec.get("min_match_share", 1.0)
        if not _number(share) or not 0 < share <= 1:
            raise ValueError(f"{field} 的 min_match_share 无效")
        pairs = [(sid, players[sid][field], _value(ours[sid], own_field))
                 for sid in shared if field in players[sid]]
        if kind == "info":
            valid = [(a, b) for _, a, b in pairs if _number(a) and _number(b)]
            rho = (pd.Series([a for a, _ in valid]).rank().corr(
                pd.Series([b for _, b in valid]).rank()) if len(valid) >= 2 else float("nan"))
            rho_text = f"{rho:.3f}" if math.isfinite(rho) else "—"
            rows.append(f"| {field} | {len(valid)}/{len(pairs)} | 信息 | Spearman {rho_text} |")
            continue
        wrong = []
        diffs = []
        for sid, expected, actual in pairs:
            diff = abs(expected - actual) if _number(expected) and _number(actual) else None
            if diff is not None:
                diffs.append(diff)
            ok = (expected == actual if kind == "exact" else
                  diff is not None and diff <= spec["tolerance"])
            if not ok:
                wrong.append((sid, expected, actual))
        matched = len(pairs) - len(wrong)
        ok = bool(pairs) and matched / len(pairs) >= share
        passed &= ok
        maximum = f"{max(diffs):g}" if diffs else "—"
        rows.append(f"| {field} | {matched}/{len(pairs)} | {'通过' if ok else '失败'} | {maximum} |")
        if not ok:
            for sid, expected, actual in wrong:
                rows.append(f"| ↳ …{str(sid)[-4:]} | 平台 {expected} | 我们 {actual} | |")
    if missing_ours:
        rows.append("平台有、同场缺失：" + "、".join(f"…{str(sid)[-4:]}" for sid in missing_ours))
    if missing_platform:
        rows.append("同场有、平台缺失：" + "、".join(f"…{str(sid)[-4:]}" for sid in missing_platform))
    return rows, passed


def check_file(path: Path) -> tuple[list[str], bool]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    demo = Path(data["demo"])
    if not demo.is_absolute():
        demo = ROOT / demo
    cfg = load_config()
    match = parse_match(resolve_demo(demo, member=data.get("demo_member")), cfg)
    lines = lobby_lines(match, next(sid for sid in match.players if sid > 0), cfg, Sight(match, cfg))
    rows, passed = compare(data, lines)
    return [f"## {path.name}", "", *rows, ""], passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)
    paths = args.files or sorted(PLATFORM_DIR.glob("*.toml"))
    if not paths:
        parser.error("没有平台 TOML 文件")
    passed = True
    for path in paths:
        rows, ok = check_file(path)
        print("\n".join(rows))
        passed &= ok
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
