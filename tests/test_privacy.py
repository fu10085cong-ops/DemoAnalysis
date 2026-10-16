"""防止真实业余玩家 SteamID 被重新写入仓库。"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEAMID = re.compile(r"7656119\d{10}")
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json"}


def test_repository_steamids_are_pro_or_fictitious():
    sample = ROOT / "src/cs2coach/baselines/pro_sample.json"
    pro_ids = {str(item["steamid"]) for item in
               json.loads(sample.read_text(encoding="utf-8"))["players"]}
    files = [path for folder in ("tests", "docs", "config", "scripts", "src")
             for path in (ROOT / folder).rglob("*")
             if path.is_file() and path.suffix in TEXT_SUFFIXES and path != sample]
    files.extend(ROOT / name for name in ("README.md", "AGENTS.md", "CLAUDE.md"))
    unexpected = []
    for path in files:
        for sid in STEAMID.findall(path.read_text(encoding="utf-8")):
            value = int(sid)
            if (sid not in pro_ids and not 76561190000000000 <= value <= 76561190000009999
                    and not re.fullmatch(r"7656119800000000\d", sid)
                    and not (path.name == "match.py" and sid == "7656119" + "7960265728")):
                unexpected.append(f"{path.relative_to(ROOT)}: {sid}")
    assert unexpected == []
