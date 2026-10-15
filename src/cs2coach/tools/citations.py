"""检查 AI 报告中的引用（CLAUDE.md 架构原则 2、3），并把引用渲染成可读形式、附上游戏内跳转命令。

引用写法：[[R5-death]]（瞬间编号）或 [[R5]]（回合）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cs2coach.features.models import MatchMoments

CITATION = re.compile(r"\[\[\s*([A-Za-z0-9\-]+)\s*\]\]")
_ROUND = re.compile(r"^R(\d+)$")
# 没写成 [[...]] 的回合 / 瞬间编号（如"R4、R5"），会绕过存在性检查。R8 左轮（R8 Revolver）不算
_BARE = re.compile(r"(?<![\w\[-])R\d+(?:-(?:kill|death|assist)\d*)?(?![\w\]-])(?!\s*(?:Revolver|revolver|左轮))")


@dataclass
class CitationCheck:
    cited: list[str]  # 按出现顺序去重
    invalid: list[str]  # 编号不存在
    bare: list[str] = field(default_factory=list)  # 没写成 [[...]] 的编号（按出现顺序去重）

    @property
    def ok(self) -> bool:
        return bool(self.cited) and not self.invalid and not self.bare


def check_citations(text: str, mm: MatchMoments) -> CitationCheck:
    ids = {m.id for m in mm.moments}
    cited: list[str] = []
    for c in CITATION.findall(text):
        if c not in cited:
            cited.append(c)
    invalid = []
    for c in cited:
        r = _ROUND.match(c)
        if c in ids or (r and 1 <= int(r.group(1)) <= mm.rounds):
            continue
        invalid.append(c)
    bare: list[str] = []
    for b in _BARE.findall(CITATION.sub(" ", text)):
        if b not in bare:
            bare.append(b)
    return CitationCheck(cited=cited, invalid=invalid, bare=bare)


def render_report(text: str, mm: MatchMoments) -> str:
    """[[编号]] → 〔编号〕，文末附上被引用瞬间的游戏内跳转命令（由程序生成，不让 AI 抄写 tick）。"""
    body = CITATION.sub(lambda m: f"〔{m.group(1).strip()}〕", text)
    by_id = {m.id: m for m in mm.moments}
    rows = [by_id[c] for c in check_citations(text, mm).cited if c in by_id]
    if not rows:
        return body
    lines = ["", "---", "", "## 附：游戏内跳转", "",
             "在 CS2 中播放这场 demo 时，打开控制台输入命令，会跳到交火开始前几秒（命令写法尚待在游戏内实测）。", "",
             "| 编号 | 回合 | tick | 命令 |", "|---|---|---|---|"]
    lines += [f"| {m.id} | {m.round} | {m.tick} | `{m.jump_command}` |" for m in rows]
    return body.rstrip() + "\n" + "\n".join(lines) + "\n"
