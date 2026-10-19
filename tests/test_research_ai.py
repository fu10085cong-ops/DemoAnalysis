"""R03 AI 判定试点脚本：抽样、答案解析、调用（假服务，不产生费用）与对比。"""

from __future__ import annotations

import json
import tomllib
from types import SimpleNamespace

import pandas as pd

from scripts import research_ai as ra


def _windows():
    rows = []
    for i in range(60):
        rows.append(dict(demo_id=f"m{i % 6}.dem", match_id=f"m{i % 6}", round_id=i // 6, player_id=str(1000 + i),
                         player_name=f"p{i}", start_tick=100 * i, first_kill_by="self" if i % 5 == 0 else "teammate",
                         shots=i % 2, damage_dealt=0, damage_taken=0))
    return pd.DataFrame(rows)


def test_sample_groups_one_window_per_round():
    picked = ra.sample(_windows(), per_group=5, seed=1)
    assert picked["first_kill_by"].value_counts().to_dict() == {"self": 5, "teammate": 5}
    assert not picked.duplicated(["match_id", "round_id"]).any()
    assert (picked.loc[picked["first_kill_by"] == "teammate", "shots"] > 0).all()
    assert picked["id"].tolist() == [f"f{i}" for i in range(1, 11)]


def test_parse_answer():
    assert ra.parse_answer('```json\n{"label": "换位", "evidence": "x"}\n```')["label"] == "换位"
    assert "error" in ra.parse_answer('{"label": "乱跑"}')
    assert ra.parse_answer("不知道")["label"] is None


class FakeProvider:
    def __init__(self, labels):
        self.labels = labels

    def start(self, system, texts, tools):
        self.text = texts[0]

    def send(self):
        label = self.labels[self.text]
        return SimpleNamespace(text=json.dumps({"label": label, "evidence": "e", "confidence": "高"}, ensure_ascii=False),
                               model="fake", usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def test_run_needs_yes_then_compare_builds_review_pack(tmp_path, monkeypatch):
    out = tmp_path / "data" / "research" / "ai" / "t"
    out.mkdir(parents=True)
    (out / "meta.json").write_text(json.dumps({"source": "src"}), encoding="utf-8")
    records = []
    for i, rule in enumerate(["换位", "原位再架", "撤离", "继续压", "换位", "原位再架", "撤离"]):
        records.append(dict(id=f"f{i + 1}", demo_id="m.dem", match_id="m", round_id=i, player_id="1", start_tick=100,
                            player_name="p", first_kill_by="self" if i % 2 else "teammate", rule=rule,
                            fight_end_s=0.5, observe_end_s=3.5, text=f"时间线{i}"))
    (out / "timelines.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    ai_labels = {r["text"]: r["rule"] for r in records}
    ai_labels["时间线0"] = "撤离"  # 一条与规则不一致
    import cs2coach.ai.providers as providers

    monkeypatch.setattr(providers, "make_provider", lambda cfg, client=None: FakeProvider(ai_labels))
    ra.run("t", None, 2, yes=False, root=tmp_path)
    assert not (out / "results").exists()  # 不加 --yes 不调用
    ra.run("t", None, 2, yes=True, root=tmp_path)
    ra.compare("t", root=tmp_path)
    text = (out / "compare.md").read_text(encoding="utf-8")
    assert "规则与 AI 第 1 遍一致：6/7" in text and "AI 第 1 遍与第 2 遍一致：7/7" in text
    pack = tomllib.loads((tmp_path / "data" / "research" / "review" / "t" / "review.toml").read_text(encoding="utf-8"))
    assert pack["labels"] == ra.NAMES
    ids = [item["id"] for item in pack["items"]]
    assert ids[0] == "f1" and len(ids) == 6  # 不一致的 1 条 + 一致中随机 5 条
    assert "rule" not in pack["items"][0]  # 盲审
