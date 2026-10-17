"""同场玩家的可比较统计；复用目标玩家的决定性瞬间和几何视野。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cs2coach.features.models import LobbyLine
from cs2coach.parsing.weapons import is_gun

if TYPE_CHECKING:
    from cs2coach.features.models import MatchMoments
    from cs2coach.features.visibility import Sight
    from cs2coach.parsing.match import MatchData


_UTILITY = {"hegrenade", "inferno", "molotov", "incgrenade"}


def lobby_lines(match: MatchData, target: int, cfg: dict, sight: Sight,
                target_moments: MatchMoments | None = None,
                shots_with_targets=None) -> list[LobbyLine]:
    """每名真实玩家一行，包括中途断线者；目标玩家沿用已计算的瞬间。"""
    from cs2coach.features.aim import shot_targets
    from cs2coach.features.moments import find_moments

    if shots_with_targets is None:
        shots_with_targets = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
    inputs = _lobby_inputs(match, cfg)
    lines = []
    for sid in match.players:
        if not sid or sid <= 0:
            continue
        mm = target_moments if sid == target and target_moments is not None else find_moments(
            match, sid, cfg, sight=sight, shots_with_targets=shots_with_targets)
        lines.append(lobby_line(match, sid, cfg, mm, is_target=sid == target, inputs=inputs))
    return lines


def _lobby_inputs(match: MatchData, cfg: dict) -> tuple:
    """同场批量计算时共用筛选结果。"""
    damage = match.damages
    enemy_damage = damage[(damage["attacker_team_num"] != damage["victim_team_num"])
                          & damage["attacker"].notna() & damage["victim"].notna()]
    gun_damage = enemy_damage[enemy_damage["weapon"].map(is_gun)]
    utility_damage = enemy_damage[enemy_damage["weapon"].isin(_UTILITY)]
    shots = match.shots[match.shots["is_gun"] & ~match.shots["controlling_bot"]]
    flashes = match.grenades[match.grenades["type"] == "flashbang"] if not match.grenades.empty else match.grenades
    blinds = match.blinds[match.blinds["duration"] >= cfg["moments"]["min_blind_seconds"]] if not match.blinds.empty else match.blinds
    prices = cfg["economy"]["grenade_prices"]
    return gun_damage, utility_damage, shots, flashes, blinds, prices


def lobby_line(match: MatchData, sid: int, cfg: dict, mm: MatchMoments,
               *, is_target: bool = True, inputs: tuple | None = None) -> LobbyLine:
    """计算单人统计，汇总无需为同场其他玩家生成瞬间。"""
    gun_damage, utility_damage, shots, flashes, blinds, prices = inputs or _lobby_inputs(match, cfg)
    player = match.players[sid]
    ticks = match.player_ticks(sid)
    teams = ticks.loc[ticks["team_num"].isin((2, 3)), "team_num"]
    team = int(teams.iloc[0]) if not teams.empty else 0
    mine = [m for m in mm.moments if m.kind in ("kill", "death")]
    fired = int((shots["steamid"] == sid).sum())
    hits = int((gun_damage["attacker"] == sid).sum())
    deaths = [m for m in mine if m.kind == "death"]
    wasted = [sum(prices[g] for g in m.death.unused_grenades) for m in deaths if m.death]
    flash_count = int((flashes["thrower"] == sid).sum()) if not flashes.empty else 0
    thrown_blinds = blinds[blinds["thrower"] == sid] if not blinds.empty else blinds
    if thrown_blinds.empty:
        enemies_flashed = teammates_flashed = 0
    else:
        known_teams = (thrown_blinds["thrower_team_num"].isin((2, 3))
                       & thrown_blinds["victim_team_num"].isin((2, 3)))
        other = thrown_blinds["victim"] != sid
        enemies_flashed = int((known_teams & other & (thrown_blinds["victim_team_num"]
                                                      != thrown_blinds["thrower_team_num"])).sum())
        teammates_flashed = int((known_teams & other & (thrown_blinds["victim_team_num"]
                                                        == thrown_blinds["thrower_team_num"])).sum())
    return LobbyLine(
        steamid=sid, name=player.name, team=team, is_target=is_target, rounds_played=mm.rounds_played,
        kills=sum(m.kind == "kill" for m in mine), deaths=sum(m.kind == "death" for m in mine),
        accuracy_pct=round(100 * hits / fired, 2) if fired else None,
        aim=mm.aim_stats,
        opening_kills=sum("opening_kill" in m.tags for m in mine),
        opening_deaths=sum("opening_death" in m.tags for m in mine),
        trade_kills=sum(max((m.trade.avenged_teammate_count or 0) if m.trade else 0,
                            int("trade_kill" in m.tags)) for m in mine if m.kind == "kill"),
        flash_assists=sum("flash_assist" in m.tags for m in mm.moments),
        trade_kill_events=sum("trade_kill" in m.tags for m in mine if m.kind == "kill"),
        flashes_thrown=flash_count,
        utility_damage=int(utility_damage.loc[utility_damage["attacker"] == sid, "dmg_effective"].sum()),
        traded_deaths=sum("traded" in m.tags for m in deaths),
        wasted_utility_avg=sum(wasted) / len(deaths) if deaths else None,
        enemies_flashed_per_flash=enemies_flashed / flash_count if flash_count else None,
        teammates_flashed_per_flash=teammates_flashed / flash_count if flash_count else None,
    )
