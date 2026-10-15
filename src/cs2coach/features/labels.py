"""标签、瞬间类型、回合结束原因的中文名称（终端输出、给 AI 的摘要共用）。"""

from __future__ import annotations

from cs2coach.parsing.weapons import weapon_family

TAG_LABELS = {
    "opening_kill": "首杀",
    "opening_death": "首死",
    "trade_kill": "补枪",
    "traded": "被补回",
    "not_traded": "未被补",
    "man_advantage": "人数优势",
    "man_disadvantage": "人数劣势",
    "post_plant": "下包后",
    "after_round_end": "回合已结束",
    "teamkill": "误杀队友",
    "victim_is_bot": "击杀 bot",
    "world_damage": "非玩家伤害",
    "no_shots_fired": "没开枪",
    "shot_first": "先开枪",
    "opponent_shot_first": "对手先开枪",
    "shot_same_tick": "同时开枪",
    "died_blind": "被闪致死",
    "opponent_blind": "对手交火时被闪",
    "blind_in_fight": "交火时自己被闪",
    "unused_utility": "道具没用",
    "saw_opponent_first": "先看见对手",
    "opponent_saw_first": "对手先看见",
    "died_without_seeing": "没看见就被打死",
    "weapon_not_ready": "阵亡时武器还不能开枪",
    "peeked_held_angle": "主动拉出（对手架点）",
    "held_angle": "架点（对手拉出）",
    "flash_assist": "闪光助攻",
    "damage_assist": "伤害助攻",
}
KIND_LABELS = {"kill": "击杀", "death": "死亡", "assist": "助攻"}
# m_eRoundWinReason（docs/fields.md §3）
ROUND_END_REASONS = {1: "炸弹爆炸", 7: "炸弹被拆", 8: "T 全灭", 9: "CT 全灭", 12: "时间耗尽"}
GRENADE_LABELS = {"flashbang": "闪光", "smokegrenade": "烟", "hegrenade": "雷", "molotov": "燃烧瓶",
                  "incgrenade": "燃烧弹", "decoy": "诱饵"}
WEAPON_LABELS = {
    "ak47": "AK-47", "m4a1": "M4A4", "m4a1_silencer": "M4A1-S", "awp": "AWP",
    "usp_silencer": "USP-S", "glock": "Glock-18", "deagle": "Desert Eagle", "ssg08": "SSG 08",
    "hkp2000": "P2000", "p250": "P250", "tec9": "Tec-9", "fiveseven": "Five-SeveN",
    "cz75a": "CZ75-Auto", "galilar": "Galil AR", "famas": "FAMAS", "aug": "AUG",
    "sg556": "SG 553", "mac10": "MAC-10", "mp9": "MP9", "mp7": "MP7", "ump45": "UMP-45",
    "p90": "P90", "mag7": "MAG-7", "nova": "Nova", "xm1014": "XM1014",
    "hegrenade": "手雷", "inferno": "燃烧", "knife": "刀",
}


def weapon_label(name: str) -> str:
    """规范武器名转为游戏内显示名；刀皮肤归为刀，未知武器保留原名。"""
    return WEAPON_LABELS.get(weapon_family(name), name)


def tag_label(tag: str) -> str:
    if tag.startswith("clutch_"):
        return "残局" + tag.removeprefix("clutch_")
    return TAG_LABELS.get(tag, tag)
