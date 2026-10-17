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
    lines = []
    damage = match.damages
    enemy_damage = damage[(damage["attacker_team_num"] != damage["victim_team_num"])
                          & damage["attacker"].notna() & damage["victim"].notna()]
    gun_damage = enemy_damage[enemy_damage["weapon"].map(is_gun)]
    utility_damage = enemy_damage[enemy_damage["weapon"].isin(_UTILITY)]
    shots = match.shots[match.shots["is_gun"] & ~match.shots["controlling_bot"]]
    for sid, player in match.players.items():
        if not sid or sid <= 0:
            continue
        ticks = match.player_ticks(sid)
        teams = ticks.loc[ticks["team_num"].isin((2, 3)), "team_num"]
        team = int(teams.iloc[0]) if not teams.empty else 0
        mm = target_moments if sid == target and target_moments is not None else find_moments(
            match, sid, cfg, sight=sight, shots_with_targets=shots_with_targets)
        mine = [m for m in mm.moments if m.kind in ("kill", "death")]
        fired = int((shots["steamid"] == sid).sum())
        hits = int((gun_damage["attacker"] == sid).sum())
        lines.append(LobbyLine(
            steamid=sid, name=player.name, team=team, is_target=sid == target,
            kills=sum(m.kind == "kill" for m in mine), deaths=sum(m.kind == "death" for m in mine),
            accuracy_pct=round(100 * hits / fired, 2) if fired else None,
            aim=mm.aim_stats,
            opening_kills=sum("opening_kill" in m.tags for m in mine),
            opening_deaths=sum("opening_death" in m.tags for m in mine),
            trade_kills=sum(max((m.trade.avenged_teammate_count or 0) if m.trade else 0,
                                int("trade_kill" in m.tags)) for m in mine if m.kind == "kill"),
            flash_assists=sum("flash_assist" in m.tags for m in mm.moments),
            utility_damage=int(utility_damage.loc[utility_damage["attacker"] == sid, "dmg_effective"].sum()),
        ))
    return lines
