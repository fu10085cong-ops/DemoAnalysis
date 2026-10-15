"""中文事实卡、缺失值处理及 CLI 输出；只用合成数据，不调用 AI。"""

import json

import pytest

from cs2coach.features.models import BlindInfo
from cs2coach.report.facts import missed_trade_facts, moment_facts, render_facts_file, render_moment_card
from tests.helpers.synthetic import A, B, C, D, NAMES, sid
from tests.test_moments import CFG, death, find_moments, hurt, moment, scenario

FORBIDDEN = ("失误", "应该", "太慢", "不够", "错误", "问题", "建议")


def facts(m, names=NAMES):
    """每个场景同时检查中立措辞、缺失值及重复句子。"""
    lines = moment_facts(m, names)
    text = render_moment_card(m, names)
    assert not any(word in text for word in FORBIDDEN)
    assert not any(word in text for word in ("None", "nan", "（）"))
    assert len(lines) == len(set(lines))
    return lines


def test_death_card_complete_sentences_and_order():
    d = moment(find_moments(scenario(), A, CFG), "R1-death")
    expected = [
        "你在 Mid，对手 dave 在 Mid，距离 10 米。",
        "你手持 AK-47，对手手持 AK-47。",
        "对手用 AK-47 击杀了你。",
        "按视野计算，你被击杀前没有看见对手。",
        "你没有朝对手开枪。",
        "你在交火中被闪：死亡时还剩 1.8 秒，这次致盲最长 2.0 秒，是对手扔的闪。",
        "你阵亡时身上还有：烟。",
        "本回合你在阵亡前没有投出道具。",
        "你阵亡后没有被补回；当时附近没有存活的队友。",
    ]
    assert facts(d) == expected
    assert render_moment_card(d, NAMES) == (
        "### 〔R1-death〕死亡（T，第 3 秒，1v1）\n\n" + "\n".join("- " + x for x in expected))


def test_kill_card_includes_named_teammate_flash():
    m = scenario(blinds=[{"tick": 80, "user_steamid": sid(C), "attacker_steamid": sid(B), "blind_duration": 1.5}])
    k = moment(find_moments(m, A, CFG), "R1-kill1")
    assert facts(k) == [
        "你在 Mid，对手 carol 在 Mid，距离 10 米。",
        "你手持 AK-47，对手手持 AK-47。",
        "你用 AK-47 击杀了对手。",
        "回合开始时就能看见这名对手。",
        "对手先开枪，领先 0.08 秒。",
        "你开了 3 枪，命中 2 枪；第一枪未命中，开第一枪时未停稳。",
        "对手在交火中被闪：击杀时还剩 1.2 秒，这次致盲最长 1.5 秒，是你的队友 bob 扔的闪。",
    ]


@pytest.mark.parametrize("flash,damage,expected", [
    (True, 0, "你助攻击杀了 dave：闪光助攻。"),
    (False, 80, "你助攻击杀了 dave：你对他造成了 80 点伤害。"),
])
def test_assist_has_no_engagement_sentences(flash, damage, expected):
    m = scenario(kills=[death(100, A, C), death(150, B, D, assister=A, assistedflash=flash), death(200, D, A)],
                 damages=[hurt(100, A, C, 100, 0), hurt(140, A, D, damage, 100 - damage)])
    a = moment(find_moments(m, A, CFG), "R1-assist1")
    lines = facts(a)
    assert expected in lines
    assert not any(word in "\n".join(lines) for word in ("看见", "你开了", "毫秒", "交火中被闪"))


def test_no_map_data_and_missing_contact_values_are_omitted():
    k = moment(find_moments(scenario(), A, CFG), "R1-kill1")
    k.engagement.visibility_note = "no_map_data"
    k.engagement.player_speed_at_contact = 150
    k.engagement.opponent_speed_at_contact = None
    k.engagement.contact_type = None
    lines = facts(k)
    assert "缺少地图数据，没有计算视野。" in lines
    assert not any(x.startswith("接触时") for x in lines)


@pytest.mark.parametrize("subject,field", [("你", "player_blind"), ("对手", "opponent_blind")])
@pytest.mark.parametrize("role,thrower,own_source,opponent_source", [
    ("self", A, "是你自己扔的闪", "是你扔的闪"),
    ("teammate", B, "是队友扔的闪", "是你的队友扔的闪"),
    ("opponent", C, "是对手扔的闪", "是对手自己扔的闪"),
    ("enemy", D, "是对手队友扔的闪", "是对手队友扔的闪"),
    (None, None, "投掷者无法确定", "投掷者无法确定"),
])
def test_flash_roles_unknown_names_and_recovered_state(subject, field, role, thrower, own_source, opponent_source):
    k = moment(find_moments(scenario(), A, CFG), "R1-kill1")
    info = BlindInfo(blinded_at_event=False, remaining_at_event_s=None, max_duration_s=2.0,
                     thrower=thrower, thrower_ambiguous=role is None, thrower_role=role)
    setattr(k.engagement, field, info)
    source = own_source if subject == "你" else opponent_source
    assert f"{subject}在交火中被闪：击杀时已经恢复，这次致盲最长 2.0 秒，{source}。" in facts(k, {})


@pytest.mark.parametrize("lead,seen,shot", [
    (0.25, "你先看见对手，领先 0.25 秒。", "你先开枪，领先 0.25 秒。"),
    (-0.25, "对手先看见你，领先 0.25 秒。", "对手先开枪，领先 0.25 秒。"),
    (0.0, "双方同时看见对方。", "双方同一 tick 开枪。"),
])
def test_visibility_and_shot_leads(lead, seen, shot):
    k = moment(find_moments(scenario(), A, CFG), "R1-kill1")
    k.engagement.seen_first_lead_s = lead
    k.engagement.first_shot_lead_s = lead
    k.engagement.visibility_note = None
    lines = facts(k)
    assert lines[3:5] == [seen, shot]


def test_complete_combat_values_health_gear_flags_and_units():
    k = moment(find_moments(scenario(), A, CFG), "R1-kill1")
    k.context.own_equip_value, k.context.opponent_equip_value = 3000, 4000
    k.context.own_health_before, k.context.opponent_health_before = 80, 100
    k.context.bomb_planted = True
    k.headshot = k.through_smoke = k.wallbang = k.noscope = True
    e = k.engagement
    e.visibility_note = None
    e.player_speed_at_contact, e.opponent_speed_at_contact, e.contact_type = 150, 0, "peek"
    e.time_to_damage_s, e.seen_to_first_shot_s, e.first_shot_to_damage_s = 0.5, 0.2, 0.3
    e.crosshair_placement_deg, e.shots_before_seen, e.opponent_time_to_damage_s = 4.25, 2, 0.25
    lines = facts(k)
    assert "你手持 AK-47，对手手持 AK-47；本回合装备价值 3000 对 4000。" in lines
    assert "交火前血量：你 80，对手 100。" in lines
    assert "你用 AK-47（爆头、穿烟、穿墙、盲狙）击杀了对手。" in lines
    assert "接触时你的速度 150、对手 0 单位/秒：自己移动进入对手视野、对手几乎静止：主动拉出，对手在架点。" in lines
    assert "从看见对手到第一次造成伤害用了 500 毫秒（看见→开枪 200 毫秒，开枪→命中 300 毫秒）。" in lines
    assert "从看见对手到第一次造成伤害，准星转过了 4.2°。" in lines
    assert "看见对手之前，你已朝他开了 2 枪。" in lines
    assert "对手从看见你到第一次造成伤害用了 250 毫秒。" in lines
    assert "### 〔R1-kill1〕击杀（T，第 1 秒，2v2，已下包）" in render_moment_card(k, NAMES)


def test_missing_fields_zero_distance_and_partial_ttd():
    k = moment(find_moments(scenario(), A, CFG), "R1-kill1")
    k.context = k.context.model_copy(update={"place": None, "opponent_place": None, "distance": 0.0,
                                            "own_weapon": None, "opponent_weapon": None,
                                            "own_health_before": float("nan"), "opponent_health_before": None})
    k.engagement.time_to_damage_s = 0
    k.engagement.crosshair_placement_deg = float("nan")
    lines = facts(k)
    assert lines[0] == "距离 0 米。"
    assert "从看见对手到第一次造成伤害用了 0 毫秒。" in lines
    assert not any(word in "\n".join(lines) for word in ("血量", "手持", "角度差"))


def test_death_fallback_flash_does_not_repeat_and_utility_counts():
    d = moment(find_moments(scenario(), A, CFG), "R1-death")
    assert sum("被闪" in x for x in facts(d)) == 1
    d.engagement.player_blind = None
    d.death.blinded_by = None
    d.death.blinded_by_teammate = None
    d.death.unused_grenades = []
    d.death.thrown_grenades = ["flashbang", "smokegrenade", "flashbang"]
    d.death.weapon_ready, d.death.last_shot_weapon = False, "awp"
    d.death.last_shot_gap_s, d.death.min_shot_interval_s = 0.2, 1.45
    lines = facts(d)
    assert "你阵亡时武器还不能开枪：上一枪（AWP）到阵亡只有 0.20 秒，该武器两枪的最短间隔是 1.45 秒。" in lines
    assert "你阵亡时被闪，还剩 1.8 秒，投掷者无法确定。" in lines
    assert sum("被闪" in x for x in lines) == 1
    assert "你阵亡时身上没有道具。" in lines
    assert "本回合你在阵亡前投出了：闪光 ×2、烟。" in lines


def test_trade_outcomes_and_missing_names():
    mm = find_moments(scenario(), A, CFG)
    d = moment(mm, "R1-death")
    d.trade.traded, d.trade.traded_by, d.trade.trade_delay_s = True, B, 0.5
    assert "你阵亡后 0.5 秒，队友 bob 击杀了对手（补回）。" in facts(d)
    assert "你阵亡后 0.5 秒，队友 击杀了对手（补回）。" in facts(d, {})
    d.trade.traded = False
    d.trade.nearest_teammate_distance = 0
    assert "你阵亡后没有被补回；当时最近的存活队友距离你 0 单位。" in facts(d)
    k = moment(mm, "R1-kill1")
    k.trade.is_trade_kill, k.trade.avenge_delay_s = True, 0.5
    assert "这是一次补枪：队友阵亡 0.5 秒后，你击杀了击杀他的对手。" in facts(k)


def test_missed_trade_card_and_complete_file():
    mm = find_moments(scenario(), A, CFG)
    mt = mm.missed_trades[0]
    assert missed_trade_facts(mt, NAMES) == [
        "队友 bob 阵亡时离你 4 单位，你在补枪时间窗口内没有击杀击杀他的对手。"]
    mt.killer_traded_by_other = True
    assert missed_trade_facts(mt, {}) == [
        "队友 阵亡时离你 4 单位，你在补枪时间窗口内没有击杀击杀他的对手；对手随后被其他队友击杀。"]
    text = render_facts_file(mm, NAMES)
    assert text.startswith("# 事实卡：alice · de_test\n\n共 1 回合，2 个瞬间。\n\n")
    assert text.count("### ") == len(mm.moments) + len(mm.missed_trades)
    assert "### 〔R1〕漏补枪\n\n- 队友 bob" in text
    assert not any(word in text for word in FORBIDDEN)
    assert text.endswith("\n")


def test_cli_writes_moments_and_facts_without_ai(tmp_path, monkeypatch):
    from cs2coach.cli import main

    m = scenario()
    mm = find_moments(m, A, CFG)
    monkeypatch.setattr("cs2coach.cli.resolve_demo", lambda path, member=None: path)
    monkeypatch.setattr("cs2coach.parsing.match.parse_match", lambda path, cfg: m)
    monkeypatch.setattr("cs2coach.features.moments.find_moments", lambda match, sid, cfg: mm)
    assert main(["analyze", "test.dem", "--player", str(A), "--out", str(tmp_path)]) == 0
    folder = tmp_path / "test" / f"alice_{A}"
    expected = folder / "facts.md"
    assert expected.read_text(encoding="utf-8") == render_facts_file(mm, NAMES)
    assert json.loads((folder / "moments.json").read_text(encoding="utf-8")) == json.loads(mm.model_dump_json())
    assert {p.name for p in folder.iterdir()} == {"moments.json", "facts.md", "problems.md"}


@pytest.mark.parametrize("subject,field", [("你", "player_blind"), ("对手", "opponent_blind")])
@pytest.mark.parametrize("role,thrower,own_source,opponent_source", [
    ("self", A, "是你自己扔的闪", "是你扔的闪"),
    ("teammate", B, "是队友 bob 扔的闪", "是你的队友 bob 扔的闪"),
    ("opponent", C, "是对手扔的闪", "是对手自己扔的闪"),
    ("enemy", D, "是对手的队友 dave 扔的闪", "是对手的队友 dave 扔的闪"),
])
def test_flash_thrower_wording_follows_blinded_subject(subject, field, role, thrower, own_source, opponent_source):
    k = moment(find_moments(scenario(), A, CFG), "R1-kill1")
    info = BlindInfo(blinded_at_event=True, remaining_at_event_s=0.8, max_duration_s=2.0,
                     thrower=thrower, thrower_ambiguous=False, thrower_role=role)
    setattr(k.engagement, field, info)
    source = own_source if subject == "你" else opponent_source
    assert f"{subject}在交火中被闪：击杀时还剩 0.8 秒，这次致盲最长 2.0 秒，{source}。" in facts(k)


@pytest.mark.parametrize("thrower,teammate,source", [
    (A, True, "是你自己扔的闪"),
    (B, True, "是队友 bob 扔的闪"),
    (D, False, "是对手扔的闪"),
    (C, False, "是对手的队友 carol 扔的闪"),
    (None, None, "投掷者无法确定"),
])
def test_death_fallback_flash_uses_player_as_subject(thrower, teammate, source):
    d = moment(find_moments(scenario(), A, CFG), "R1-death")
    d.engagement.player_blind = None
    d.death.blinded_by, d.death.blinded_by_teammate = thrower, teammate
    assert f"你阵亡时被闪，还剩 1.8 秒，{source}。" in facts(d)


def test_no_shots_omits_entire_gun_stat_sentence():
    d = moment(find_moments(scenario(), A, CFG), "R1-death")
    d.engagement.first_shot_hit = True
    d.engagement.first_shot_stopped = True
    lines = facts(d)
    assert lines.count("你没有朝对手开枪。") == 1
    assert not any(x.startswith("你开了") or "第一枪" in x for x in lines)


@pytest.mark.parametrize("name,expected", [
    ("ak47", "AK-47"), ("m4a1", "M4A4"), ("m4a1_silencer", "M4A1-S"),
    ("awp", "AWP"), ("usp_silencer", "USP-S"), ("glock", "Glock-18"),
    ("deagle", "Desert Eagle"), ("ssg08", "SSG 08"), ("hkp2000", "P2000"),
    ("hegrenade", "手雷"), ("inferno", "燃烧"), ("knife_karambit", "刀"),
    ("bayonet", "刀"), ("knife_future_skin", "刀"), ("unknown_gun", "unknown_gun"), ("", ""),
])
def test_weapon_label_preserves_game_names_and_handles_knives(name, expected):
    from cs2coach.features.labels import weapon_label

    assert weapon_label(name) == expected


@pytest.mark.parametrize("name,display", [("m4a1", "M4A4"), ("usp_silencer", "USP-S"),
                                          ("knife_karambit", "刀"), ("unknown_gun", "unknown_gun")])
def test_game_weapon_names_in_kill_death_and_readiness_sentences(name, display):
    mm = find_moments(scenario(), A, CFG)
    k, d = moment(mm, "R1-kill1"), moment(mm, "R1-death")
    k.weapon = d.weapon = name
    d.death.weapon_ready, d.death.last_shot_weapon = False, name
    d.death.last_shot_gap_s, d.death.min_shot_interval_s = 0.2, 1.45
    assert f"你用 {display} 击杀了对手。" in facts(k)
    lines = facts(d)
    assert f"对手用 {display} 击杀了你。" in lines
    assert (f"你阵亡时武器还不能开枪：上一枪（{display}）到阵亡只有 0.20 秒，"
            "该武器两枪的最短间隔是 1.45 秒。") in lines
