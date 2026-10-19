"""R01：从职业 Mirage demo 导出 CT 首次 5v4 行为窗口。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import tomllib
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from awpy.map_control import NavMesh
from demoparser2 import DemoParser

from cs2coach.config import load_config
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.research.labels import label_window
from cs2coach.research.models import BehaviourWindow, Situation
from cs2coach.research.triggers import find_situations
from cs2coach.research.windows import behaviour_windows

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src/cs2coach/baselines/pro_demos.toml"
LABELS = ("hold", "advance", "retreat", "lateral", "rotate", "unknown")


def _match_id(name: str) -> str:
    return re.sub(r"-p[12]$", "", Path(name).stem)


def sources(map_name: str) -> list[tuple[Path, str | None]]:
    rows = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["demos"]
    entries = [(ROOT / row["archive"], row["member"]) for row in rows if row["map"] == map_name]
    extra = ROOT / "data/demos/pro_mirage"
    if map_name == "de_mirage" and extra.exists():
        entries.extend((path, None) for path in sorted(extra.glob("*.dem")))
    # 同名比赛只导出一次，但分段 p1/p2 保留两份源数据并共享 match_id。
    unique: dict[tuple[str, str], tuple[Path, str | None]] = {}
    for path, member in entries:
        name = member or path.name
        part = re.search(r"-p[12]$", Path(name).stem)
        unique.setdefault((_match_id(name), part.group() if part else ""), (path, member))
    return list(unique.values())


def _process(entry: tuple[Path, str | None], cfg: dict) -> tuple[list[Situation], list[BehaviourWindow], Counter, Counter]:
    path, member = entry
    demo = resolve_demo(path, member=member)
    match = parse_match(demo, cfg)
    nav = NavMesh(str(Path.home() / ".awpy/2000930/navs" / f"{match.meta.map_name}.nav"))
    sight = Sight(match, cfg)
    situations, skipped = find_situations(match)
    windows = [window for situation in situations
               for window in behaviour_windows(match, situation, sight, nav, cfg)]
    places = Counter(match.ticks["last_place_name"].dropna().astype(str).tolist())
    return situations, windows, skipped, places


def _places_only(entry: tuple[Path, str | None]) -> Counter:
    """地点清单只扫描这一列，不重新生成研究表。"""
    path, member = entry
    demo = resolve_demo(path, member=member)
    frame = DemoParser(str(demo)).parse_ticks(["last_place_name"])
    if "last_place_name" not in frame:
        raise ValueError(f"demo 缺少 last_place_name: {demo.name}")
    return Counter(frame["last_place_name"].dropna().astype(str).tolist())


def list_places(map_name: str, jobs: int) -> Counter:
    counts: Counter = Counter()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for found in pool.map(_places_only, sources(map_name)):
            counts.update(found)
    return counts


def _csv_value(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, tuple, dict)) else value


def _write_csv(path: Path, models: list, model_class) -> None:
    fields = list(model_class.model_fields)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: _csv_value(value) for key, value in item.model_dump().items()} for item in models)


def export(map_name: str, name: str, jobs: int, list_places: bool = False) -> tuple[Path, Counter]:
    cfg = load_config()
    entries = sources(map_name)
    situations: list[Situation] = []
    windows: list[BehaviourWindow] = []
    skipped: Counter = Counter()
    places: Counter = Counter()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for ss, ws, reasons, found_places in pool.map(_process, entries, [cfg] * len(entries)):
            situations.extend(ss)
            windows.extend(ws)
            skipped.update(reasons)
            places.update(found_places)
    output = ROOT / "data/research" / name
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "situations.csv", situations, Situation)
    _write_csv(output / "windows.csv", windows, BehaviourWindow)
    matches = {_match_id(member or path.name) for path, member in entries}
    lines = ["# R01 Mirage CT 5v4 导出摘要", "", f"Demo 数：{len(entries)}",
             f"比赛数：{len(matches)}", f"触发回合数：{len(situations)}", f"窗口数：{len(windows)}",
             "", *_label_lines(windows), "", "## 跳过原因", ""]
    lines.extend(f"- {reason}: {count}" for reason, count in sorted(skipped.items()))
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if list_places:
        for place, count in places.most_common():
            print(f"{place}\t{count}")
    return output, places


def _label_lines(windows: list[BehaviourWindow]) -> list[str]:
    labels = Counter(window.main_label for window in windows)
    lines = ["## 主标签", ""]
    lines.extend(f"- {label}: {labels[label]}" for label in LABELS)
    lines.extend(["", f"unknown 比例：{labels['unknown'] / len(windows):.1%}" if windows else "unknown 比例：无窗口"])
    return lines


def _read_windows(path: Path) -> list[BehaviourWindow]:
    structured = {key for key, field in BehaviourWindow.model_fields.items()
                  if any(kind in str(field.annotation) for kind in ("list", "tuple"))}
    rows = []
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            values = {}
            for key, value in row.items():
                if value == "":
                    continue
                values[key] = json.loads(value) if key in structured else value
            rows.append(BehaviourWindow(**values))
    return rows


def relabel(source: str, name: str) -> Path:
    """只按当前配置重新打标签，不解析 demo（校准阈值和地点分组时用）。"""
    cfg = load_config()
    origin = ROOT / "data/research" / source
    windows = _read_windows(origin / "windows.csv")
    for window in windows:
        window.main_label, window.aux_labels, window.label_confidence = label_window(window, cfg)
    output = ROOT / "data/research" / name
    output.mkdir(parents=True, exist_ok=True)
    (output / "situations.csv").write_bytes((origin / "situations.csv").read_bytes())
    _write_csv(output / "windows.csv", windows, BehaviourWindow)
    lines = ["# R01 Mirage CT 5v4 导出摘要（只重新打标签）", "", f"来源：data/research/{source}",
             f"窗口数：{len(windows)}", "", *_label_lines(windows)]
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", default="de_mirage")
    parser.add_argument("--name", default="r01-dev")
    parser.add_argument("--jobs", type=int, default=load_config()["tools"]["jobs"])
    parser.add_argument("--list-places", action="store_true")
    parser.add_argument("--relabel", metavar="来源导出名", help="只按当前配置重新打标签，不解析 demo")
    args = parser.parse_args()
    if args.jobs < 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("--jobs 须为正数，--name 只能含字母、数字、下划线和连字符")
    if args.relabel:
        print(relabel(args.relabel, args.name))
    elif args.list_places:
        for place, count in list_places(args.map, args.jobs).most_common():
            print(f"{place}\t{count}")
    else:
        output, _ = export(args.map, args.name, args.jobs)
        print(output)


if __name__ == "__main__":
    main()
