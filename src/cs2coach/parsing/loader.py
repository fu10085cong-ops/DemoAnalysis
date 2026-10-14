"""定位 / 解压 demo 文件，返回可直接交给 demoparser2 的 .dem 路径。

支持：.dem、.dem.bz2、.dem.gz、.zip、.rar。
- 压缩包内有多个 .dem（如 HLTV 一个 rar 打包多张图）时，必须用 member 指定，否则报错并列出候选。
- 解压结果写入 cache_dir（默认与输入文件同目录下的 .extracted/），已存在则复用。
- .rar：用 rarfile 读目录（纯 Python），解压调用系统工具（unrar / 7z / bsdtar 任一）。
  不用 rarfile 自带的解压：它的 bsdtar 后端会传 `-f -- <文件>`，bsdtar 会把 `--` 当成归档名而失败。
"""

from __future__ import annotations

import bz2
import gzip
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

import rarfile

_CHUNK = 1024 * 1024

RAR_TOOL_HINT = (
    "解压 .rar 需要系统中有以下任一工具：unrar、7z/7zz、bsdtar。\n"
    "  macOS：系统自带 bsdtar，无需安装（或 brew install sevenzip）\n"
    "  Windows 10/11：系统自带 tar.exe（bsdtar），或安装 7-Zip\n"
    "  Linux：sudo apt install libarchive-tools（或 unrar / 7zip）"
)


class DemoLoadError(Exception):
    pass


def resolve_demo(path: Path, cache_dir: Path | None = None, member: str | None = None) -> Path:
    path = Path(path)
    if not path.is_file():
        raise DemoLoadError(f"demo 文件不存在: {path}")

    name = path.name.lower()
    if name.endswith(".dem"):
        return path

    cache_dir = Path(cache_dir) if cache_dir else path.parent / ".extracted"

    if name.endswith(".dem.bz2"):
        return _decompress_stream(path, cache_dir / path.name[: -len(".bz2")], bz2.open)
    if name.endswith(".dem.gz"):
        return _decompress_stream(path, cache_dir / path.name[: -len(".gz")], gzip.open)
    if name.endswith(".zip"):
        return _extract_zip(path, cache_dir, member)
    if name.endswith(".rar"):
        return _extract_rar(path, cache_dir, member)

    raise DemoLoadError(f"不支持的文件类型: {path.name}（支持 .dem / .dem.bz2 / .dem.gz / .zip / .rar）")


def _decompress_stream(src: Path, dst: Path, opener) -> Path:
    if dst.is_file():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with opener(src, "rb") as fin, open(tmp, "wb") as fout:
        shutil.copyfileobj(fin, fout, _CHUNK)
    tmp.replace(dst)
    return dst


def _pick_member(src: Path, names: list[str], member: str | None) -> str:
    """从压缩包内的 .dem 列表中选出一个。member 可写完整路径或仅文件名。"""
    if not names:
        raise DemoLoadError(f"压缩包内没有 .dem 文件: {src}")
    if member is None:
        if len(names) == 1:
            return names[0]
        listing = "\n".join(f"  - {n}" for n in names)
        raise DemoLoadError(f"压缩包内有 {len(names)} 个 demo，请用 --demo-member 指定其一:\n{listing}")
    matches = [n for n in names if n == member or PurePosixPath(n).name == member]
    if len(matches) != 1:
        listing = "\n".join(f"  - {n}" for n in names)
        reason = "未找到" if not matches else "匹配到多个"
        raise DemoLoadError(f"--demo-member {member!r} {reason}，可选:\n{listing}")
    return matches[0]


def _cache_target(src: Path, cache_dir: Path, member_name: str) -> Path:
    # 按压缩包名分子目录，避免不同压缩包里同名 demo 互相复用缓存；只取文件名，避免路径穿越
    return cache_dir / src.stem / PurePosixPath(member_name.replace("\\", "/")).name


def _extract_zip(src: Path, cache_dir: Path, member: str | None) -> Path:
    with zipfile.ZipFile(src) as zf:
        names = [m.filename for m in zf.infolist() if m.filename.lower().endswith(".dem") and not m.is_dir()]
        chosen = _pick_member(src, names, member)
        dst = _cache_target(src, cache_dir, chosen)
        if dst.is_file():
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".dem.part")
        with zf.open(chosen) as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout, _CHUNK)
        tmp.replace(dst)
        return dst


def _extract_rar(src: Path, cache_dir: Path, member: str | None) -> Path:
    try:
        rf = rarfile.RarFile(src)
    except rarfile.NeedFirstVolume as e:
        raise DemoLoadError(f"这是分卷压缩包的非首卷，请传入第一卷（.part1.rar）: {src}") from e
    except rarfile.Error as e:
        raise DemoLoadError(f"无法读取 rar 文件 {src}: {e}") from e

    with rf:
        if rf.needs_password():
            raise DemoLoadError(f"不支持加密的 rar 文件: {src}")
        infos = {i.filename: i for i in rf.infolist() if i.filename.lower().endswith(".dem") and not i.is_dir()}
    chosen = _pick_member(src, list(infos), member)
    info = infos[chosen]
    if PurePosixPath(chosen).name.startswith("-"):
        raise DemoLoadError(f"不支持以 '-' 开头的文件名: {chosen}")

    dst = _cache_target(src, cache_dir, chosen)
    if dst.is_file() and dst.stat().st_size == info.file_size:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = _rar_extract_cmd(src.resolve(), chosen)
    tmp = dst.with_suffix(".dem.part")
    with open(tmp, "wb") as fout:
        proc = subprocess.run(cmd, stdout=fout, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    size = tmp.stat().st_size
    if proc.returncode != 0 or size != info.file_size:
        tmp.unlink(missing_ok=True)
        err = proc.stderr.decode(errors="replace").strip()[:500]
        raise DemoLoadError(
            f"解压 {chosen} 失败（工具 {Path(cmd[0]).name}，退出码 {proc.returncode}，"
            f"得到 {size} / 期望 {info.file_size} 字节）: {err}"
        )
    tmp.replace(dst)
    return dst


def _rar_extract_cmd(archive: Path, member: str) -> list[str]:
    """按 unrar → 7z → bsdtar 的顺序找可用工具，返回把 member 输出到 stdout 的命令。"""
    tool = find_rar_tool()
    if tool is None:
        raise DemoLoadError(RAR_TOOL_HINT)
    kind, exe = tool
    if kind == "unrar":
        return [exe, "p", "-inul", str(archive), member]
    if kind == "7z":
        return [exe, "e", "-so", "-bd", str(archive), member]
    return [exe, "-x", "-O", "-f", str(archive), member]


def find_rar_tool() -> tuple[str, str] | None:
    """返回 (类型, 可执行文件路径)；类型为 unrar / 7z / bsdtar。"""
    for kind, names in (("unrar", ["unrar"]), ("7z", ["7z", "7zz"]), ("bsdtar", ["bsdtar"])):
        for n in names:
            exe = shutil.which(n)
            if exe:
                return kind, exe
    # Windows 10/11 与 macOS 的 tar 就是 bsdtar；GNU tar 不支持 rar
    tar = shutil.which("tar")
    if tar and _is_bsdtar(tar):
        return "bsdtar", tar
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base and (exe := Path(base) / "7-Zip" / "7z.exe").is_file():
                return "7z", str(exe)
    return None


def _is_bsdtar(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return "bsdtar" in out.stdout.lower()
