# 解析层的输入与输出

> 供特征层开发时查阅。字段为什么这样取、各来源的差异见 `docs/fields.md`；核对方法见 `scripts/check_parsing.py`。

## 输入

```python
from cs2coach.config import load_config
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match

demo = resolve_demo(Path("xxx.zip"), member=None)   # .dem / .bz2 / .gz / .zip / .rar → 解压后的 .dem
match = parse_match(demo, load_config())             # 每张图约 10–15 秒
player = match.require_player(steamid, cfg["parsing"]["min_target_coverage"])  # 目标玩家不存在或数据不完整时报错
```

## 输出：`MatchData`（只在内存中，不写文件）

### 结构化记录（pydantic）

| 字段 | 内容 |
|---|---|
| `meta` | `map_name`、`server_name`、`patch_version`、`tick_rate`、`last_tick` |
| `players` | `{steamid: PlayerInfo}`，`PlayerInfo` 有 `name`、`coverage`（在场回合中，回合进行中有坐标的 tick 占比）、`absent_rounds`（中途掉线或重连前不在场的回合，fields.md 陷阱 24）。只含真实玩家，不含 bot |
| `rounds` | `list[Round]`，见下 |
| `warnings` | 不影响运行的问题（如某名玩家数据不完整） |

`Round`：`number`（从 1 开始）、`start_tick`（冻结期开始）、`freeze_end_tick`（回合实际开始，可能为 None）、`buy_end_tick`（购买时间结束）、`end_tick`（胜负判定）、`last_tick`（本回合最后一个 tick，含结束后的自由时间）、`winner`（`Team.T` / `Team.CT`）、`reason`。方法：`contains(tick)`、`is_live(tick)`（冻结期结束到胜负判定之间）。

### 表（pandas DataFrame）

**通用约定**
- steamid 一律是整数（`int64` 或可空的 `Int64`），缺失为 `<NA>`
- 所有事件表都有 `round`；不属于任何回合的事件（热身、比赛结束后）已丢弃
- 阵营 `team_num`：2 = T，3 = CT，**取事件发生那一 tick 的值**（中场会换边）
- 武器名一律是规范名（`ak47`、`usp_silencer`、`knife_karambit`、`hegrenade`……，见 `parsing/weapons.py`）
- 有问题的记录不删除，用标志列标出，由特征层决定是否使用

#### `ticks`：逐 tick 玩家状态
每个 tick 每名玩家一行，按 (steamid, tick) 排序。用 `player_ticks(steamid, start, end)`、`state_at(steamid, tick)` 查询，不要自己全表筛选。

| 列 | 说明 |
|---|---|
| `tick`、`steamid` | |
| `X`、`Y`、`Z`、`pitch`、`yaw` | 位置（`Z` 是脚底高度）和视角 |
| `team_num`、`health`、`armor_value`、`is_alive` | |
| `is_airborne`、`is_walking`、`duck_amount`、`is_scoped` | |
| `speed_xy` | 水平速度（u/s），由相邻 tick 的坐标差计算；上一 tick 缺失时为 NaN。**不要用 demoparser2 的 velocity** |
| `active_weapon_name` | 手持武器的显示名（`AK-47`、`USP-S`），与规范名不同 |
| `flash_duration` | 当前致盲时长 |
| `last_place_name` | 地图区域名（`BombsiteB`、`Banana`） |
| `spotted_by_mask` | 在雷达上被哪些人发现，位掩码；第 `spotted_bit(steamid)` 位。约 0.5 秒更新一次，只能做粗判断（fields.md §8） |
| `controlling_bot` | 正在接管 bot。此时这名玩家自己的坐标停在死亡位置，**不能用来分析他** |

#### `kills`：击杀 / 死亡
| 列 | 说明 |
|---|---|
| `tick`、`round`、`attacker`、`victim`、`assister`、`weapon` | `victim` 为空表示被击杀的是 bot（`victim_unknown`） |
| `headshot`、`thrusmoke`、`penetrated`、`attackerblind`、`noscope`、`assistedflash`、`attackerinair`、`hitgroup`、`distance` | 事件原始字段（`attackerinair` 部分来源没有）。`distance` 单位为米（双方坐标距离 ÷ distance ≈ 39.37，游戏单位约等于英寸） |
| `attacker_` / `victim_` + `team_num`、`X`、`Y`、`Z`、`last_place_name` | 双方在击杀 tick 的状态 |
| `assister_team_num` | |
| `victim_prev_active_weapon_name`、`victim_prev_health` | 受害者死亡前一 tick 的手持武器、血量 |
| `victim_prev_inventory`、`victim_prev_grenades` | 受害者死亡前一 tick 的背包（显示名列表）和其中的投掷物（规范名列表，重复保留） |
| `is_enemy_kill` | 被敌人击杀 |
| `is_teamkill`、`is_suicide`、`is_world` | 队友误杀、自杀、没有击杀者（摔死、炸死、被 bot 击杀） |
| `assist_counts` | 助攻是否算数（助攻者与受害者阵营不同） |
| `after_round_end` | 胜负判定之后的击杀 |
| `in_freeze_time` | 冻结期内的死亡（只可能是自杀），**不参与分析** |
| `by_bot_control`、`victim_bot_control` | 击杀者 / 受害者当时在接管 bot，**不参与对该玩家的分析** |

#### `damages`：伤害
`tick`、`round`、`attacker`、`victim`、`weapon`、`dmg_health`（武器原始伤害，不受剩余血量限制，满血被 AWP 爆头可达 400+）、`dmg_effective`（实际扣掉的血量，统计伤害时用这个）、`dmg_armor`、`health`（受伤后剩余血量，0 即致命一击）、`hitgroup`、`attacker_team_num`、`victim_team_num`。道具伤害的 `weapon` 为 `hegrenade`、`inferno`；摔伤为空字符串。5E 的原始事件把 USP-S、M4A1-S 记成同栏位的基础型号，已按攻击者手持的武器纠正（fields.md 陷阱 20）。

#### `shots`：开枪与投掷（所有 `weapon_fire`，含刀）
`tick`、`round`、`steamid`、`weapon`、`is_gun`、`is_grenade`、`controlling_bot`；枪械另有 `fire_bullets` 的字段：`aim_pitch`、`aim_yaw`（子弹实际方向，已叠加后坐力，不含随机散布）、`inaccuracy`、`spread`、`recoil_index`（连射第几发，从 0 开始）。

#### `blinds`：致盲
`tick`、`round`、`victim`、`thrower`、`duration`、`thrower_ambiguous`、`victim_team_num`、`thrower_team_num`。有 `player_blind` 的来源直接用它（同一个人可能同时被两颗闪致盲，各一行）；ESL 由 `flash_duration` 重建，同 tick 多颗闪时 `thrower` 为空、`thrower_ambiguous` 为 True。

#### `grenades`：投掷物生效
`tick`（爆开 / 起火 / 烟雾展开）、`end_tick`（烟和火的结束，可能为空）、`type`（`hegrenade`、`flashbang`、`smokegrenade`、`inferno`、`decoy`）、`thrower`、`thrower_team_num`、`entityid`、`x`、`y`、`z`、`round`。

#### `bomb`：炸弹
`tick`、`round`、`event`（`planted` / `defused` / `exploded`）、`steamid`。

#### `economy`：每回合购买时间结束时的经济
每回合每名玩家一行：`round`、`tick`（= `buy_end_tick`）、`steamid`、`team_num`、`is_alive`、`armor_value`、`inventory`、`grenades`、`balance`、`start_balance`、`cash_spent_this_round`、`current_equip_value`、`round_start_equip_value`、`has_helmet`、`has_defuser`。购买时间内已经阵亡的玩家 `inventory` 为空，花费数据仍然有效。

### 查询方法

| 方法 | 作用 |
|---|---|
| `player_ticks(steamid, start=None, end=None)` | 某名玩家在 [start, end] 的逐 tick 状态 |
| `state_at(steamid, tick)` | 某名玩家在某 tick 的状态（一行），没有时为 None |
| `round_of(tick)`、`round(number)` | 按 tick / 回合号取回合 |
| `name_of(steamid)` | 显示用的名字 |
| `spotted_bit(steamid)` | 该玩家在 `spotted_by_mask` 中的位 |
| `require_player(steamid, min_coverage)` | 取目标玩家，不存在或数据不完整时抛出 `DemoParseError` |
