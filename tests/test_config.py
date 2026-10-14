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


def test_missing_and_malformed_config(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[parsing\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_config(bad)
