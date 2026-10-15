import numpy as np
import pandas as pd
import pytest

from cs2coach.parsing.match import (
    DemoParseError,
    assign_rounds,
    build_bomb,
    build_damages,
    build_kills,
    build_shots,
    estimate_tick_rate,
    horizontal_speed,
    live_mask,
    build_blinds,
    mark_absent_rounds,
    reconstruct_blinds,
    roster_names,
    spotted_masks,
)
from tests.helpers.synthetic import A, B, C, D, make_match, make_round, make_ticks, sid

# ---------- 逐 tick 数据 ----------


def test_horizontal_speed_from_position_diff():
    df = pd.DataFrame({"steamid": [1, 1, 1, 1, 2, 2], "tick": [10, 11, 12, 14, 11, 12],
                       "X": [0.0, 3.0, 3.0, 5.0, 100.0, 100.0], "Y": [0.0, 4.0, 4.0, 5.0, 0.0, 1.0]})
    sp = horizontal_speed(df, 64)
    assert np.isnan(sp[0])  # 第一行没有上一 tick
    assert sp[1] == pytest.approx(5 * 64)
    assert sp[2] == 0
    assert np.isnan(sp[3])  # tick 不连续（12 → 14）
    assert np.isnan(sp[4])  # 换了玩家
    assert sp[5] == pytest.approx(64)


def test_spotted_masks():
    bits = {111: 0, 222: 1, 333: 2}
    col = pd.Series([[], [222], [111, 333], None, [999]])
    assert spotted_masks(col, bits).tolist() == [0, 0b010, 0b101, 0, 0]


def test_estimate_tick_rate():
    for rate in (64, 128):
        t = np.arange(1, 200)
        df = pd.DataFrame({"tick": t, "game_time": t / rate})
        assert estimate_tick_rate(df) == rate
    assert estimate_tick_rate(pd.DataFrame({"tick": [1], "game_time": [0.0]})) == 0


def test_live_mask_and_assign_rounds():
    rounds = [make_round(1, start=1, freeze_end=10, end=50, last=59), make_round(2, start=60, freeze_end=70, end=90, last=99)]
    t = np.array([0, 5, 10, 50, 55, 60, 70, 99, 120])
    assert live_mask(t, rounds).tolist() == [False, False, True, True, False, False, True, False, False]
    assert assign_rounds(pd.Series(t), rounds).tolist() == [0, 1, 1, 1, 1, 2, 2, 2, 0]


def test_player_lookups():
    ticks = make_ticks(overrides={(A, 50): {"X": 7.0}})
    m = make_match(ticks)
    assert m.state_at(A, 50)["X"] == 7.0
    assert m.state_at(A, 9999) is None
    w = m.player_ticks(B, 20, 29)
    assert w["tick"].tolist() == list(range(20, 30)) and (w["steamid"] == B).all()
    assert len(m.player_ticks(A)) == 120
    assert m.round_of(50).number == 1 and m.round_of(500) is None
    assert m.name_of(A) == "alice" and m.name_of(123) == "123"


def test_coverage_and_require_player():
    # 回合进行中（tick 10–100）C 有一半 tick 没有坐标
    ticks = make_ticks(overrides={(C, t): {"X": np.nan} for t in range(10, 56)})
    m = make_match(ticks)
    assert m.players[A].coverage == 1.0
    assert m.players[C].coverage == pytest.approx(45 / 91)  # 回合进行中 91 个 tick，其中 10–55 缺坐标
    assert any("carol" in w for w in m.warnings)
    assert m.require_player(A, 0.95).name == "alice"
    with pytest.raises(DemoParseError, match="修复版"):
        m.require_player(C, 0.95)
    with pytest.raises(DemoParseError, match="alice"):  # 找不到时列出比赛中的玩家
        m.require_player(42, 0.95)


# ---------- 击杀 ----------


def death(tick, attacker, victim, assister=None, weapon="ak47", **flags):
    return {"tick": tick, "attacker_steamid": sid(attacker), "user_steamid": sid(victim),
            "assister_steamid": sid(assister), "weapon": weapon, "headshot": flags.get("headshot", False),
            "thrusmoke": False, "attackerblind": False, "noscope": False,
            "assistedflash": flags.get("assistedflash", False), "penetrated": 0, "hitgroup": "head", "distance": 10.0}


def test_build_kills_annotates_teams_and_flags():
    ticks = make_ticks(overrides={(C, 49): {"active_weapon_name": "M4A1-S", "health": 40}, (C, 50): {"X": 11.0}})
    m = make_match(ticks)
    ev = pd.DataFrame([
        death(50, A, C, assister=B, headshot=True),  # 普通击杀，队友助攻
        death(60, None, D),                           # 摔死 / 炸死：没有 attacker
        death(70, B, A),                              # 队友误杀
        death(80, D, D, weapon="hegrenade"),          # 自杀
        death(110, C, B),                             # 回合结束后的击杀
        death(500, C, A),                             # 不属于任何回合，丢弃
    ])
    k = build_kills(ev, m)
    assert k["tick"].tolist() == [50, 60, 70, 80, 110]
    r = k.iloc[0]
    assert (r.attacker, r.victim, r.assister) == (A, C, B)
    assert r.attacker_team_num == 2 and r.victim_team_num == 3 and r.victim_X == 11.0
    assert r.victim_prev_active_weapon_name == "M4A1-S" and r.victim_prev_health == 40  # 取死亡前一 tick
    assert r.headshot and not r.is_suicide and not r.is_teamkill and r.assist_counts and r["round"] == 1
    assert k.iloc[1].is_world and not k.iloc[1].is_suicide and not k.iloc[1].is_teamkill
    assert k.iloc[2].is_teamkill and not k.iloc[2].is_suicide and not k.iloc[2].is_world
    assert k.iloc[3].is_suicide and not k.iloc[3].is_world and not k.iloc[3].is_teamkill
    assert k["is_enemy_kill"].tolist() == [True, False, False, False, True]
    assert k["after_round_end"].tolist() == [False, False, False, False, True]


def test_assist_counts_follows_scoreboard_rule():
    # 记分板规则（ESL 三张图逐条核对）：助攻者与受害者不同阵营才算
    m = make_match()
    ev = pd.DataFrame([
        death(50, A, C, assister=D),  # D 是受害者 C 的队友（打过自己人）→ 不算
        death(60, B, A, assister=C),  # 队友误杀，但助攻者 C 是受害者的敌人 → 算
        death(70, A, D, assister=None),
    ])
    assert build_kills(ev, m)["assist_counts"].tolist() == [False, True, False]


def test_suicide_in_freeze_time_is_flagged():
    # 完美样本：冻结期内用控制台自杀，记分板不计这次死亡
    m = make_match(rounds=[make_round(freeze_end=30)])
    k = build_kills(pd.DataFrame([death(20, A, A, weapon="world"), death(50, C, A)]), m)
    assert k["in_freeze_time"].tolist() == [True, False]
    assert k.iloc[0].is_suicide


def test_kills_involving_bots():
    # 5E 样本：接管 bot 时的击杀记在玩家名下（记分板却记给 bot）；被击杀的 bot 在事件里没有 steamid
    ticks = make_ticks(overrides={(A, t): {"controlling_bot": True, "is_alive": False} for t in range(40, 61)})
    m = make_match(ticks)
    ev = pd.DataFrame([death(50, A, C), death(70, C, None), death(80, None, None)])
    k = build_kills(ev, m)
    assert k["tick"].tolist() == [50, 70]  # 击杀者和受害者都未知的记录没有意义，丢弃
    assert k.iloc[0].by_bot_control and k.iloc[0].is_enemy_kill
    assert k.iloc[1].victim_unknown and not k.iloc[1].is_enemy_kill and not k.iloc[1].is_teamkill
    assert not k.iloc[1].by_bot_control


def test_build_kills_empty():
    k = build_kills(pd.DataFrame(), make_match())
    assert k.empty and "round" in k.columns


# ---------- 伤害、开枪、致盲、炸弹 ----------


def test_build_damages():
    ev = pd.DataFrame([{"tick": 40, "attacker_steamid": sid(A), "user_steamid": sid(C), "weapon": "hegrenade",
                        "dmg_health": 30, "dmg_armor": 5, "health": 70, "hitgroup": "generic"}])
    d = build_damages(ev, make_match())
    assert d.iloc[0].attacker == A and d.iloc[0].victim_team_num == 3 and d.iloc[0].attacker_team_num == 2
    assert d.iloc[0].dmg_health == 30 and d.iloc[0]["round"] == 1


def test_build_shots_merges_fire_bullets_for_guns_only():
    wf = pd.DataFrame([
        {"tick": 30, "user_steamid": sid(A), "weapon": "weapon_ak47"},
        {"tick": 31, "user_steamid": sid(A), "weapon": "weapon_knife_butterfly"},
        {"tick": 32, "user_steamid": sid(B), "weapon": "weapon_flashbang"},
        {"tick": 33, "user_steamid": sid(C), "weapon": "weapon_m4a1"},
    ])
    fb = pd.DataFrame([
        {"tick": 30, "user_steamid": sid(A), "angles_x": 1.5, "angles_y": 90.0, "inaccuracy": 0.01, "spread": 0.002, "recoil_index": 0.0},
        {"tick": 33, "user_steamid": sid(C), "angles_x": -2.0, "angles_y": 10.0, "inaccuracy": 0.2, "spread": 0.002, "recoil_index": 3.0},
    ])
    s = build_shots(wf, fb, make_match(make_ticks(overrides={(C, 33): {"controlling_bot": True}})))
    assert s["weapon"].tolist() == ["ak47", "knife_butterfly", "flashbang", "m4a1"]
    assert s["is_gun"].tolist() == [True, False, False, True]
    assert s["is_grenade"].tolist() == [False, False, True, False]
    assert s.iloc[0].aim_pitch == 1.5 and s.iloc[3].recoil_index == 3.0
    assert np.isnan(s.iloc[1].aim_pitch) and np.isnan(s.iloc[2].aim_pitch)
    assert s["controlling_bot"].tolist() == [False, False, False, True]


def test_reconstruct_blinds():
    o = {}
    # tick 40 的闪：C 被闪 2.5s（敌人），B 被闪 0.4s（队友）；D 之前已经被闪且时长没变 → 不算这颗闪
    for s_, d in ((C, 2.5), (B, 0.4)):
        o[(s_, 40)] = {"flash_duration": d}
    o[(D, 39)] = {"flash_duration": 1.0}
    o[(D, 40)] = {"flash_duration": 1.0}
    m = make_match(make_ticks(overrides=o))
    det = pd.DataFrame([{"tick": 40, "user_steamid": sid(A)}])
    b = reconstruct_blinds(det, m).sort_values("victim")
    assert b["victim"].tolist() == [B, C]
    assert b["thrower"].tolist() == [A, A]
    assert b["duration"].tolist() == pytest.approx([0.4, 2.5])
    assert (b["victim_team_num"] == b["thrower_team_num"]).tolist() == [True, False]
    assert not b["thrower_ambiguous"].any()


def test_reconstruct_blinds_two_flashes_same_tick():
    # 5E、完美样本中出现过：两颗闪在同一 tick 爆开。flash_duration 分不出是哪颗闪的，
    # 按距离判断也不可靠，所以投掷者留空并标记为不确定
    o = {(C, 40): {"flash_duration": 2.0, "X": 100.0}, (B, 40): {"flash_duration": 0.8, "X": 900.0}}
    m = make_match(make_ticks(overrides=o))
    det = pd.DataFrame([{"tick": 40, "user_steamid": sid(A), "x": 150.0, "y": 0.0, "z": 0.0},
                        {"tick": 40, "user_steamid": sid(D), "x": 1000.0, "y": 0.0, "z": 0.0}])
    b = reconstruct_blinds(det, m).sort_values("victim")
    assert b["victim"].tolist() == [B, C]  # 每个受害者只记一次
    assert b["thrower"].isna().all()
    assert b["thrower_ambiguous"].all()


def test_build_blinds_prefers_player_blind():
    m = make_match()
    pb = pd.DataFrame([  # 同一 tick 两颗闪同时致盲 C：player_blind 各记一条
        {"tick": 40, "user_steamid": sid(C), "attacker_steamid": sid(A), "blind_duration": 2.0},
        {"tick": 40, "user_steamid": sid(C), "attacker_steamid": sid(B), "blind_duration": 2.1},
        {"tick": 500, "user_steamid": sid(C), "attacker_steamid": sid(A), "blind_duration": 1.0},  # 不在回合内
    ])
    b = build_blinds(pb, pd.DataFrame(), m)
    assert b["thrower"].tolist() == [A, B] and b["victim"].tolist() == [C, C]
    assert b["duration"].tolist() == [2.0, 2.1]
    assert not b["thrower_ambiguous"].any()
    assert (b["victim_team_num"] != b["thrower_team_num"]).all()
    # 没有 player_blind（ESL）时退回重建
    o = {(C, 40): {"flash_duration": 2.5}}
    rec = build_blinds(pd.DataFrame(), pd.DataFrame([{"tick": 40, "user_steamid": sid(A)}]), make_match(make_ticks(overrides=o)))
    assert rec["victim"].tolist() == [C] and rec["thrower"].tolist() == [A]


def test_bots_are_not_real_steamids():
    from cs2coach.parsing.match import is_real_steamid

    assert is_real_steamid(76561198113666193)
    assert not is_real_steamid(0) and not is_real_steamid(11)


def test_build_bomb():
    events = {
        "bomb_planted": pd.DataFrame([{"tick": 60, "user_steamid": sid(A)}]),
        "bomb_exploded": pd.DataFrame([{"tick": 100, "user_steamid": sid(A)}]),
    }
    b = build_bomb(events, make_match())
    assert b["event"].tolist() == ["planted", "exploded"] and b["round"].tolist() == [1, 1]
    assert build_bomb({}, make_match()).empty


def test_player_ticks_bool_columns_survive_disconnected_rows():
    # 玩家掉线期间这一行所有字段为空（5E、完美样本）：布尔列不能因此变成 object
    from cs2coach.parsing.match import PLAYER_PROPS, _player_ticks

    rows = []
    for t, alive in ((1, True), (2, None)):
        row = {p: None for p in PLAYER_PROPS}
        row.update({"tick": t, "steamid": A, "X": 0.0 if alive else np.nan, "Y": 0.0 if alive else np.nan})
        if alive:
            row.update({"is_alive": True, "is_airborne": False, "is_walking": True, "is_scoped": False,
                        "CCSPlayerController.m_bControllingBot": False})
        rows.append(row)
    df = _player_ticks(pd.DataFrame(rows), 64)
    for c in ("is_alive", "is_airborne", "is_walking", "is_scoped", "controlling_bot"):
        assert df[c].dtype == bool, c
    assert df["is_walking"].tolist() == [True, False]
    assert np.isnan(df["X"].iloc[1])


def test_roster_names_from_player_info():
    info = pd.DataFrame({"steamid": [A, B, 0, 76561190000000000 + 5], "name": [" a", "b", "bot", "spec"],
                         "team_number": [2, 3, 2, 1]})
    assert roster_names(info, pd.DataFrame()) == {A: "a", B: "b"}


def test_roster_names_fall_back_to_ticks_when_player_info_is_empty():
    # 中途截断的 demo（服务器重启后拆成两段）parse_player_info 为空，从逐 tick 数据取名字和队伍
    info = pd.DataFrame(columns=["steamid", "name", "team_number"])
    raw = pd.DataFrame({"tick": [1, 1, 2, 2, 2], "steamid": [A, C, A, C, 0],
                        "name": ["old", "c", "new", "c", "bot"], "team_num": [2, 1, 2, 3, 3]})
    assert roster_names(info, raw) == {A: "new", C: "c"}


def _two_rounds():
    return [make_round(1, 1, 10, 100, 120), make_round(2, 121, 130, 220, 240)]


def _absent_in_round1(sid):
    # sid 第 1 回合进行中（tick 10–100）没有队伍、没有坐标：掉线、重连前
    return {(sid, t): {"team_num": np.nan, "X": np.nan, "health": np.nan, "is_alive": False} for t in range(10, 101)}


def test_absent_round_is_excluded_from_coverage():
    m = make_match(make_ticks(last=240, overrides=_absent_in_round1(C)), rounds=_two_rounds())
    assert m.players[C].coverage < 0.95 and any(f"（{C}）数据不完整" in w for w in m.warnings)
    mark_absent_rounds(m, 0.5, 0.5, 0.95)
    assert m.players[C].absent_rounds == [1] and m.players[C].coverage == 1.0
    assert not any(f"（{C}）数据不完整" in w for w in m.warnings)
    assert m.players[A].absent_rounds == [] and m.players[A].coverage == 1.0


def test_round_with_events_is_not_absent():
    # 有开枪事件说明玩家其实在打，只是没读到数据（陷阱 12），不能当成掉线
    m = make_match(make_ticks(last=240, overrides=_absent_in_round1(C)), rounds=_two_rounds())
    m.shots = pd.DataFrame({"steamid": pd.array([C], dtype="Int64"), "round": [1]})
    mark_absent_rounds(m, 0.5, 0.5, 0.95)
    assert m.players[C].absent_rounds == [] and m.players[C].coverage < 0.95


def test_too_many_absent_rounds_are_not_excluded():
    m = make_match(make_ticks(overrides=_absent_in_round1(C)))  # 只有 1 回合，全部不在场
    mark_absent_rounds(m, 0.5, 0.5, 0.95)
    assert m.players[C].absent_rounds == [] and m.players[C].coverage < 0.95
