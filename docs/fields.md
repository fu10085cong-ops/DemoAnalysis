# demoparser2 字段调研

> 所有结论都来自对实际 demo 的解析，不是凭记忆写的。复现方法：
>
> ```bash
> python scripts/explore_demo.py   [demo路径] [--demo-member 名字]   # 文件头、事件及字段、tick 字段是否存在
> python scripts/verify_semantics.py [demo路径] [--demo-member 名字] # 关键字段的实际含义
> ```
>
> - 环境：demoparser2 **0.42.0**，Python 3.12
> - 样本 demo（四种来源，见 §1）：
>   - **A：Valve 匹配**：`demoparser_test_demo.dem`。de_mirage，Valve 官方服务器 SourceTV（patch 13984），64 tick，10 个回合（中途结束）
>   - **B：ESL 赛事**：ESL Pro League S24 Vitality vs MOUZ，第 1 张图 `vitality-vs-mouz-m1-cache.dem`（来自 BO5 的 .rar），de_cache，ESL Match Server（patch 14188），64 tick，**21 个回合（完整比赛）**，326 MB
> - **C：完美**：`9210110943532552076_0.dem`，服务器"完美世界竞技平台天梯服务器"（patch 14190），de_dust2，20 回合
>   - **D：5E**：`g161-20261011220029576929390_de_nuke.dem`，服务器名只有"Counter-Strike 2"（是否为 5E 由提供者告知，文件头中无法确认）（patch 14190），de_nuke，21 回合
> - 完整探测结果：`data/output/explore/<demo名>.json`

## 0. 必须知道的解析陷阱

| # | 现象 | 影响 / 对策 |
|---|------|------|
| 1 | `parse_ticks` 遇到不存在的字段名**不报错，直接不返回这一列** | 解析层必须检查返回的列是否齐全，缺列时直接报错 |
| 2 | `parse_event` 请求 demo 里不存在的事件时，**返回空 `list`，而不是 DataFrame** | 解析层统一包装成空 DataFrame |
| 3 | **demoparser2 自带的 `velocity` / `velocity_X/Y/Z` 不可靠**：<br>· 只请求 2 个 tick，或请求不连续的 tick 时，**这一列直接消失**<br>· 按步长 8 采样时数值变成 8 倍（130 → 1040）<br>· 请求 `[t-500, t-1, t]` 时，t 处的值明显错误（-7455）<br>· 连续请求时前两行为 NaN | **不用这些字段。** 特征层自己用连续 tick 的 `X/Y/Z` 做差分：`(X[t] - X[t-1]) × tick_rate` |
| 4 | **事件附带的 `user_velocity_X/Y`**（`parse_event(..., player=["velocity_X"])`）**恰好等于 `(X[t-1] - X[t-2]) × 64`**：两种 demo 各取 40 个移动中开枪的样本，误差都是 0.000；与 `X[t]-X[t-1]` 相比，平均误差约 8 u/s | 它比开枪时刻滞后 1 个 tick（约 15ms）。语义明确，可以用，但为了统一，仍以第 3 条中自己算的速度为准 |
| 5 | 事件里的 steamid（`user_steamid`、`attacker_steamid`）是**字符串**；`parse_ticks` 返回的 `steamid` 是**整数** | 解析层统一转成 int |
| 6 | 死亡那一 tick 的 `inventory` **已经是空列表**；`current_equip_value` 保持不变（两种 demo 一致） | 死亡时的道具取 `tick - 1` 的值 |
| 7 | **demoparser2 会合成缺失的回合事件**：B 类 demo 中 `round_start`、`round_end`、`round_officially_ended` 都不在 `list_game_events()` 里，但 `parse_event` 能返回数据。合成版的字段也不同：`winner`/`reason` 是**字符串**（`"CT"`、`"t_killed"`），还多了 `round` 列；A 类原生事件中这两列是**整数**（winner 2/3，reason 9/7/…） | 回合切分**不依赖事件**，改用 tick 字段（见 §3） |
| 8 | `parse_ticks` **每次调用都会重新扫描整个 demo**：A（60MB）约 0.3s，B（326MB）约几秒 | 需要的 tick 合并成一次调用，再按玩家和 tick 筛选，不要在循环里逐个调用 |
| 9 | tick rate = 64（两种 demo 都由 `game_time` 估算得出） | 时间换算用 tick / tick_rate，tick_rate 放进配置 |
| 10 | `team_num`：2 = T，3 = CT。中场会换边 | 阵营必须**按事件发生的 tick 取值**，不能用整场固定的阵营 |
| 12 | **demoparser2 0.42.0（PyPI 版）的 bug：实体编号 > 2047 的角色读不出数据（已确认并已解决）**。C（完美）中 10 人里有 2 人的角色实体字段全部为空（坐标、视角、血量、背包、`flash_duration`；`is_alive` 显示为 False），事件附带的 `user_X` 等也为空，但在 CS2 游戏中回放时他们**正常出现**（产品负责人实测）。根因：0.42.0 把实体句柄按 `& 0x7FF`（11 位）解码，而 CS2 的网络句柄有 14 位编号（`& 0x3FFF`），编号超过 2047 的角色被映射到错误的实体（2397→349、2981→933，都差 2048）。完美服务器上的实体编号普遍偏高，其他来源都在 230 以内。**武器句柄同样按 `0x7FF` 解码**，实体编号偏高时背包和手持武器也可能被读错。上游 [issue #362](https://github.com/LaihoE/demoparser/issues/362) 已在主分支修复，但截至 2026-10-14 尚未发布到 PyPI | **用 `scripts/build_demoparser.py` 从锁定的上游提交编译修复版**（demoparser `45ca85ae`；GameTracking-CS2 锁定在 `10f3693c`，因为更新的 .proto 无法编译）。回归验证：A、B（三张图）、D 的 explore/verify 结果除 `usercmd_*` 的非空率略有上升外完全一致；C 的两名玩家数据恢复。官方发布修复版后改回 PyPI 版本。解析层仍要逐个玩家检查数据完整性 |
| 13 | 完美 demo 事件里的 `user_name` 前面**多一个空格**（如 `" 某玩家"`） | 一律按 steamid 匹配，名字只用于显示（去掉首尾空格） |
| 14 | 5E 有热身回合：tick 35 的 `round_start`/`round_end`（winner/reason 为空）和 tick 163 的 `round_freeze_end` 都发生在热身期；`round_officially_ended` 有 40 条（有重复） | 回合切分要过滤 `is_warmup_period`；不依赖 `round_officially_ended` |
| 15 | **5E 的 demo 在比赛前后都有额外内容**：比赛开始前有带击杀的热身（`is_warmup_period` 为 True，记分板在第 1 回合开始时清零）；最后一回合结束后服务器还在录制，可能还有击杀，记分板会把它算进去 | 事件只保留属于某个回合的；记分板对照取**最后一回合的 last_tick**，不能取 demo 最后一个 tick |
| 16 | **玩家掉线后由 bot 顶替，队友死后可以接管这个 bot**（5E 样本）。bot 在 `parse_player_info` 里的 steamid 是很小的数（如 11），逐 tick 表里没有它的行；击杀 bot 的 `player_death` 中受害者 steamid 为空。接管期间 `CCSPlayerController.m_bControllingBot` 为 True，此时：玩家自己的坐标停在死亡位置、`is_alive` 为 False；击杀事件记在**玩家**名下，记分板却记在 **bot** 名下；这期间的 `weapon_fire` 对不上 `fire_bullets` | 名单只保留真实的 SteamID64；击杀表保留"受害者未知"的记录（`victim_unknown`）；接管 bot 期间的击杀和开枪分别标记 `by_bot_control`、`controlling_bot`，**不参与对该玩家的分析** |
| 17 | **同一 tick 可能有两颗闪同时爆开**（5E 1 次、完美 2 次）。一个人可能同时被两颗闪致盲，`player_blind` 会各记一条（时长略有不同），而 `flash_duration` 只有一个值，§2.2 的重建方法分不出是哪颗闪的。按距离取最近的那颗也不可靠：完美样本中 4 名受害者有 2 名判错（其中 1 人只被较远的那颗致盲） | **有 `player_blind` 时直接用它**（Valve、完美、5E）；只有 ESL 用重建，同 tick 多颗闪时投掷者留空并标记 `thrower_ambiguous`，宁可少算也不记错 |
| 18 | **冻结期内的自杀**（weapon 为 `world`，推测是控制台 `kill` 或换队）：完美 3 次、5E 2 次。记分板 `deaths_total` 当时都会加 1，但**完美会在约 25 秒后扣回**（仍在冻结期内），5E 不扣 | 击杀表标记 `in_freeze_time`，不参与分析；核对记分板时两种口径都接受 |
| 19 | **`player_death` 的武器名不统一**：5E 的 13 个样本中有 5 个带平台皮肤前缀（`5e_tyloo2025_ak47`、`5ExTEAMSPIRIT_awp`、`5e_fazesr2026004_knife_karambit`）；完美的 8 个样本中有 5 个带后缀（`ak47_vip`、`m4a1_silencer_txz08`、`awp_ace_vip`）。`player_hurt` 和 `weapon_fire`（去掉 `weapon_` 前缀后）的写法是干净的 | `parsing/weapons.py` 按 `_` 切开、取其中最长的已知武器名。核对：击杀的武器与致命一击 `player_hurt` 的武器逐条一致 |
| 20 | **5E 的 `player_hurt` 把 USP-S 记成 `hkp2000`、M4A1-S 记成 `m4a1`**（共用栏位的基础型号）；同一 demo 的 `player_death`、`weapon_fire` 是对的，完美的 `player_hurt` 也是对的。已用攻击者当时的 `active_weapon_name` 确认 | 伤害表的武器名是基础型号、而攻击者手持的是同栏位另一把枪时，改成手持的那把 |
| 21 | **5E 会在第一回合后重开比赛**（2026-10-14 的两个 5E 样本都是如此）：第一回合正常打完、有 round_end，随后重新进入热身、`begin_new_match` 再次触发，记分板和 `total_rounds_played` 清零 | 回合切分在回合号倒退时丢弃被重打的回合，这一回合里的击杀等事件不属于任何回合、一并丢弃；核对脚本把重开前的 round_end 单独列出，不算不一致 |
| 22 | **中途被截断的 demo 里 `parse_player_info` 是空表**：ESL Pro League S24 Falcons vs NaVi 的 Mirage 因服务器重启被拆成 p1（前 12 回合）、p2（第 13 回合起，`total_rounds_played` 接着算）。p1 没有玩家信息，逐 tick 数据完整；p2 的记分板是两段的累计值，开头 tick 4 有 10 次 world 击杀（恢复备份时的重置，落在冻结期） | 花名册为空时改用逐 tick 数据中出现在 T / CT 的玩家（`roster_names`）；这样会带上热身时进过队伍的教练等人，由覆盖率检查标出。p2 的记分板对不上属正常 |
| 23 | **开枪那一 tick 的 `is_scoped` 已经是开枪后的状态**：AWP 开枪后自动退镜，`weapon_fire` 所在 tick 的 `is_scoped` 为 False，前 1～3 个 tick 为 True（ESL Vitality vs MOUZ Dust2，40 发 AWP 全部如此，2026-10-15 核实） | 判断"开枪时是否开镜"要读开枪前一 tick（tick − 1） |
| 24 | **中途掉线 / 重连**：重连前的回合里，该玩家的行仍然存在，但 `team_num`、`health`、坐标全为空，`is_alive` 为 False；重连时有 `player_connect_full`（`player_disconnect` 不一定有）。5E Mirage（g173-n-20261014223216271995270）中两名玩家各缺 2 个回合，覆盖率因此只有 93%、94%，被当成数据不完整拒绝分析（2026-10-16） | `mark_absent_rounds`：回合进行中至少一半 tick 没有队伍、且这一回合没有任何开枪 / 伤害 / 击杀 / 死亡事件，算不在场；覆盖率只算在场回合，问题频率按在场回合数（`MatchMoments.rounds_played`）。有事件的回合不算掉线，防止把陷阱 12 的读数缺失误当成掉线 |
| 11 | `parse_grenades()` 返回所有投掷物实体的逐 tick 轨迹（A 有 51.7 万行，B 有 252 万行），其中还混有 `CWeaponGlock`、`CKnife` 等非投掷物 | MVP 不用它 |

## 1. 不同来源的差异（重要）

✓ = 原生，合 = demoparser2 合成（不在 `list_game_events()` 中），✗ = 没有

| | A：Valve | B：ESL | C：完美 | D：5E |
|---|---|---|---|---|
| `player_blind` | ✓ | ✗ | ✓ | ✓ |
| `buytime_ended` | ✓ | ✗ | ✓ | ✓ |
| `round_start` / `round_end` | ✓ | 合 | 合 | 合 |
| `round_officially_ended` | ✓ | 合 | ✓ | ✓（有重复） |
| `bomb_beginplant` / `bomb_begindefuse` | ✓ | ✗ | ✓ | ✓ |
| `item_equip`、`weapon_zoom`、`weapon_reload`、`player_footstep` | ✓ | ✗ | ✓ | ✓ |
| `player_jump` | ✓ | ✗ | ✓ | ✗ |
| `other_death` | ✓ | ✗ | ✗ | ✓ |
| `grenade_thrown` | ✗ | ✓ | ✗ | ✗ |
| tick 字段 `aim_punch_angle` | ✓ | ✗ | ✗ | ✗ |
| tick 字段 `ducked`、`m_iEquipmentValue`、`usercmd_*` | ✗ | ✓ | ✓ | ✓ |
| 用 PyPI 0.42.0 时有玩家数据整场缺失（陷阱 12，修复版已解决） | 否 | 否 | **是（2/10）** | 否 |
| 热身回合混在回合事件里（陷阱 14） | 否 | 否 | 否 | 是 |
| 击杀前一 tick 受害者被击杀者 spotted | 44/64 | 71/120 | 97/135 | 128/169 |

只在个别来源出现、与 MVP 无关的事件：`hltv_chase`、`player_sound`、`entity_killed`、`chat_message`、`decoy_*`、`player_connect*` 等，完整列表见各自的 explore JSON。

**结论：规则只能依赖所有来源都有的数据。** 致盲和回合都使用 §2、§3 中的替代方案（四种来源都已验证）。`usercmd_*` 只在部分来源中存在，不能作为依赖。

## 2. 检测项与字段对照

图例：✅ 字段存在且已验证语义 ⚠️ 存在但只是近似值，或需要变通 ❌ 缺失

### 2.1 枪法

| 检测项 | 实现方式 | 字段 | 状态 |
|---|---|---|---|
| 开枪时的移动速度（急停） | 用 `weapon_fire` 给出开枪的 tick 和玩家；在 `[t-1, t]` 这两个连续 tick 上自己算水平速度（陷阱 3、4） | `weapon_fire`；tick 字段 `X/Y` | ✅ |
|  | 辅助判断：是否在空中、是否开镜、是否下蹲、武器精度 | `is_airborne`、`is_scoped`、`duck_amount`、`fire_bullets.inaccuracy`、`fire_bullets.player_inair` | ✅ |
|  | 排除刀和投掷物 | `weapon_fire.weapon` 的取值形如 `weapon_ak47`、`weapon_knife_*`、`weapon_flashbang`…… | ✅ |
|  | `weapon_fire`(枪械) 与 `fire_bullets` 一一对应：A 为 1242/1242，B 为 2440/2444 | `fire_bullets` 带有射击角度、`inaccuracy`、`recoil_index` | ✅ |
| 首次发现敌人到首次开枪的反应时间 | 取敌人 E 的 `approximate_spotted_by` 中首次出现玩家 P 的 tick，再取 P 之后第一次开枪或第一次对 E 造成伤害的 tick | tick 字段 `approximate_spotted_by`（steamid 列表） | ⚠️ |
| 遭遇敌人时的准星偏差角 | 在 P 刚发现 E 的那一 tick，计算 P 的视线方向与"P 眼睛 → E 头部"向量之间的夹角 | `X/Y/Z`、`pitch/yaw`（两人都要）、`duck_amount` | ⚠️ |

**需要变通的地方：**
- **spotted 是雷达上的"发现"状态，不等于真实视线**，而且赛事 demo 里命中率更低（51%）。在 A 上，从被发现到第一次命中的时间差中位数为 0.27s，有一部分是负值（先命中、后被发现）。
  - 对策：反应时间作为**近似指标**。没有 spotted 记录的交火直接跳过，不做推断；报告里注明"基于雷达可见性"；样本太少时不下结论（最小样本数放进配置）。
- **准星偏差角**：`Z` 是脚底高度，眼睛高度和头部高度用配置中的常量估算（站立和下蹲之间按 `duck_amount` 插值）。没有 hitbox 数据。

### 2.2 道具

| 检测项 | 实现方式 | 字段 | 状态 |
|---|---|---|---|
| 闪光致盲敌人和队友的次数与时长 | **有 `player_blind` 时直接用它；没有时（ESL）从 tick 字段重建**（同 tick 多颗闪的情况见陷阱 17）：在每个 `flashbang_detonate` 的 tick t，`flash_duration[t] > 0` 且与 `flash_duration[t-1]` 不同的玩家，就是被这颗闪致盲的人，投掷者取 `flashbang_detonate.user_steamid`，敌我按 tick t 时的 `team_num` 判断 | `flashbang_detonate`；tick 字段 `flash_duration`、`team_num` | ✅ |
|  | **重建方法已用三种来源的 `player_blind` 验证**：A 为 72/72，D 为 84/84，没有多也没有少，投掷者全部一致，时长误差 < 0.0001s。C 为 252/252（用 PyPI 0.42.0 时只有 199 条对上，漏掉的 53 条都属于陷阱 12 中读不出数据的两名玩家；当时曾误判为"被致盲者已死亡"，已更正）。B 重建出 204 条 | `player_blind`（A、C、D 有）只作对照 | ✅ |
| 死亡时身上未用的道具 | 取 `player_death` 的 tick - 1 时的 `inventory`，筛出投掷物名称 | `inventory`（如 `['M9 Bayonet','Glock-18','Smoke Grenade']`） | ✅（取 tick-1） |
| 道具伤害 | 筛选 `player_hurt` 中 weapon 为 `hegrenade`、`inferno`、`smokegrenade` 的记录，按 tick 时的阵营区分敌我 | `player_hurt` | ✅ |
|  | `m_iUtilityDamage` 是全场累计值，与按回合汇总的 `player_hurt` 对不齐 | 只作交叉验证 | ⚠️ |

`player_hurt.weapon` 的取值：枪械名（`ak47`、`m4a1`……）、`inferno`、`hegrenade`、`smokegrenade`、`planted_c4`（B）、`''`（推测是摔伤）。

### 2.3 补枪交易

| 检测项 | 实现方式 | 字段 | 状态 |
|---|---|---|---|
| 队友阵亡后是否在短时间内完成补枪 | 对每个 `player_death`（受害者是队友，击杀者是敌人 K），检查目标玩家是否在 N 秒内击杀了 K | `player_death`(+`player=["team_num","X","Y","Z"]`) | ✅ |
| 自己阵亡后是否被队友补回 | 反过来：目标玩家被 K 击杀后，检查 N 秒内是否有队友击杀了 K | 同上 | ✅ |

`player_death` 还有 `assister_steamid`、`assistedflash`、`headshot`、`thrusmoke`、`attackerblind`、`penetrated`、`distance`（两种 demo 都有）。

**助攻的口径**：`assister_steamid` 可能是**受害者的队友**（他打过自己人），记分板 `assists_total` 不算这种助攻；而队友误杀中，**敌方**的助攻者照样算。规则是"助攻者与受害者阵营不同（按击杀 tick 取阵营）才算"。按这个规则统计，ESL 三张图 30 名玩家的击杀、死亡、助攻与记分板全部一致（`scripts/check_parsing.py`）。

### 2.4 经济

| 检测项 | 实现方式 | 字段 | 状态 |
|---|---|---|---|
| 每回合的购买情况 | 在**购买结束的 tick** 取每个玩家的数据。B 没有 `buytime_ended`，因此用 `round_freeze_end + mp_buytime × tick_rate` 推算。A 上验证：`buytime_ended` 恰好在冻结期结束后 20.02s，`mp_buytime` = 20 | `cash_spent_this_round`、`current_equip_value`、`balance`、`start_balance`、`inventory`、`armor_value`、`has_helmet`、`has_defuser`；`server_cvar`(`mp_buytime`) | ✅ |
| 死亡时的装备价值 | 取 `player_death` tick 时的 `current_equip_value`（死后保持不变） | `current_equip_value` | ✅ |
| 与队伍整体经济是否同步 | 在同一 tick 按 `team_num` 汇总全队数据，比较个人和队友的购买档位 | 同上 | ✅ |

**字段含义**：`start_balance` 是回合开始时的金钱；`balance` 是当前金钱；`cash_spent_this_round` 是本回合已经花的钱；`round_start_equip_value` 是从上一回合带过来的装备价值；`current_equip_value` 是当前装备的总价值。两种 demo 的 `server_cvar` 中都有 `mp_buytime=20`、`mp_freezetime=20`、`mp_maxrounds=24`、`mp_roundtime=1.92`。

## 3. 回合切分（两种 demo 通用）

不依赖 `round_start` / `round_end` 事件（它们在 B 中是合成的，字段类型也不同），统一改用：

| 边界 | 来源 | 验证 |
|---|---|---|
| 冻结期结束（回合实际开始） | `round_freeze_end` 事件（两种 demo 都是原生的） | A 有 9 个，B 有 21 个 |
| 回合结束 | tick 字段 `CCSGameRulesProxy.CCSGameRules.m_iRoundWinStatus` 由 0 变为非 0 的那一 tick。该 tick 上的值就是胜方（2=T，3=CT），`m_eRoundWinReason` 是结束原因 | A：10/10 与 `round_end` 的 tick、胜方、原因完全一致；B：21/21、20/20、28/28；C：20/20；D：21/21（另有 1 个热身期的 `round_end`，应过滤） |
| 回合号 | 回合结束那一 tick，`total_rounds_played` 已经加 1（等于从 1 开始的回合序号）；回合进行中等于从 0 开始的序号 | 两种 demo 一致 |
| 中场、加时 | `game_phase`：2 = 上半场，4 = 中场，3 = 下半场 | B |

`m_eRoundWinReason` 与合成事件中 reason 的对应关系（B）：1 = `bomb_exploded`，7 = `bomb_defused`，8 = `t_killed`，9 = `ct_killed`，12 = `time_ran_out`。A 中还出现了 18，含义未确认。

战术暂停会让冻结期变长（例如 B 第 5 回合约 55 秒），不影响上面的切分方法。

## 4. 缺失的字段

| 字段 | 结论 |
|---|---|
| `in_crouch`、`fall_velocity` | 两种 demo 都不返回。下蹲判断用 `duck_amount`（0–1），两种 demo 都有 |
| `aim_punch_angle` | 只有 A 有，MVP 不使用 |
| `usercmd_*` | 只有 B 有，MVP 不依赖 |
| 真实视线 / 遮挡（烟雾、墙体） | demo 里没有这类数据，需要地图几何。属于后续阶段的预留内容 |

## 5. 已验证的 tick 级玩家字段

`is_in_reload` 已在完美、5E、ESL 三种来源的 `parse_ticks` 中验证为逐 tick 布尔字段；ESL 没有 `weapon_reload` 事件，换弹状态须读取此字段。

| 分组 | 字段 |
|---|---|
| 身份/状态 | name, steamid, team_num, team_name, health, is_alive, armor_value, life_state, move_type, last_place_name, in_buy_zone |
| 坐标/视角 | X, Y, Z, pitch, yaw |
| 移动 | is_airborne, is_walking, duck_amount, is_scoped（不用 velocity*，见陷阱 3） |
| 武器 | active_weapon, active_weapon_name, active_weapon_ammo, is_in_reload, shots_fired |
| 可见性 | spotted, approximate_spotted_by（等价于原始字段 `CCSPlayerPawn.m_bSpottedByMask`，demoparser2 已转成 steamid 列表） |
| 闪光 | flash_duration, flash_max_alpha, enemies_flashed_this_round, enemies_flashed_total |
| 经济 | balance, start_balance, current_equip_value, round_start_equip_value, cash_spent_this_round, total_cash_spent, has_helmet, has_defuser |
| 背包 | inventory（名称列表）, inventory_as_ids |
| 比赛规则 | total_rounds_played, rounds_played_this_phase, is_freeze_period, is_warmup_period, is_bomb_planted, game_time, round_start_time, is_match_started, game_phase；原始路径 `CCSGameRulesProxy.CCSGameRules.m_iRoundWinStatus`、`m_eRoundWinReason` |

## 6. 事件全表

以 A 为基准；B 的情况见 §1。括号内为 A 中的出现次数。

| 事件 | 字段 |
|---|---|
| player_death (73) | assistedflash, assister_name, assister_steamid, attacker_name, attacker_steamid, attackerblind, distance, dmg_armor, dmg_health, dominated, headshot, hitgroup, noreplay, noscope, penetrated, revenge, thrusmoke, tick, user_name, user_steamid, weapon, weapon_fauxitemid, weapon_itemid, weapon_originalowner_xuid, wipe（B 还有 attackerinair） |
| player_hurt (264) | armor, attacker_name, attacker_steamid, dmg_armor, dmg_health, health, hitgroup, tick, user_name, user_steamid, weapon |
| player_blind (72，仅 A) | attacker_name, attacker_steamid, blind_duration, entityid, tick, user_name, user_steamid |
| weapon_fire (1590) | silenced, tick, user_name, user_steamid, weapon |
| fire_bullets (1242) | angles_x/y/z, attack_type, ent_origin_x/y/z, inaccuracy, item_def_index, mode, num_bullets_remaining, origin_x/y/z, player, player_inair, player_scoped, recoil_index, round, seed, sound_dsp_effect, sound_type, spread, tick, user_name, user_steamid, weapon_id |
| grenade_thrown（仅 B） | tick, user_name, user_steamid, weapon |
| flashbang_detonate / hegrenade_detonate / smokegrenade_detonate / smokegrenade_expired / inferno_startburn / inferno_expire | entityid, tick, user_name, user_steamid, x, y, z |
| item_pickup (512) | defindex, item, silent, tick, user_name, user_steamid |
| bomb_planted / bomb_defused / bomb_exploded | site, tick, user_name, user_steamid（B 还有 c4） |
| bomb_dropped / bomb_pickup | entindex / —, tick, user_name, user_steamid |
| round_freeze_end | tick |
| round_end（A 原生 / B 合成） | A：legacy, message, nomusic, player_count, reason(int), tick, winner(int)；B：reason(str), round, tick, winner(str) |
| round_start（A 原生 / B 合成） | A：fraglimit, objective, tick, timelimit；B：round, tick |
| server_cvar | name, tick, value |
| 其余事件 | 见 §1 与 explore 输出的 JSON |

**事件附加字段**：`parse_event(name, player=[...], other=[...])` 两种 demo 都可用。`player` 字段按事件中出现的每个玩家角色各生成一组带前缀的列（如 `user_X`、`attacker_X`、`assister_X`）；`other` 字段直接作为列名。注意：附加到 `round_end` 事件上的 `total_rounds_played` 显示的仍是**加 1 之前**的旧值。

## 7. 同一来源内的稳定性（ESL BO5 的三张图）

| | m1-cache | m2-inferno | m3-dust2（含加时） |
|---|---|---|---|
| 回合数 | 21 | 20 | 28 |
| tick 字段可用性 | 三张图完全一致 | | |
| 合成的回合事件 / 没有 `player_blind`、`buytime_ended` | 是 / 是 | 是 / 是 | 是 / 是 |
| 由 `m_iRoundWinStatus` 重建的回合与事件逐条一致 | 21/21 | 20/20 | 28/28（加时也一致） |
| `weapon_fire`(枪械) 能对上 `fire_bullets` | 100% | 100% | 100% |
| 事件速度 = `(X[t-1]-X[t-2])×64` 的误差 | 0.000 | 0.000 | 0.000 |
| 击杀前受害者被击杀者 spotted | 68/133（51%） | 71/120（59%） | 83/173（48%） |
| 重建的致盲：自己 / 同队（含自己） / 敌人 | 31 / 104 / 100 | 37 / 135 / 121 | 115 / 307 / 260 |
| `usercmd_*` 非空率 | 92% | 92% | 93% |

只在部分图出现的事件：`hltv_fixed`、`player_disconnect`、`round_announce_final`，都与 MVP 无关。

**致盲时长分布**：自闪和队友闪大多不到 1 秒（dust2 自闪 115 条中有 78 条 < 1s），属于擦边致盲。重建方法与 `player_blind` 的口径一致，也会把这些记录进来。因此规则层需要一个**最短致盲时长的阈值**（放进配置），否则队友闪的次数会被严重高估。

## 8. spotted 为什么不稳定（对反应时间影响很大）

两部分合计：4 个 demo、整场比赛的统计（Valve、inferno），以及 490 次击杀的拆解。

**1. 更新节奏约为 33 tick（约 0.5 秒），而不是每个 tick 都更新。**
- 所有玩家标记变化的 tick 是全局同步的，相邻两次变化的间隔基本是 33 的倍数（Valve 中 187/256 次间隔能被 33 整除；中位数间隔是 33）
- 存活时间里 mask 非空的比例很低：Valve 为 2.0%，inferno 为 0.7%。一次"被发现"通常只持续一个周期
- `spotted` 布尔值与 mask 非空 100% 一致，两者是同一个信息
- 推测这是服务器按固定周期计算雷达发现状态（这是根据数据做的推断，没有源码依据）。后果是：**持续时间短于约 0.5 秒的交火可能完全落在两次更新之间，从头到尾都没有被标记**；同时也解释了"先命中、后被发现"的负延迟

**2. 烟雾会挡住"发现"。** 按击杀类型统计，击杀前一 tick 已被发现的比例：

| 击杀类型 | 次数 | 前一 tick 已被发现 | 1 秒内曾被发现 | 3 秒内曾被发现 |
|---|---|---|---|---|
| 普通枪械 | 385 | 0.64 | 0.66 | 0.68 |
| 穿墙/穿物 | 20 | 0.50 | 0.55 | 0.55 |
| 穿烟 | 75 | 0.09 | 0.15 | 0.16 |
| 道具/炸弹 | 7 | 0.00 | 0.00 | 0.00 |

普通枪械击杀中，没被发现的约 36%，与距离、是否爆头、受害者朝向都**没有明显关系**（各组都在 0.55–0.69 之间），与第 1 点的"采样太粗"相符。

**3. ESL 比 Valve 更低的原因**：穿烟击杀的占比更高（ESL 为 13–19%，Valve 为 11%）；普通枪械击杀的发现率也更低（ESL 为 0.60–0.68，Valve 为 0.76）。推测职业比赛中的对枪更快，更容易落在更新间隔内（未验证）。

**结论**：spotted 的时间分辨率约为 0.5 秒，而常见的反应时间只有 0.2–0.3 秒，**用 spotted 计算反应时间在精度上不可行**。最多只能用于"这次交火之前是否有过信息"这类粗粒度的判断。反应时间需要换一种定义（作为下一步的设计取舍）。

## 9. 压枪（后坐力控制）数据

**`fire_bullets.angles_x/y` 是每发子弹的实际瞄准方向，已叠加后坐力，不含随机散布**（随机散布由 `seed`、`inaccuracy`、`spread` 决定）。

- **与视角和后坐力的关系**（A，1242 发）：`angles ≈ 视角(tick t) + 2.03 × aim_punch_angle(tick t)`。残差中位数 0.09°，90 分位 0.57°；不做修正时差值中位数 0.41°。改用 t-1 的视角或 punch，拟合都明显变差。剩余误差推测来自子 tick 开火时刻（未验证）
- **B 没有 `aim_punch_angle`**，可以反推：`后坐力偏移 ≈ (angles - 视角) / 2`。在 B（inferno）的步枪上，按连射发数的中位数，pitch 从第 2 发的 -0.1° 逐步增大到第 12 发的 -4.0°（枪口上跳），yaw 先右后左地漂移，符合步枪后坐力的形态
- **能否区分命中**（B inferno 步枪，2118 发）：每发子弹的实际方向与最近存活敌人的估算胸口位置之间的夹角，命中的中位数为 **1.34°**，未命中的中位数为 **7.42°**。区分度明显；但命中样本有长尾（90 分位 31°），说明"子弹与命中事件的对应"或"目标点估算"还需要再验证

可以保证准确的：每发子弹的实际方向、连射发数（`recoil_index`）、后坐力状态、开火时刻、命中部位、`inaccuracy`（移动和连射带来的扩散）。

只能近似的：每发子弹与目标身体之间的偏差（要估算眼睛、胸口和头部高度，也不知道目标是否可见），以及未命中子弹的"原本目标"是谁。

可以进一步挖掘的：B 有 `usercmd_mouse_dx/dy`（原始鼠标输入），可以直接观察下拉动作。但鼠标位移与视角变化的比例取决于玩家的灵敏度，尚未验证。

## 10. 解析层的核对结果

`parsing/match.py` 的 `parse_match` 把整场比赛的逐 tick 玩家状态一次读入内存（每张图约 10–15 秒，逐 tick 表约 120 MB；输出格式见 `docs/parsing.md`），所有事件表都按 tick 附加了阵营和回合号。`scripts/check_parsing.py` 把它的结果与 demo 中的独立来源逐项对照，拿到新来源的 demo 时先跑一遍：

| | m1-cache | m2-inferno | m3-dust2（含加时） |
|---|---|---|---|
| 回合切分 vs `round_end` | 21/21 | 20/20 | 28/28 |
| 击杀/死亡/助攻 vs 记分板 | 10/10 人 | 10/10 人 | 10/10 人 |
| 枪械 `weapon_fire` 对上 `fire_bullets` | 2440/2440 | 2729/2729 | 3071/3071 |
| 重建的致盲条数（与 §7 一致） | 204 | 256 | 567 |

**更多来源（2026-10-14）**：

| 来源 | demo 数 | 全部一致 | 说明 |
|---|---|---|---|
| 5E | 13（mirage、dust2、inferno） | 13 | 处理了陷阱 15–18 后全部一致；其中 12 个有 `player_blind`，重建的致盲与之逐条一致（同 tick 多颗闪的投掷者除外，见陷阱 17） |
| 完美 | 8（inferno、dust2、mirage、ancient；天梯和约战） | 8 | 用 PyPI 版 demoparser2 时 7 个触发陷阱 12（每场 1–9 名玩家的坐标为 0%）；换成修复版后玩家数据全部恢复。另外处理了陷阱 17、18 |
| ESL | 3 | 3 | 修改后复查，仍然全部一致 |

**第二轮补充（经济、背包、投掷物、武器名）**，24 个 demo 的结果：

| 核对项 | 结果 |
|---|---|
| 击杀的武器 vs 致命一击 `player_hurt` 的武器（规范化后，陷阱 19、20） | 全部一致；每场有 0–5 次击杀找不到对应的致命伤害（如摔死、炸弹） |
| 购买结束：推算公式 vs `buytime_ended` | 20 个有该事件的 demo 全部逐回合一致；ESL 和 1 个 5E 没有该事件，用推算 |
| 经济快照 | 没有缺失。存活玩家 `balance = start_balance - cash_spent_this_round` 的占比 82%–98%（购买期间有击杀奖励、队友给钱等，不是错误） |
| 死亡前一 tick 手持的武器在背包里 | 3691/3691 |
| 投掷物生效次数 vs 投出次数 | 闪、烟、雷基本 1:1（个别 ±1，发生在回合交界）；燃烧弹生效略少（没起火的燃烧瓶）。烟和火有 2%–8% 配不上结束事件，推测是回合结束时还在燃烧 / 未散去，被直接清除 |
| 实际扣血（`dmg_effective`）vs 记分板伤害 `m_iDamage` | 每人每场系统性地低约 1%（7–27 点）。试过"上次剩余血量""前一 tick 血量"等几种算法都对不上，原因未查明。`dmg_health` 原始值则高出 20%–50%（不受剩余血量限制），不能直接相加 |

Valve 匹配的样本不在当前机器上，尚未用解析层核对。

## 11. 局限

- 已验证 4 个 demo：1 个 Valve 匹配，3 个同一场 ESL 赛事。FACEIT 和 128 tick 的 demo 还没验证。
