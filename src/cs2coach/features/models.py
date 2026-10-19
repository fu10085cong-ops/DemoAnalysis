"""特征层的输出：决定性瞬间及其指标。

这些结构会原样交给 AI（通过工具层），所以字段名要自解释，单位写在名字或注释里。
每个瞬间都有 round 和 tick，AI 的结论必须引用它们，程序会检查引用是否真实存在。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

MomentKind = Literal["kill", "death", "assist"]


class Shot(BaseModel):
    """交火中目标玩家的一发枪械射击。"""

    tick: int
    weapon: str
    speed: float | None  # 开枪时的水平速度（u/s）
    max_speed: float | None  # 该武器（开镜时按开镜速度）的最高速度；未知武器为 None
    speed_ratio: float | None  # speed / max_speed
    stopped: bool | None  # speed_ratio 低于阈值（已停稳）；未知武器为 None
    crouched: bool
    airborne: bool
    scoped: bool  # 开枪前一 tick 的开镜状态，缺数据时取开枪 tick（fields.md 陷阱 23）
    recoil_index: float | None  # 连射第几发，从 0 开始
    hit: bool  # 同一 tick 对这次交火的对手造成了伤害
    seen_opponent: bool | None = None  # 开枪前一 tick 能否看见这次交火的对手；无地图视野时为空
    # 子弹方向与"眼睛 → 对手身体中心"的夹角（度，近似，见 features/aim.py）；没判定为瞄准对手时为 None
    aim_error_deg: float | None = None


class BlindInfo(BaseModel):
    """交火窗口内某一方被闪的情况（只记录时长 ≥ min_blind_seconds 的致盲）。"""

    blinded_at_event: bool
    remaining_at_event_s: float | None
    max_duration_s: float
    thrower: int | None
    thrower_ambiguous: bool
    # 相对于目标玩家：本人、队友、本次对手、对手的其他队友；未知时留空。
    thrower_role: Literal["self", "teammate", "opponent", "enemy"] | None


class Engagement(BaseModel):
    """一次交火：从击杀 / 死亡往前，双方互相瞄准的开枪和互相伤害连续发生的那一段。

    开枪的目标按子弹方向判断（近似，命中子弹上 96%–99% 判对），所以开枪数、谁先开枪都是近似值。
    """

    start_tick: int
    end_tick: int
    duration_s: float
    shots: list[Shot]
    shots_fired: int
    shots_hit: int
    first_shot_hit: bool | None  # 没开枪时为 None
    damage_dealt: int  # 这次交火中对对手造成的伤害
    damage_taken: int  # 这次交火中受到对手的伤害
    player_first_shot_tick: int | None
    opponent_first_shot_tick: int | None
    # 正数表示目标玩家先开枪（领先的秒数），负数表示对手先开枪；有一方没开枪时为 None
    first_shot_lead_s: float | None
    # 急停统计：不含下蹲、空中的射击（Leetify 口径）
    counter_strafe_shots: int
    counter_strafe_stopped: int
    first_shot_stopped: bool | None
    first_seen_shot_stopped: bool | None = None  # 看见对手后的第一枪；没有则为空
    first_seen_shot_hit: bool | None = None
    # —— 视野（地图几何射线检测，见 features/visibility.py）——
    player_first_seen_tick: int | None = None  # 目标玩家第一次看见对手（这段连续可见期的起点）
    seen_to_death_s: float | None = None  # 仅死亡瞬间：首次看见对手到阵亡的秒数
    opponent_first_seen_tick: int | None = None  # 对手第一次看见目标玩家
    player_seen_while_blind: bool | None = None  # 几何首次可见时本人是否正被闪白；无起点时为空
    opponent_seen_while_blind: bool | None = None
    seen_first_lead_s: float | None = None  # 正数表示目标玩家先看见对手（领先的秒数）
    # Leetify 同名指标：从看见对手到第一次对他造成伤害；对手的同一指标
    time_to_damage_s: float | None = None
    opponent_time_to_damage_s: float | None = None
    # Time to Damage 拆成三段（Leetify 没有），前两段相加等于 Time to Damage：
    # 看见 → 看见后的第一枪、看见后的第一枪 → 第一次命中、第一次命中 → 击杀（只有击杀）
    seen_to_first_shot_s: float | None = None
    first_shot_to_damage_s: float | None = None
    damage_to_kill_s: float | None = None
    # 看见对手之前就朝他开的枪数（预瞄穿点、隔着掩体扫射，或上一次露头时的射击）
    shots_before_seen: int | None = None
    # 接触时刻（双方中较早看见对方的那一 tick）前一小段时间内双方的平均水平速度（u/s），用来判断谁在移动、谁在架点
    contact_tick: int | None = None
    player_speed_at_contact: float | None = None
    opponent_speed_at_contact: float | None = None
    # peek：自己移动进入对手视野而对手几乎静止（主动拉出、对方架点）；held：反过来（自己架点、对手拉出）；
    # both_moving：双方都在移动；both_still：双方都几乎静止；unclear：有一方处在减速、急停等过渡状态；
    # None：没有接触时刻。阈值见 config [moments] contact_still_speed / contact_moving_speed
    contact_type: Literal["peek", "held", "both_moving", "both_still", "unclear"] | None = None
    # Leetify 同名指标：从看见对手到第一次造成伤害，准星转过的角度（度）
    crosshair_placement_deg: float | None = None
    # 首次看见时准星与对手眼睛的偏差；pitch 正数表示准星在头下方
    crosshair_initial_deg: float | None = None
    crosshair_initial_offset_m: float | None = None
    crosshair_initial_yaw_deg: float | None = None
    crosshair_initial_pitch_deg: float | None = None
    opponent_in_sight_before_view_ticks: int | None = None  # 首次进入屏幕前连续有几何视线的 tick 数
    airborne_near_seen: bool | None = None
    # 视野指标为空的原因：no_map_data / first_hit_unseen（首次打中时没看见，之后看见）/
    # lost_sight（之前看见、锚点时视线中断）/ not_seen（时间窗口内没看见）/
    # seen_before_window（回溯 3 秒内一直可见）/ seen_since_previous_kill / seen_since_round_start / smoke / blind
    visibility_note: str | None = None
    last_seen_before_s: float | None = None  # lost_sight：最后看见到事件发生的秒数
    opponent_off_screen: bool | None = None  # not_seen：锚点附近有几何视线，但对手在屏幕外
    player_blind: BlindInfo | None = None
    opponent_blind: BlindInfo | None = None


class Context(BaseModel):
    """瞬间发生时的局势（取瞬间前一个 tick）。"""

    side: Literal["T", "CT"]
    alive_own: int  # 含目标玩家
    alive_enemy: int
    round_time_s: float  # 冻结期结束后经过的秒数
    bomb_planted: bool
    seconds_since_plant: float | None = None  # 本回合最后一次下包到这一瞬间的秒数；没下包时为空
    own_equip_value: int | None  # 购买时间结束时的装备价值
    opponent_equip_value: int | None
    own_weapon: str | None  # 手持武器（显示名）
    opponent_weapon: str | None
    own_health_before: int | None  # 交火开始前的血量
    opponent_health_before: int | None
    distance: float | None  # 击杀距离（米，事件原始值；见 docs/parsing.md）
    place: str | None
    opponent_place: str | None


class Trade(BaseModel):
    # 死亡：击杀者是否在窗口内被队友击杀
    traded: bool | None = None
    traded_by: int | None = None
    trade_delay_s: float | None = None
    nearest_teammate_distance: float | None = None  # 死亡时最近的存活队友距离（游戏单位）
    # 击杀：是否替刚被这名对手击杀的队友报仇
    is_trade_kill: bool | None = None
    avenged_teammate_count: int | None = None  # 同一击杀可能替多名队友报仇
    avenged_teammate: int | None = None
    avenge_delay_s: float | None = None


class DeathState(BaseModel):
    unused_grenades: list[str]  # 死亡时身上没用掉的投掷物（规范名，重复保留）
    shots_last_2s: int = 0  # 阵亡前配置时长内，本人朝任意目标的枪械开枪数
    thrown_grenades: list[str] = []  # 本回合阵亡 tick 之前投出的道具，按时间顺序、保留重复
    reloading_at_death: bool = False
    reload_started_s: float | None = None  # 从换弹开始到阵亡
    ammo_before_reload: int | None = None  # 换弹开始前一 tick 的弹匣剩余
    # 换弹开始前一 tick 手持武器的弹匣容量：本场所有玩家持同名武器（active_weapon_name）时
    # active_weapon_ammo 的最大值（037）；只在换弹中阵亡时计算，没有数据时为 None
    magazine_size: int | None = None
    # 武器是否来得及开枪：上一枪到阵亡的间隔小于该武器两枪的最短间隔时为 False（如 AWP 刚开过一枪还在拉栓）。
    # 没有上一枪或该武器没有配置最短间隔时为 None
    weapon_ready: bool | None = None
    last_shot_weapon: str | None = None
    last_shot_gap_s: float | None = None
    min_shot_interval_s: float | None = None
    blinded: bool  # 死亡时处于致盲中（致盲时长不低于阈值）
    blind_remaining_s: float | None
    blinded_by: int | None
    blinded_by_teammate: bool | None


class AssistInfo(BaseModel):
    flash_assist: bool
    damage_to_victim: int  # 本回合对被击杀者造成的伤害
    killer: int | None


class Moment(BaseModel):
    id: str  # 如 "R5-death"、"R5-kill2"，在一场比赛内唯一
    kind: MomentKind
    round: int
    tick: int
    player: int
    opponent: int | None  # 击杀：受害者；死亡：击杀者；助攻：受害者。bot 或世界伤害时为 None
    opponent_name: str | None
    weapon: str  # 击杀所用武器（规范名）
    headshot: bool
    through_smoke: bool
    wallbang: bool
    noscope: bool
    attacker_blind: bool  # 击杀者当时处于致盲中（事件原始字段）
    tags: list[str]
    context: Context
    engagement: Engagement | None  # 助攻、世界伤害没有交火
    trade: Trade | None
    death: DeathState | None  # 只有死亡有
    assist: AssistInfo | None  # 只有助攻有
    jump_tick: int
    jump_command: str  # CS2 控制台命令，跳到交火开始前几秒（写法待在游戏内实测）


class MissedTrade(BaseModel):
    """队友阵亡时目标玩家存活且在附近、窗口内在雷达上发现过击杀者，但没在窗口内击杀他。

    距离是直线距离；"发现"是雷达状态（约 0.5 秒更新一次，docs/fields.md §8），都只是近似。
    """

    round: int
    tick: int
    teammate: int
    killer: int
    distance: float  # 目标玩家与阵亡队友的直线距离（游戏单位）
    killer_traded_by_other: bool  # 击杀者在窗口内被别的队友击杀
    killer_in_view_s: float | None = None
    killer_first_in_view_s: float | None = None
    reloading_at_death: bool = False
    other_fight_s: float | None = None
    side: Literal["T", "CT"] | None = None  # 队友阵亡时目标玩家阵营
    alive_own: int | None = None  # 队友阵亡后，含目标玩家
    alive_enemy: int | None = None
    round_time_s: float | None = None  # 冻结期结束后已过秒数
    bomb_planted: bool | None = None


class TradeChance(MissedTrade):
    """附近队友阵亡后的补枪候选，包含自己补上的情况；是否计入由共用条件判断。"""

    traded_by_me: bool
    my_trade_tick: int | None = None


class Excluded(BaseModel):
    round: int
    tick: int
    kind: str
    reason: str


class AimStats(BaseModel):
    """整场的枪法统计，名称和口径沿用 Leetify（docs/metrics_research.md），不同之处写在每个字段后面。

    "看见敌人"一律用地图几何视野（Leetify 用自己的位置重放，方法未公开）。样本数不足时数值为 None。
    """

    time_to_damage_ms: float | None  # 中位数；≥ 1 秒的样本排除（Leetify 口径）
    time_to_damage_samples: int
    crosshair_placement_deg: float | None  # 中位数
    crosshair_placement_samples: int
    crosshair_offset_m: float | None = None  # 初始偏差中位数（米），自定义口径
    crosshair_offset_samples: int = 0
    counter_strafing_pct: float | None  # 步枪、看见敌人时、未下蹲的射击中，速度 < 34% 最高速度的比例
    counter_strafing_shots: int
    spray_accuracy_pct: float | None  # 步枪连续 3 发以上的扫射中，命中敌人的比例（只算看见敌人时）
    spray_shots: int
    spotted_accuracy_pct: float | None  # 看见敌人时的开枪命中率
    spotted_shots: int
    headshot_accuracy_pct: float | None  # 命中头部 ÷ 命中总数（枪械，不含 AWP）
    hits: int
    # 面板合并多场时使用原始样本，旧缓存缺少这些字段仍可读取。
    time_to_damage_values_ms: list[float] = []
    crosshair_offset_values_m: list[float] = []
    counter_strafing_stopped: int = 0
    spray_hits: int = 0


class LobbyLine(BaseModel):
    steamid: int
    name: str
    team: int
    is_target: bool
    kills: int
    deaths: int
    accuracy_pct: float | None
    aim: AimStats | None
    opening_kills: int
    opening_deaths: int
    trade_kills: int
    trade_kill_events: int = 0  # 一次补枪击杀只算一次，不按补回的队友人数累加
    trade_chances: int = 0
    missed_trade_chances: int = 0
    flashes_thrown: int = 0
    flash_assists: int
    utility_damage: int
    rounds_played: int = 0
    traded_deaths: int = 0
    wasted_utility_avg: float | None = None
    enemies_flashed_per_flash: float | None = None
    teammates_flashed_per_flash: float | None = None


class Highlight(BaseModel):
    """特征层高光；报告层再附上比赛标签和加载命令。"""

    round: int
    kind: str
    title: str
    time: str
    what: str
    tick: int
    size: int = 0  # 残局为对手人数，多杀为击杀数；报告层按它排序


class MatchMoments(BaseModel):
    demo: str
    map_name: str
    tick_rate: int
    player: int
    player_name: str
    rounds: int
    moments: list[Moment]
    # 队友阵亡、自己就在附近且存活、却没在窗口内补枪（不是自己的击杀 / 死亡，单独列出）
    missed_trades: list[MissedTrade]
    trade_chances: list[TradeChance] = []
    excluded: list[Excluded]  # 不参与分析的记录（接管 bot、冻结期自杀等）及原因
    aim_stats: AimStats | None = None
    lobby: list[LobbyLine] = []
    highlights: list[Highlight] = []
    # 本场所有玩家的名字（steamid → 名字，已去掉首尾空格）；多场汇总只缓存 MatchMoments，靠它显示队友名（042）
    player_names: dict[int, str] = {}
    # 视野计算用的 awpy-data 版本；为 None 时视野相关指标全部为空，原因见 visibility_unavailable
    visibility_data_version: str | None = None
    visibility_unavailable: str | None = None
    # 目标玩家不在场的回合（中途掉线、重连前），不计入问题频率
    absent_rounds: list[int] = []
    panel: PanelCounts | None = None

    @property
    def rounds_played(self) -> int:
        """目标玩家在场的回合数。"""
        return self.rounds - len(self.absent_rounds)


from cs2coach.features.panel import PanelCounts
MatchMoments.model_rebuild()
