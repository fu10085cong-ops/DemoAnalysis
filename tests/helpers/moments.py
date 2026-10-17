"""手工构造瞬间和整场结果，用于基准与问题前提条件的单元测试。"""

from cs2coach.features.labels import weapon_label
from cs2coach.features.models import MatchMoments, Moment, Shot
from tests.helpers.synthetic import A, C


def make_shot(tick=90, weapon="ak47", recoil_index=0.0, hit=False, stopped=True, **overrides):
    data = dict(tick=tick, weapon=weapon, speed=0.0, max_speed=215.0, speed_ratio=0.5 if stopped is False else 0.0,
                stopped=stopped, crouched=False, airborne=False, scoped=False,
                recoil_index=recoil_index, hit=hit, seen_opponent=True)
    return Shot(**(data | overrides))


def make_moment(kind="death", weapon="ak47", **overrides):
    shot = make_shot(weapon=weapon)
    engagement = dict(start_tick=80, end_tick=100, duration_s=0.3125, shots=[shot], shots_fired=1,
                      shots_hit=0, first_shot_hit=False, damage_dealt=0, damage_taken=100,
                      player_first_shot_tick=90, opponent_first_shot_tick=85, first_shot_lead_s=-0.078,
                      counter_strafe_shots=1, counter_strafe_stopped=1, first_shot_stopped=True,
                      time_to_damage_s=0.3, opponent_time_to_damage_s=0.2,
                      crosshair_placement_deg=3.0, contact_type="both_still")
    context = dict(side="T", alive_own=2, alive_enemy=2, round_time_s=10, bomb_planted=False,
                   own_equip_value=3000, opponent_equip_value=3000, own_weapon=weapon_label(weapon),
                   opponent_weapon="AK-47", own_health_before=100, opponent_health_before=100,
                   distance=10.0, place="Mid", opponent_place="Mid")
    death = dict(unused_grenades=[], blinded=False, blind_remaining_s=None,
                 blinded_by=None, blinded_by_teammate=None)
    data = dict(id=f"R1-{kind}" + ("1" if kind != "death" else ""), kind=kind, round=1, tick=100,
                player=A, opponent=C, opponent_name="carol", weapon=weapon, headshot=False,
                through_smoke=False, wallbang=False, noscope=False, attacker_blind=False,
                tags=[], context=context, engagement=engagement if kind != "assist" else None,
                trade=dict(traded=False) if kind == "death" else dict(is_trade_kill=False),
                death=death if kind == "death" else None,
                assist=dict(flash_assist=False, damage_to_victim=80, killer=C) if kind == "assist" else None,
                jump_tick=1, jump_command="demo_gototick 1")
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = data[key] | value
        else:
            data[key] = value
    # 032：出枪慢比较"看见 → 开枪"；没指定时视为整段 TTD 都花在开枪前，旧样本含义不变
    if data["engagement"] is not None:
        data["engagement"].setdefault("seen_to_first_shot_s", data["engagement"].get("time_to_damage_s"))
    if isinstance(overrides.get("engagement"), dict):
        changes = overrides["engagement"]
        if "shots" not in changes and ("first_shot_stopped" in changes or "first_shot_hit" in changes):
            first = data["engagement"]["shots"][0]
            first = first.model_copy(update={
                "stopped": changes.get("first_shot_stopped", first.stopped),
                "hit": changes.get("first_shot_hit", first.hit),
                "speed_ratio": 0.5 if changes.get("first_shot_stopped", first.stopped) is False else 0.0,
            })
            data["engagement"]["shots"] = [first]
        seen = next((s for s in sorted(data["engagement"]["shots"], key=lambda s: s.tick)
                     if s.seen_opponent is True), None)
        data["engagement"].setdefault("first_seen_shot_stopped", seen.stopped if seen else None)
        data["engagement"].setdefault("first_seen_shot_hit", seen.hit if seen else None)
    return Moment.model_validate(data)


def make_mm(moments=None, **overrides):
    data = dict(demo="synthetic.dem", map_name="de_test", tick_rate=64, player=A, player_name="alice",
                rounds=1, moments=moments or [], missed_trades=[], excluded=[])
    return MatchMoments(**(data | overrides))
