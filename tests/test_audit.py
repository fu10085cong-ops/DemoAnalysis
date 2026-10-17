"""S01 审计工具只用合成数据，不含真实玩家信息。"""

from __future__ import annotations

import math
import tomllib
from collections import Counter
from types import SimpleNamespace

import pytest

from cs2coach.audit import (comparison_report, hit_key, precision_report, sample_hits, scan_hit,
                            update_labels, wilson, write_hits)
from cs2coach.config import ConfigError, load_config
from cs2coach.features.problems import ProblemHit
from tests.helpers.synthetic import A, make_audit_engagement


def hit(problem="slow_reaction", target="R1-death", **evidence):
    return ProblemHit(problem=problem, target=target, weapon="ak47", evidence=evidence, thresholds={})


def record(key, problem="slow_reaction", demo="a.dem", suspicious=False):
    return {"hit_key": key, "problem": problem, "demo": demo, "member": None,
            "suspicious": [{"id": "test", "detail": "test"}] if suspicious else [],
            "disabled": False, "target": "R1-death", "map": "de_test", "steamid": A,
            "player_name": "alice", "fact_card": "事实", "basis": "依据", "evidence": {},
            "thresholds": {}, "jump_command": "demo_gototick 1"}


def test_hit_key_stable_and_unique_for_match_segments(tmp_path):
    first = hit()
    assert hit_key("a.dem", None, A, first) == hit_key("a.dem", None, A, first)
    assert hit_key("a.dem", None, A, first) != hit_key("b.dem", None, A, first)
    assert hit_key("a.dem", "one.dem", A, first) != hit_key("a.dem", "two.dem", A, first)
    match_a = hit("spray_control", "match", segment="4–6", tick=10)
    match_b = hit("spray_control", "match", segment="7–10", tick=10)
    assert hit_key("a.dem", None, A, match_a) != hit_key("a.dem", None, A, match_b)
    path = tmp_path / "hits.jsonl"
    with pytest.raises(ValueError, match="重复"):
        write_hits(path, [record("same"), record("same")])
    assert not path.exists()


@pytest.mark.parametrize("problem,condition,changes,evidence", [
    ("slow_reaction", "fired_before_seen", {"shots_before_seen": 1}, {}),
    ("slow_reaction", "negative_segment", {"seen_to_first_shot_s": -0.1}, {}),
    ("poor_crosshair_placement", "blind_at_seen", {"player_seen_while_blind": True}, {}),
    ("slow_reaction", "visibility_note_set", {"visibility_note": "lost_sight"}, {}),
    ("peeked_into_held_angle", "contact_speed_borderline", {"player_speed_at_contact": 40.0}, {}),
    ("shot_while_moving", "stop_borderline", {"shots": [SimpleNamespace(tick=1, speed_ratio=0.34)]}, {}),
    ("spray_control", "spray_mostly_unseen", {}, {"shots": 6, "excluded_unseen_shots": 6}),
    ("poor_crosshair_placement", "crosshair_extreme", {}, {"crosshair_placement_deg": 91.0}),
    ("spray_control", "through_smoke", {}, {}),
])
def test_each_suspicious_rule_trigger_and_nontrigger(problem, condition, changes, evidence):
    cfg = load_config()
    triggered = {s["id"] for s in scan_hit(hit(problem, **evidence),
                   SimpleNamespace(engagement=make_audit_engagement(**changes), kind="death",
                                   through_smoke=condition == "through_smoke"), cfg)}
    assert condition in triggered
    normal = {s["id"] for s in scan_hit(hit(problem),
                SimpleNamespace(engagement=make_audit_engagement(), kind="death", through_smoke=False), cfg)}
    assert condition not in normal


@pytest.mark.parametrize("note,expected", [
    ("not_seen", True), ("lost_sight", True), ("first_hit_unseen", True),
    ("smoke", True), ("blind", True), ("no_map_data", True),
    ("seen_before_window", False), ("seen_since_previous_kill", False),
    ("seen_since_round_start", False), (None, False),
])
def test_visibility_note_only_flags_incomplete_notes(note, expected):
    cfg = load_config()
    moment = SimpleNamespace(engagement=make_audit_engagement(visibility_note=note),
                             kind="death", through_smoke=False)
    ids = {s["id"] for s in scan_hit(hit("peeked_into_held_angle"), moment, cfg)}
    assert ("visibility_note_set" in ids) is expected
    assert "visibility_note_set" not in {s["id"] for s in scan_hit(hit("died_without_seeing"), moment, cfg)}


def test_through_smoke_only_applies_to_visibility_problems_and_kill_death():
    cfg = load_config()
    moment = SimpleNamespace(engagement=make_audit_engagement(), kind="death", through_smoke=True)
    assert "through_smoke" in {s["id"] for s in scan_hit(hit("died_without_seeing"), moment, cfg)}
    assert "through_smoke" not in {s["id"] for s in scan_hit(hit("shot_while_moving"), moment, cfg)}
    moment.kind = "assist"
    assert "through_smoke" not in {s["id"] for s in scan_hit(hit("slow_reaction"), moment, cfg)}


def test_borderline_boundaries_and_missing_data():
    cfg = load_config()
    e = make_audit_engagement(player_speed_at_contact=46, opponent_speed_at_contact=200)
    assert any(s["id"] == "contact_speed_borderline" for s in scan_hit(hit("peeked_into_held_angle"), SimpleNamespace(engagement=e), cfg))
    e.player_speed_at_contact = 47
    assert not scan_hit(hit("peeked_into_held_angle"), SimpleNamespace(engagement=e), cfg)
    assert scan_hit(hit("slow_reaction"), None, cfg) == []


def test_sampling_reproducible_per_problem_demo_cap_and_priority_half():
    rows = [record(f"{p}-{d}-{i}", problem=p, demo=f"{d}.dem", suspicious=i < 3)
            for p in ("slow_reaction", "poor_crosshair_placement") for d in range(5) for i in range(5)]
    first = sample_hits(rows, per_problem=8, per_demo=3, seed=42)
    second = sample_hits(list(reversed(rows)), per_problem=8, per_demo=3, seed=42)
    assert [(row["hit_key"], source) for row, source in first] == [(row["hit_key"], source) for row, source in second]
    for problem in ("slow_reaction", "poor_crosshair_placement"):
        selected = [(row, source) for row, source in first if row["problem"] == problem]
        assert max(Counter(row["demo"] for row, _ in selected).values()) <= 3
        assert sum(source == "suspicious_priority" for _, source in selected) <= len(selected) // 2
        assert all(not row["suspicious"] for row, source in selected if source == "random")
    assert sum(source == "suspicious_priority" for _, source in first) > 0


def test_sampling_fills_quota_when_all_hits_are_suspicious():
    rows = [record(f"key-{i}", demo=f"{i // 3}.dem", suspicious=True) for i in range(30)]
    selected = sample_hits(rows, per_problem=20, per_demo=3)
    assert len(selected) == 20
    assert sum(source == "suspicious_priority" for _, source in selected) == 20
    assert sum(source == "random" for _, source in selected) == 0


def test_labels_rerun_preserves_existing_bytes_and_appends(tmp_path):
    path = tmp_path / "labels.toml"
    first, second = record("key-1"), record("key-2")
    update_labels(path, [(first, "random")], {"slow_reaction"})
    original = path.read_text(encoding="utf-8")
    edited = original.replace('verdict = ""', 'verdict = "wrong"').replace('reason = ""', 'reason = "视野不准"')
    path.write_text(edited, encoding="utf-8")
    update_labels(path, [(first, "random"), (second, "suspicious_priority")], {"slow_reaction"})
    result = path.read_text(encoding="utf-8")
    assert result.startswith(edited)
    labels = tomllib.loads(result)["labels"]
    assert labels["key-1"]["verdict"] == "wrong"
    assert labels["key-1"]["reason"] == "视野不准"
    assert labels["key-1"]["needs_ingame"] is True
    assert labels["key-2"]["selection"] == "suspicious_priority"
    update_labels(path, [(first, "random"), (second, "suspicious_priority")], {"slow_reaction"})
    assert path.read_text(encoding="utf-8") == result


def test_precision_excludes_unsure_and_weights_population_strata():
    rows = [record(f"key-{i}", suspicious=i < 2) for i in range(6)]
    labels = {"key-0": {"selection": "suspicious_priority", "verdict": "correct"},
              "key-1": {"selection": "suspicious_priority", "verdict": "wrong", "reason": "视野不准"},
              "key-2": {"selection": "random", "verdict": "correct"},
              "key-3": {"selection": "random", "verdict": "correct"},
              "key-4": {"selection": "random", "verdict": "wrong", "reason": "时间不准"},
              "key-5": {"selection": "random", "verdict": "unsure"}}
    report = precision_report(rows, labels)
    assert "| slow_reaction | 可疑层 | 2 | 33.3% | 2 | 1 | 1 | 0 | 50.0% |" in report
    assert "| slow_reaction | 不可疑层 | 4 | 66.7% | 4 | 2 | 1 | 1 | 66.7% |" in report
    assert "| slow_reaction | 整体（加权） | 6 | 100.0% | 6 | 3 | 2 | 1 | 61.1% |" in report
    from cs2coach.audit import wilson
    a, b = wilson(1, 1), wilson(2, 1)
    interval = f"{(a[0] / 3 + 2 * b[0] / 3):.1%}–{(a[1] / 3 + 2 * b[1] / 3):.1%}"
    assert f"61.1% | {interval} |" in report
    assert "视野不准：1" in report and "时间不准：1" in report
    assert "0.0%–0.0%" not in report
    lo, hi = wilson(1, 1)
    assert math.isclose(lo, 0.09453120573423068)
    assert math.isclose(hi, 0.9054687942657693)
    assert wilson(0, 0) is None


def test_wilson_weighted_interval_does_not_collapse_at_extremes():
    rows = [record("wrong", suspicious=True), record("right", suspicious=False)]
    labels = {"wrong": {"verdict": "wrong"}, "right": {"verdict": "correct"}}
    report = precision_report(rows, labels)
    a, b = wilson(0, 1), wilson(1, 0)
    assert f"50.0% | {(a[0] + b[0]) / 2:.1%}–{(a[1] + b[1]) / 2:.1%}" in report


def test_comparison_matches_hit_keys_and_lists_removed_correct():
    before = [record("keep"), record("wrong"), record("correct")]
    after = [before[0], record("new")]
    labels = {"keep": {"verdict": "correct"}, "wrong": {"verdict": "wrong"},
              "correct": {"verdict": "correct", "note": "应当保留"}}
    report = comparison_report(before, after, labels)
    assert "| slow_reaction | 3 | 66.7% | 1 | 100.0% | 1 | 1 | 0 | 1 |" in report
    assert "`correct`：应当保留" in report
    assert "`wrong`：" not in report


def test_report_cli_reads_labels_from_old_directory(tmp_path, monkeypatch):
    from scripts import audit_report

    old = tmp_path / "data/audit/before"
    new = tmp_path / "data/audit/after"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    write_hits(old / "hits.jsonl", [record("keep"), record("removed")])
    write_hits(new / "hits.jsonl", [record("keep")])
    (old / "labels.toml").write_text('[labels.keep]\nverdict = "correct"\n'
                                      '[labels.removed]\nverdict = "wrong"\n', encoding="utf-8")
    monkeypatch.setattr(audit_report, "ROOT", tmp_path)
    assert audit_report.main(["--name", "after", "--labels-from", "before"]) == 0
    text = (new / "precision.md").read_text(encoding="utf-8")
    assert "# 改动前后同批判定对比" in text
    assert "| slow_reaction | 2 | 50.0% | 1 | 100.0% | 1 | 0 |" in text
    assert not (new / "labels.toml").exists()


@pytest.mark.parametrize("body", [
    '[problems.exclusions]\nthrough_smoke = ["unknown_problem"]\n',
    '[problems]\nmax_shots_before_seen = 0\n',
    '[problems]\nmoving_shot_margin = -0.1\n',
    '[problems]\nmissed_trade_exclude_traded_by_other = 1\n',
])
def test_problem_config_rejects_invalid_values(tmp_path, body):
    path = tmp_path / "bad.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_overall_precision_is_insufficient_until_each_nonempty_stratum_is_judged():
    rows = [record("s", suspicious=True), record("o")]
    report = precision_report(rows, {"o": {"verdict": "correct"}, "s": {"verdict": "unsure"}})
    assert "| slow_reaction | 整体（加权） | 2 | 100.0% | 2 | 1 | 0 | 1 | 不足 | 不足 |" in report
    report = precision_report([record("o")], {"o": {"verdict": "correct"}})
    assert "| slow_reaction | 整体（加权） | 1 | 100.0% | 1 | 1 | 0 | 0 | 100.0% |" in report


def test_overall_weights_use_all_hits_including_unlabeled_hits():
    rows = [record("s", suspicious=True)] + [record(f"o-{i}") for i in range(3)]
    report = precision_report(rows, {"s": {"verdict": "correct"}, "o-0": {"verdict": "wrong"}})
    assert "| slow_reaction | 整体（加权） | 4 | 100.0% | 2 | 1 | 1 | 0 | 25.0% |" in report


def test_audit_config_rejects_unknown_condition(tmp_path):
    cfg = tmp_path / "wrong.toml"
    cfg.write_text('[[audit.rules]]\ncondition = "unknown"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_audit_config_validates_incomplete_visibility_notes(tmp_path):
    cfg = tmp_path / "wrong_notes.toml"
    cfg.write_text('[audit]\nincomplete_visibility_notes = ["not_seen", "not_seen"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="incomplete_visibility_notes"):
        load_config(cfg)
