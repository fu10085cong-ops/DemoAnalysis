"""AI 与程序分歧裁定包。"""

import json
import sys
import tomllib
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research_review import _write_review  # noqa: E402
from research_stance import adjudicate, ai_adjudicate, load_config  # noqa: E402


def _pack(tmp_path):
    pack = tmp_path / "data" / "research" / "review" / "sample"
    pack.mkdir(parents=True)
    rows = []
    for number in (10, 2, 3, 4):
        rows.append(dict(id=f"g{number}", demo_id=f"demo{number}.dem", match_id=f"m{number}", round_id=1,
                         player_id=str(number), start_tick=100, player_name="选手", start_place="A",
                         round_time_s=20.0, replay_until_s=8.0, focus=[0.0, 3.0], focus_note="观察",
                         archive="stance/raw/archive/demo", jump_command="demo_gototick 100",
                         result="人工旧答案", comment="人工旧理由"))
    _write_review(pack, {"source": "stance/source", "labels": ["激进·主动寻求对枪", "保守", "看不出"]}, rows)
    pd.DataFrame([{"id": f"g{n}", "split": "dev" if n != 10 else "holdout"} for n in (10, 2, 3, 4)]).to_csv(
        pack / "answer_key.csv", index=False)
    windows = tmp_path / "data" / "research" / "stance" / "source"
    windows.mkdir(parents=True)
    pd.DataFrame([dict(demo_id=f"demo{n}.dem", round_id=1, player_id=str(n), behaviour="保守", detail="理由",
                       engagement_end_s=0.5, observed_s=3.0, stop_reason="observe", move=100.0, height=0.0,
                       threat_start=1.0, threat_end=1.2, threat_count=2) for n in (10, 2, 3, 4)]).to_csv(
        windows / "windows.csv", index=False)
    for sub, name, labels in (("text", "t2", {10: "激进·主动寻求对枪", 2: "激进·主动寻求对枪",
                                                 3: "保守", 4: None}),
                              ("vision", "v2", {10: "激进·被动寻求对枪", 2: "激进·主动寻求对枪",
                                                   3: "激进·前压", 4: "保守"})):
        folder = pack / sub
        folder.mkdir()
        (folder / f"{name}.jsonl").write_text("\n".join(json.dumps(
            {"id": f"g{n}", "label": label, "confidence": "高", "evidence": f"依据{n}", "error": "失败" if label is None else None},
            ensure_ascii=False) for n, label in labels.items()), encoding="utf-8")
    return pack


def _read(path):
    return tomllib.loads((path / "review.toml").read_text(encoding="utf-8"))


def test_ai_adjudicate_selects_sorts_and_hides_previous(tmp_path):
    _pack(tmp_path)
    data = _read(ai_adjudicate("sample", "source", "out", ["text/t2", "vision/v2"], root=tmp_path))
    assert [item["id"] for item in data["items"]] == ["g2", "g10", "g3"]
    assert data["items"][0]["reveal"][0] == "分歧类型：两个 AI 都反对程序"
    assert data["items"][2]["reveal"][0] == "分歧类型：只有 看图 v2 反对程序"
    assert data["items"][0]["reveal"][-2:] == [
        "文字 t2：激进·主动寻求对枪（高）——依据2", "看图 v2：激进·主动寻求对枪（高）——依据2"]
    assert all("previous" not in item and "人工旧" not in str(item) for item in data["items"])
    assert len(_read(ai_adjudicate("sample", "source", "dev", ["text/t2", "vision/v2"], "dev", tmp_path))["items"]) == 2
    assert len(_read(ai_adjudicate("sample", "source", "holdout", ["text/t2", "vision/v2"], "holdout", tmp_path))["items"]) == 1


def test_adjudicate_reveal_stays_the_same_after_extraction(tmp_path):
    pack = _pack(tmp_path)
    data = _read(adjudicate("sample", "source", "old", "all", tmp_path))
    row = data["items"][0]
    cfg = load_config()
    q = cfg["research"]["threat"]["push_min_delta"]
    s = cfg["research"]["stance"]
    assert row["reveal"] == [
        "你之前的判断：人工旧答案（人工旧理由）",
        "程序的判断：保守（理由）",
        "交战在首杀后 0.5 秒结束；观察 3.0 秒，观察满截止",
        f"移动 100 单位，高度变化 0（原位界线 {s['stay_max_move']:g} / {s['stay_max_height']:g}）",
        f"威胁危险度 1.00 → 1.20（变化 0.20；≥ {q:g} 为前压），已知威胁 2 个",
        "看页面「程序用的威胁」：起点和终点就是在这张图上比较的",
    ]
