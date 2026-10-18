"""本场体检排名、问题选择、同场表与两版前页。"""

import pytest

from cs2coach.config import ConfigError, load_config
from cs2coach.features.models import AimStats, LobbyLine
from cs2coach.features.problems import FrequencyRow, ProblemHit, ProblemReport
from cs2coach.report.card import _marked, build_card, render_front, render_lobby_tables
from cs2coach.report.basic import render_basic_report
from cs2coach.report.render import render_diagnosis
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A


def aim(**changes):
    data = dict(time_to_damage_ms=400, time_to_damage_samples=10, crosshair_placement_deg=3.0,
                crosshair_placement_samples=10, counter_strafing_pct=75, counter_strafing_shots=10,
                spray_accuracy_pct=30, spray_shots=10, spotted_accuracy_pct=25, spotted_shots=10,
                headshot_accuracy_pct=20, hits=10)
    return AimStats(**(data | changes))


def lobby(n=10):
    return [LobbyLine(steamid=A + i, name=f"玩家{i}", team=2 if i < 5 else 3, is_target=i == 0,
                      kills=20 - i, deaths=5 + i, accuracy_pct=10 + i, aim=aim(
                          crosshair_offset_m=9 - i, spray_accuracy_pct=10 + i,
                          headshot_accuracy_pct=40 - i), opening_kills=i, opening_deaths=0,
                      trade_kills=i, flash_assists=i, utility_damage=i * 10) for i in range(n)]


def report(hits=()):
    return ProblemReport(hits=[ProblemHit(problem=p, target=t, weapon="ak47", evidence={}, thresholds={})
                               for p, t in hits], disabled=[], skipped={}, baseline_source=None)


def freq(pid, hits=1, excess=1):
    return FrequencyRow(problem=pid, label=pid, hits=hits, per_10_rounds=1,
                        pro_per_10_rounds=0, ratio=2, prominent=True, excess_hits=excess)


def test_rank_direction_ties_missing_and_config_cutoffs():
    cfg = load_config()
    lines = lobby()
    mm = make_mm(lobby=lines)
    card = build_card(mm, report(), [], cfg, None)
    by = {r.key: r for r in card.rows}
    assert (by['headshot'].rank, by['headshot'].grade) == (1, 1)
    assert (by['crosshair'].rank, by['crosshair'].grade) == (10, 5)
    lines[1].aim.headshot_accuracy_pct = 40
    assert next(r for r in build_card(mm, report(), [], cfg, None).rows if r.key == 'headshot').rank == 1
    lines[0].aim.headshot_accuracy_pct = None
    row = next(r for r in build_card(mm, report(), [], cfg, None).rows if r.key == 'headshot')
    assert row.rank is None and row.value_text is None and row.ranked_count == 9
    cfg['report']['card']['min_ranked'] = 11
    assert all(r.grade is None for r in build_card(mm, report(), [], cfg, None).rows)
    cfg['report']['card']['min_ranked'] = 1
    cfg['report']['card']['grade_cutoffs'] = [5, 10, 15, 20]
    lines[0].aim.headshot_accuracy_pct = 40
    assert next(r for r in build_card(mm, report(), [], cfg, None).rows if r.key == 'headshot').grade == 2


def test_problem_note_focus_and_fallback():
    cfg = load_config()
    mm = make_mm(lobby=lobby())
    problems = report([('spray_control', 'match'), ('poor_crosshair_placement', 'R1-death')])
    frequency = [freq('spray_control', 2, 3), freq('poor_crosshair_placement', 2, 1)]
    card = build_card(mm, problems, frequency, cfg, None)
    assert card.focus.key == 'spray'  # 同为第 10，取多出次数较多的
    assert next(r for r in card.rows if r.key == 'spray').problem_note == '但有 1 次压枪差，见下文'
    assert card.focus_drills
    mm.lobby[0].aim.crosshair_offset_m = 0.1
    assert build_card(mm, problems, frequency, cfg, None).focus.key == 'spray'
    cfg['report']['card']['min_ranked'] = 11
    assert build_card(mm, problems, frequency, cfg, None).focus.key == 'spray'
    assert build_card(mm, report(), [], cfg, None).focus is None
    cfg['report']['card']['min_ranked'] = 6
    assert build_card(mm, report([('spray_control', 'match')]), [], cfg, None).focus.key == 'spray'


def test_highlight_priority_and_none():
    cfg = load_config()
    moments = [make_moment('kill', id='R1-kill1'), make_moment('assist', id='R1-assist1')]
    mm = make_mm(moments=moments, lobby=lobby())
    problems = report([('flash_assist', 'R1-assist1'), ('won_duel_seen_first', 'R1-kill1')])
    assert '先发现' in build_card(mm, problems, [], cfg, None).highlight
    assert build_card(mm, report(), [], cfg, None).highlight is None


def test_highlight_fastest_candidate_and_ttd_display_limit():
    cfg = load_config()
    slow = make_moment('kill', id='R1-kill1', round=1, engagement={'time_to_damage_s': 1.14})
    fast = make_moment('kill', id='R12-kill1', round=12, headshot=True,
                       engagement={'time_to_damage_s': 0.125},
                       context={'place': 'ShortStairs', 'alive_own': 3, 'alive_enemy': 1})
    mm = make_mm(moments=[slow, fast], lobby=lobby())
    hits = report([('won_duel_seen_first', slow.id), ('won_duel_seen_first', fast.id)])
    highlight = build_card(mm, hits, [], cfg, None).highlight
    assert highlight.startswith('R12 在 ShortStairs 先发现')
    assert '0.13 秒' in highlight and '爆头' in highlight and '3v1' in highlight
    assert '打中并爆头击杀对手' in highlight
    assert '1.14 秒' not in highlight
    assert '秒' not in build_card(mm, report([('won_duel_seen_first', slow.id)]), [], cfg, None).highlight
    cfg['report']['card']['highlight_show_ttd_max_ms'] = 100
    assert '秒' not in build_card(mm, hits, [], cfg, None).highlight


def test_trade_highlight_chooses_fastest_delay():
    cfg = load_config()
    slow = make_moment('kill', id='R1-kill1', trade={'avenge_delay_s': 2.5})
    fast = make_moment('kill', id='R3-kill1', round=3, trade={'avenge_delay_s': 0.3})
    mm = make_mm(moments=[slow, fast], lobby=lobby())
    hits = report([('good_trade', slow.id), ('good_trade', fast.id)])
    assert build_card(mm, hits, [], cfg, None).highlight.startswith('R3 在 Mid 0.30 秒内')


def test_highlight_ttd_limit_config_validation(tmp_path):
    path = tmp_path / 'card.toml'
    path.write_text('[report.card]\nhighlight_show_ttd_max_ms = -1\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='highlight_show_ttd_max_ms'):
        load_config(path)


def test_table_markers_team_order_and_shared_front():
    cfg = load_config()
    lines = lobby()
    mm = make_mm(lobby=lines)
    table = '\n'.join(render_lobby_tables(mm, cfg))
    assert '| ▶你 | ▲20-5 | ▼10%' in table
    assert '▼9.00 米' in table  # 初始偏差越小越好
    assert table.index('玩家4') < table.index('玩家5')
    assert '玩家0' not in table
    assert table.count('| **对手** |') == 2
    assert table.split('### 枪法')[1].index('玩家4') < table.split('### 枪法')[1].index('**对手**')
    assert table.split('### 对局')[1].index('**对手**') < table.split('### 对局')[1].index('玩家5')
    front = '\n'.join(render_front(mm, report(), [], cfg, None))
    assert '## 本场体检' in front and '## 同场对比' in front
    assert '道具伤害' in front
    assert '补枪按 5 秒窗口统计' in front
    assert '{' not in front and '}' not in front


def test_tied_markers_only_when_whole_group_fits():
    cfg = load_config()
    lines = lobby()
    mm = make_mm(lobby=lines)
    for line, value in zip(lines, [2, 1, 1, 1, 1, 0, 0, 0, 0, 0]):
        line.flash_assists = value
    duel = '\n'.join(render_lobby_tables(mm, cfg)).split('### 对局')[1]
    flash_cells = [row.split('|')[4].strip() for row in duel.splitlines()
                   if (row.startswith('| 玩家') or row.startswith('| ▶你')) and '闪光助攻' not in row]
    assert flash_cells.count('▲2') == 1
    assert flash_cells.count('1') == 4
    assert flash_cells.count('0') == 5
    for line in lines:
        line.flash_assists = 1
    duel = '\n'.join(render_lobby_tables(mm, cfg)).split('### 对局')[1]
    flash_cells = [row.split('|')[4].strip() for row in duel.splitlines()
                   if (row.startswith('| 玩家') or row.startswith('| ▶你')) and '闪光助攻' not in row]
    assert flash_cells == ['1'] * 10

    for line, value in zip(lines, [100, 100, 100, 100, 90, 80, 70, 60, 50, 40]):
        line.utility_damage = value
    assert all(_marked(line, 'utility_damage', 'higher', lines, 3) == '100' for line in lines[:4])
    lines[3].utility_damage = 90
    assert all(_marked(line, 'utility_damage', 'higher', lines, 3) == '▲100' for line in lines[:3])
    for line in lines:
        line.utility_damage = 100
    assert all(_marked(line, 'utility_damage', 'higher', lines, 3) == '100' for line in lines)


def test_basic_and_ai_report_same_front():
    from tests.test_render import sample

    cfg = load_config()
    d, mm, names = sample()
    mm.lobby = lobby()
    basic = render_basic_report(mm, [], d.frequency, d.score, None, cfg, d.problems)
    ai = render_diagnosis(d, mm, names, cfg, None)
    def front(text):
        return text.split('## 本场体检', 1)[1].split('下面是详细分析', 1)[0]
    assert front(basic) == front(ai)
    assert '## 同场对比' in front(basic)
    assert '{' not in basic and '}' not in basic
    assert '{' not in ai and '}' not in ai
