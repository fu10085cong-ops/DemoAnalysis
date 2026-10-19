"""透明且可人工校准的第一版行为标签。"""

from __future__ import annotations

from cs2coach.research.models import BehaviourWindow


def _place(name: str | None, position, floors: dict) -> str | None:
    """按高度拆分的报点（如 Palace 楼下 / 楼上），其余原样返回。"""
    if name is None or name not in floors or position is None:
        return name
    return f"{name}/{'lower' if position[2] < floors[name] else 'upper'}"


def _zone_label(window: BehaviourWindow, cfg: dict) -> str | None:
    """起点和终点在同一防守区组内 → 保持；从防守区组或纵深进入另一个防守区组 → 转点。"""
    zones = cfg["research"].get("zones", {}).get(window.map, {})
    floors = cfg["research"].get("floors", {}).get(window.map, {})
    start = _place(window.start_place, window.start_position, floors)
    end = _place(window.end_place, window.end_position, floors)
    start_zones = {name for name, places in zones.items() if start in places}
    end_zones = {name for name, places in zones.items() if end in places}
    if start_zones & end_zones:
        return "hold"
    if end_zones and (start_zones or window.start_region == "deep"):
        return "rotate"
    return None


def label_window(window: BehaviourWindow, cfg: dict) -> tuple[str, list[str], float]:
    limits = cfg["research"]["labels"]
    aux = []
    if window.utility_types:
        aux.append("utility")
    start, end = window.teammate_distance_start, window.teammate_distance_end
    if start is not None and end is not None and start - end > limits["regroup_min_distance"] \
            and end < limits["regroup_max_end_distance"]:
        aux.append("regroup")
    if window.shots > 0 or window.damage_taken > 0:
        aux.append("engaged")

    delta, move = window.nav_delta, window.displacement
    short = window.died and (window.tick_rate is None or
                             window.end_tick - window.start_tick <
                             limits["min_window_seconds"] * window.tick_rate)
    if short:
        return "unknown", aux, 0.0
    zone = _zone_label(window, cfg)
    if zone is not None:
        return zone, aux, 1.0  # 按地点分组判断，没有阈值距离可言
    if delta is None or move is None:
        return "unknown", aux, 0.0
    if move < limits["hold_max_displacement"] and abs(delta) < limits["hold_max_nav_delta"]:
        label = "hold"
        margin = min((limits["hold_max_displacement"] - move) / limits["hold_max_displacement"],
                     (limits["hold_max_nav_delta"] - abs(delta)) / limits["hold_max_nav_delta"])
    elif delta <= -limits["move_min_nav_delta"]:
        label = "advance"
        margin = (-delta - limits["move_min_nav_delta"]) / limits["move_min_nav_delta"]
    elif delta >= limits["move_min_nav_delta"]:
        label = "retreat"
        margin = (delta - limits["move_min_nav_delta"]) / limits["move_min_nav_delta"]
    elif move >= limits["lateral_min_displacement"] and abs(delta) < limits["move_min_nav_delta"]:
        label = "lateral"
        margin = min((move - limits["lateral_min_displacement"]) / limits["lateral_min_displacement"],
                     (limits["move_min_nav_delta"] - abs(delta)) / limits["move_min_nav_delta"])
    else:
        return "unknown", aux, 0.0
    return label, aux, round(max(0.0, min(1.0, margin)), 3)
