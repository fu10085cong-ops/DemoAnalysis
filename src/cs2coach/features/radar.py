"""分段雷达：每人每场指标、同方向百分位与维度聚合。"""

from statistics import mean, median

from cs2coach.features.models import LobbyLine


RADAR_METRICS: dict[str, bool] = {
    "time_to_damage_ms": True, "crosshair_offset_m": True,
    "counter_strafing_pct": False, "spray_accuracy_pct": False,
    "trade_kill_events_per_round": False, "traded_death_pct": False,
    "utility_damage_per_round": False, "enemies_flashed_per_flash": False,
    "missed_trade_pct": True,
}


def sample_count(line: LobbyLine, metric: str) -> int:
    """指标真正参与统计的样本数，不用代表例子的数量代替。"""
    fields = {"time_to_damage_ms": "time_to_damage_samples",
              "crosshair_offset_m": "crosshair_offset_samples",
              "counter_strafing_pct": "counter_strafing_shots", "spray_accuracy_pct": "spray_shots"}
    if metric in fields:
        return getattr(line.aim, fields[metric]) if line.aim else 0
    if metric == "traded_death_pct":
        return line.deaths
    if metric == "missed_trade_pct":
        return line.trade_chances
    if metric == "enemies_flashed_per_flash":
        return line.flashes_thrown
    return line.rounds_played


def minimum_samples(metric: str, cfg: dict) -> int:
    """枪法沿用原门槛，其他指标按自己的分母设门槛。"""
    if metric in tuple(RADAR_METRICS)[:4]:
        return cfg["aim_stats"]["min_samples"]
    key = {"traded_death_pct": "deaths", "enemies_flashed_per_flash": "flashes",
           "missed_trade_pct": "trade_chances"}.get(metric, "rounds")
    return cfg["radar"]["min_samples"][key]


def player_metrics(line: LobbyLine, cfg: dict) -> dict[str, float | None]:
    """分母为零或原指标样本不足时保留空值。"""
    values = {
        **{key: getattr(line.aim, key) if line.aim else None
           for key in ("time_to_damage_ms", "crosshair_offset_m", "counter_strafing_pct", "spray_accuracy_pct")},
        "trade_kill_events_per_round": line.trade_kill_events / line.rounds_played if line.rounds_played else None,
        "traded_death_pct": 100 * line.traded_deaths / line.deaths if line.deaths else None,
        "missed_trade_pct": 100 * line.missed_trade_chances / line.trade_chances if line.trade_chances else None,
        "utility_damage_per_round": line.utility_damage / line.rounds_played if line.rounds_played else None,
        "enemies_flashed_per_flash": line.enemies_flashed_per_flash,
    }
    return {key: value if sample_count(line, key) >= minimum_samples(key, cfg) else None
            for key, value in values.items()}


def percentile(metric: str, value: float | None, tier: dict, cfg: dict) -> float | None:
    """沿用 baseline.compare 的严格比较、相等算半个样本和整数舍入。"""
    values = tier.get("metrics", {}).get(metric, {}).get("values", [])
    if value is None or not values or len(values) < cfg["radar"]["min_samples"]["tier_values"]:
        return None
    better = sum(value < x if RADAR_METRICS[metric] else value > x for x in values)
    better += 0.5 * sum(value == x for x in values)
    return round(100 * better / len(values))


def axis_values(metrics: dict[str, float | None], tier: dict, axes: list[dict], cfg: dict) -> list[float | None]:
    """维度内指标等权平均，跳过缺失值。"""
    result = []
    for axis in axes:
        values = [percentile(key, metrics.get(key), tier, cfg) for key in axis["metrics"]]
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
