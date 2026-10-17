import pytest

from cs2coach.config import ConfigError, load_config


def test_default_config_loads():
    cfg = load_config()
    assert 64 in cfg["parsing"]["supported_tick_rates"]
    assert 0 < cfg["parsing"]["min_target_coverage"] <= 1


def test_user_config_overrides_single_key(tmp_path):
    p = tmp_path / "my.toml"
    p.write_text("[parsing]\nmin_target_coverage = 0.5\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["parsing"]["min_target_coverage"] == 0.5
    assert cfg["parsing"]["supported_tick_rates"] == load_config()["parsing"]["supported_tick_rates"]


def test_unknown_key_is_rejected(tmp_path):
    p = tmp_path / "my.toml"
    p.write_text("[parsing]\nmin_target_coverge = 0.5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="parsing.min_target_coverge"):
        load_config(p)


def test_grenade_prices_are_validated(tmp_path):
    path = tmp_path / "prices.toml"
    path.write_text("[economy.grenade_prices]\nflashbang = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="economy.grenade_prices"):
        load_config(path)
    path.write_text("[economy.grenade_prices]\nflashbang = 250\n", encoding="utf-8")
    assert load_config(path)["economy"]["grenade_prices"]["flashbang"] == 250


@pytest.mark.parametrize("value", ["0", "1.5"])
def test_reload_min_ammo_left_requires_positive_integer(tmp_path, value):
    path = tmp_path / "reload.toml"
    path.write_text(f"[problems]\nreload_min_ammo_left = {value}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="problems.reload_min_ammo_left"):
        load_config(path)


def test_extra_pro_steamids_override_requires_integer_list(tmp_path):
    path = tmp_path / "pro.toml"
    path.write_text("[training]\nextra_pro_steamids = [76561198113666193]\n", encoding="utf-8")
    assert load_config(path)["training"]["extra_pro_steamids"] == [76561198113666193]
    path.write_text('[training]\nextra_pro_steamids = ["76561198113666193"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="training.extra_pro_steamids"):
        load_config(path)


def test_missing_and_malformed_config(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[parsing\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_config(bad)


def test_env_file_does_not_override_existing(tmp_path, monkeypatch):
    from cs2coach.env import load_env_file

    env = tmp_path / ".env"
    env.write_text('# 注释\nDEEPSEEK_API_KEY="sk-test"\nexport OTHER_KEY=abc\nEXISTING=new\nnot a pair\n', encoding="utf-8")
    import os

    fake_environ = {"EXISTING": "old"}  # 不碰真实的环境变量
    monkeypatch.setattr(os, "environ", fake_environ)
    assert load_env_file(env) == ["DEEPSEEK_API_KEY", "OTHER_KEY"]
    assert fake_environ == {"EXISTING": "old", "DEEPSEEK_API_KEY": "sk-test", "OTHER_KEY": "abc"}
    assert load_env_file(tmp_path / "missing") == []
