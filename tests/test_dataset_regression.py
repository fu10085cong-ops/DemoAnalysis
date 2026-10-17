"""S07：数据集导出与规则回归的合成数据测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from tests.helpers.synthetic import A, make_match

from cs2coach import dataset
from cs2coach.features.problems import ProblemHit, ProblemReport
from scripts import dataset_export, rule_regression


class RowModel:
    def __init__(self, **values):
        self.values = values
        for key, value in values.items():
            setattr(self, key, value)

    def model_dump(self, **_):
        return self.values.copy()


def test_dataset_rows_and_cache(tmp_path, monkeypatch):
    match = make_match()
    mm = SimpleNamespace(
        map_name="de_test", player_name="alice", rounds_played=1,
        moments=[SimpleNamespace(id="R1-death", context=RowModel(side="T"),
                                 engagement=RowModel(shots_fired=0, shots=[{"tick": 2}]),
                                 death=RowModel(unused_grenades=["flashbang"]),
                                 trade=RowModel(traded=False), assist=None,
                                 model_dump=lambda **_: {"id": "R1-death", "kind": "death", "round": 1,
                                                        "tick": 20, "opponent_name": "bob", "tags": ["test"]})],
        missed_trades=[RowModel(round=1, tick=19, teammate=A, killer=A, distance=100.0)],
        aim_stats=RowModel(time_to_damage_ms=200.0))
    cfg = {"parsing": {"min_target_coverage": 0}, "problems": {"disabled": ["disabled_rule"]}}
    monkeypatch.setattr(dataset_export, "Sight", lambda *_: SimpleNamespace(available=True))
    monkeypatch.setattr(dataset_export, "shot_targets", lambda *_: pd.DataFrame(columns=["target", "target_angle"]))
    monkeypatch.setattr(dataset_export, "find_moments", lambda *_args, **_kwargs: mm)
    hits = [ProblemHit(problem=p, target="R1-death", weapon=None, evidence={}, thresholds={})
            for p in ("active_rule", "disabled_rule")]
    monkeypatch.setattr(dataset_export, "detect_problems", lambda *_: ProblemReport(
        hits=hits, disabled=[], skipped={}, baseline_source=None))
    rows = dataset_export.rows_for_match(match, cfg, {}, "amateur", "demo.dem", None)
    assert {row["kind"] for row in rows} == {"moments", "missed_trades", "players"}
    moment = next(row for row in rows if row["kind"] == "moments")
    assert moment["ctx_side"] == "T" and moment["eng_shots_fired"] == 0
    assert json.loads(moment["death_unused_grenades"]) == ["flashbang"]
    assert json.loads(moment["hits"]) == ["active_rule"]
    assert json.loads(moment["disabled_hits"]) == ["disabled_rule"]
    assert "alice" not in json.dumps(rows) and "bob" not in json.dumps(rows)

    demo = tmp_path / "demo.dem"
    demo.touch()
    monkeypatch.setattr(dataset_export, "parse_match", lambda *_: match)
    monkeypatch.setattr(dataset_export, "resolve_demo", lambda path, member=None: path)
    out = tmp_path / "dataset"
    first = dataset_export.export_one(("amateur", "demo.dem", None), out, cfg, {}, root=tmp_path)
    assert first[2] is False
    monkeypatch.setattr(dataset_export, "parse_match", lambda *_: (_ for _ in ()).throw(AssertionError("不应解析")))
    second = dataset_export.export_one(("amateur", "demo.dem", None), out, cfg, {}, root=tmp_path)
    assert second[2] is True
    frame = pd.read_parquet(next(out.glob("*.parquet")))
    assert set(frame["kind"]) == {"moments", "missed_trades", "players"}
    monkeypatch.setattr(dataset, "DATASET_ROOT", tmp_path)
    monkeypatch.setattr(dataset, "code_hash", lambda: "dataset")
    loaded = dataset.load("moments", group="amateur")
    assert len(loaded) == 4 and set(loaded["moment_kind"]) == {"death"}


def test_regression_counts_and_newest_label(tmp_path):
    old = [{"hit_key": key, "problem": "p"} for key in ("a", "b", "c", "d")]
    new = [{"hit_key": key, "problem": "p"} for key in ("a", "c", "e")]
    early, late = tmp_path / "2026-10-17-a", tmp_path / "2026-10-17-b"
    early.mkdir()
    late.mkdir()
    (early / "labels.toml").write_text('[labels.a]\nverdict="wrong"\n[labels.b]\nverdict="wrong"\n'
                                       '[labels.c]\nverdict="correct"\nworth="no"\n'
                                       '[labels.d]\nverdict="correct"\nworth="no"\n', encoding="utf-8")
    (late / "labels.toml").write_text('[labels.a]\nverdict="correct"\nworth="yes"\n', encoding="utf-8")
    labels = rule_regression.read_labels([late, early])
    assert labels["a"]["verdict"] == "correct"
    assert rule_regression.label_dirs(late, tmp_path) == [early, late]
    row = rule_regression.compare_hits(old, new, labels)[0]
    assert (row["old"], row["new"], row["delta"]) == (4, 3, -1)
    assert row["retained"] == {"judged": 2, "correct": 2, "worth_yes": 1, "worth_no": 1}
    assert row["removed"] == {"judged": 2, "wrong": 1, "correct": 1, "worth_no": 1}
    assert row["precision"] == 1 and row["usefulness"] == 0.5
    assert row["removed_wrong_share"] == 0.5 and row["removed_worth_no_share"] == 0.5
