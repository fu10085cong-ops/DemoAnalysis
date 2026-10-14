"""字段语义验证脚本：explore_demo.py 只确认字段"存在"，这里确认字段"含义"。

用法:
    python scripts/verify_semantics.py [demo路径] [--demo-member 名字]

逐项打印验证结果，用于 docs/fields.md 的结论。兼容不同来源的 demo
（Valve 匹配 demo 有 player_blind / round_end 等事件；ESL 等赛事 demo 没有，需要从 tick 字段重建）。

注意：parse_ticks 每次调用都会重新扫描整个 demo（大 demo 一次要好几秒），
所以需要的 tick 尽量合并成一次调用再按玩家和 tick 筛选。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from demoparser2 import DemoParser

from cs2coach.console import ensure_utf8_stdio
from cs2coach.parsing.loader import resolve_demo

sys.path.insert(0, str(Path(__file__).parent))
from explore_demo import find_default_demo, parse_event_df  # noqa: E402

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)

GR = "CCSGameRulesProxy.CCSGameRules."


def section(title: str) -> None:
    print(f"\n{'=' * 10} {title} {'=' * 10}")


def ticks_for(p: DemoParser, props: list[str], pairs: pd.DataFrame) -> pd.DataFrame:
    """一次性解析 pairs(steamid, tick) 涉及的所有 tick，再筛出对应玩家的行。"""
    if pairs.empty:
        return pd.DataFrame(columns=["steamid", "tick", *props])
    df = p.parse_ticks(props, ticks=sorted(set(pairs["tick"].astype(int))))
    keys = pairs[["steamid", "tick"]].astype("int64").drop_duplicates()
    return df.merge(keys, on=["steamid", "tick"])


def last_tick(p: DemoParser) -> int:
    ends = [parse_event_df(p, ev) for ev in ("round_end", "player_death", "weapon_fire")]
    return max(int(e["tick"].max()) for e in ends if len(e)) + 64 * 10


def check_rounds(p: DemoParser) -> pd.DataFrame:
    section("回合边界：事件 vs 比赛规则 tick 字段")
    listed = set(p.list_game_events())
    frames = []
    for ev in ["round_start", "round_freeze_end", "round_end", "round_officially_ended"]:
        df = parse_event_df(p, ev)
        tag = "列出" if ev in listed else ("未列出(demoparser2 合成)" if len(df) else "无")
        print(f"  {ev}: {len(df)} 条，list_game_events 中{tag}" + (f"，列 {list(df.columns)}" if len(df) else ""))
        df = df.assign(event=ev)
        frames.append(df)
    allr = pd.concat(frames).sort_values("tick")

    # 不依赖事件的回合结束：m_iRoundWinStatus 由 0 变为非 0 的 tick
    sid = int(p.parse_player_info()["steamid"].iloc[0])
    gr = p.parse_ticks([GR + "m_iRoundWinStatus", GR + "m_eRoundWinReason", "total_rounds_played"],
                       players=[sid], ticks=list(range(0, last_tick(p)))).sort_values("tick")
    st = gr[GR + "m_iRoundWinStatus"]
    derived = gr[(st != 0) & (st.shift(fill_value=0) == 0)].rename(columns=lambda c: c.replace(GR, ""))
    derived = derived[["tick", "m_iRoundWinStatus", "m_eRoundWinReason", "total_rounds_played"]]
    ev_end = allr[allr["event"] == "round_end"]
    print(f"\n由 m_iRoundWinStatus 推出的回合结束 {len(derived)} 个；round_end 事件 {len(ev_end)} 个")
    if len(ev_end):
        m = pd.merge_asof(ev_end[["tick", "winner", "reason"]].sort_values("tick"), derived.sort_values("tick"),
                          on="tick", direction="nearest", tolerance=64)
        print(m.to_string(index=False))
    else:
        print(derived.to_string(index=False))
    return allr


def check_velocity(p: DemoParser, sid: int, start: int) -> None:
    section("速度：velocity 是否依赖请求 tick 的间隔")
    props = ["X", "Y", "velocity", "velocity_X", "velocity_Y", "is_alive"]
    dense = p.parse_ticks(props, players=[sid], ticks=list(range(start, start + 64)))
    sparse = p.parse_ticks(props, players=[sid], ticks=list(range(start, start + 64, 8)))
    dense["manual_speed_per_tick_x64"] = np.hypot(dense["X"].diff(), dense["Y"].diff()) * 64
    print("连续 tick（步长 1）:")
    print(dense[["tick", "X", "Y", "velocity", "velocity_X", "velocity_Y", "manual_speed_per_tick_x64"]].head(12).to_string(index=False))
    print("\n步长 8 采样:")
    print(sparse[["tick", "velocity", "velocity_X", "velocity_Y"]].to_string(index=False))
    print(f"\n连续 tick 下 velocity 最大值: {dense['velocity'].max():.1f}")


def check_fire(p: DemoParser) -> None:
    section("开火：weapon_fire vs fire_bullets")
    wf = parse_event_df(p, "weapon_fire", player=["pitch", "yaw", "velocity_X", "velocity_Y"])
    fb = parse_event_df(p, "fire_bullets")
    print("weapon_fire 的 weapon 取值:", wf["weapon"].value_counts().to_dict())

    gun = wf[~wf["weapon"].str.contains("knife|grenade|flashbang|smoke|molotov|incgrenade|decoy|c4|bayonet", case=False, regex=True)]
    print(f"weapon_fire 总数 {len(wf)}，排除刀/道具后 {len(gun)}；fire_bullets {len(fb)}")
    m = gun.merge(fb.drop(columns=["user_name"]), on=["tick", "user_steamid"], how="left", indicator=True)
    print("weapon_fire(枪械) 在同 tick 同玩家能匹配到 fire_bullets 的比例:", round((m["_merge"] == "both").mean(), 3))
    both = m[m["_merge"] == "both"]
    if len(both):
        dyaw = ((both["angles_y"] - both["user_yaw"] + 180) % 360) - 180
        dpitch = both["angles_x"] - both["user_pitch"]
        print(f"fire_bullets.angles vs tick pitch/yaw 差值: |dyaw| 中位数 {dyaw.abs().median():.3f}°, |dpitch| 中位数 {dpitch.abs().median():.3f}°")

    # 事件附带的 user_velocity_X 对应哪一段坐标差分：(X[t-1]-X[t-2])、(X[t]-X[t-1]) 还是 (X[t+1]-X[t])
    moving = gun[(gun["user_velocity_X"].abs() + gun["user_velocity_Y"].abs()) > 20].head(40).copy()
    moving["steamid"] = moving["user_steamid"].astype("int64")
    pairs = pd.concat([moving.assign(tick=moving["tick"] + d) for d in (-2, -1, 0, 1)])[["steamid", "tick"]]
    pos = ticks_for(p, ["X"], pairs).sort_values(["steamid", "tick"])
    g = pos.groupby("steamid")
    pos["d_t"] = g["X"].diff() * 64
    pos.loc[g["tick"].diff() != 1, "d_t"] = np.nan
    pos["d_prev"] = pos.groupby("steamid")["d_t"].shift(1)
    pos["d_next"] = pos.groupby("steamid")["d_t"].shift(-1)
    m = moving.merge(pos, on=["steamid", "tick"])
    print(f"\n事件 user_velocity_X 与坐标差分×64 的平均绝对误差（{len(m)} 个移动中开枪样本）:")
    for c, label in (("d_prev", "X[t-1]-X[t-2]"), ("d_t", "X[t]-X[t-1]"), ("d_next", "X[t+1]-X[t]")):
        print(f"  {label}: {np.nanmean((m['user_velocity_X'] - m[c]).abs()):.3f}")


def check_spotted(p: DemoParser) -> None:
    section("可见性：approximate_spotted_by 的语义")
    deaths = parse_event_df(p, "player_death")
    deaths = deaths[deaths["attacker_steamid"].notna() & (deaths["attacker_steamid"] != deaths["user_steamid"])].copy()
    deaths["steamid"] = deaths["user_steamid"].astype("int64")
    deaths["tick"] = deaths["tick"] - 1  # 死亡前一 tick
    df = ticks_for(p, ["approximate_spotted_by"], deaths[["steamid", "tick"]])
    m = deaths.merge(df, on=["steamid", "tick"])
    hit = m.apply(lambda r: str(r["attacker_steamid"]) in [str(x) for x in (r["approximate_spotted_by"] or [])], axis=1)
    print(f"击杀前一 tick，受害者的 approximate_spotted_by 包含击杀者的比例: {int(hit.sum())}/{len(m)}")


def reconstruct_blinds(p: DemoParser) -> pd.DataFrame:
    """不依赖 player_blind：在每个 flashbang_detonate tick，flash_duration 相对上一 tick 发生变化且 > 0 的玩家即被这颗闪致盲。"""
    det = parse_event_df(p, "flashbang_detonate")
    if det.empty:
        return pd.DataFrame(columns=["tick", "user_steamid", "attacker_steamid", "duration", "same_team"])
    det_ticks = sorted(set(det["tick"].astype(int)))
    df = p.parse_ticks(["flash_duration", "team_num"], ticks=sorted({t + d for t in det_ticks for d in (-1, 0)}))
    piv = df.pivot_table(index="steamid", columns="tick", values="flash_duration")
    team = df.set_index(["steamid", "tick"])["team_num"]
    rows = []
    for _, d in det.iterrows():
        t, thrower = int(d["tick"]), int(d["user_steamid"])
        if t not in piv or t - 1 not in piv:
            continue
        hit = piv[(piv[t] > 0) & (piv[t] != piv[t - 1])]
        rows += [{"tick": t, "user_steamid": int(sid), "attacker_steamid": thrower, "duration": float(v),
                  "same_team": team.get((sid, t)) == team.get((thrower, t))} for sid, v in hit[t].items()]
    return pd.DataFrame(rows)


def check_flash(p: DemoParser) -> None:
    section("闪光：player_blind 事件 vs 由 flash_duration 重建")
    rec = reconstruct_blinds(p)
    blind = parse_event_df(p, "player_blind")
    print(f"重建的致盲记录 {len(rec)} 条；player_blind 事件 {len(blind)} 条")
    if len(blind):
        blind = blind.assign(user_steamid=blind["user_steamid"].astype("int64"), attacker_steamid=blind["attacker_steamid"].astype("int64"))
        m = blind.merge(rec, on=["tick", "user_steamid"], how="outer", indicator=True, suffixes=("_ev", "_rec"))
        both = m[m["_merge"] == "both"]
        print("  对照:", m["_merge"].value_counts().to_dict(),
              f"投掷者一致 {int((both['attacker_steamid_ev'] == both['attacker_steamid_rec']).sum())}/{len(both)}",
              f"时长最大误差 {(both['blind_duration'] - both['duration']).abs().max():.4f}s")
    if len(rec):
        self_ = rec["user_steamid"] == rec["attacker_steamid"]
        print(f"  其中自闪 {int(self_.sum())} 条，同队(含自己) {int(rec['same_team'].sum())} 条，敌人 {int((~rec['same_team']).sum())} 条")


def check_death_inventory(p: DemoParser) -> None:
    section("死亡时的道具/装备：inventory 在死亡 tick 前后的值")
    deaths = parse_event_df(p, "player_death").head(4).copy()
    deaths["steamid"] = deaths["user_steamid"].astype("int64")
    pairs = pd.concat([deaths.assign(tick=deaths["tick"] + d) for d in (-1, 0, 1)])[["steamid", "tick"]]
    df = ticks_for(p, ["inventory", "current_equip_value", "is_alive"], pairs).sort_values(["steamid", "tick"])
    for _, d in deaths.iterrows():
        rows = df[(df["steamid"] == d["steamid"]) & df["tick"].between(d["tick"] - 1, d["tick"] + 1)]
        print(f"  {d['user_name']} 死于 tick {d['tick']}:")
        for _, r in rows.iterrows():
            print(f"     tick {r['tick']} alive={r['is_alive']} equip={r['current_equip_value']} inv={r['inventory']}")


def check_economy(p: DemoParser, rounds: pd.DataFrame) -> None:
    section("经济：冻结期结束时各经济字段")
    fe = rounds[rounds["event"] == "round_freeze_end"]["tick"].tolist()
    df = p.parse_ticks(["team_num", "balance", "start_balance", "cash_spent_this_round", "current_equip_value", "round_start_equip_value", "total_rounds_played"], ticks=fe[1:3])
    print(df.drop(columns=["steamid"]).to_string(index=False))


def check_damage(p: DemoParser) -> None:
    section("道具伤害：player_hurt.weapon 取值")
    hurt = parse_event_df(p, "player_hurt")
    print(hurt["weapon"].value_counts().to_dict())


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("demo", type=Path, nargs="?", default=None)
    ap.add_argument("--demo-member", default=None, help="压缩包内有多个 demo 时指定其一")
    args = ap.parse_args(argv)
    demo = resolve_demo(args.demo or find_default_demo(Path("data/demos")), member=args.demo_member)
    print(f"demo: {demo}")
    p = DemoParser(str(demo))

    rounds = check_rounds(p)
    fe = sorted(rounds[rounds["event"] == "round_freeze_end"]["tick"].tolist())
    sid = int(p.parse_player_info()["steamid"].iloc[0])
    check_velocity(p, sid, int(fe[1]) + 5 * 64)  # 第 2 回合冻结期结束后 5 秒
    check_fire(p)
    check_spotted(p)
    check_flash(p)
    check_death_inventory(p)
    check_economy(p, rounds)
    check_damage(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
