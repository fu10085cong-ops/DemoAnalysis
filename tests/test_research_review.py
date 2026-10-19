"""R02 研究复核工具：分层抽样、盲审清单、回填与混淆矩阵。"""

import json
import tomllib

import pandas as pd

from scripts import research_review as rr


def _windows(tmp_path):
    rows = []
    labels = ["hold"] * 10 + ["advance"] * 10 + ["retreat"] * 10 + ["lateral"] * 10 + ["unknown"] * 20
    for i, label in enumerate(labels):
        rows.append({"demo_id": f"m{i % 6}-mirage.dem", "match_id": f"m{i % 6}-mirage", "round_id": i, "tick": 1000 + i,
                     "player_id": str(76561190000000000 + i), "player_name": f"pro{i}", "tick_rate": 64,
                     "start_tick": 1000 + i, "end_tick": 1000 + i + (30 if label == "unknown" and i % 4 == 0 else 320),
                     "round_time_s": 30.0, "start_place": "Jungle", "main_label": label,
                     "displacement": 200.0, "nav_delta": -120.0})
    out = tmp_path / "data" / "research" / "src"
    out.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(out / "windows.csv", index=False)
    manifest = tmp_path / "data" / "demos" / "pro_mirage"
    manifest.mkdir(parents=True)
    (manifest / "manifest.json").write_text(json.dumps({"matches": [
        {"file": f"m{i}-mirage.dem", "event": "IEM Rio 2026" if i < 3 else "CCT 小赛"} for i in range(6)]}), encoding="utf-8")


def test_build_samples_by_label_blind_and_one_window_per_round(tmp_path):
    _windows(tmp_path)
    rr.main(["build", "--source", "src", "--name", "t1", "--seed", "1"], root=tmp_path)
    pack = tmp_path / "data" / "research" / "review" / "t1"
    items = tomllib.loads((pack / "review.toml").read_text(encoding="utf-8"))["items"]
    frame = pd.read_csv(tmp_path / "data" / "research" / "src" / "windows.csv")
    labels = [frame.loc[frame.round_id == item["round_id"], "main_label"].item() for item in items]
    assert sorted(pd.Series(labels).value_counts().items()) == [("advance", 3), ("hold", 3), ("lateral", 3), ("retreat", 3), ("unknown", 8)]
    assert len({(i["match_id"], i["round_id"]) for i in items}) == len(items)
    short = [i for i in items if frame.loc[frame.round_id == i["round_id"], "end_tick"].item()
             - i["start_tick"] < 64]
    assert len(short) == 2  # unknown 中 1 秒内结束的窗口
    checklist = (pack / "checklist.md").read_text(encoding="utf-8")
    for word in ("hold", "advance", "retreat", "lateral", "main_label"):
        assert word not in checklist  # 盲审：不显示程序标签
    cfg = (pack / "cs2coach_research.cfg").read_text(encoding="utf-8")
    assert 'alias p1 "playdemo replays/research/' in cfg and f'alias r1 "demo_gototick {items[0]["start_tick"] - 128}"' in cfg


def test_score_confusion_matrix_and_disagreements(tmp_path):
    _windows(tmp_path)
    rr.main(["build", "--source", "src", "--name", "t2", "--seed", "2"], root=tmp_path)
    pack = tmp_path / "data" / "research" / "review" / "t2"
    text = (pack / "review.toml").read_text(encoding="utf-8")
    frame = pd.read_csv(tmp_path / "data" / "research" / "src" / "windows.csv")
    items = tomllib.loads(text)["items"]
    # 前两条照程序标签填，第三条故意填错，其余不填
    for n, item in enumerate(items[:3]):
        program = frame.loc[frame.round_id == item["round_id"], "main_label"].item()
        human = rr.NAMES[program] if n < 2 else ("前压" if program != "advance" else "后撤")
        text = text.replace(f'id = "{item["id"]}"\n', f'id = "{item["id"]}"\n', 1)
        block_start = text.index(f'id = "{item["id"]}"\n')
        result_at = text.index('result = ""', block_start)
        text = text[:result_at] + f'result = "{human}"' + text[result_at + len('result = ""'):]
    (pack / "review.toml").write_text(text, encoding="utf-8")
    report = rr.main(["score", "t2"], root=tmp_path)
    score = (pack / "score.md").read_text(encoding="utf-8")
    assert report == 0
    assert "已填 3 / 20 条" in score and "（2/3）" in score
    assert "| 程序 / 人工 |" in score
    assert score.count("| w") == 1  # 只有一条不一致
