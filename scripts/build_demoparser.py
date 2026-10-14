"""从锁定的上游提交编译 demoparser2，输出 wheel 到 vendor/wheels/。

为什么需要：PyPI 上的 demoparser2 0.42.0 按 11 位解码实体句柄，实体编号 > 2047 的角色
（完美 demo 中常见）读不出任何数据。上游已在主分支修复（issue #362）但尚未发布。
详见 docs/fields.md §0 陷阱 12。官方发布包含修复的版本后，应改回使用 PyPI 版本并删除本脚本。

前置条件：git、Rust 工具链（https://rustup.rs）、protoc、uv。各平台的安装方法见 README.md。

用法:
    python scripts/build_demoparser.py              # 构建 wheel
    python scripts/build_demoparser.py --install    # 构建并安装到当前虚拟环境
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

DEMOPARSER_REPO = "https://github.com/LaihoE/demoparser.git"
# 主分支 2026-10-05，包含 "fix: decode 14-bit demo entity handles (#363)"
DEMOPARSER_COMMIT = "45ca85aeac8fb0de9d385124d2c7fe0e7b8ff0c8"

GAMETRACKING_REPO = "https://github.com/SteamDatabase/GameTracking-CS2.git"
# 2026-09-23（游戏版本 2000913）。demoparser 的构建脚本默认克隆 GameTracking-CS2 的最新版，
# 但 2026-10-04 之后的 .proto 使用了 protoc 无法解析的自定义选项（boxed_type 等），所以锁定在此之前。
GAMETRACKING_COMMIT = "10f3693c381475016b128c549928d14eb3adfcf2"
GAMETRACKING_PATHS = ["/Protobufs/", "/game/csgo/pak01_dir/resource/csgo_english.txt"]

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "vendor" / "wheels"


TOOLS: dict[str, str] = {}


def run(cmd: list[str], cwd: Path | None = None) -> None:
    # 用 shutil.which 解析出的完整路径调用，Windows 上不依赖 PATHEXT 的查找行为
    cmd = [TOOLS.get(cmd[0], cmd[0]), *cmd[1:]]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _force_remove(func, path, _exc) -> None:
    # Windows 上 git 的对象文件是只读的，直接删除会失败
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


def fetch(repo: str, commit: str, dest: Path, sparse: list[str] | None = None) -> None:
    run(["git", "init", "-q", str(dest)])
    run(["git", "remote", "add", "origin", repo], cwd=dest)
    extra = []
    if sparse:
        run(["git", "sparse-checkout", "set", "--no-cone", *sparse], cwd=dest)
        extra = ["--filter=blob:none"]
    run(["git", "fetch", "-q", "--depth=1", *extra, "origin", commit], cwd=dest)
    run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # 不依赖 cs2coach 包：首次安装时可能还没装好
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="构建后安装到当前 Python 环境")
    args = ap.parse_args(argv)

    missing = []
    for tool in ("git", "cargo", "protoc", "uv", "uvx"):
        found = shutil.which(tool)
        if found is None:
            missing.append(tool)
        else:
            TOOLS[tool] = found
    if missing:
        print(f"缺少 {', '.join(missing)}，请先安装（见 README.md）", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="dpb_"))
    try:
        src = tmp / "demoparser"
        fetch(DEMOPARSER_REPO, DEMOPARSER_COMMIT, src)
        # 预先放好锁定版本；构建脚本里的 git clone 会因目录已存在而失败，但其结果被忽略，会直接使用这里的文件
        fetch(GAMETRACKING_REPO, GAMETRACKING_COMMIT, src / "src" / "csgoproto" / "GameTracking-CS2", sparse=GAMETRACKING_PATHS)
        run(["uvx", "--from", "maturin>=1.0,<2.0", "maturin", "build", "--release",
             "-m", str(src / "src" / "python" / "Cargo.toml"),
             "-o", str(OUT_DIR), "-i", sys.executable])
    finally:
        rmtree(tmp)

    wheels = sorted(OUT_DIR.glob("demoparser2-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        print("没有生成 wheel", file=sys.stderr)
        return 1
    print(f"\n已生成: {wheels[-1]}")
    if args.install:
        run(["uv", "pip", "install", "--reinstall", "--no-deps", "--python", sys.executable, str(wheels[-1])])
    return 0


if __name__ == "__main__":
    sys.exit(main())
