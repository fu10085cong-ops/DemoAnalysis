"""从本地业余比赛 demo 读取完整名单，检查仓库中是否残留玩家身份信息。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser

from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import is_real_steamid
from cs2coach.parsing.models import Team
from cs2coach.summary.discover import _members, _supported

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIRS = (ROOT / "data/demos/5e_demo", ROOT / "data/demos/perfectworld_demo")
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json"}


def _demo_rosters() -> tuple[int, set[str], set[str]]:
    """只读每场的玩家名单；压缩包解压结果由 resolve_demo 缓存。"""
    pro_path = ROOT / "src/cs2coach/baselines/pro_sample.json"
    pro_ids = {int(item["steamid"]) for item in
               json.loads(pro_path.read_text(encoding="utf-8"))["players"]}
    names: set[str] = set()
    steamids: set[str] = set()
    demos = 0
    for folder in DEMO_DIRS:
        if not folder.is_dir():
            raise FileNotFoundError(f"demo 目录不存在：{folder}")
        paths = sorted(path for path in folder.rglob("*")
                       if path.is_file() and ".extracted" not in path.parts and _supported(path))
        for path in paths:
            for member in _members(path):
                demo = resolve_demo(path, member=member)
                parser = DemoParser(str(demo))
                info = parser.parse_player_info()
                if info.empty:
                    # 截断 demo 没有 player_info 时保留逐 tick 出现过的所有名字。
                    raw = parser.parse_ticks(["team_num"])
                    players = raw.loc[raw["team_num"].isin((Team.T, Team.CT)), ["steamid", "name"]]
                else:
                    players = info.loc[info["team_number"].isin((Team.T, Team.CT)), ["steamid", "name"]]
                demos += 1
                for sid, name in players.itertuples(index=False, name=None):
                    if pd.isna(sid) or not is_real_steamid(int(sid)) or int(sid) in pro_ids:
                        continue
                    steamids.add(str(int(sid)))
                    if pd.notna(name) and str(name).strip():
                        names.add(str(name).strip())
    return demos, names, steamids


def _source_files() -> list[Path]:
    files = [path for folder in ("tests", "docs", "config", "scripts", "src")
             for path in (ROOT / folder).rglob("*")
             if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts]
    return sorted(files + [ROOT / name for name in ("README.md", "AGENTS.md", "CLAUDE.md")])


def main() -> int:
    try:
        demos, names, steamids = _demo_rosters()
    except Exception as exc:
        print(f"读取 demo 名单失败：{exc}", file=sys.stderr)
        return 2
    print(f"已扫描 {demos} 场 demo，收集 {len(names)} 个非职业玩家名、{len(steamids)} 个 SteamID。")
    hits = 0
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(name in line for name in names) or any(sid in line for sid in steamids):
                print(f"{path.relative_to(ROOT)}:{number}")
                hits += 1
    print(f"身份信息命中：{hits} 处。")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
