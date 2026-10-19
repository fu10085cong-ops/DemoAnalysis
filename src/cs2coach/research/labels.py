"""透明且可人工校准的第一版行为标签。"""

from __future__ import annotations

from cs2coach.research.models import BehaviourWindow


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
    if delta is None or move is None or short:
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
