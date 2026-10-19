"""查找包含目标玩家的 demo，并优先使用原始文件。

同一场比赛按 demo 内容（parsing.loader.demo_fingerprint）去重，不按文件名：原始文件、压缩包和 .extracted/
里的解压副本只保留排在前面的一个；不同目录下文件名相同的不同比赛都保留，match_id 加指纹后缀区分。
"""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

import rarfile
from demoparser2 import DemoParser
from pydantic import BaseModel

from cs2coach.parsing.loader import demo_fingerprint, resolve_demo
from cs2coach.parsing.match import roster_names


class DemoRef(BaseModel):
    path: Path
    member: str | None
    match_id: str
    map_name: str
    player_name: str


class SkippedDemo(BaseModel):
    path: Path
    member: str | None = None
    match_id: str
    reason: str


class DemoRefs(list[DemoRef]):
    """保持 list 返回值，同时附带扫描中无法读取的文件。"""

    def __init__(self, refs=(), skipped=()):
        super().__init__(refs)
        self.skipped: list[SkippedDemo] = list(skipped)


def _supported(path: Path) -> bool:
    return path.name.lower().endswith((".dem", ".dem.bz2", ".dem.gz", ".zip", ".rar"))


def _match_id(path: Path, member: str | None) -> str:
    name = PurePosixPath(member.replace("\\", "/")).name if member else path.name
    for suffix in (".dem.bz2", ".dem.gz", ".dem"):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return Path(name).stem


def _members(path: Path) -> list[str | None]:
    lower = path.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return sorted(info.filename for info in archive.infolist()
                          if info.filename.lower().endswith(".dem") and not info.is_dir())
    if lower.endswith(".rar"):
        with rarfile.RarFile(path) as archive:
            if archive.needs_password():
                raise ValueError("压缩包需要密码")
            return sorted(info.filename for info in archive.infolist()
                          if info.filename.lower().endswith(".dem") and not info.is_dir())
    return [None]


def _inspect(path: Path, member: str | None, steamid: int, seen: set[str]) -> tuple[DemoRef | None, str]:
    """返回 (DemoRef 或 None, demo 指纹)。seen 是已处理过的指纹；同一份 demo 再次出现时不重复解析。"""
    demo = resolve_demo(path, member=member)
    fingerprint = demo_fingerprint(demo)
    if fingerprint in seen:
        return None, fingerprint
    seen.add(fingerprint)
    parser = DemoParser(str(demo))
    info = parser.parse_player_info()
    if info.empty:
        # 截断 demo 没有 player_info；按 parsing.match.roster_names 的口径查少量 tick 字段。
        raw = parser.parse_ticks(["team_num"])
        names = roster_names(info, raw)
    else:
        names = roster_names(info, info)
    if steamid not in names:
        return None, fingerprint
    return DemoRef(path=path, member=member, match_id=_match_id(path, member),
                   map_name=str(parser.parse_header().get("map_name", "")),
                   player_name=names[steamid]), fingerprint


def inspect_player_demos(steamid: int, candidates: list[tuple[Path, str | None]]) -> DemoRefs:
    """扫描指定文件；同一份 demo 保留第一个可读取的文件。"""
    refs = DemoRefs()
    seen: set[str] = set()
    used: dict[str, str] = {}  # 显示用 match_id → 指纹
    for path, selected in candidates:
        try:
            members = [selected] if selected is not None else _members(path)
            if not members:
                raise ValueError("压缩包内没有 .dem 文件")
        except (OSError, ValueError, zipfile.BadZipFile, rarfile.Error) as exc:
            refs.skipped.append(SkippedDemo(path=path, member=selected,
                match_id=_match_id(path, selected), reason=str(exc)))
            continue
        for member in members:
            try:
                ref, fingerprint = _inspect(path, member, steamid, seen)
            except Exception as exc:  # 损坏的 demo、解压失败或不支持的解析格式
                refs.skipped.append(SkippedDemo(path=path, member=member,
                                                match_id=_match_id(path, member), reason=str(exc)))
                continue
            if ref is not None:
                if used.setdefault(ref.match_id, fingerprint) != fingerprint:
                    ref.match_id = f"{ref.match_id}_{fingerprint[:8]}"
                    used[ref.match_id] = fingerprint
                refs.append(ref)
    refs.sort(key=lambda ref: ref.match_id)
    return refs


def find_player_demos(steamid: int, demos_dir: Path) -> list[DemoRef]:
    """递归找目标玩家的比赛，优先原始文件，失败原因保存在返回列表的 skipped。"""
    root = Path(demos_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"demo 目录不存在: {root}")
    paths = [path for path in root.rglob("*") if path.is_file() and _supported(path)]
    paths.sort(key=lambda path: (".extracted" in path.parts, str(path).lower()))
    return inspect_player_demos(steamid, [(path, None) for path in paths])
