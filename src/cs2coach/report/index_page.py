"""扫描输出目录并生成可离线打开的报告目录页。"""

import html
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


@dataclass
class ReportEntry:
    """一份可进入报告目录的报告。"""

    player_name: str
    steamid: int
    kind: str
    title: str
    verdict: str
    href: str
    updated: float


def collect_reports(root: Path) -> list[ReportEntry]:
    """只扫描输出根目录下两层合法目录，按玩家和更新时间排序报告。"""
    if not root.is_dir():
        return []

    entries: list[ReportEntry] = []
    for player_dir in root.iterdir():
        if not player_dir.is_dir():
            continue
        prefix, separator, steamid_text = player_dir.name.rpartition("_")
        if not separator or not prefix or not steamid_text.isdigit():
            continue
        steamid = int(steamid_text)
        for match_dir in player_dir.iterdir():
            data_file = match_dir / "report_data.json"
            page_file = match_dir / "report.html"
            if not match_dir.is_dir() or not data_file.is_file() or not page_file.is_file():
                continue
            try:
                data = json.loads(data_file.read_text(encoding="utf-8"))
                kind = data["kind"]
                title = data["title"]
                if not isinstance(kind, str) or not isinstance(title, str):
                    continue
                updated = page_file.stat().st_mtime
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
                continue

            default_tier = data.get("default_tier")
            verdict = ""
            tiers = data.get("tiers")
            if isinstance(tiers, list):
                for tier in tiers:
                    if isinstance(tier, dict) and tier.get("id") == default_tier:
                        candidate = tier.get("verdict")
                        verdict = candidate if isinstance(candidate, str) else ""
                        break
            href = page_file.relative_to(root).as_posix()
            entries.append(ReportEntry(prefix, steamid, kind, title, verdict, href,
                                      updated))

    by_player: dict[tuple[str, int], list[ReportEntry]] = defaultdict(list)
    for entry in entries:
        by_player[(entry.player_name, entry.steamid)].append(entry)
    ordered: list[ReportEntry] = []
    player_groups = sorted(
        by_player.items(),
        key=lambda item: (-max(entry.updated for entry in item[1]), item[0]),
    )
    for _, player_entries in player_groups:
        ordered.extend(sorted(
            player_entries,
            key=lambda entry: (0 if entry.kind == "summary" else 1, -entry.updated, entry.title),
        ))
    return ordered


def render_index(entries: list[ReportEntry]) -> str:
    """生成不依赖外部资源的 HTML 目录页。"""
    body: list[str] = []
    if not entries:
        body.append('<p class="empty">还没有报告。运行 python -m cs2coach analyze &lt;demo&gt; --player &lt;steamid&gt; 生成第一份。</p>')
    else:
        grouped: dict[tuple[str, int], list[ReportEntry]] = defaultdict(list)
        for entry in entries:
            grouped[(entry.player_name, entry.steamid)].append(entry)
        player_order = list(dict.fromkeys((entry.player_name, entry.steamid) for entry in entries))
        for player_key in player_order:
            player_name = player_key[0]
            body.append(f'<section class="player"><h2>{html.escape(player_name)}</h2>')
            for entry in grouped[player_key]:
                kind = "多场汇总" if entry.kind == "summary" else "单场"
                updated = datetime.fromtimestamp(entry.updated).strftime("%Y-%m-%d %H:%M")
                href = html.escape(quote(entry.href, safe="/"), quote=True)
                lines = [
                    f'<a class="report" href="{href}">',
                    f'  <span class="title">{html.escape(entry.title)}</span>',
                    f'  <span class="type">{kind}</span>',
                    f'  <span class="time">{updated}</span>',
                ]
                if entry.verdict:
                    lines.append(f'  <span class="verdict">{html.escape(entry.verdict)}</span>')
                lines.append("</a>")
                body.append("\n".join(lines))
            body.append("</section>")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cs2coach 报告目录</title>
<style>
:root {{
  --bg: #f3f4f6; --panel: #ffffff; --fg: #15191f; --muted: #5d6673; --line: #dde1e7;
  --you: #1f6fd1; --chip: #eef1f5;
  --f-body: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif;
  --f-mono: Consolas, ui-monospace, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #12151a; --panel: #1a1f26; --fg: #e7eaee; --muted: #9aa3ae; --line: #2c333d;
  --you: #5aa2f5; --chip: #232a33; color-scheme: dark }} }}
:root[data-theme="dark"] {{
  --bg: #12151a; --panel: #1a1f26; --fg: #e7eaee; --muted: #9aa3ae; --line: #2c333d;
  --you: #5aa2f5; --chip: #232a33; color-scheme: dark }}
* {{ box-sizing: border-box }}
body {{ background: var(--bg); color: var(--fg); font-family: var(--f-body); font-size: 15px; line-height: 1.6;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 24px 48px }}
.wrap {{ max-width: 860px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px }}
h1 {{ font-size: 22px; margin: 0; text-wrap: balance }}
h2 {{ font-size: 17px; margin: 0 0 10px }}
.player {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px 20px }}
.report {{ display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 4px 14px; align-items: baseline;
  color: inherit; text-decoration: none; padding: 12px 4px; border-top: 1px solid var(--line) }}
.report:hover .title {{ color: var(--you) }}
.report:focus-visible {{ outline: 2px solid var(--you); outline-offset: 2px; border-radius: 4px }}
.title {{ font-weight: 500; text-wrap: balance }}
.type, .time {{ color: var(--muted); font-size: 13px; white-space: nowrap }}
.time {{ font-family: var(--f-mono) }}
.verdict {{ grid-column: 1 / -1; color: var(--muted); font-size: 13px; text-wrap: pretty }}
.empty {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px 20px; margin: 0 }}
@media (max-width: 640px) {{
  .report {{ grid-template-columns: minmax(0, 1fr) auto }}
  .time {{ grid-column: 1 / -1 }}
}}
</style>
</head>
<body>
<main class="wrap">
<h1>cs2coach 报告目录</h1>
{chr(10).join(body)}
</main>
</body>
</html>
"""


def write_index(root: Path) -> Path:
    """在输出根目录写入 UTF-8、LF 换行的目录页。"""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "index.html"
    path.write_text(render_index(collect_reports(root)), encoding="utf-8", newline="\n")
    return path
