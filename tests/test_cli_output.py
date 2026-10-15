"""analyze 输出目录、文件命名与历史报告归档。"""

import os
from datetime import datetime
from pathlib import Path

from cs2coach.cli import _output_folder, main
from cs2coach.config import load_config
from tests.helpers.synthetic import A
from tests.test_moments import find_moments, scenario


def test_output_folder_uses_resolved_member_and_sanitizes_player_name(tmp_path):
    folder = _output_folder(tmp_path, Path("archive/.extracted/inside.dem"),
                            '  A/B:*?"<>|  ', A)
    assert folder == tmp_path / f"A_B________{A}" / "inside"


def test_output_folder_reuses_player_folder_by_steamid(tmp_path):
    # 同一个人在不同平台名字不同（如 5E 的"梁孙超"、完美的"Jayleo"），按 steamid 归到同一个文件夹
    (tmp_path / f"梁孙超_{A}").mkdir()
    folder = _output_folder(tmp_path, Path("9215177492731139980_0.dem"), "Jayleo", A)
    assert folder == tmp_path / f"梁孙超_{A}" / "9215177492731139980_0"


def test_rerun_moves_both_ai_files_and_keeps_program_outputs(tmp_path, monkeypatch, capsys):
    cfg = load_config()
    match = scenario()
    mm = find_moments(match, A, cfg)
    monkeypatch.setattr("cs2coach.cli.resolve_demo", lambda path, member=None: tmp_path / "inside.dem")
    monkeypatch.setattr("cs2coach.parsing.match.parse_match", lambda path, config: match)
    monkeypatch.setattr("cs2coach.features.moments.find_moments", lambda *args: mm)
    folder = tmp_path / f"alice_{A}" / "inside"
    folder.mkdir(parents=True)
    stamp = 1792116840
    label = datetime.fromtimestamp(stamp).strftime("%Y%m%d-%H%M")

    for name in ("report.md", "submission.json"):
        old = folder / name
        old.write_text("first " + name, encoding="utf-8")
        os.utime(old, (stamp, stamp))
    args = ["analyze", "archive.zip", "--player", str(A), "--out", str(tmp_path)]
    assert main(args) == 0
    assert not (folder / "report.md").exists() and not (folder / "submission.json").exists()
    archive = folder / "历史版本"
    assert (archive / f"report_{label}.md").read_text(encoding="utf-8") == "first report.md"
    assert (archive / f"submission_{label}.json").read_text(encoding="utf-8") == "first submission.json"
    assert {p.name for p in folder.iterdir()} == {"moments.json", "facts.md", "problems.md", "历史版本"}

    for name in ("report.md", "submission.json"):
        old = folder / name
        old.write_text("second " + name, encoding="utf-8")
        os.utime(old, (stamp, stamp))
    assert main(args) == 0
    assert (archive / f"report_{label}_2.md").read_text(encoding="utf-8") == "second report.md"
    assert (archive / f"submission_{label}_2.json").read_text(encoding="utf-8") == "second submission.json"
    assert capsys.readouterr().out.rstrip().endswith(str(folder))
