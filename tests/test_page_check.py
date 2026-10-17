"""页面一致性七条规则的正反例，以及 CLI 和全玩家检查入口。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from cs2coach.config import load_config
from cs2coach.report.page_check import check_page
from cs2coach.report.page_data import build_match_page
from tests.helpers.moments import make_mm, make_moment
from tests.test_page_data import frequency, report, sample
from tests.test_radar import line


def valid_page():
    page = build_match_page(make_mm([make_moment()], lobby=[line()], rounds=10),
                            report(), frequency(6, 6), load_config(), sample())
    problem = page.tiers[0].problems[0]
    problem.id = "missed_trade"
    problem.evidence_ticks = {"本场": [problem.examples[0].tick]}
    return page


@pytest.mark.parametrize("rule", range(1, 8))
def test_each_rule_accepts_valid_page(rule):
    page = valid_page()
    view = page.tiers[0]
    if rule == 4:
        view.strengths = [view.strengths[0]] * load_config()["radar"]["max_strengths"]
        view.problems *= load_config()["radar"]["max_problems"]
        view.problems[0].examples *= load_config()["radar"]["max_examples"]
    elif rule == 5:
        view.strengths[0].percentile = load_config()["radar"]["strength_min_percentile"]
    elif rule == 6:
        view.strengths[0].samples = load_config()["radar"]["min_samples"]["rounds"]
    # 两个分段都逐一检查，避免只核对默认分段。
    page.tiers.append(deepcopy(page.tiers[0]))
    page.tiers[1].id = "other"
    assert check_page(page, load_config()) == []


@pytest.mark.parametrize("rule,message", [
    (1, "维度冲突"), (2, "结论选题不符"), (3, "口径或单位为空"),
    (4, "数量超限"), (5, "优点百分位不足"), (6, "优点样本不足"), (7, "漏补例子无命中依据"),
])
def test_each_rule_rejects_invalid_page(rule, message):
    page = valid_page()
    page.tiers.append(deepcopy(page.tiers[0]))
    view = page.tiers[1]
    view.id = "other"
    if rule == 1:
        view.strengths[0].axis = view.problems[0].axis
    elif rule == 2:
        view.verdict = "最该练的是其他问题。"
    elif rule == 3:
        view.problems[0].unit = " "
    elif rule == 4:
        view.strengths *= load_config()["radar"]["max_strengths"] + 1
    elif rule == 5:
        view.strengths[0].percentile = 64
    elif rule == 6:
        view.strengths[0].samples = 9
    else:
        view.problems[0].examples[0].tick += 1
    issues = check_page(page, load_config())
    assert len(issues) == 1 and issues[0].startswith(f"{message}：other：")


def test_verdict_selects_largest_bad_excess_and_ignores_warn():
    page = valid_page()
    view = page.tiers[0]
    top = view.problems[0]
    top.name = "问题甲"
    top.count, top.expected = 20, 2
    other = top.model_copy(deep=True, update={"id": "slow_reaction", "name": "问题乙", "count": 30, "expected": 20})
    warn = other.model_copy(update={"id": "spray_control", "name": "问题丙", "severity": "warn", "count": 100})
    view.problems = [other, warn, top]
    view.verdict = "最该练的是问题甲：这场出现 20 次。"
    assert check_page(page, load_config()) == []
    view.verdict = "最该练的是问题乙：这场出现 30 次。"
    assert any("结论选题不符" in issue for issue in check_page(page, load_config()))


def test_no_bad_must_not_claim_training_priority():
    page = valid_page()
    view = page.tiers[0]
    view.problems[0].severity = "warn"
    assert any("结论选题不符" in issue for issue in check_page(page, load_config()))
    view.verdict = "这场没有检出明显多于分段的问题。"
    assert check_page(page, load_config()) == []


@pytest.mark.parametrize("field", ["measure", "unit"])
@pytest.mark.parametrize("section", ["strengths", "problems"])
def test_text_fields_required_for_every_row(section, field):
    page = valid_page()
    setattr(getattr(page.tiers[0], section)[0], field, "")
    assert any("口径或单位为空" in issue for issue in check_page(page, load_config()))


@pytest.mark.parametrize("section", ["problems", "examples", "highlights"])
def test_all_size_limits(section):
    page = valid_page()
    cfg = load_config()
    if section == "problems":
        page.tiers[0].problems *= cfg["radar"]["max_problems"] + 1
    elif section == "examples":
        page.tiers[0].problems[0].examples *= cfg["radar"]["max_examples"] + 1
    else:
        from cs2coach.report.page_data import Highlight
        example = page.tiers[0].problems[0].examples[0]
        page.highlights = [Highlight(kind="clutch", title="残局获胜", **example.model_dump(exclude={"situation"}))] * 7
    assert any("数量超限" in issue for issue in check_page(page, cfg))


def test_missed_trade_evidence_is_match_specific():
    page = valid_page()
    page.tiers[0].problems[0].examples[0].match_label = "Dust2 M2"
    assert any("漏补例子无命中依据" in issue for issue in check_page(page, load_config()))


def test_cli_prints_check_without_interrupting(capsys):
    from cs2coach.cli import _check_report_page
    page = valid_page()
    _check_report_page(page, load_config())
    assert capsys.readouterr().out == ""
    page.tiers[0].strengths[0].samples = 0
    _check_report_page(page, load_config())
    assert "报告页一致性检查：1 处" in capsys.readouterr().out


def test_all_players_script_anonymizes_and_skips_coverage(tmp_path, monkeypatch, capsys):
    from scripts import page_check_all as runner
    from tests.helpers.synthetic import A, B
    from tests.test_moments import scenario

    manifest = tmp_path / "tiers.toml"
    manifest.write_text('[[tier]]\nid = "amateur_tmp"\ndirs = []\n', encoding="utf-8")
    monkeypatch.setattr(runner, "TIER_CONFIG", manifest)
    monkeypatch.setattr(runner, "load_tiers", lambda cfg: sample())
    monkeypatch.setattr(runner, "load_baseline", lambda: {})
    monkeypatch.setattr(runner, "demo_entries", lambda tier: [(tmp_path / "test.dem", None)])
    monkeypatch.setattr(runner, "resolve_demo", lambda path, **kwargs: path)
    match = scenario()
    match.players[B].coverage = 0
    monkeypatch.setattr(runner, "parse_match", lambda *args: match)
    monkeypatch.setattr(runner, "Sight", lambda *args: SimpleNamespace(available=True))
    monkeypatch.setattr(runner, "find_moments", lambda match, sid, *args, **kwargs: make_mm(player=sid))
    monkeypatch.setattr(runner, "lobby_line", lambda match, sid, *args, **kwargs: line(steamid=sid))
    monkeypatch.setattr(runner, "detect_problems", lambda *args: report())
    seen = []
    monkeypatch.setattr(runner, "check_page", lambda page, cfg: seen.append(page) or [])
    assert runner.main() == 0 and len(seen) == 3
    assert all(len(page.tiers[0].comparisons) == 3 for page in seen)
    output = capsys.readouterr().out
    assert str(A) not in output and "报告页一致性检查：0 处违规" in output
    monkeypatch.setattr(runner, "check_page", lambda *args: ["维度冲突：amateur_tmp：trade"])
    assert runner.main() == 1
    output = capsys.readouterr().out
    assert "维度冲突：3 处" in output and "玩家01" in output
