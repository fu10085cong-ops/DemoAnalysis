"""启用仓库自带的 git 钩子（scripts/hooks/），每台电脑运行一次：python scripts/install_hooks.py

用 core.hooksPath 指向仓库里的目录，独立工作目录（git worktree）自动共用。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    subprocess.run(["git", "config", "core.hooksPath", "scripts/hooks"], cwd=ROOT, check=True)
    hook = ROOT / "scripts/hooks/pre-commit"
    try:
        hook.chmod(hook.stat().st_mode | 0o111)  # Windows 上无效也无害：Git for Windows 用自带的 sh 运行钩子
    except OSError:
        pass
    print("已启用 scripts/hooks/：每次提交前自动运行隐私扫描。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
