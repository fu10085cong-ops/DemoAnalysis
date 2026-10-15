"""解析层核对脚本：把 parse_match 的结果与 demo 中的独立数据来源逐项对照。

用法:
    python scripts/check_parsing.py <demo路径> [--demo-member 名字]

拿到新来源（完美、5E、FACEIT……）的 demo 时先跑一遍，全部"一致"才能放心使用。

对照项：
1. 回合：由 m_iRoundWinStatus 切分的回合 vs round_end 事件（tick、胜方）
2. 击杀 / 死亡 / 助攻：由 player_death 统计的数字 vs 记分板字段 kills_total / deaths_total / assists_total
3. 致盲：由 flash_duration 重建的记录 vs player_blind 事件（只有部分来源有；有时解析层直接用 player_blind）
4. 开枪：枪械 weapon_fire 能否都对上 fire_bullets
5. 武器名：规范化后都能认出；击杀的武器与致命一击 player_hurt 的武器一致
6. 购买结束时间：推算公式 vs buytime_ended 事件
7. 经济快照没有缺失；死亡前一 tick 手持的武器在背包里
8. 投掷物：生效次数 vs 投出次数（只做参考）
9. 每名玩家的数据完整性
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser

from cs2coach.config import load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.parsing.events import event_df
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import MatchData, buytime_at, compute_buy_end, parse_match, reconstruct_blinds
from cs2coach.parsing.weapons import is_known, weapon_family

_WINNER = {"T": 2, "CT": 3, 2: 2, 3: 3}


def verdict(ok: bool) -> str:
    return "一致" if ok else "**不一致**"


def check_rounds(m: MatchData, p: DemoParser) -> bool:
    ev = event_df(p, "round_end")
    if ev.empty:
        print(f"回合：{len(m.rounds)} 个；demo 中没有 round_end 事件，无法对照")
        return True
    ev = ev[ev["winner"].map(lambda w: w in _WINNER)]  # 热身回合的 winner 为空（陷阱 14）
    # 比赛重开（如 5E 在第一回合后重开比赛，陷阱 21）之前的回合已作废，记分板也清零，不参与对照
    first = m.rounds[0].start_tick
    voided = sorted(int(t) for t in ev["tick"] if int(t) < first)
    ev = ev[ev["tick"] >= first]
    evs = {int(t): _WINNER[w] for t, w in zip(ev["tick"], ev["winner"])}
    mine = {r.end_tick: int(r.winner) for r in m.rounds}
    ok = evs == mine
    note = f"；比赛重开前作废的回合 {len(voided)} 个（tick {', '.join(map(str, voided))}）" if voided else ""
    print(f"回合：切分 {len(mine)} 个，round_end 事件 {len(evs)} 个（不含热身）{note} → {verdict(ok)}")
    if not ok:
        for t in sorted(set(evs) | set(mine)):
            if evs.get(t) != mine.get(t):
                print(f"   tick {t}: 事件 {evs.get(t)}  切分 {mine.get(t)}")
    return ok


def check_scoreboard(m: MatchData, p: DemoParser) -> bool:
    # 取最后一个回合结束时的记分板：5E 的 demo 在比赛结束后还会继续录制（可能又有击杀）
    sb = p.parse_ticks(["kills_total", "deaths_total", "assists_total"], ticks=[m.rounds[-1].last_tick])
    if not {"kills_total", "deaths_total", "assists_total"} <= set(sb.columns):
        print("记分板：demo 中没有 kills_total 等字段，无法对照")
        return True
    sb = sb.assign(steamid=sb["steamid"].astype("int64")).set_index("steamid")
    k = m.kills
    # 接管 bot 时的击杀，记分板记在 bot 名下；击杀 bot（受害者未知）记分板照算
    real = k[(k["is_enemy_kill"] | k["victim_unknown"]) & ~k["by_bot_control"]]
    counts = pd.DataFrame({
        "kills": real.groupby("attacker").size(),
        "deaths": k[~k["in_freeze_time"]].groupby("victim").size(),
        # 冻结期内的自杀：完美会在几十秒后从记分板扣回，5E 照计（陷阱 18），两种口径都接受
        "freeze_deaths": k[k["in_freeze_time"]].groupby("victim").size(),
        "assists": k[k["assist_counts"]].groupby("assister").size(),
    })
    counts.index = counts.index.astype("int64")
    t = sb.join(counts).reindex(list(m.players)).fillna(0)
    deaths_ok = (t["deaths"] == t["deaths_total"]) | (t["deaths"] + t["freeze_deaths"] == t["deaths_total"])
    t["ok"] = ((t["kills"] == t["kills_total"]) & deaths_ok
               & (t["assists"] == t["assists_total"]))
    ok = bool(t["ok"].all())
    extra = []
    if k["by_bot_control"].any():
        extra.append(f"接管 bot 时的击杀 {int(k['by_bot_control'].sum())} 次")
    if k["in_freeze_time"].any():
        extra.append(f"冻结期内自杀 {int(k['in_freeze_time'].sum())} 次（不参与分析）")
    if k["victim_unknown"].any():
        extra.append(f"击杀 bot {int(k['victim_unknown'].sum())} 次")
    if extra:
        print(f"（{'，'.join(extra)}：已按记分板口径处理）")
    print(f"记分板：{int(t['ok'].sum())}/{len(t)} 名玩家的击杀/死亡/助攻一致 → {verdict(ok)}")
    if not ok:
        bad = t[~t["ok"]]
        for sid, r in bad.iterrows():
            print(f"   {m.name_of(sid)}: 记分板 {r.kills_total}/{r.deaths_total}/{r.assists_total}"
                  f"  解析 {int(r.kills)}/{int(r.deaths)}/{int(r.assists)}")
    return ok


def check_blinds(m: MatchData, p: DemoParser) -> bool:
    """有 player_blind 时，解析层直接用它；这里检查由 flash_duration 重建的结果（ESL 只能用重建）能否对上。"""
    rec = reconstruct_blinds(event_df(p, "flashbang_detonate"), m)
    amb = rec[rec["thrower_ambiguous"]]
    enemy = int((m.blinds["victim_team_num"] != m.blinds["thrower_team_num"]).sum())
    note = f"，其中 {len(amb)} 条同 tick 多颗闪、投掷者无法确定" if len(amb) else ""
    if event_df(p, "player_blind").empty:
        print(f"致盲：demo 中没有 player_blind，由 flash_duration 重建 {len(rec)} 条（敌人 {enemy}{note}），无法对照")
        return True
    ev = m.blinds  # 来自 player_blind
    keys_ev = set(zip(ev["tick"], ev["victim"]))
    keys_rec = set(zip(rec["tick"], rec["victim"]))
    throwers = ev.groupby(["tick", "victim"])["thrower"].agg(list)
    sure = rec[~rec["thrower_ambiguous"]]
    agree = sum(throwers.get((t, v)) == [th] for t, v, th in zip(sure["tick"], sure["victim"], sure["thrower"]))
    ok = keys_ev == keys_rec and agree == len(sure)
    print(f"致盲：player_blind {len(ev)} 条（敌人 {enemy}）；重建 {len(rec)} 条{note}，"
          f"受害者对上 {len(keys_ev & keys_rec)}/{len(keys_ev | keys_rec)}，投掷者一致 {agree}/{len(sure)} → {verdict(ok)}")
    return ok


def check_shots(m: MatchData) -> bool:
    guns = m.shots[m.shots["is_gun"]]
    bot = guns["controlling_bot"]
    missing = int(guns.loc[~bot, "aim_yaw"].isna().sum())
    ok = missing <= max(5, 0.01 * len(guns))
    note = f"（另有接管 bot 时开枪 {int(bot.sum())} 次，不参与分析）" if bot.any() else ""
    print(f"开枪：枪械 {len(guns)} 次，缺 fire_bullets 的 {missing} 次{note} → {verdict(ok)}")
    return ok


def check_weapons(m: MatchData) -> bool:
    """武器名都能认出；致命一击的 player_hurt 与 player_death 的武器一致（5E 的击杀武器名带皮肤前缀，陷阱 19）。"""
    names = pd.concat([m.kills["weapon"], m.damages["weapon"], m.shots["weapon"]])
    unknown = sorted({w for w in names if w and not is_known(w)})
    k = m.kills[m.kills["attacker"].notna() & m.kills["victim"].notna()]
    fatal = m.damages[m.damages["health"] == 0][["tick", "attacker", "victim", "weapon"]].drop_duplicates(["tick", "attacker", "victim"])
    j = k.merge(fatal, on=["tick", "attacker", "victim"], suffixes=("", "_hurt"))
    same = j["weapon"].map(weapon_family) == j["weapon_hurt"].map(weapon_family)
    ok = not unknown and bool(same.all())
    print(f"武器：认不出的名字 {unknown or '无'}；击杀与致命伤害的武器一致 {int(same.sum())}/{len(j)}"
          f"（另有 {len(k) - len(j)} 次击杀找不到对应的致命伤害）→ {verdict(ok)}")
    for _, r in j[~same].head(5).iterrows():
        print(f"   tick {r.tick}: 击杀 {r.weapon}  伤害 {r.weapon_hurt}")
    return ok


def check_buy_end(m: MatchData, p: DemoParser) -> bool:
    """购买结束时间：推算公式 vs buytime_ended 事件（ESL 没有这个事件，只能用推算）。"""
    ev = sorted(event_df(p, "buytime_ended").get("tick", pd.Series(dtype=int)).astype(int))
    if not ev:
        print(f"购买结束：demo 中没有 buytime_ended，按 mp_buytime 推算 {sum(r.buy_end_tick is not None for r in m.rounds)} 个回合")
        return True
    cvars = event_df(p, "server_cvar")
    default = load_config()["parsing"]["default_buytime_seconds"]
    pairs = []
    for r in m.rounds:
        if r.freeze_end_tick is None:
            continue
        hit = [t for t in ev if r.freeze_end_tick < t <= r.end_tick]
        if hit:
            pairs.append((hit[0], compute_buy_end(r.freeze_end_tick, buytime_at(cvars, r.freeze_end_tick, default), m.meta.tick_rate)))
    same = sum(a == b for a, b in pairs)
    ok = same == len(pairs)
    print(f"购买结束：buytime_ended 与推算一致 {same}/{len(pairs)} 个回合 → {verdict(ok)}")
    return ok


def check_economy(m: MatchData) -> bool:
    e = m.economy
    present = e[e["team_num"].notna()]
    missing = int(present["balance"].isna().sum())
    alive = present[present["is_alive"] == True]  # noqa: E712
    inv = alive["start_balance"] - alive["cash_spent_this_round"] == alive["balance"]
    ok = missing == 0 and len(present) > 0
    print(f"经济：{len(present)} 条快照（{e['round'].nunique()} 回合），缺数据 {missing} 条；"
          f"存活玩家 balance = start_balance - cash_spent 的占比 {inv.mean():.1%}（购买期间有击杀奖励、捡枪等，不要求 100%）→ {verdict(ok)}")
    k = m.kills[m.kills["victim"].notna()]
    held = [w in inv_ for w, inv_ in zip(k["victim_prev_active_weapon_name"], k["victim_prev_inventory"])
            if isinstance(inv_, (list, tuple)) and isinstance(w, str)]
    print(f"   死亡前一 tick 手持的武器在背包里：{sum(held)}/{len(held)}")
    return ok and sum(held) == len(held)


_THROWN_TYPE = {"hegrenade": "hegrenade", "flashbang": "flashbang", "smokegrenade": "smokegrenade",
                "molotov": "inferno", "incgrenade": "inferno", "decoy": "decoy"}


def check_grenades(m: MatchData) -> bool:
    """投掷物：生效次数 vs 投出次数（weapon_fire），烟和火能否配上结束事件。只做参考，不判定不一致。"""
    thrown = m.shots[m.shots["is_grenade"]]["weapon"].map(_THROWN_TYPE).value_counts()
    det = m.grenades["type"].value_counts()
    parts = [f"{t} {int(det.get(t, 0))}/{int(thrown.get(t, 0))}" for t in ("flashbang", "smokegrenade", "hegrenade", "inferno", "decoy")]
    lasting = m.grenades[m.grenades["type"].isin(["smokegrenade", "inferno"])]
    print(f"投掷物：生效/投出 {'，'.join(parts)}；烟和火配上结束事件 {int(lasting['end_tick'].notna().sum())}/{len(lasting)}")
    return True


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("demo", type=Path)
    ap.add_argument("--demo-member", default=None)
    args = ap.parse_args(argv)
    demo = resolve_demo(args.demo, member=args.demo_member)
    m = parse_match(demo, load_config())
    p = DemoParser(str(demo))
    print(f"demo: {demo}\n地图 {m.meta.map_name}，{m.meta.tick_rate} tick，服务器 {m.meta.server_name}\n")

    results = [check_rounds(m, p), check_scoreboard(m, p), check_blinds(m, p), check_shots(m),
               check_weapons(m), check_buy_end(m, p), check_economy(m), check_grenades(m)]
    print("\n玩家数据完整性（回合进行中有坐标的 tick 占比）：")
    for pl in m.players.values():
        print(f"   {pl.coverage:>6.1%}  {pl.name}（{pl.steamid}）")
    for w in m.warnings:
        print(f"警告: {w}")
    ok = all(results)
    print("\n结论：" + ("全部一致" if ok else "存在不一致，见上方"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
