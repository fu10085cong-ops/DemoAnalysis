"""按回合选确定性的高光，优先残局获胜，其次多杀、首杀赢下、闪光助攻。"""

from collections import Counter, defaultdict

from cs2coach.features.labels import weapon_label
from cs2coach.features.models import Highlight, MatchMoments
from cs2coach.parsing.models import Round, Team


def round_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def find_highlights(mm: MatchMoments, rounds: list[Round]) -> list[Highlight]:
    """胜负按相关瞬间的阵营判断，换边后仍准确。"""
    grouped = defaultdict(list)
    for moment in sorted(mm.moments, key=lambda m: m.tick):
        if not {"teamkill", "after_round_end", "victim_is_bot"}.intersection(moment.tags):
            grouped[moment.round].append(moment)
    winners = {r.number: r.winner for r in rounds}
    result = []
    for number, moments in sorted(grouped.items()):
        kills = [m for m in moments if m.kind == "kill"]
        clutch = next(((m, int(tag.removeprefix("clutch_1v")))
                       for m in kills for tag in m.tags
                       if tag.startswith("clutch_1v") and tag.removeprefix("clutch_1v").isdigit()), None)
        opening = next((m for m in kills if "opening_kill" in m.tags), None)
        flash = next((m for m in moments if m.kind == "assist" and "flash_assist" in m.tags), None)

        def won(m):
            return winners.get(number) == (Team.T if m.context.side == "T" else Team.CT)

        if clutch and clutch[1] >= 2 and won(clutch[0]):
            related, enemies = clutch
            kind, title, size = "clutch", f"1v{enemies} 残局获胜", enemies
            what = f"1 打 {enemies}，击杀 {sum(m.tick >= related.tick for m in kills)} 人后赢下回合"
        elif len(kills) >= 3:
            related = kills[0]
            size = len(kills)
            kind, title = "multikill", {3: "三杀", 4: "四杀", 5: "五杀"}.get(len(kills), f"{len(kills)} 杀")
            weapons = Counter(weapon_label(m.weapon) for m in kills)
            what = "用 " + "、".join(weapons) + f" 击杀 {len(kills)} 人，其中 {sum(m.headshot for m in kills)} 次爆头"
        elif opening and won(opening):
            related = opening
            kind, title = "opening_win", "首杀并赢下"
            name = mm.player_names.get(opening.opponent, opening.opponent_name or "对手")
            what = f"首杀对手 {name}，全队赢下回合"
        elif flash:
            related = flash
            kind, title = "flash_assist", "闪光助攻"
            name = mm.player_names.get(flash.assist.killer if flash.assist else None, "队友")
            what = f"队友 {name} 在对手被闪时击杀"
        else:
            continue
        if kind not in ("clutch", "multikill"):
            size = 0
        result.append(Highlight(round=number, kind=kind, title=f"R{number} {title}",
            time=round_time(related.context.round_time_s), what=what, tick=related.jump_tick, size=size))
    return result
