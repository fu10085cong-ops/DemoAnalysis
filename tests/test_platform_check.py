"""平台数据对照的逐项比较与真实样本回归。"""

import tomllib
from pathlib import Path

import pytest

from cs2coach.features.models import LobbyLine
from scripts.platform_check import PLATFORM_DIR, ROOT, check_file, compare, main
from tests.helpers.synthetic import A, B, C


def _line(sid, opening, accuracy):
    return LobbyLine(steamid=sid, name="虚构玩家", team=2, is_target=False,
                     kills=0, deaths=0, accuracy_pct=accuracy, aim=None,
                     opening_kills=opening, opening_deaths=0, trade_kills=0,
                     flash_assists=0, utility_damage=0)


def _data():
    return {
        "compare": [
            {"platform_field": "opening", "ours": "opening_kills", "kind": "exact"},
            {"platform_field": "accuracy", "ours": "accuracy_pct", "kind": "tolerance", "tolerance": 1.0},
            {"platform_field": "rank", "ours": "accuracy_pct", "kind": "info"},
        ],
        "player": [
            {"steamid": A, "opening": 1, "accuracy": 10.0, "rank": 1},
            {"steamid": B, "opening": 2, "accuracy": 20.0, "rank": 2},
            {"steamid": C, "opening": 3, "accuracy": 30.0, "rank": 3},
        ],
    }


def test_compare_exact_tolerance_share_and_info():
    data = _data()
    lines = [_line(A, 1, 10.5), _line(B, 2, 20.5), _line(C, 0, 33.0)]
    rows, ok = compare(data, lines)
    assert not ok
    assert any("opening | 2/3 | 失败" in row for row in rows)
    assert any("accuracy | 2/3 | 失败" in row for row in rows)
    assert any("rank | 3/3 | 信息 | Spearman 1.000" in row for row in rows)
    assert any(str(C)[-4:] in row and "平台 3" in row for row in rows)
    data["compare"][0]["min_match_share"] = 2 / 3
    data["compare"][1]["min_match_share"] = 2 / 3
    assert compare(data, lines)[1]
    data["compare"][2]["ours"] = "opening_kills"  # 信息类相关变差，也不影响退出码
    assert compare(data, lines)[1]


def test_compare_lists_missing_players_and_rejects_unknown_field():
    data = _data()
    lines = [_line(A, 1, 10), _line(B, 2, 20), _line(76561198000000004, 0, 40)]
    rows, ok = compare(data, lines)
    assert not ok
    assert any("平台有、同场缺失" in row and str(C)[-4:] in row for row in rows)
    assert any("同场有、平台缺失" in row and "0004" in row for row in rows)
    data["compare"][0]["ours"] = "aim.missing"
    with pytest.raises(ValueError, match="未知枪法字段"):
        compare(data, lines)


def test_main_exit_code(monkeypatch, tmp_path, capsys):
    path = tmp_path / "one.toml"
    monkeypatch.setattr("scripts.platform_check.check_file", lambda path: (["| 检查 |"], True))
    assert main([str(path)]) == 0
    assert "| 检查 |" in capsys.readouterr().out
    monkeypatch.setattr("scripts.platform_check.check_file", lambda path: (["| 失败 |"], False))
    assert main([str(path)]) == 1


_FILES = sorted(PLATFORM_DIR.glob("*.toml"))


@pytest.mark.parametrize("path", _FILES or [None], ids=lambda path: path.name if path else "无平台文件")
def test_real_platform_data(path):
    if path is None:
        pytest.skip("没有平台数据文件")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    demo = Path(data["demo"])
    if not demo.is_absolute():
        demo = ROOT / demo
    if not demo.is_file():
        pytest.skip("对应 demo 不存在")
    rows, ok = check_file(path)
    assert ok, "\n".join(rows)
