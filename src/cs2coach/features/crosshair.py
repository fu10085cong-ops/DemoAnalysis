"""预瞄初始偏差的共用样本口径与情境排除条件。"""

import math

from cs2coach.baseline import EXCLUDED_TAGS, engagement_weapon
from cs2coach.features.models import Moment


def active_exclusions(moment: Moment | None, p: dict) -> set[str]:
    if moment is None:
        return set()
    engagement = moment.engagement
    active = set()
    if moment.through_smoke:
        active.add("through_smoke")
    if moment.wallbang:
        active.add("wallbang")
    if engagement is not None:
        if engagement.visibility_note == "smoke":
            active.add("first_seen_through_smoke")
        if engagement.player_blind is not None:
            active.add("player_blinded_in_fight")
        if (engagement.opponent_blind is not None and
                engagement.opponent_blind.thrower_role in ("self", "teammate")):
            active.add("opponent_blinded_by_own_side")
        if (engagement.shots_before_seen is not None and
                engagement.shots_before_seen >= p["max_shots_before_seen"]):
            active.add("fired_before_seen")
        if engagement.airborne_near_seen is True:
            active.add("airborne_near_seen")
        if (engagement.opponent_in_sight_before_view_ticks is not None and
                engagement.opponent_in_sight_before_view_ticks >= p["crosshair_turn_min_ticks"]):
            active.add("turned_into_view")
    if moment.kind == "death" and moment.weapon in ("hegrenade", "inferno", "molotov", "incgrenade"):
        active.add("killed_by_utility")
    return active


def crosshair_offset_sample(moment: Moment, cfg: dict) -> float | None:
    """可参与预瞄统计的初始偏差（米）；与准星问题的判定前提一致，不含阈值和 no_cost。"""
    if (moment.kind not in ("kill", "death") or EXCLUDED_TAGS.intersection(moment.tags)
            or moment.engagement is None):
        return None
    if engagement_weapon(moment) not in cfg["aim_stats"]["weapons"]:
        return None
    value = moment.engagement.crosshair_initial_offset_m
    if value is None or not math.isfinite(value):
        return None
    p = cfg["problems"]
    distance = moment.context.distance if moment.context is not None else None
    if distance is not None and math.isfinite(distance) and distance < p["crosshair_min_distance_m"]:
        return None
    active = active_exclusions(moment, p)
    if any(condition in active and "poor_crosshair_placement" in ids
           for condition, ids in p["exclusions"].items()):
        return None
    return value
