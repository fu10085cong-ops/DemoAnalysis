"""对外报告结构：保证证据字段与核心章节不会悄悄缺失。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AnalysisReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    demo: dict[str, Any]
    timebase: dict[str, Any]
    target_player: dict[str, Any]
    rounds: list[dict[str, Any]]
    moments: list[dict[str, Any]]
    metrics: dict[str, Any]
    economy_snapshots: list[dict[str, Any]]
    flash_summary: dict[str, Any]
    trade_analysis: dict[str, Any]
    flash_effects_on_target: list[dict[str, Any]]
    movement_fire_samples: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    thresholds: dict[str, Any]
    data_quality: dict[str, Any]
