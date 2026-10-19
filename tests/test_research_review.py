"""R02 研究复核工具：分层抽样、盲审清单、回填与混淆矩阵。"""

import json
import tomllib

import pandas as pd

from cs2coach.config import load_config

from scripts import research_review as rr


def _windows(tmp_path):
    rows = []
    labels = ["hold"] * 10 + ["advance"] * 10 + ["retreat"] * 10 + ["lateral"] * 10 + ["rotate"] * 10 + ["unknown"] * 20
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
    review = load_config()["research"]["review"]
    assert dict(pd.Series(labels).value_counts()) == review["counts"]
    assert len({(i["match_id"], i["round_id"]) for i in items}) == len(items)
    short = [i for i in items if frame.loc[frame.round_id == i["round_id"], "end_tick"].item()
             - i["start_tick"] < 64]
    assert len(short) == review["short_unknown"]  # unknown 中 1 秒内结束的窗口
    checklist = (pack / "checklist.md").read_text(encoding="utf-8")
    for word in ("hold", "advance", "retreat", "lateral", "rotate", "main_label"):
        assert word not in checklist  # 盲审：不显示程序标签
    cfg = (pack / "cs2coach_research.cfg").read_text(encoding="utf-8")
    assert 'alias p1 "playdemo replays/research/' in cfg and f'alias r1 "demo_gototick {items[0]["start_tick"] - 64 * load_config()["research"]["review"]["lead_seconds"]}"' in cfg


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
    assert f"已填 3 / {len(items)} 条" in score and "（2/3）" in score
    assert "| 程序 / 人工 |" in score
    assert score.count("| w") == 1  # 只有一条不一致


def _fake_demo(path, protocol, compressed=False):
    def field_str(n, text):
        data = text.encode()
        return bytes([n << 3 | 2]) + rr._encode_varint(len(data)) + data
    payload = field_str(1, "PBDEMS2") + bytes([2 << 3]) + rr._encode_varint(protocol) + field_str(5, "de_mirage")
    cmd = 1 | (64 if compressed else 0)
    body = rr._encode_varint(cmd) + rr._encode_varint(0) + rr._encode_varint(len(payload)) + payload
    path.write_bytes(b"PBDEMS2" + bytes([0]) + bytes(8) + body + b"REST-OF-DEMO")


def test_patch_protocol_rewrites_only_the_version_field(tmp_path):
    demo = tmp_path / "x.dem"
    _fake_demo(demo, 14141)
    before = demo.read_bytes()
    assert rr.patch_protocol(demo, 14191) == 14141
    after = demo.read_bytes()
    assert len(after) == len(before) and after.endswith(b"REST-OF-DEMO")
    assert sum(a != b for a, b in zip(before, after)) <= 2  # 只改了版本号的两个字节
    assert rr.patch_protocol(demo, 14191) is None  # 已经一致，不再改


def test_patch_protocol_skips_compressed_headers_and_length_changes(tmp_path):
    demo = tmp_path / "c.dem"
    _fake_demo(demo, 14141, compressed=True)
    assert rr.patch_protocol(demo, 14191) is None
    short = tmp_path / "s.dem"
    _fake_demo(short, 100)  # 一个字节的编码，换成两个字节会挪动后面的内容
    assert rr.patch_protocol(short, 14191) is None


def test_game_protocol_from_steam_inf(tmp_path):
    (tmp_path / "steam.inf").write_text("ClientVersion=2000933\nPatchVersion=1.41.9.1\n", encoding="utf-8")
    assert rr.game_protocol(tmp_path) == 14191
    assert rr.game_protocol(tmp_path / "missing") is None


def test_relabel_rewrites_labels_without_demos(tmp_path, monkeypatch):
    from scripts import research_export as export
    from cs2coach.research.models import BehaviourWindow

    source = tmp_path / "data" / "research" / "old"
    source.mkdir(parents=True)
    window = BehaviourWindow(demo_id="a.dem", match_id="a", round_id=1, tick=100, map="de_mirage", alive_ct=5,
                             alive_t=4, player_id="1", player_name="[tag] pro", tick_rate=64, start_tick=100,
                             end_tick=740, jump_command="demo_gototick 0", start_place="Connector",
                             end_place="BombsiteA", start_position=(0.0, 0.0, -170.0), end_position=(300.0, 0.0, -170.0),
                             displacement=357.0, nav_delta=27.0, utility_types=["flashbang"], main_label="lateral")
    export._write_csv(source / "windows.csv", [window], BehaviourWindow)
    (source / "situations.csv").write_text("demo_id\na.dem\n", encoding="utf-8")
    monkeypatch.setattr(export, "ROOT", tmp_path)
    out = export.relabel("old", "new")
    rows = pd.read_csv(out / "windows.csv")
    assert rows.loc[0, "main_label"] == "hold" and rows.loc[0, "player_name"] == "[tag] pro"
    assert json.loads(rows.loc[0, "aux_labels"]) == ["utility"]
    assert "- hold: 1" in (out / "summary.md").read_text(encoding="utf-8")
