import numpy as np
import pandas as pd
import pytest

from cs2coach.parsing.events import to_steamid
from cs2coach.parsing.weapons import is_grenade, is_gun, is_knife, normalize_weapon, weapon_family


def test_to_steamid_keeps_full_precision():
    # 76561198113666193 超过 2^53，经过 float 会变成 76561198113666192
    s = to_steamid(pd.Series(["76561198113666193", np.nan, "0", None, " 76561197989744167 "]))
    assert s.dtype == "Int64"
    assert s.iloc[0] == 76561198113666193
    assert s.iloc[4] == 76561197989744167
    assert s.iloc[1:4].isna().all()


def test_to_steamid_accepts_integers_but_not_floats():
    s = to_steamid(pd.Series([76561198113666193, 0], dtype="int64"))
    assert s.iloc[0] == 76561198113666193 and pd.isna(s.iloc[1])
    # 浮点表示的 SteamID64 已经丢了精度，视为缺失而不是给出错误的值
    assert to_steamid(pd.Series([7.6561198113666193e16])).isna().all()


@pytest.mark.parametrize("raw,expected", [
    ("weapon_ak47", "ak47"), ("ak47", "ak47"), (np.nan, ""), (None, ""),
    # 5E 的 player_death 带平台皮肤前缀（docs/fields.md 陷阱 19）
    ("5e_tyloo2025_ak47", "ak47"), ("5ExTEAMSPIRIT_usp_silencer", "usp_silencer"),
    ("5e_fazesr2026004_ak47_ace", "ak47"), ("5e_fazesr2026004_knife_karambit", "knife_karambit"),
    ("5e_2026pass3_hegrenade", "hegrenade"),
    # 完美的后缀
    ("ak47_vip", "ak47"), ("m4a1_silencer_txz08", "m4a1_silencer"), ("m4a1_txz08", "m4a1"), ("awp_ace_vip", "awp"),
    ("weapon_knife_butterfly", "knife_butterfly"), ("knife_some_future_skin", "knife"),
    ("inferno", "inferno"), ("planted_c4", "planted_c4"), ("world", "world"),
    ("something_unknown", "something_unknown"),  # 认不出时原样返回，由核对脚本报告
])
def test_normalize_weapon(raw, expected):
    assert normalize_weapon(raw) == expected


def test_weapon_family():
    assert weapon_family("knife_karambit") == weapon_family("bayonet") == "knife"
    assert weapon_family("molotov") == weapon_family("incgrenade") == "inferno"
    assert weapon_family("ak47") == "ak47"
    assert is_knife("knife_t") and not is_knife("ak47")


@pytest.mark.parametrize("weapon", ["ak47", "m4a1_silencer", "awp", "deagle", "glock", "usp_silencer", "mp9", "nova"])
def test_guns(weapon):
    assert is_gun(weapon)
    assert not is_grenade(weapon)


@pytest.mark.parametrize("weapon", ["knife", "knife_butterfly", "bayonet", "knife_m9_bayonet", "c4", "taser", "world", ""])
def test_non_guns(weapon):
    assert not is_gun(weapon)


@pytest.mark.parametrize("weapon", ["hegrenade", "flashbang", "smokegrenade", "molotov", "incgrenade", "decoy"])
def test_grenades(weapon):
    assert is_grenade(weapon)
    assert not is_gun(weapon)
