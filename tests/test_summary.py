"""多场查找、聚合与命令行输出；全部使用合成 demo 和假解析器。"""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cs2coach import cli
from cs2coach.config import load_config
from cs2coach.features.models import AimStats
from cs2coach.features.problems import ProblemHit, ProblemReport, frequency_from_counts
from cs2coach.summary.aggregate import aggregate_matches, build_summary
from cs2coach.summary.discover import DemoRef, find_player_demos
from cs2coach.summary.render import render_summary_problems
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A


def ref(name, map_name="de_dust2"):
    return DemoRef(path=Path(f"{name}.dem"), member=None, match_id=name,
                   map_name=map_name, player_name="alice")


def report(pid, count):
    hits = [ProblemHit(problem=pid, target=f"R{i + 1}-death", weapon="ak47",
                       evidence={}, thresholds={}) for i in range(count)]
    return ProblemReport(hits=hits, disabled=[], skipped={}, baseline_source="测试基准")


def stats(ttd):
    return AimStats(time_to_damage_ms=ttd, time_to_damage_samples=5,
                    crosshair_placement_deg=None, crosshair_placement_samples=0,
                    counter_strafing_pct=None, counter_strafing_shots=0,
                    spray_accuracy_pct=None, spray_shots=0,
                    spotted_accuracy_pct=None, spotted_shots=0,
                    headshot_accuracy_pct=None, hits=0)


def test_discover_prefers_archive_over_extracted_and_sorts(tmp_path, monkeypatch):
    for name in ("b", "a"):
        archive = tmp_path / f"{name}.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(f"{name}.dem", b"demo")
        cached = tmp_path / ".extracted" / name / f"{name}.dem"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"demo")
    monkeypatch.setattr("cs2coach.summary.discover.resolve_demo",
                        lambda path, member=None: path)

    class Parser:
        def __init__(self, path):
            self.path = path

        def parse_player_info(self):
            return pd.DataFrame([dict(steamid=A, name="alice", team_number=2)])

        def parse_header(self):
            return {"map_name": "de_dust2"}

    monkeypatch.setattr("cs2coach.summary.discover.DemoParser", Parser)
    found = find_player_demos(A, tmp_path)
    assert [item.match_id for item in found] == ["a", "b"]
    assert all(item.path.suffix == ".zip" for item in found)
    assert [item.member for item in found] == ["a.dem", "b.dem"]


def test_discover_skips_unreadable_and_uses_tick_roster_fallback(tmp_path, monkeypatch):
    (tmp_path / "bad.zip").write_bytes(b"bad archive")
    (tmp_path / "good.dem").write_bytes(b"demo")

    class Parser:
        def __init__(self, path):
            pass

        def parse_player_info(self):
            return pd.DataFrame()

        def parse_ticks(self, fields):
            assert fields == ["team_num"]
            return pd.DataFrame([dict(tick=1, steamid=A, name="alice", team_num=2)])

        def parse_header(self):
            return {"map_name": "de_mirage"}

    monkeypatch.setattr("cs2coach.summary.discover.DemoParser", Parser)
    found = find_player_demos(A, tmp_path)
    assert [item.match_id for item in found] == ["good"]
    assert found[0].map_name == "de_mirage"
    assert len(found.skipped) == 1 and found.skipped[0].match_id == "bad"


def test_aggregate_uses_played_rounds_and_shared_frequency_math():
    cfg = load_config()
    baseline = {"problem_rates": {"slow_reaction": {"hits": 10, "rounds": 100}}}
    mm1 = make_mm([make_moment(kind="kill")], rounds=10, absent_rounds=[1, 2],
                  map_name="de_dust2", aim_stats=stats(400))
    mm2 = make_mm([make_moment(kind="death")], rounds=20, map_name="de_mirage",
                  player_name="newname", aim_stats=stats(600))
    games = [(ref("b", "de_mirage"), mm2, report("slow_reaction", 1), (10, 10)),
             (ref("a"), mm1, report("slow_reaction", 3), (5, 5))]
    result = aggregate_matches(A, games, baseline, cfg)
    row = next(r for r in result.frequency if r.problem == "slow_reaction")
    standalone = next(r for r in frequency_from_counts({"slow_reaction": 4}, 28, set(cfg["problems"]["disabled"]),
                                                  baseline, cfg) if r.problem == "slow_reaction")
    assert result.rounds_played_total == 28
    assert row.hits == 4 and row.expected_hits == pytest.approx(2.8)
    assert row.chance == pytest.approx(standalone.chance)
    assert row.matches_with_hits == 2 and row.matches_above_pro == 1
    assert row.by_map == {"de_dust2": 3, "de_mirage": 1}
    assert result.names == ["alice", "newname"]
    assert [m.number for m in result.matches] == ["M1", "M2"]
    assert result.matches[0].rounds_played == 8
    assert result.aim_overall["time_to_damage_ms"].median == 500
    assert result.aim_overall["time_to_damage_ms"].matches == 2
    assert result.aim_overall["crosshair_placement_deg"].matches == 0
    assert "_details" not in result.model_dump_json()
    assert result.detail("M1")[0] is mm1
    assert "| Time to Damage | 500.0ms | 2 |" in render_summary_problems(result)


def test_build_summary_skips_one_failed_match(tmp_path, monkeypatch):
    from tests.test_radar import line
    monkeypatch.setattr("cs2coach.features.lobby.lobby_line", lambda *args: line())
    refs = [ref("a"), ref("b")]
    monkeypatch.setattr("cs2coach.summary.aggregate.resolve_demo", lambda path, member=None: path)
    def parse(path, cfg):
        if path.stem == "a":
            raise ValueError("覆盖率不足")
        return SimpleNamespace(require_player=lambda sid, coverage: SimpleNamespace(steamid=sid),
                               meta=SimpleNamespace(map_name="de_test"))
    monkeypatch.setattr("cs2coach.summary.aggregate.parse_match", parse)
    monkeypatch.setattr("cs2coach.features.moments.find_moments", lambda *args:
                        make_mm(rounds=10))
    monkeypatch.setattr("cs2coach.summary.aggregate.detect_problems", lambda *args: report("slow_reaction", 1))
    monkeypatch.setattr("cs2coach.summary.aggregate.match_score", lambda *args: (6, 4))
    result = build_summary(A, refs, load_config(), None)
    assert [m.match_id for m in result.matches] == ["b"]
    assert result.matches[0].number == "M1"
    assert len(result.skipped) == 1 and "覆盖率不足" in result.skipped[0].reason


def test_build_summary_does_not_cache_match_without_visibility(tmp_path, monkeypatch):
    from tests.test_radar import line
    monkeypatch.setattr("cs2coach.features.lobby.lobby_line", lambda *args: line())
    from cs2coach import cache
    demo = tmp_path / "a.dem"
    demo.write_bytes(b"demo")
    refs = [DemoRef(path=demo, member=None, match_id="a", map_name="de_dust2", player_name="alice")]
    monkeypatch.setattr("cs2coach.summary.aggregate.cached",
                        lambda *args, **kw: cache.cached(*args, cache_dir=tmp_path / "c", **kw))
    monkeypatch.setattr("cs2coach.summary.aggregate.resolve_demo", lambda path, member=None: path)
    monkeypatch.setattr("cs2coach.summary.aggregate.parse_match", lambda path, cfg: SimpleNamespace(
        require_player=lambda sid, coverage: SimpleNamespace(steamid=sid)))
    reasons = iter(["ValueError: parse error: mesh: truncated body", None])
    monkeypatch.setattr("cs2coach.features.moments.find_moments", lambda *args:
                        make_mm(rounds=10).model_copy(update={"visibility_unavailable": next(reasons)}))
    monkeypatch.setattr("cs2coach.summary.aggregate.detect_problems", lambda *args: report("slow_reaction", 1))
    monkeypatch.setattr("cs2coach.summary.aggregate.match_score", lambda *args: (6, 4))
    build_summary(A, refs, load_config(), None)
    assert not list((tmp_path / "c").glob("*.pkl.gz"))       # 视野失败的结果不写入
    build_summary(A, refs, load_config(), None)
    assert len(list((tmp_path / "c").glob("*.pkl.gz"))) == 1  # 重新计算成功后写入


def test_summary_cli_writes_and_archives_files(tmp_path, monkeypatch, capsys):
    mm = make_mm(rounds=10)
    sample = aggregate_matches(A, [(ref("a"), mm, report("slow_reaction", 1), (6, 4))],
                               None, load_config())
    monkeypatch.setattr("cs2coach.summary.discover.find_player_demos", lambda *args: [ref("a")])
    monkeypatch.setattr("cs2coach.summary.aggregate.build_summary", lambda *args: sample)
    monkeypatch.setattr("cs2coach.baseline.load_baseline", lambda: None)
    folder = tmp_path / f"earlier_name_{A}" / "汇总"
    folder.mkdir(parents=True)
    for _ in range(2):
        assert cli.main(["summary", "--player", str(A), "--out", str(tmp_path)]) == 0
    assert (folder / "problems.md").is_file()
    assert (folder / "summary.json").is_file()
    assert (folder / "basic_report.md").is_file()
    assert len(list((folder / "历史版本").glob("*.md"))) == 2
    assert len(list((folder / "历史版本").glob("basic_report_*.md"))) == 1
    assert len(list((folder / "历史版本").glob("*.json"))) == 2
    assert (folder / "report_data.json").is_file()
    assert 'id="page-data"' in (folder / "report.html").read_text(encoding="utf-8")
    assert len(list((folder / "历史版本").glob("report_*.html"))) == 1
    assert capsys.readouterr().out.rstrip().endswith(f"报告页：{folder / 'report.html'}")
    data = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert data["steamid"] == A and data["matches"][0]["number"] == "M1"
    assert "## 每场概况" in render_summary_problems(sample)
