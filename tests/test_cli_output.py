"""analyze 输出目录、文件命名与历史报告归档。"""

import os
from datetime import datetime
from pathlib import Path

from cs2coach.cli import _build_parser, _claim_folder, _output_folder, _page_tiers, main
from cs2coach.config import load_config
from tests.helpers.synthetic import A
from tests.test_moments import find_moments, scenario


def test_output_folder_uses_resolved_member_and_sanitizes_player_name(tmp_path):
    folder = _output_folder(tmp_path, Path("archive/.extracted/inside.dem"),
                            '  A/B:*?"<>|  ', A)
    assert folder == tmp_path / f"A_B________{A}" / "inside"


def test_output_folder_reuses_player_folder_by_steamid(tmp_path):
    # 同一个人在不同平台名字不同（如 5E 的"玩家03"、完美的"玩家07"），按 steamid 归到同一个文件夹
    (tmp_path / f"玩家03_{A}").mkdir()
    folder = _output_folder(tmp_path, Path("9215177492731139980_0.dem"), "玩家07", A)
    assert folder == tmp_path / f"玩家03_{A}" / "9215177492731139980_0"


def test_output_folder_separates_different_demos_with_same_name(tmp_path):
    demo = Path("x/match.dem")
    first = _output_folder(tmp_path, demo, "alice", A, "a" * 64)
    assert first == tmp_path / f"alice_{A}" / "match"
    first.mkdir(parents=True)
    _claim_folder(first, "a" * 64)
    # 同名不同内容：另建带指纹后缀的文件夹；原 demo 重跑仍回到原文件夹
    second = _output_folder(tmp_path, Path("y/match.dem"), "alice", A, "b" * 64)
    assert second == tmp_path / f"alice_{A}" / f"match_{'b' * 8}"
    second.mkdir()
    _claim_folder(second, "b" * 64)
    assert _output_folder(tmp_path, demo, "alice", A, "a" * 64) == first
    assert _output_folder(tmp_path, Path("y/match.dem"), "alice", A, "b" * 64) == second


def test_output_folder_adopts_legacy_folder_without_record(tmp_path):
    legacy = tmp_path / f"alice_{A}" / "match"
    legacy.mkdir(parents=True)
    assert _output_folder(tmp_path, Path("match.dem"), "alice", A, "c" * 64) == legacy


def test_rerun_moves_both_ai_files_and_keeps_program_outputs(tmp_path, monkeypatch, capsys):
    cfg = load_config()
    match = scenario()
    mm = find_moments(match, A, cfg)
    (tmp_path / "inside.dem").write_bytes(b"demo")
    monkeypatch.setattr("cs2coach.cli.resolve_demo", lambda path, member=None: tmp_path / "inside.dem")
    monkeypatch.setattr("cs2coach.parsing.match.parse_match", lambda path, config: match)
    monkeypatch.setattr("cs2coach.features.moments.find_moments", lambda *args, **kwargs: mm)
    folder = tmp_path / f"alice_{A}" / "inside"
    folder.mkdir(parents=True)
    stamp = 1792116840
    label = datetime.fromtimestamp(stamp).strftime("%Y%m%d-%H%M")

    for name in ("report.md", "submission.json", "report.html"):
        old = folder / name
        old.write_text("first " + name, encoding="utf-8")
        os.utime(old, (stamp, stamp))
    args = ["analyze", "archive.zip", "--player", str(A), "--out", str(tmp_path)]
    assert main(args) == 0
    assert not (folder / "report.md").exists() and not (folder / "submission.json").exists()
    archive = folder / "历史版本"
    assert (archive / f"report_{label}.md").read_text(encoding="utf-8") == "first report.md"
    assert (archive / f"submission_{label}.json").read_text(encoding="utf-8") == "first submission.json"
    assert (archive / f"report_{label}.html").read_text(encoding="utf-8") == "first report.html"
    assert 'id="page-data"' in (folder / "report.html").read_text(encoding="utf-8")
    assert b"\r\n" not in (folder / "report.html").read_bytes()
    assert {p.name for p in folder.iterdir()} == {"moments.json", "facts.md", "problems.md", "basic_report.md", "report_data.json", "report.html", "历史版本", ".demo_id"}

    for name in ("report.md", "submission.json"):
        old = folder / name
        old.write_text("second " + name, encoding="utf-8")
        os.utime(old, (stamp, stamp))
    assert main(args) == 0
    assert (archive / f"report_{label}_2.md").read_text(encoding="utf-8") == "second report.md"
    assert (archive / f"submission_{label}_2.json").read_text(encoding="utf-8") == "second submission.json"
    assert len(list(archive.glob("report_*.html"))) == 2
    assert capsys.readouterr().out.rstrip().endswith(f"报告页：{folder / 'report.html'}")


def test_target_selects_default_and_keeps_all_built_tiers(monkeypatch, capsys):
    tiers = {"pro": {"id": "pro"}, "amateur_tmp": {"id": "amateur_tmp"}}
    monkeypatch.setattr("cs2coach.report.page_data.load_tiers", lambda cfg: tiers)
    for command in (["analyze", "example.dem"], ["summary"]):
        cfg = load_config()
        args = _build_parser().parse_args(command + ["--player", str(A), "--target", "pro"])
        assert _page_tiers(cfg, args) == tiers
        assert cfg["radar"]["default_tier"] == "pro"
        args.target = "missing"
        assert _page_tiers(cfg, args) == {}
    assert "missing" in capsys.readouterr().out
