"""048 并行测试用的模块级函数：子进程只能导入模块级函数（Windows 用 spawn 启动子进程）。"""

import os
import time


def square_with_offset(value: int, offset: int) -> tuple[int, int]:
    # 越小的值睡得越久，让完成顺序和输入顺序相反，以检验结果仍按输入顺序返回
    time.sleep(0.05 * (3 - value) if value < 3 else 0)
    return value * value + offset, os.getpid()


def fail_on_two(value: int) -> int:
    if value == 2:
        raise ValueError("第二项坏了")
    return value
