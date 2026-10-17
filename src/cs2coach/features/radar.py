"""分段雷达：每人每场指标、同方向百分位与维度聚合。"""

from statistics import mean, median

from cs2coach.features.models import LobbyLine


RADAR_METRICS: dict[str, bool] = {
    "time_to_damage_ms": True, "crosshair_placement_deg": True,
    "counter_strafing_pct": False, "spray_accuracy_pct": False,
    "trade_kills_per_round": False, "traded_death_pct": False,
    "utility_damage_per_round": False, "enemies_flashed_per_flash": False,
    "wasted_utility_avg": True,
}


def player_metrics(line: LobbyLine) -> dict[str, float | None]:
    """分母为零或原指标样本不足时保留空值。"""
    return {
        **{key: getattr(line.aim, key) if line.aim else None
           for key in ("time_to_damage_ms", "crosshair_placement_deg", "counter_strafing_pct", "spray_accuracy_pct")},
        "trade_kills_per_round": line.trade_kills / line.rounds_played if line.rounds_played else None,
        "traded_death_pct": 100 * line.traded_deaths / line.deaths if line.deaths else None,
        "utility_damage_per_round": line.utility_damage / line.rounds_played if line.rounds_played else None,
        "enemies_flashed_per_flash": line.enemies_flashed_per_flash,
        "wasted_utility_avg": line.wasted_utility_avg,
    }


def percentile(metric: str, value: float | None, tier: dict) -> float | None:
    """沿用 baseline.compare 的严格比较、相等算半个样本和整数舍入。"""
    values = tier.get("metrics", {}).get(metric, {}).get("values", [])
    if value is None or not values:
        return None
    better = sum(value < x if RADAR_METRICS[metric] else value > x for x in values)
    better += 0.5 * sum(value == x for x in values)
    return round(100 * better / len(values))


def axis_values(metrics: dict[str, float | None], tier: dict, axes: list[dict]) -> list[float | None]:
    """维度内指标等权平均，跳过缺失值。"""
    result = []
    for axis in axes:
        values = [percentile(key, metrics.get(key), tier) for key in axis["metrics"]]
        valid = [x for x in values if x is not None]
        result.append(mean(valid) if valid else None)
    return result


def median_axes(values: list[list[float | None]], size: int) -> list[float | None]:
    """多场先逐场算维度，再逐维度取中位数。"""
    result = []
    for index in range(size):
        valid = [row[index] for row in values if row[index] is not None]
        result.append(median(valid) if valid else None)
    return result
