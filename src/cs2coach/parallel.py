"""逐场多进程并行：结果按输入顺序返回，单项失败不影响其他项。"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass
class Outcome:
    value: Any            # 成功时的返回值，失败时为 None
    error: str | None     # 失败时为 "异常类型: 消息"


def default_jobs(cfg: dict) -> int:
    """配置 tools.jobs 与 CPU 核数的较小值。"""
    return min(cfg["tools"]["jobs"], os.cpu_count() or 1)


def _run_one(fn: Callable, item: tuple, args: tuple) -> Any:
    return fn(*item, *args)


def _capture(fn: Callable, item: tuple, args: tuple) -> Outcome:
    try:
        return Outcome(_run_one(fn, item, args), None)
    except Exception as exc:
        return Outcome(None, f"{type(exc).__name__}: {exc}")


def _outcome_of(future) -> Outcome:
    try:
        return Outcome(future.result(), None)
    except Exception as exc:
        return Outcome(None, f"{type(exc).__name__}: {exc}")


def map_ordered(fn: Callable, items: Iterable[tuple], jobs: int,
                args: tuple = (), on_done: Callable | None = None) -> list[Outcome]:
    """对每项调用 fn(*item, *args)，按输入顺序返回 Outcome；任何一项的异常只记入该项。

    jobs <= 1 时在本进程串行执行，不创建进程池；否则用 ProcessPoolExecutor。
    on_done(index, outcome) 在每项完成时在主进程调用一次，完成顺序可与输入顺序不同。
    """
    items = list(items)
    if not items:
        return []
    if jobs <= 1:
        outcomes = []
        for index, item in enumerate(items):
            outcome = _capture(fn, item, args)
            outcomes.append(outcome)
            if on_done is not None:
                on_done(index, outcome)
        return outcomes
    outcomes: list[Outcome] = [None] * len(items)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        pending = {pool.submit(_run_one, fn, item, args): index
                   for index, item in enumerate(items)}
        for future in as_completed(pending):
            index = pending[future]
            outcome = _outcome_of(future)
            outcomes[index] = outcome
            if on_done is not None:
                on_done(index, outcome)
    return outcomes
