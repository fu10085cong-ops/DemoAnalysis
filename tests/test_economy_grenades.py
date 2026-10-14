"""购买结束时间、经济快照、死亡时背包、投掷物、武器栏位纠正。"""

import pandas as pd
import pytest

from cs2coach.parsing.match import (
    add_death_inventory,
    assign_buy_end,
    build_damages,
    build_economy,
    build_grenades,
    build_kills,
    buytime_at,
    compute_buy_end,
    grenades_in,
)
from cs2coach.parsing.weapons import fix_slot_sibling
from tests.helpers.synthetic import A, B, C, D, NAMES, TICK_RATE, make_match, make_round, make_ticks, sid

# ---------- 购买结束 ----------


def cvars(*rows):
    return pd.DataFrame(rows, columns=["tick", "name", "value"])


def test_buytime_at_uses_value_in_effect():
    # 5E：录制开始时（tick -1）是热身的 600，比赛开始时改成 20
    c = cvars((-1, "mp_buytime", "600"), (-1, "mp_buy_anywhere", "1"), (-1, "mp_buytime", "600"), (1779, "mp_buytime", "20"))
    assert buytime_at(c, 100, 20) == 600
    assert buytime_at(c, 1779, 20) == 20
    assert buytime_at(c, 5000, 20) == 20
    assert buytime_at(pd.DataFrame(), 5000, 15) == 15  # 没有 cvar 时用配置的默认值


def test_compute_buy_end_matches_observed_offset():
    # 样本中 buytime_ended 恰好在冻结期结束后 20.02 秒（1281 tick）
    assert compute_buy_end(1000, 20, 64) == 1000 + 1281


def test_assign_buy_end_prefers_event_and_falls_back():
    rounds = [make_round(1, start=1, freeze_end=100, end=5000, last=5100),
              make_round(2, start=5101, freeze_end=5200, end=9000, last=9100),
              make_round(3, start=9101, freeze_end=9200, end=9500, last=9600),  # 购买时间内回合就结束了
              make_round(4, start=9601, freeze_end=None, end=9900, last=9999)]
    c = cvars((-1, "mp_buytime", "20"))
    out = assign_buy_end(rounds, [1390], c, TICK_RATE, 20)
    assert out[0].buy_end_tick == 1390  # 有事件用事件
    assert out[1].buy_end_tick == 5200 + 1281  # 没有事件按 mp_buytime 推算
    assert out[2].buy_end_tick == 9500  # 不晚于回合结束
    assert out[3].buy_end_tick is None


# ---------- 背包与经济 ----------


def test_grenades_in_inventory_keeps_duplicates():
    inv = ["knife_t", "Glock-18", "Flashbang", "Flashbang", "Smoke Grenade", "C4 Explosive", "Molotov"]
    assert grenades_in(inv) == ["flashbang", "flashbang", "smokegrenade", "molotov"]
    assert grenades_in([]) == [] and grenades_in(None) == []


def snapshot(tick, inventories):
    rows = []
    for s in NAMES:
        rows.append({"steamid": s, "tick": tick, "inventory": inventories.get(s, ["knife"]), "balance": 800,
                     "start_balance": 4000, "cash_spent_this_round": 3200, "current_equip_value": 4100,
                     "round_start_equip_value": 200, "has_helmet": True, "has_defuser": s == C})
    return pd.DataFrame(rows)


def test_build_economy_one_row_per_player_per_round():
    rounds = assign_buy_end([make_round(freeze_end=10, end=100)], [50], pd.DataFrame(), TICK_RATE, 20)
    m = make_match(rounds=rounds)
    e = build_economy(snapshot(50, {A: ["knife_t", "AK-47", "Smoke Grenade"]}), m)
    assert len(e) == len(NAMES) and set(e["round"]) == {1} and set(e["tick"]) == {50}
    a = e[e["steamid"] == A].iloc[0]
    assert a["grenades"] == ["smokegrenade"] and a["balance"] == 800 and a["team_num"] == 2 and a["is_alive"]
    assert e[e["steamid"] == C].iloc[0]["has_defuser"]


def test_death_inventory_taken_one_tick_before_death():
    m = make_match()
    k = build_kills(pd.DataFrame([{"tick": 61, "attacker_steamid": sid(C), "user_steamid": sid(A),
                                   "assister_steamid": sid(None), "weapon": "ak47", "headshot": False,
                                   "thrusmoke": False, "attackerblind": False, "noscope": False, "assistedflash": False,
                                   "penetrated": 0, "hitgroup": "chest", "distance": 5.0}]), m)
    snap = pd.concat([snapshot(60, {A: ["knife_t", "AK-47", "Flashbang", "High Explosive Grenade"]}),
                      snapshot(61, {A: []})])  # 死亡那一 tick 背包已清空（陷阱 6）
    k = add_death_inventory(k, snap, m)
    assert k.iloc[0]["victim_prev_inventory"] == ["knife_t", "AK-47", "Flashbang", "High Explosive Grenade"]
    assert k.iloc[0]["victim_prev_grenades"] == ["flashbang", "hegrenade"]


# ---------- 投掷物 ----------


def det(tick, steamid, entityid, x=0.0):
    return {"tick": tick, "user_steamid": sid(steamid), "entityid": entityid, "x": x, "y": 0.0, "z": 0.0}


def test_build_grenades_pairs_end_events_by_entity():
    events = {
        "smokegrenade_detonate": pd.DataFrame([det(20, A, 7), det(70, B, 7)]),  # entityid 会被复用
        "smokegrenade_expired": pd.DataFrame([det(60, A, 7), det(110, B, 7)]),
        "inferno_startburn": pd.DataFrame([det(30, C, 9)]),
        "inferno_expire": pd.DataFrame([det(45, C, 9)]),
        "hegrenade_detonate": pd.DataFrame([det(40, D, 11)]),
        "flashbang_detonate": pd.DataFrame([det(500, A, 12)]),  # 不在任何回合内，丢弃
    }
    g = build_grenades(events, make_match())
    assert g["type"].tolist() == ["smokegrenade", "inferno", "hegrenade", "smokegrenade"]
    assert g["end_tick"].tolist() == [60, 45, pd.NA, 110]
    assert g["thrower"].tolist() == [A, C, D, B]
    assert g["thrower_team_num"].tolist() == [2, 3, 3, 2]


def test_build_grenades_empty():
    assert build_grenades({}, make_match()).empty


# ---------- 武器栏位 ----------


@pytest.mark.parametrize("weapon,active,expected", [
    ("hkp2000", "USP-S", "usp_silencer"),  # 5E 的 player_hurt 错记（陷阱 20）
    ("hkp2000", "P2000", "hkp2000"),
    ("m4a1", "M4A1-S", "m4a1_silencer"),
    ("m4a1", "M4A4", "m4a1"),
    ("m4a1", None, "m4a1"),
    ("ak47", "USP-S", "ak47"),
])
def test_fix_slot_sibling(weapon, active, expected):
    assert fix_slot_sibling(weapon, active) == expected


def test_build_damages_corrects_slot_sibling():
    ticks = make_ticks(overrides={(A, 40): {"active_weapon_name": "USP-S"}})
    ev = pd.DataFrame([{"tick": 40, "attacker_steamid": sid(A), "user_steamid": sid(C), "weapon": "hkp2000",
                        "dmg_health": 35, "dmg_armor": 0, "health": 65, "hitgroup": "head"}])
    assert build_damages(ev, make_match(ticks))["weapon"].tolist() == ["usp_silencer"]
