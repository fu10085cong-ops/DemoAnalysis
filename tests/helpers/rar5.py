"""在测试中生成最小的 RAR5 存储模式（不压缩）归档，避免依赖专有的 rar 打包工具或二进制夹具。

格式参考 RAR 5.0 archive format 公开文档：签名 + 主头 + 文件头(+数据) + 结束头，
每个头 = CRC32(头) + vint(头大小) + 头内容。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIGNATURE = b"Rar!\x1a\x07\x01\x00"


def _vint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _block(body: bytes) -> bytes:
    sized = _vint(len(body)) + body
    return struct.pack("<I", zlib.crc32(sized)) + sized


def _file_block(name: str, data: bytes) -> bytes:
    name_b = name.encode("utf-8")
    body = (
        _vint(2)  # 头类型：文件
        + _vint(0x02)  # 头标志：有数据区
        + _vint(len(data))  # 数据区大小
        + _vint(0x04)  # 文件标志：有数据 CRC32
        + _vint(len(data))  # 解压后大小
        + _vint(0x20)  # 属性
        + struct.pack("<I", zlib.crc32(data))
        + _vint(0)  # 压缩信息：算法版本 0，方法 0 = 存储
        + _vint(0)  # 宿主系统：Windows
        + _vint(len(name_b))
        + name_b
    )
    return _block(body) + data


def write_rar5_store(path: Path, files: dict[str, bytes]) -> Path:
    parts = [SIGNATURE, _block(_vint(1) + _vint(0) + _vint(0))]  # 主头
    parts += [_file_block(n, d) for n, d in files.items()]
    parts.append(_block(_vint(5) + _vint(0) + _vint(0)))  # 结束头
    path.write_bytes(b"".join(parts))
    return path
