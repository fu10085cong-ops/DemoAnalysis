"""R02 2D 回放：取窗口数据与回填答案。"""

import json
import tomllib

import pandas as pd
import pytest

from cs2coach.config import load_config
from scripts import research_replay as replay
from scripts import research_review as rr
from tests.helpers.synthetic import NAMES, A, C, make_match, make_ticks
from tests.test_research_review import _windows


def test_extract_window_frames_events_relative_to_trigger():
    cfg = load_config()
    match = make_match(make_ticks(last=500))
    match.kills = pd.DataFrame([{"tick": 300, "attacker": A, "victim": C}, {"tick": 5, "attacker": A, "victim": C}])
    match.shots = pd.DataFrame([{"tick": 310, "steamid": A, "is_gun": True}, {"tick": 311, "steamid": C, "is_gun": True}])
    match.grenades = pd.DataFrame([{"tick": 320, "type": "smokegrenade", "x": 1.0, "y": 2.0}])
    item = {"id": "w1", "start_tick": 300, "player_id": str(A)}
    out = replay.extract(match, [item], cfg)["w1"]
    rate = match.meta.tick_rate
    lead = cfg["research"]["review"]["lead_seconds"]
    assert out["frames"][0][0] == -lead  # 从 T 阵亡前 lead 秒开始
    step = round(rate / cfg["research"]["review"]["replay_fps"]) / rate
    assert out["frames"][1][0] - out["frames"][0][0] == pytest.approx(step, abs=1e-3)
    assert out["kills"] == [[0.0, str(A), str(C)]]  # 范围外的击杀不取
    assert out["shots"] == [round(10 / rate, 3)]  # 只取要看的 CT 的开枪
    assert out["grenades"] == [[round(20 / rate, 3), "smoke", 1.0, 2.0]]
    assert out["target"] == str(A) and str(A) in out["players"]
    first = {p[0]: p for p in out["frames"][0][1]}
    assert len(first) == len(NAMES) and all(len(p) == 6 for p in first.values())


def test_import_writes_answers_back(tmp_path):
    _windows(tmp_path)
    rr.main(["build", "--source", "src", "--name", "t3", "--seed", "3"], root=tmp_path)
    exported = tmp_path / "res.json"
    exported.write_text(json.dumps({"pack": "t3", "results": {"w1": {"result": "前压", "comment": "推中路"},
                                                             "w2": {"result": "看不出"}}}, ensure_ascii=False), encoding="utf-8")
    rr.main(["import", "t3", str(exported)], root=tmp_path)
    items = tomllib.loads((tmp_path / "data/research/review/t3/review.toml").read_text(encoding="utf-8"))["items"]
    assert (items[0]["result"], items[0]["comment"]) == ("前压", "推中路")
    assert items[1]["result"] == "看不出" and items[2]["result"] == ""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"pack": "t3", "results": {"w1": {"result": "冲"}}}, ensure_ascii=False), encoding="utf-8")
    try:
        rr.main(["import", "t3", str(bad)], root=tmp_path)
        assert False, "无效答案应报错"
    except ValueError:
        pass
