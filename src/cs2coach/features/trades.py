"""补枪机会与漏补检测共用的情境排除条件。"""

from cs2coach.features.models import MissedTrade, TradeChance


def trade_chance_skip(record: TradeChance | MissedTrade, cfg: dict) -> str | None:
    """不计入的原因（与 missed_trade 的 skipped 键后缀一致），计入返回 None。"""
    p = cfg["problems"]
    if p["missed_trade_exclude_traded_by_other"] and record.killer_traded_by_other:
        return "killer_traded_by_other"
    if record.alive_own == 1:
        return "last_alive"
    if record.killer_in_view_s is None:
        return "missing"
    # 补上了说明看得够久；最短时长仅用于排除一闪而过、来不及补的漏补。
    if not getattr(record, "traded_by_me", False) and record.killer_in_view_s < p["missed_trade_min_view_seconds"]:
        return "killer_in_view_s"
    if (record.killer_first_in_view_s is None or record.killer_first_in_view_s >
            cfg["moments"]["trade_window_seconds"] - p["missed_trade_min_react_seconds"]):
        return "killer_first_in_view_s"
    if record.reloading_at_death:
        return "reloading_at_death"
    if record.other_fight_s is not None:
        return "other_fight_s"
    return None
