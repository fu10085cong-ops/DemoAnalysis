"""定位 / 解压 demo 文件，返回可直接交给 demoparser2 的 .dem 路径。

支持：.dem、.dem.bz2、.dem.gz、.zip、.rar。
- 压缩包内有多个 .dem（如 HLTV 一个 rar 打包多张图）时，必须用 member 指定，否则报错并列出候选。
- 解压结果写入 cache_dir（默认与输入文件同目录下的 .extracted/），已存在则复用。
- .rar：用 rarfile 读目录（纯 Python），解压调用系统工具（unrar / 7z / bsdtar 任一）。
  不用 rarfile 自带的解压：它的 bsdtar 后端会传 `-f -- <文件>`，bsdtar 会把 `--` 当成归档名而失败。
"""

from __future__ import annotations

import bz2
import hashlib
import gzip
import json
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
        dst = cache_dir / _source_cache_key(path) / path.name[: -len(".bz2")]
        return _decompress_stream(path, dst, bz2.open)
    if name.endswith(".dem.gz"):
        dst = cache_dir / _source_cache_key(path) / path.name[: -len(".gz")]
        return _decompress_stream(path, dst, gzip.open)
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


def _cache_target(src: Path, cache_dir: Path, member_name: str, *, duplicate_basename: bool = False) -> Path:
    # 用归档身份和完整成员路径区分缓存；成员路径哈希可避免路径穿越及包内同名冲突。
    member_path = member_name.replace("\\", "/")
    member_key = hashlib.sha256(member_path.encode("utf-8")).hexdigest()[:16]
    basename = PurePosixPath(member_path).name
    stem = src.stem
    if stem in {"", ".", ".."} or Path(stem).name != stem:
        stem = hashlib.sha256(src.name.encode("utf-8")).hexdigest()[:16]
    legacy = cache_dir / stem / basename
    manifest = _cache_manifest(legacy)
    if duplicate_basename:
        return cache_dir / _source_cache_key(src) / member_key / basename
    if legacy.is_file() and manifest.is_file():
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        expected = {"source": _source_cache_key(src), "member": member_path}
        if existing and existing != expected:
            return cache_dir / _source_cache_key(src) / member_key / basename
    return legacy


def _source_cache_key(src: Path) -> str:
    """让缓存随源文件路径或文件版本变化，避免旧解压结果被复用。"""
    resolved = src.resolve()
    stat = resolved.stat()
    identity = f"{resolved}\0{stat.st_size}\0{stat.st_mtime_ns}\0{stat.st_ctime_ns}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _cache_manifest(dst: Path) -> Path:
    return dst.with_name(dst.name + ".cache.json")


def _cache_matches(dst: Path, src: Path, member_name: str) -> bool:
    manifest = _cache_manifest(dst)
    if not dst.is_file() or not manifest.is_file():
        return False
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return existing == {"source": _source_cache_key(src), "member": member_name.replace("\\", "/")}


def _write_cache_manifest(dst: Path, src: Path, member_name: str) -> None:
    manifest = _cache_manifest(dst)
    tmp = manifest.with_suffix(manifest.suffix + ".part")
    payload = {"source": _source_cache_key(src), "member": member_name.replace("\\", "/")}
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(manifest)


def _extract_zip(src: Path, cache_dir: Path, member: str | None) -> Path:
    with zipfile.ZipFile(src) as zf:
        names = [m.filename for m in zf.infolist() if m.filename.lower().endswith(".dem") and not m.is_dir()]
        chosen = _pick_member(src, names, member)
        basename = PurePosixPath(chosen.replace("\\", "/")).name
        duplicate = sum(PurePosixPath(n.replace("\\", "/")).name == basename for n in names) > 1
        dst = _cache_target(src, cache_dir, chosen, duplicate_basename=duplicate)
        if _cache_matches(dst, src, chosen):
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".dem.part")
        with zf.open(chosen) as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout, _CHUNK)
        tmp.replace(dst)
        _write_cache_manifest(dst, src, chosen)
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

    basename = PurePosixPath(chosen.replace("\\", "/")).name
    duplicate = sum(PurePosixPath(n.replace("\\", "/")).name == basename for n in infos) > 1
    dst = _cache_target(src, cache_dir, chosen, duplicate_basename=duplicate)
    if _cache_matches(dst, src, chosen) and dst.stat().st_size == info.file_size:
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
    _write_cache_manifest(dst, src, chosen)
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
