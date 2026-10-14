"""武器名规范化。

事件里的武器名并不统一（docs/fields.md 陷阱 19）：
- weapon_fire 带 "weapon_" 前缀：weapon_ak47
- 5E 的 player_death 带平台皮肤前缀：5e_tyloo2025_ak47、5ExTEAMSPIRIT_awp、5e_fazesr2026004_knife_karambit
- 完美的 player_death 带后缀：ak47_vip、m4a1_silencer_txz08、awp_ace_vip
- player_hurt 的写法是干净的

做法：按 "_" 切开，找出其中最长的已知武器名。已知名单取自 24 个样本 demo 中 player_hurt / weapon_fire 的
实际取值；少数样本中没出现过的枪按 CS2 的武器类名补全（标注在下面）。
"""

from __future__ import annotations


GUNS = {
    # 样本中出现过的
    "ak47", "aug", "awp", "bizon", "deagle", "elite", "famas", "fiveseven", "galilar", "glock", "hkp2000",
    "m4a1", "m4a1_silencer", "mac10", "mag7", "mp7", "mp9", "negev", "nova", "p250", "p90", "revolver",
    "ssg08", "tec9", "ump45", "usp_silencer", "xm1014",
    # 样本中没出现过，按 CS2 武器类名补全
    "cz75a", "g3sg1", "m249", "mp5sd", "sawedoff", "scar20", "sg556",
}
GRENADES = {"hegrenade", "flashbang", "smokegrenade", "molotov", "incgrenade", "decoy"}
# 刀的具体型号只影响外观；统一归为 knife 家族判断
KNIVES = {
    "knife", "knife_t", "bayonet", "knife_butterfly", "knife_canis", "knife_cord", "knife_falchion",
    "knife_flip", "knife_gut", "knife_gypsy_jackknife", "knife_karambit", "knife_kukri", "knife_m9_bayonet",
    "knife_outdoor", "knife_skeleton", "knife_stiletto", "knife_survival_bowie", "knife_tactical",
    "knife_ursus", "knife_widowmaker",
}
# 只出现在 player_death / player_hurt 里的伤害来源
OTHER = {"taser", "c4", "planted_c4", "inferno", "world"}

KNOWN = GUNS | GRENADES | KNIVES | OTHER


def normalize_weapon(name: object) -> str:
    """任意来源的武器名 → 规范名。认不出时原样返回（去掉 weapon_ 前缀、转小写），由核对脚本报告。"""
    if not isinstance(name, str) or not name:
        return ""
    raw = name.lower().removeprefix("weapon_")
    if raw in KNOWN:
        return raw
    tokens = raw.split("_")
    best = ""
    for i in range(len(tokens)):
        for j in range(len(tokens), i, -1):
            cand = "_".join(tokens[i:j])
            if cand in KNOWN and len(cand) > len(best):
                best = cand
                break
    if best:
        return best
    if "knife" in tokens or "bayonet" in tokens:
        return "knife"  # 样本中没见过的刀型号
    return raw


def is_known(weapon: str) -> bool:
    return weapon in KNOWN


def is_knife(weapon: str) -> bool:
    return weapon in KNIVES or weapon.startswith("knife")


def is_gun(weapon: str) -> bool:
    return weapon in GUNS


def is_grenade(weapon: str) -> bool:
    return weapon in GRENADES


def weapon_family(weapon: str) -> str:
    """用于跨事件比对：所有刀算同一种；燃烧弹和燃烧瓶造成的伤害在事件里都记作 inferno。"""
    if is_knife(weapon):
        return "knife"
    if weapon in ("molotov", "incgrenade"):
        return "inferno"
    return weapon


# 共用一个武器栏位的两把枪：基础型号 → (另一把的显示名, 另一把的规范名)。
# 5E 的 player_hurt 把 USP-S 记成 hkp2000、M4A1-S 记成 m4a1（陷阱 20），要按攻击者手持的武器纠正
_SLOT_SIBLINGS = {
    "hkp2000": ("USP-S", "usp_silencer"),
    "m4a1": ("M4A1-S", "m4a1_silencer"),
    # 以下三组在样本的 player_hurt 中没出现过错记，按同样的栏位关系补全
    "p250": ("CZ75-Auto", "cz75a"),
    "deagle": ("R8 Revolver", "revolver"),
    "mp7": ("MP5-SD", "mp5sd"),
}


def fix_slot_sibling(weapon: str, active_weapon_name: object) -> str:
    """事件武器名是基础型号、而攻击者当时手持的是同栏位的另一把时，改成手持的那把。"""
    sibling = _SLOT_SIBLINGS.get(weapon)
    if sibling and active_weapon_name == sibling[0]:
        return sibling[1]
    return weapon


# inventory 字段里道具的显示名 → 规范名（样本中出现过的写法）
INVENTORY_GRENADES = {
    "Flashbang": "flashbang",
    "Smoke Grenade": "smokegrenade",
    "High Explosive Grenade": "hegrenade",
    "Molotov": "molotov",
    "Incendiary Grenade": "incgrenade",
    "Decoy Grenade": "decoy",  # 样本中没出现过，按游戏内显示名补全
}
