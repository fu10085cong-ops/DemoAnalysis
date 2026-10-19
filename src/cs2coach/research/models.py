"""R01 研究表的结构化行。"""

from __future__ import annotations

from pydantic import BaseModel


class Situation(BaseModel):
    demo_id: str
    match_id: str
    round_id: int
    tick: int
    map: str
    trigger: str = "ct_first_5v4"
    alive_ct: int
    alive_t: int
    round_time_s: float | None = None
    time_remaining_s: float | None = None
    bomb_planted: bool | None = None
    first_killer_id: str | None = None  # 触发击杀的 CT（击杀者缺失时为空）


class BehaviourWindow(Situation):
    player_id: str
    player_name: str
    first_kill_by: str | None = None  # 第一个人头是谁拿的："self" 自己 / "teammate" 队友；击杀者缺失为空
    tick_rate: int | None = None
    start_tick: int
    end_tick: int
    start_position: tuple[float, float, float] | None = None
    end_position: tuple[float, float, float] | None = None
    displacement: float | None = None
    start_place: str | None = None
    end_place: str | None = None
    start_region: str | None = None
    end_region: str | None = None
    nav_start_distance: float | None = None
    nav_end_distance: float | None = None
    nav_delta: float | None = None
    yaw_change: float | None = None
    utility_types: list[str] = []
    shots: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    killed_enemy: bool = False
    died: bool = False
    death_tick: int | None = None
    teammate_distance_start: float | None = None
    teammate_distance_end: float | None = None
    visible_enemies_trigger: int | None = None
    visible_enemies_window: int | None = None
    radar_enemies_window: int | None = None
    advantage_held: bool | None = None
    ct_won: bool | None = None
    main_label: str = "unknown"
    aux_labels: list[str] = []
    label_confidence: float = 0.0
    jump_command: str
    review_status: str = "unreviewed"
