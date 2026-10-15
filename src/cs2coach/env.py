"""读取项目根目录的 .env（已在 .gitignore 中），用于放 API key，不必修改系统环境变量。

格式：每行 KEY=VALUE，# 开头为注释，值两边的引号会去掉。已经存在的环境变量优先，不会被覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> list[str]:
    """把 path 中的变量写入 os.environ（不覆盖已有的），返回实际写入的变量名。"""
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
