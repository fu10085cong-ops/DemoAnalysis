"""控制台输出编码。

Windows 的控制台和重定向文件默认使用 GBK（cp936）等本地编码，玩家昵称里常见的特殊字符
无法编码时 print 会直接抛 UnicodeEncodeError。入口处统一改为 UTF-8，无法编码的字符替换掉。
"""

from __future__ import annotations

import sys


def ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
