"""同场数据的事件计数、有效道具伤害和目标一致性。"""

from cs2coach.config import load_config
from cs2coach.features.lobby import lobby_lines
from cs2coach.features.moments import find_moments
from cs2coach.features.visibility import Sight
from cs2coach.parsing.match import build_damages
from cs2coach.parsing.models import PlayerInfo
from tests.helpers.moments import make_mm, make_moment
from tests.helpers.synthetic import A, B, D, AlwaysVisible
from tests.test_moments import hurt, scenario


def test_lobby_event_counts_accuracy_utility_and_target_consistency():
    cfg = load_config()
    damages = [hurt(90, A, D, 300, 80, 'hegrenade'),
               hurt(95, A, D, 30, 50, 'ak47'),
               hurt(95, A, 76561198000000003, 30, 70),
               hurt(100, A, 76561198000000003, 120, 0),
               hurt(150, D, B, 100, 0), hurt(200, D, A, 100, 0)]
    match = scenario(damages=damages)
    match.ticks.loc[(match.ticks['steamid'] == B) & (match.ticks['tick'] > 250), 'team_num'] = None
    match.players[0] = PlayerInfo(steamid=0, name='bot', coverage=0)
    sight = Sight(match, cfg, checker=AlwaysVisible())
    target = find_moments(match, A, cfg, sight=sight)
    # 标签沿用 find_moments 的输出，额外模拟一次本场补枪与闪光助攻。
    target.moments[0].tags.append('trade_kill')
    target.moments.append(make_moment('assist', id='R1-assist1', tags=['flash_assist']))
    lines = lobby_lines(match, A, cfg, sight, target_moments=target)
    assert len(lines) == 4
    assert next(line for line in lines if line.steamid == B).team == 2  # 断线后仍保留起手阵营
    mine = next(line for line in lines if line.is_target)
    assert mine.aim == target.aim_stats
    assert mine.kills == sum(m.kind == 'kill' for m in target.moments)
    assert mine.deaths == sum(m.kind == 'death' for m in target.moments)
    assert mine.accuracy_pct == 100.0  # 三次枪械伤害事件 / 三枪，道具伤害不入分子
    assert mine.opening_kills == 1 and mine.opening_deaths == 0
    assert mine.trade_kills == 1 and mine.flash_assists == 1
    assert mine.utility_damage == 20  # 300 原始伤害，仅扣掉 20 血


def test_lobby_counts_avenged_teammates_for_one_kill():
    cfg = load_config()
    match = scenario()
    sight = Sight(match, cfg, checker=AlwaysVisible())
    target = make_mm([make_moment('kill', tags=['trade_kill'],
                                  trade={'is_trade_kill': True, 'avenged_teammate_count': 2})])
    lines = lobby_lines(match, A, cfg, sight, target_moments=target)
    assert next(line for line in lines if line.is_target).trade_kills == 2
