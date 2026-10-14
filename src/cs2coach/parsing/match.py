"""解析一个 demo，得到后续各层共用的 MatchData。

设计取舍：整场比赛的逐 tick 玩家状态一次性读入内存（ESL inferno 约 170k tick × 10 人，耗时约 3 秒、
内存约 200 MB）。这样速度可以自己做差分（陷阱 3），任意瞬间前后的窗口也不必再扫描 demo（陷阱 8）。

表的约定：
- steamid 一律是 int64 / Int64（陷阱 5）
- 事件表都有 round 列；不属于任何回合的事件（热身、比赛开始前）直接丢弃
- 玩家的阵营、坐标等一律取事件发生那一 tick 的值（陷阱 10）
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from demoparser2 import DemoParser

from cs2coach.parsing.events import event_df, to_steamid
from cs2coach.parsing.weapons import INVENTORY_GRENADES, fix_slot_sibling, is_grenade, is_gun, normalize_weapon
from cs2coach.parsing.models import MatchMeta, PlayerInfo, Round, Team
from cs2coach.parsing.rounds import segment_rounds

_GR = "CCSGameRulesProxy.CCSGameRules."

# 逐 tick 的玩家字段（docs/fields.md §5 中各来源都有的字段）
PLAYER_PROPS = [
    "X", "Y", "Z", "pitch", "yaw",
    "team_num", "health", "armor_value", "is_alive",
    "is_airborne", "is_walking", "duck_amount", "is_scoped",
    "active_weapon_name", "flash_duration", "last_place_name",
    "approximate_spotted_by",
    "CCSPlayerController.m_bControllingBot",
]
# 原始路径较长的字段 → 规范化后的列名
_PLAYER_RENAME = {
    # 玩家死后接管 bot 时为 True。这期间该玩家自己的坐标停在死亡位置，击杀事件记在玩家名下，
    # 记分板却记在 bot 名下（5E 样本中核对过），所以这段时间的数据不能用来分析该玩家
    "CCSPlayerController.m_bControllingBot": "controlling_bot",
}
# 比赛规则字段（每个 tick 对所有玩家相同）→ 规范化后的列名
RULE_PROPS = {
    "game_time": "game_time",
    "total_rounds_played": "total_rounds_played",
    "is_freeze_period": "is_freeze_period",
    "is_warmup_period": "is_warmup_period",
    _GR + "m_iRoundWinStatus": "win_status",
    _GR + "m_eRoundWinReason": "win_reason",
}
_CATEGORICAL = ["active_weapon_name", "last_place_name"]
_BOOL_PROPS = ["is_alive", "is_airborne", "is_walking", "is_scoped", "controlling_bot"]


class DemoParseError(Exception):
    pass


@dataclass
class MatchData:
    demo_path: Path
    meta: MatchMeta
    players: dict[int, PlayerInfo]
    rounds: list[Round]
    # 逐 tick 玩家状态，按 (steamid, tick) 排序。除 PLAYER_PROPS 外还有：
    #   speed_xy：水平速度（u/s），(X[t]-X[t-1], Y[t]-Y[t-1]) × tick_rate，上一 tick 缺失时为 NaN
    #   spotted_by_mask：approximate_spotted_by 的位掩码，第 i 位对应 spotted_bit(steamid) = i
    #   controlling_bot：见 _PLAYER_RENAME
    ticks: pd.DataFrame
    kills: pd.DataFrame
    damages: pd.DataFrame
    shots: pd.DataFrame  # 所有 weapon_fire（含刀、投掷物），枪械附带 fire_bullets 的字段
    blinds: pd.DataFrame  # 致盲记录：player_blind，没有时由 flash_duration 重建（docs/fields.md §2.2）
    bomb: pd.DataFrame
    grenades: pd.DataFrame = field(default_factory=pd.DataFrame)  # 投掷物爆开 / 起火 / 烟雾展开
    economy: pd.DataFrame = field(default_factory=pd.DataFrame)  # 每回合购买时间结束时每名玩家的经济与背包
    warnings: list[str] = field(default_factory=list)
    _spans: dict[int, tuple[int, int]] = field(default_factory=dict, repr=False)
    _bits: dict[int, int] = field(default_factory=dict, repr=False)
    _tick_arr: np.ndarray | None = field(default=None, repr=False)  # ticks["tick"] 的缓存，按 (steamid, tick) 排序

    def round_of(self, tick: int) -> Round | None:
        i = bisect.bisect_right([r.start_tick for r in self.rounds], tick) - 1
        if i >= 0 and self.rounds[i].contains(tick):
            return self.rounds[i]
        return None

    def round(self, number: int) -> Round | None:
        return next((r for r in self.rounds if r.number == number), None)

    def _range(self, steamid: int, start: int | None, end: int | None) -> tuple[int, int]:
        """steamid 在 [start, end] 内的行号范围（ticks 表中的位置）。"""
        a, b = self._spans.get(int(steamid), (0, 0))
        if self._tick_arr is None:
            self._tick_arr = self.ticks["tick"].to_numpy()
        t = self._tick_arr[a:b]
        lo = a if start is None else a + int(np.searchsorted(t, start, side="left"))
        hi = b if end is None else a + int(np.searchsorted(t, end, side="right"))
        return lo, hi

    def player_ticks(self, steamid: int, start: int | None = None, end: int | None = None) -> pd.DataFrame:
        """某个玩家在 [start, end] 内的逐 tick 状态（含两端）。"""
        lo, hi = self._range(steamid, start, end)
        return self.ticks.iloc[lo:hi]

    def state_at(self, steamid: int, tick: int) -> pd.Series | None:
        lo, hi = self._range(steamid, tick, tick)
        return self.ticks.iloc[lo] if hi > lo else None

    def spotted_bit(self, steamid: int) -> int | None:
        return self._bits.get(int(steamid))

    def name_of(self, steamid: int) -> str:
        p = self.players.get(int(steamid))
        return p.name if p else str(steamid)

    def require_player(self, steamid: int, min_coverage: float) -> PlayerInfo:
        """取目标玩家；不在比赛中或数据不完整时报错，不带着缺失的数据继续分析。"""
        p = self.players.get(int(steamid))
        if p is None:
            listing = "\n".join(f"  {sid}  {pl.name}" for sid, pl in self.players.items())
            raise DemoParseError(f"demo 中没有玩家 {steamid}。比赛中的玩家:\n{listing}")
        if p.coverage < min_coverage:
            raise DemoParseError(
                f"玩家 {p.name}（{steamid}）的数据不完整：回合进行中只有 {p.coverage:.0%} 的 tick 有坐标。"
                "如果这是完美平台的 demo，很可能是 PyPI 版 demoparser2 0.42.0 的实体编号 bug，"
                "请按 README 安装修复版（docs/fields.md 陷阱 12）"
            )
        return p


def parse_match(demo: Path, cfg: dict[str, Any]) -> MatchData:
    pcfg = cfg["parsing"]
    parser = DemoParser(str(demo))
    header = parser.parse_header()

    raw = parser.parse_ticks(PLAYER_PROPS + list(RULE_PROPS))
    missing = [c for c in [*PLAYER_PROPS, *RULE_PROPS, "tick", "steamid"] if c not in raw.columns]
    if missing:  # parse_ticks 对不存在的字段静默丢弃（陷阱 1）
        raise DemoParseError(f"demo 缺少必需的 tick 字段: {missing}")
    if raw.empty:
        raise DemoParseError("demo 中没有任何 tick 数据")

    rules = (raw[["tick", *RULE_PROPS]].drop_duplicates("tick").rename(columns=RULE_PROPS)
             .sort_values("tick").reset_index(drop=True))
    tick_rate = estimate_tick_rate(rules)
    if tick_rate not in pcfg["supported_tick_rates"]:
        raise DemoParseError(f"无法确定 tick rate（估算为 {tick_rate}）")

    rounds = segment_rounds(rules, event_df(parser, "round_freeze_end").get("tick", pd.Series(dtype=int)).tolist())
    if not rounds:
        raise DemoParseError("demo 中没有完整的回合")

    ticks = _player_ticks(raw, tick_rate)
    del raw

    info = parser.parse_player_info()
    roster = info[info["team_number"].isin([Team.T, Team.CT])]
    names = {int(s): str(n).strip() for s, n in zip(roster["steamid"], roster["name"]) if is_real_steamid(int(s))}
    ticks["spotted_by_mask"] = spotted_masks(ticks.pop("approximate_spotted_by"), spotted_bits(ticks))

    meta = MatchMeta(
        map_name=header.get("map_name", ""),
        server_name=header.get("server_name", ""),
        patch_version=header.get("patch_version", ""),
        tick_rate=tick_rate,
        last_tick=int(rules["tick"].iloc[-1]),
    )
    match = build_match(Path(demo), meta, names, rounds, ticks, pcfg["min_other_coverage"])
    match.kills = build_kills(event_df(parser, "player_death"), match)
    match.damages = build_damages(event_df(parser, "player_hurt"), match)
    match.shots = build_shots(event_df(parser, "weapon_fire"), event_df(parser, "fire_bullets"), match)
    match.blinds = build_blinds(event_df(parser, "player_blind"), event_df(parser, "flashbang_detonate"), match)
    match.bomb = build_bomb({name: event_df(parser, name) for name in _BOMB_EVENTS}, match)
    match.grenades = build_grenades({name: event_df(parser, name) for name in (*_GRENADE_START, *_GRENADE_END)}, match)

    # 购买时间结束的 tick（不依赖 buytime_ended：ESL 没有）
    buytime_ended = event_df(parser, "buytime_ended").get("tick", pd.Series(dtype=int)).astype(int).tolist()
    match.rounds = assign_buy_end(match.rounds, buytime_ended, event_df(parser, "server_cvar"),
                                  tick_rate, pcfg["default_buytime_seconds"])

    # 经济和背包只在少数 tick 需要（购买结束、死亡前一 tick），单独解析一次，不放进逐 tick 表
    snap_ticks = {r.buy_end_tick for r in match.rounds if r.buy_end_tick is not None}
    snap_ticks |= {int(t) - 1 for t in match.kills["tick"]}
    snap = pd.DataFrame()
    if snap_ticks:
        snap = parser.parse_ticks(ECON_PROPS, ticks=sorted(snap_ticks))
        missing = [c for c in ECON_PROPS if c not in snap.columns]
        if missing:
            raise DemoParseError(f"demo 缺少必需的经济字段: {missing}")
    match.economy = build_economy(snap, match)
    match.kills = add_death_inventory(match.kills, snap, match)
    return match


def build_match(demo_path: Path, meta: MatchMeta, names: dict[int, str], rounds: list[Round],
                ticks: pd.DataFrame, min_coverage: float) -> MatchData:
    """由逐 tick 表组装 MatchData（事件表为空，由调用方填充）。ticks 须按 (steamid, tick) 排序。"""
    steamids = ticks["steamid"].to_numpy()
    spans = {sid: (int(np.searchsorted(steamids, sid, "left")), int(np.searchsorted(steamids, sid, "right")))
             for sid in names}
    live = live_mask(ticks["tick"].to_numpy(), rounds)
    x = ticks["X"].to_numpy()
    players: dict[int, PlayerInfo] = {}
    warnings: list[str] = []
    for sid, name in names.items():
        a, b = spans[sid]
        lv = live[a:b]
        cov = float(np.isfinite(x[a:b][lv]).mean()) if lv.any() else 0.0
        players[sid] = PlayerInfo(steamid=sid, name=name, coverage=cov)
        if cov < min_coverage:
            warnings.append(f"玩家 {name}（{sid}）数据不完整：回合进行中只有 {cov:.0%} 的 tick 有坐标，"
                            "与其相关的指标可能不准确")
    empty = pd.DataFrame()
    return MatchData(demo_path=demo_path, meta=meta, players=players, rounds=rounds, ticks=ticks,
                     kills=empty, damages=empty, shots=empty, blinds=empty, bomb=empty,
                     warnings=warnings, _spans=spans, _bits=spotted_bits(ticks))


# ---------- 逐 tick 数据 ----------

# 个人账号 SteamID64 的下限；bot 的 steamid 是 0 或很小的数（5E 中见过替补掉线玩家的 bot，steamid 为 11）
_STEAMID64_BASE = 76561197960265728


def is_real_steamid(steamid: int) -> bool:
    return steamid > _STEAMID64_BASE


def estimate_tick_rate(rules: pd.DataFrame) -> int:
    """由相邻 tick 的 game_time 差估算（docs/fields.md 陷阱 9）。"""
    r = rules[["tick", "game_time"]].dropna()
    dt = r["game_time"].diff() / r["tick"].diff()
    dt = dt[dt > 0]
    if dt.empty:
        return 0
    return int(round(1.0 / float(dt.median())))


def _player_ticks(raw: pd.DataFrame, tick_rate: int) -> pd.DataFrame:
    df = raw[["tick", "steamid", *PLAYER_PROPS]].rename(columns=_PLAYER_RENAME)
    # 玩家掉线期间这些行的字段全部为空（5E、完美样本）。数值保留 NaN；布尔字段补 False，
    # 否则整列会变成 object，混入 None
    for c in _BOOL_PROPS:
        df[c] = df[c].fillna(False).astype(bool)
    df["steamid"] = df["steamid"].astype("int64")
    df = df[df["steamid"] != 0].sort_values(["steamid", "tick"], kind="stable").reset_index(drop=True)
    for c in _CATEGORICAL:
        df[c] = df[c].astype("category")
    df["speed_xy"] = horizontal_speed(df, tick_rate)
    return df


def horizontal_speed(df: pd.DataFrame, tick_rate: int) -> np.ndarray:
    """按 (steamid, tick) 排好序的表，计算每行相对上一 tick 的水平速度。上一 tick 不存在时为 NaN。

    不用 demoparser2 的 velocity 字段（陷阱 3）。
    """
    sid = df["steamid"].to_numpy()
    tick = df["tick"].to_numpy()
    x = df["X"].to_numpy(dtype="float64")
    y = df["Y"].to_numpy(dtype="float64")
    out = np.full(len(df), np.nan, dtype="float32")
    if len(df) > 1:
        ok = (sid[1:] == sid[:-1]) & (tick[1:] == tick[:-1] + 1)
        sp = np.hypot(x[1:] - x[:-1], y[1:] - y[:-1]) * tick_rate
        out[1:] = np.where(ok, sp, np.nan)
    return out


def spotted_bits(ticks: pd.DataFrame) -> dict[int, int]:
    """steamid → spotted_by_mask 中的位序号（按 steamid 升序编号）。"""
    return {int(sid): i for i, sid in enumerate(sorted(set(ticks["steamid"].unique().tolist())))}


def spotted_masks(col: pd.Series, bits: dict[int, int]) -> np.ndarray:
    """approximate_spotted_by（steamid 列表）→ int64 位掩码，避免逐行保存 Python 列表占用大量内存。"""
    if len(bits) > 63:
        raise DemoParseError(f"玩家数 {len(bits)} 超过位掩码上限 63")
    out = np.zeros(len(col), dtype="int64")
    for i, lst in enumerate(col):
        if lst is None or len(lst) == 0:
            continue
        m = 0
        for s in lst:
            b = bits.get(int(s))
            if b is not None:
                m |= 1 << b
        out[i] = m
    return out


def live_mask(ticks: np.ndarray, rounds: list[Round]) -> np.ndarray:
    """ticks 中处于回合进行中（冻结期结束 → 胜负判定）的位置。"""
    out = np.zeros(len(ticks), dtype=bool)
    for r in rounds:
        start = r.freeze_end_tick if r.freeze_end_tick is not None else r.start_tick
        out |= (ticks >= start) & (ticks <= r.end_tick)
    return out


def assign_rounds(ticks: pd.Series, rounds: list[Round]) -> pd.Series:
    """每个 tick 所属的回合号；不属于任何回合时为 0。"""
    starts = np.array([r.start_tick for r in rounds])
    lasts = np.array([r.last_tick for r in rounds])
    nums = np.array([r.number for r in rounds])
    t = ticks.to_numpy()
    i = np.searchsorted(starts, t, side="right") - 1
    ok = (i >= 0) & (t <= lasts[np.clip(i, 0, None)])
    return pd.Series(np.where(ok, nums[np.clip(i, 0, None)], 0), index=ticks.index, dtype="int64")


def attach_state(events: pd.DataFrame, match: MatchData, sid_col: str, prefix: str,
                 props: list[str], tick_offset: int = 0, state: pd.DataFrame | None = None) -> pd.DataFrame:
    """给事件表附加 sid_col 对应玩家在 (tick + tick_offset) 时的状态，列名加 prefix。

    state 默认取逐 tick 表；也可以传入只含部分 tick 的快照表（列：steamid、tick、props）。
    """
    if events.empty:
        return events.assign(**{prefix + p: pd.Series(dtype=object) for p in props})
    keys = pd.DataFrame({"steamid": events[sid_col].astype("Int64"),
                         "tick": events["tick"].astype("int64") + tick_offset}, index=events.index)
    src = match.ticks if state is None else state
    state = src[["steamid", "tick", *props]].astype({"steamid": "Int64", "tick": "int64"})
    m = keys.reset_index().merge(state, on=["steamid", "tick"], how="left").set_index("index")
    return events.join(m[props].rename(columns=lambda c: prefix + c))


def _finish(df: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    df = df.assign(round=assign_rounds(df["tick"], match.rounds))
    return df[df["round"] > 0].sort_values("tick", kind="stable").reset_index(drop=True)


# ---------- 事件 ----------

_KILL_FLAGS = ["headshot", "thrusmoke", "attackerblind", "noscope", "assistedflash", "attackerinair"]


def build_kills(ev: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    cols = ["tick", "attacker", "victim", "assister", "weapon", *_KILL_FLAGS, "penetrated", "hitgroup", "distance"]
    if ev.empty:
        return pd.DataFrame(columns=cols + ["round"])
    df = pd.DataFrame({
        "tick": ev["tick"].astype("int64"),
        "attacker": to_steamid(ev["attacker_steamid"]),
        "victim": to_steamid(ev["user_steamid"]),
        "assister": to_steamid(ev["assister_steamid"]) if "assister_steamid" in ev else pd.NA,
        "weapon": ev["weapon"].map(normalize_weapon),
    })
    for f in _KILL_FLAGS:  # attackerinair 只在部分来源中存在
        df[f] = ev[f].fillna(False).astype(bool) if f in ev else pd.NA
    df["penetrated"] = ev["penetrated"].fillna(0).astype(int) > 0
    df["hitgroup"] = ev["hitgroup"]
    df["distance"] = ev["distance"].astype(float)
    # 受害者是 bot 时事件里没有 steamid（5E 样本）。保留击杀者的这次击杀，但受害者未知
    df = df[df["victim"].notna() | df["attacker"].notna()]
    df["victim_unknown"] = df["victim"].isna()
    df = attach_state(df, match, "victim", "victim_", ["team_num", "X", "Y", "Z", "last_place_name"])
    df = attach_state(df, match, "attacker", "attacker_", ["team_num", "X", "Y", "Z", "last_place_name"])
    # 死亡那一 tick 的 inventory 已清空（陷阱 6）；手持武器等取 tick-1
    df = attach_state(df, match, "victim", "victim_prev_", ["active_weapon_name", "health"], tick_offset=-1)
    df = attach_state(df, match, "assister", "assister_", ["team_num"])
    df = attach_state(df, match, "attacker", "attacker_", ["controlling_bot"])
    df = attach_state(df, match, "victim", "victim_", ["controlling_bot"])
    df["by_bot_control"] = df["attacker_controlling_bot"].fillna(False).astype(bool)
    df["victim_bot_control"] = df["victim_controlling_bot"].fillna(False).astype(bool)
    df = df.drop(columns=["attacker_controlling_bot", "victim_controlling_bot"])
    # is_world：没有击杀者（摔死、被炸弹炸死，或击杀者是没有 SteamID 的 bot）
    df["is_world"] = df["attacker"].isna()
    df["is_suicide"] = (df["attacker"] == df["victim"]).fillna(False).astype(bool)
    df["is_teamkill"] = (~df["is_world"] & ~df["is_suicide"] & ~df["victim_unknown"]
                         & (df["attacker_team_num"] == df["victim_team_num"]))
    # 被敌人击杀（是否计入记分板还要看 by_bot_control：接管 bot 时的击杀记在 bot 名下）
    df["is_enemy_kill"] = (df["attacker"].notna() & ~df["victim_unknown"]
                           & (df["attacker_team_num"] != df["victim_team_num"]))
    # assister 可能是受害者的队友（打过自己人），记分板不算这种助攻；敌人在队友误杀中的助攻则算。
    # 与记分板 assists_total 逐条核对过（docs/fields.md §2.3）
    df["assist_counts"] = df["assister"].notna() & (df["assister_team_num"] != df["victim_team_num"])
    df = _finish(df, match)
    rounds = [match.round(r) for r in df["round"]]
    df["after_round_end"] = [t > r.end_tick for t, r in zip(df["tick"], rounds)]
    # 冻结期内的死亡只可能是自杀（控制台 kill、换队等，完美样本中见过），记分板不计
    df["in_freeze_time"] = [r.freeze_end_tick is not None and t < r.freeze_end_tick for t, r in zip(df["tick"], rounds)]
    return df


def build_damages(ev: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    cols = ["tick", "attacker", "victim", "weapon", "dmg_health", "dmg_armor", "health", "hitgroup"]
    if ev.empty:
        return pd.DataFrame(columns=cols + ["round", "dmg_effective"])
    df = pd.DataFrame({
        "tick": ev["tick"].astype("int64"),
        "attacker": to_steamid(ev["attacker_steamid"]),
        "victim": to_steamid(ev["user_steamid"]),
        "weapon": ev["weapon"].map(normalize_weapon),
        "dmg_health": ev["dmg_health"].astype(int),
        "dmg_armor": ev["dmg_armor"].astype(int),
        "health": ev["health"].astype(int),
        "hitgroup": ev["hitgroup"],
    })
    df = df[df["victim"].notna()]
    df = attach_state(df, match, "victim", "victim_", ["team_num"])
    df = attach_state(df, match, "attacker", "attacker_", ["team_num", "active_weapon_name"])
    df["weapon"] = [fix_slot_sibling(w, a) for w, a in zip(df["weapon"], df.pop("attacker_active_weapon_name"))]
    df = attach_state(df, match, "victim", "victim_prev_", ["health"], tick_offset=-1)
    df = _finish(df, match)
    df["dmg_effective"] = effective_damage(df)
    return df.drop(columns=["victim_prev_health"])


def effective_damage(df: pd.DataFrame) -> list[int]:
    """实际扣掉的血量。dmg_health 是武器的原始伤害，不受剩余血量限制（满血被 AWP 爆头记 400+）。

    同一回合内按事件顺序，用受害者上一次受伤后的剩余血量（第一次受伤取前一 tick 的血量）减去这次受伤后的血量。
    df 须已按 tick 排序，且有 round、victim、health、dmg_health、victim_prev_health 列。
    """
    prev: dict[tuple[int, int], int] = {}
    out = []
    for rnd, victim, health, dmg, before in zip(df["round"], df["victim"], df["health"], df["dmg_health"],
                                                df["victim_prev_health"]):
        key = (int(rnd), int(victim))
        start = prev.get(key)
        if start is None:
            start = int(before) if pd.notna(before) else int(health) + int(dmg)
        out.append(max(0, min(int(dmg), start - int(health))))
        prev[key] = int(health)
    return out


_BULLET_COLS = {
    "angles_x": "aim_pitch", "angles_y": "aim_yaw", "inaccuracy": "inaccuracy",
    "spread": "spread", "recoil_index": "recoil_index",
}


def build_shots(wf: pd.DataFrame, fb: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    cols = ["tick", "steamid", "weapon", "is_gun", "is_grenade", "controlling_bot", *_BULLET_COLS.values()]
    if wf.empty:
        return pd.DataFrame(columns=cols + ["round"])
    df = pd.DataFrame({
        "tick": wf["tick"].astype("int64"),
        "steamid": to_steamid(wf["user_steamid"]),
        "weapon": wf["weapon"].map(normalize_weapon),
    })
    df = df[df["steamid"].notna()]
    df["is_gun"] = df["weapon"].map(is_gun).astype(bool)
    df["is_grenade"] = df["weapon"].map(is_grenade).astype(bool)
    df = attach_state(df, match, "steamid", "", ["controlling_bot"])
    df["controlling_bot"] = df["controlling_bot"].fillna(False).astype(bool)

    if not fb.empty:
        fb = pd.DataFrame({"tick": fb["tick"].astype("int64"), "steamid": to_steamid(fb["user_steamid"]),
                           **{new: fb[old].astype(float) for old, new in _BULLET_COLS.items()}})
        # 同一 tick 同一玩家只保留一条（weapon_fire 与 fire_bullets 一一对应，见 docs/fields.md §2.1）
        fb = fb.drop_duplicates(["tick", "steamid"])
        guns = df[df["is_gun"]].reset_index().merge(fb, on=["tick", "steamid"], how="left").set_index("index")
        df = df.join(guns[list(_BULLET_COLS.values())])
    else:
        for c in _BULLET_COLS.values():
            df[c] = np.nan
    return _finish(df, match)


_BLIND_COLS = ["tick", "victim", "thrower", "duration", "thrower_ambiguous"]


def build_blinds(player_blind: pd.DataFrame, det: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    """致盲记录。demo 有 player_blind（Valve、完美、5E）时直接用它，否则（ESL）由 flash_duration 重建。

    同一 tick 爆开的两颗闪可能同时致盲同一个人，player_blind 会各记一条，重建则做不到（见 reconstruct_blinds）。
    """
    if player_blind.empty:
        return reconstruct_blinds(det, match)
    df = pd.DataFrame({
        "tick": player_blind["tick"].astype("int64"),
        "victim": to_steamid(player_blind["user_steamid"]),
        "thrower": to_steamid(player_blind["attacker_steamid"]),
        "duration": player_blind["blind_duration"].astype(float),
        "thrower_ambiguous": False,
    })
    df = df[df["victim"].notna()]
    df = attach_state(df, match, "victim", "victim_", ["team_num"])
    df = attach_state(df, match, "thrower", "thrower_", ["team_num"])
    return _finish(df, match)


def reconstruct_blinds(det: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    """在每个 flashbang_detonate 的 tick t，flash_duration[t] > 0 且与 t-1 不同的玩家就是被这颗闪致盲的人。

    已用 player_blind 在 Valve、完美、5E 上逐条验证（docs/fields.md §2.2）；ESL 没有 player_blind，只能用这个方法。

    同一 tick 有多颗闪爆开时，flash_duration 无法区分是哪一颗致盲的（受害者可能被其中一颗或几颗致盲），
    按距离判断也不可靠（完美样本中最近的那颗有一半是错的），所以投掷者留空并标记 thrower_ambiguous。
    """
    if det.empty:
        return pd.DataFrame(columns=_BLIND_COLS + ["victim_team_num", "thrower_team_num", "round"])
    det = det.assign(tick=det["tick"].astype("int64"), thrower=to_steamid(det["user_steamid"]))
    rows = []
    for t, group in det.groupby("tick", sort=True):
        for sid in match.players:
            cur = match.state_at(sid, t)
            prev = match.state_at(sid, t - 1)
            if cur is None or prev is None:
                continue
            d = float(cur["flash_duration"])
            if not (d > 0 and d != float(prev["flash_duration"])):
                continue
            thrower = group["thrower"].iloc[0] if len(group) == 1 else pd.NA
            rows.append({"tick": int(t), "victim": sid, "thrower": thrower, "duration": d,
                         "thrower_ambiguous": len(group) > 1})
    df = pd.DataFrame(rows, columns=_BLIND_COLS)
    df = df.astype({"tick": "int64", "victim": "Int64", "thrower": "Int64", "duration": float,
                    "thrower_ambiguous": bool})
    df = attach_state(df, match, "victim", "victim_", ["team_num"])
    df = attach_state(df, match, "thrower", "thrower_", ["team_num"])
    return _finish(df, match)


_BOMB_EVENTS = {"bomb_planted": "planted", "bomb_defused": "defused", "bomb_exploded": "exploded"}


def build_bomb(events: dict[str, pd.DataFrame], match: MatchData) -> pd.DataFrame:
    frames = []
    for name, kind in _BOMB_EVENTS.items():
        ev = events.get(name, pd.DataFrame())
        if ev.empty:
            continue
        frames.append(pd.DataFrame({"tick": ev["tick"].astype("int64"), "event": kind,
                                    "steamid": to_steamid(ev["user_steamid"])}))
    if not frames:
        return pd.DataFrame(columns=["tick", "event", "steamid", "round"])
    return _finish(pd.concat(frames, ignore_index=True), match)


# ---------- 投掷物 ----------

# 投掷物生效的事件 → 类型。燃烧瓶和燃烧弹的起火事件相同，统一为 inferno
_GRENADE_START = {
    "hegrenade_detonate": "hegrenade",
    "flashbang_detonate": "flashbang",
    "smokegrenade_detonate": "smokegrenade",
    "inferno_startburn": "inferno",
    "decoy_started": "decoy",
}
# 持续型投掷物的结束事件，按 entityid 与开始事件配对
_GRENADE_END = {"smokegrenade_expired": "smokegrenade", "inferno_expire": "inferno"}


def build_grenades(events: dict[str, pd.DataFrame], match: MatchData) -> pd.DataFrame:
    """投掷物生效记录，每颗一行。烟和火有 end_tick：同一 entityid 在开始之后的第一个结束事件。"""
    cols = ["tick", "end_tick", "type", "thrower", "entityid", "x", "y", "z"]
    starts = []
    for name, kind in _GRENADE_START.items():
        ev = events.get(name, pd.DataFrame())
        if ev.empty:
            continue
        starts.append(pd.DataFrame({
            "tick": ev["tick"].astype("int64"), "type": kind, "thrower": to_steamid(ev["user_steamid"]),
            "entityid": ev["entityid"].astype("int64"),
            "x": ev["x"].astype(float), "y": ev["y"].astype(float), "z": ev["z"].astype(float),
        }))
    if not starts:
        return pd.DataFrame(columns=cols + ["thrower_team_num", "round"])
    df = pd.concat(starts, ignore_index=True).sort_values("tick", kind="stable").reset_index(drop=True)

    ends: dict[tuple[str, int], list[int]] = {}
    for name, kind in _GRENADE_END.items():
        ev = events.get(name, pd.DataFrame())
        if ev.empty:
            continue
        for t, e in zip(ev["tick"], ev["entityid"]):
            ends.setdefault((kind, int(e)), []).append(int(t))
    for v in ends.values():
        v.sort()
    end_ticks = []
    for t, kind, e in zip(df["tick"], df["type"], df["entityid"]):
        cand = ends.get((kind, int(e)), [])
        i = bisect.bisect_left(cand, int(t))
        end_ticks.append(cand[i] if i < len(cand) else None)
    df["end_tick"] = pd.array(end_ticks, dtype="Int64")
    df = attach_state(df[cols], match, "thrower", "thrower_", ["team_num"])
    return _finish(df, match)


# ---------- 经济与背包 ----------

# 只在少数 tick 解析的字段（inventory 是列表，放进逐 tick 表太占内存）
ECON_PROPS = [
    "inventory", "balance", "start_balance", "cash_spent_this_round",
    "current_equip_value", "round_start_equip_value", "has_helmet", "has_defuser",
]
# buytime_ended 恰好在冻结期结束后 mp_buytime 秒再多 1 个 tick（样本中有该事件的 demo 全部如此）
_BUY_END_EXTRA_TICKS = 1


def buytime_at(cvars: pd.DataFrame, tick: int, default: float) -> float:
    """tick 时生效的 mp_buytime。

    tick 为 -1 的 cvar 是录制开始时的初始值（5E 热身时为 600），之后的修改带有实际 tick，按出现顺序取最后一个。
    """
    if cvars.empty or "name" not in cvars:
        return default
    c = cvars[(cvars["name"] == "mp_buytime") & (cvars["tick"] <= tick)]
    if c.empty:
        return default
    try:
        return float(c["value"].iloc[-1])
    except ValueError:
        return default


def assign_buy_end(rounds: list[Round], buytime_ended: list[int], cvars: pd.DataFrame,
                   tick_rate: int, default_buytime: float) -> list[Round]:
    """给每个回合填上 buy_end_tick：优先用 buytime_ended 事件，没有时按 mp_buytime 推算。"""
    ev = sorted(buytime_ended)
    out = []
    for r in rounds:
        if r.freeze_end_tick is None:
            out.append(r)
            continue
        i = bisect.bisect_right(ev, r.freeze_end_tick)
        if i < len(ev) and ev[i] <= r.end_tick:
            buy_end = ev[i]
        else:
            buy_end = compute_buy_end(r.freeze_end_tick, buytime_at(cvars, r.freeze_end_tick, default_buytime), tick_rate)
        out.append(r.model_copy(update={"buy_end_tick": min(buy_end, r.end_tick)}))
    return out


def compute_buy_end(freeze_end_tick: int, buytime_seconds: float, tick_rate: int) -> int:
    return freeze_end_tick + int(round(buytime_seconds * tick_rate)) + _BUY_END_EXTRA_TICKS


def grenades_in(inventory: object) -> list[str]:
    """inventory（显示名列表）中的投掷物，转成规范名；保留重复（两颗闪就是两个 flashbang）。"""
    if not isinstance(inventory, (list, tuple, np.ndarray)):
        return []
    return [INVENTORY_GRENADES[x] for x in inventory if x in INVENTORY_GRENADES]


def build_economy(snap: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    """每回合购买时间结束时每名玩家的经济与背包。snap 是在这些 tick 上解析的 ECON_PROPS。"""
    cols = ["round", "tick", "steamid", "team_num", "is_alive", "armor_value", *ECON_PROPS, "grenades"]
    rows = [(r.number, r.buy_end_tick) for r in match.rounds if r.buy_end_tick is not None]
    if not rows or snap.empty:
        return pd.DataFrame(columns=cols)
    keys = pd.DataFrame([(n, t, sid) for n, t in rows for sid in match.players], columns=["round", "tick", "steamid"])
    keys = keys.astype({"tick": "int64", "steamid": "int64"})
    snap = snap.assign(steamid=snap["steamid"].astype("int64"), tick=snap["tick"].astype("int64"))
    df = keys.merge(snap[["steamid", "tick", *ECON_PROPS]], on=["steamid", "tick"], how="left")
    state = match.ticks[["steamid", "tick", "team_num", "is_alive", "armor_value"]].astype({"tick": "int64"})
    df = df.merge(state, on=["steamid", "tick"], how="left")
    df["grenades"] = df["inventory"].map(grenades_in)
    return df[cols].sort_values(["round", "steamid"]).reset_index(drop=True)


def add_death_inventory(kills: pd.DataFrame, snap: pd.DataFrame, match: MatchData) -> pd.DataFrame:
    """受害者死亡前一 tick 的背包（死亡那一 tick 已清空，陷阱 6），以及其中的投掷物。"""
    if kills.empty or snap.empty:
        return kills.assign(victim_prev_inventory=pd.Series([None] * len(kills), index=kills.index, dtype=object),
                            victim_prev_grenades=[[] for _ in range(len(kills))])
    k = attach_state(kills, match, "victim", "victim_prev_", ["inventory"], tick_offset=-1, state=snap)
    k["victim_prev_grenades"] = k["victim_prev_inventory"].map(grenades_in)
    return k
