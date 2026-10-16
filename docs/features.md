# 特征层：决定性瞬间与指标

> 入口：`features/moments.py` 的 `find_moments(match, steamid, cfg) -> MatchMoments`；数据结构见 `features/models.py`。
> 所有阈值在 `config/default.toml` 的 `[moments]`、`[counter_strafe]`、`[aim]`、`[aim_stats]`、`[visibility]` 段。
> 命令行：`python -m cs2coach analyze <demo> --player <steamid64>` 打印列表，并把完整结果写入 `data/output/<玩家名>_<steamid>/<比赛名>/moments.json`。
> 同时输出该目录的 `facts.md`：由 `report/facts.py` 按固定句式生成全部瞬间与漏补枪的中文事实卡，不调用 AI；缺失字段省略，投掷者未知时明确说明。击杀距离按米、队友距离按游戏单位、速度按单位/秒显示。

## 决定性瞬间

**全部列出，由 AI 判断轻重**（2026-10-14 产品负责人确定）。程序只打确定性的标签。

| 类型 | 范围 |
|---|---|
| 击杀 `kill` | 目标玩家的击杀（含误杀队友、击杀 bot，分别打标签） |
| 死亡 `death` | 目标玩家的死亡（含摔死、炸死） |
| 关键助攻 `assist` | 记分板算数的助攻中，**闪光助攻**，或本回合对被击杀者造成的实际伤害 ≥ `key_assist_min_damage` |

不参与分析、列在 `excluded` 里的：接管 bot 时的击杀 / 死亡、冻结期内的自杀、自杀。

编号 `id`：`R<回合>-kill<n>`、`R<回合>-death`、`R<回合>-assist<n>`，一场比赛内唯一。AI 只在结构化结论的 `targets` 中填写编号，程序核对存在性与对应问题的命中，引用和跳转由程序排版。

## 每个瞬间的内容

### 局势 `context`（瞬间前一 tick）
双方存活人数、回合已进行的秒数、是否已下包、双方购买结束时的装备价值、双方手持武器、交火开始前双方血量、击杀距离、双方所在区域。

### 交火 `engagement`（击杀、死亡）
**范围**（动态回溯，2026-10-14 确定）：从瞬间往前，把**双方互相瞄准的开枪**和**互相造成的伤害**串起来，相邻两次间隔不超过 `engagement_gap_seconds` 就算同一次交火，整段不超过 `engagement_max_seconds`，也不早于目标玩家本回合上一次击杀。

**开枪打向谁**（近似，`features/aim.py`）：取开枪前一 tick 的存活敌人，子弹方向与"眼睛 → 身体中心"夹角最小的那个；超过 `max_target_angle` 视为没瞄准任何人。命中了对方的那一枪一律算。用命中子弹核对，5 个样本 demo 中 96%–99% 判对。

| 指标 | 定义 | 准确度 |
|---|---|---|
| `shots_fired` / `shots_hit` / `first_shot_hit` | 这次交火中瞄准对手的开枪数、同一 tick 对对手造成伤害的开枪数、第一枪是否命中 | 命中准确；开枪数依赖目标判断（近似） |
| `damage_dealt` / `damage_taken` | 这次交火中双方实际扣掉的血量（不是武器原始伤害） | 准确（与记分板总伤害相差约 1%，原因未查明，见下） |
| `first_shot_lead_s` | 对手第一枪的 tick 减去自己第一枪的 tick（秒）；正数表示自己先开枪 | 近似（依赖目标判断） |
| 急停 `shots[].stopped` | 开枪时水平速度 < 武器最高速度 × `accurate_speed_ratio`（34%，Leetify 口径）；开镜按开镜速度，开镜状态取开枪前一 tick | 速度准确（坐标差分）；各武器最高速度已用样本核对；开镜状态见 fields.md 陷阱 23 |
| `shots[].scoped` | 开枪前一 tick 的开镜状态；前一 tick 缺数据时退回开枪 tick。速度、下蹲、空中等仍取开枪 tick | AWP 开枪后自动退镜，开枪 tick 已是退镜后的状态（fields.md 陷阱 23） |
| `counter_strafe_shots` / `counter_strafe_stopped` / `first_shot_stopped` | 急停统计，不含下蹲（`duck_amount ≥ crouch_duck_amount`）和空中的射击 | 同上 |
| `shots[].aim_error_deg` | 子弹方向与对手身体中心的夹角 | 近似（眼睛、身体高度是估计值） |

### 视野（击杀、死亡的 `engagement` 中，2026-10-14 起）
"看见"用地图几何判断（`features/visibility.py`）：awpy 的地图碰撞网格做射线检测，从眼睛到对手身上可配置的 11 个点（头、头顶、头两侧、两肩、胸口及两侧、腰、脚），任一点同时在屏幕范围内（水平 ±53°、垂直 ±37°）且没有被遮挡，就算看见。烟雾检测独立使用胸口高度。不用雷达 spotted（约 0.5 秒更新一次，`docs/fields.md` §8）。

| 指标 | 定义 | 准确度 |
|---|---|---|
| `player_first_seen_tick` / `opponent_first_seen_tick` | 双方各自这段连续可见期的起点（往前最多回溯 3 秒；中断 ≤ 2 tick 不算中断） | 4 场实测普通击杀前一 tick（允许 2 tick 误差）可见 620/621；不含烟雾、门等动态遮挡 |
| `seen_first_lead_s` | 对手看见自己的时刻减去自己看见对手的时刻；正数表示自己先看见 | 同上 |
| `time_to_damage_s`（Leetify 同名） | 看见对手 → 第一次对他造成伤害 | 同上 |
| `seen_to_first_shot_s` / `first_shot_to_damage_s` / `damage_to_kill_s` | Time to Damage 拆成三段（Leetify 没有）：看见 → 第一枪（负数表示看见前就开枪）、第一枪 → 第一次命中、第一次命中 → 击杀 | 同上 |
| `crosshair_placement_deg`（Leetify 同名） | 从看见对手到第一次造成伤害，准星转过的角度 | 视角数据准确 |
| `shots_before_seen` | 看见对手之前就朝他开的枪数（预瞄穿点、隔着掩体扫射、上一次露头时的射击）；`seen_to_first_shot_s` 只从看见后的第一枪算，前两段相加等于 Time to Damage | 同上 |
| `contact_tick` / `player_speed_at_contact` / `opponent_speed_at_contact` / `contact_type` | 接触时刻（双方中较早看见对方的 tick）前 0.25 秒内双方的平均速度；低于 40 u/s 算几乎静止，不低于 120 算在移动，中间（减速、急停中）不下结论。`peek` 自己移动、对手静止（主动拉出，对手架点）；`held` 反过来；`unclear` 有一方处在过渡状态。用来支撑"干拉""架点"这类判断 | 速度准确；阈值依据 3 个 demo、878 个样本的速度分布（两个峰 0–10 和 200–220） |
| `visibility_note` | 上述指标为空的原因：`first_hit_unseen`（首次造成伤害时看不到对手，之后看见了）、`lost_sight`（锚点前的回溯窗口看见过，之后视线中断）、`not_seen`（窗口内确实没看见）、`seen_before_window`（回溯 3 秒内一直可见，起点未知）、`smoke`、`blind`、`no_map_data` 等；`lost_sight` 记录 `last_seen_before_s`，`not_seen` 记录 `opponent_off_screen`（锚点附近有几何视线但对手在屏幕外） | — |

**烟雾**：CS2 的烟是体素，会被雷和子弹打散，无法精确建模。只用来排除："第一次看见"那一刻视线经过烟雾附近（球体近似，半径 144、展开后 1–18 秒有效）时，起点可能偏早，不计算 Time to Damage、Crosshair Placement。校准：被排除的 3%–5% 样本 Time to Damage 中位数 0.9–1.4 秒，保留样本的中位数几乎不变。

**被闪白**：几何首次可见时，观察者正在经历总时长 ≥ `moments.min_blind_seconds`、剩余时长 ≥ `visibility.blind_min_remaining_seconds` 的致盲，则这一方的“看见”不可靠。首次可见 tick 保留用于核对，`player_seen_while_blind` / `opponent_seen_while_blind` 标记是否被闪白；没有首次可见 tick 时为 None。本人被闪白时 `visibility_note=blind`，烟雾原因优先，TTD、看见到开枪、开枪到命中及准星移动均留空；对手被闪白时对手 TTD 留空。任一方被闪白都不计算谁先看见谁。整场遭遇的 TTD 和准星使用相同过滤；其他枪法统计不变。剩余阈值暂定 1 秒，考虑闪光末段逐渐消退，待数据校准。Leetify 未公开是否采用这样的致盲过滤，这是我们的额外处理。

**核对**：旧 6 点算法在 6 个 demo、736 次击杀中测得雷达 spotted 比几何“第一次看见”晚 0.23–0.28 秒（中位数），85%–88% 在 0–0.55 秒之间；这些延迟尚未按新算法重测。旧算法的 5E、完美击杀者 TTD 中位数分别为 469、516 ms，也尚未重测。新版职业整场 TTD 中位数见下方 v2 基准。

### 补枪 `trade`
- 死亡：击杀者是否在 `trade_window_seconds` 内被队友击杀（`traded`、`traded_by`、`trade_delay_s`）；死亡时最近存活队友的直线距离。
- 击杀：这名对手是否在窗口内刚击杀了自己的队友（`is_trade_kill`）。

### 死亡状态 `death`
- `unused_grenades`：死亡前一 tick 背包里的投掷物。
- `thrown_grenades`：目标玩家在本回合、阵亡 tick 之前已经投出的道具规范名，来自 `shots.is_grenade`，按时间顺序且保留重复；不含接管 bot 时的投掷。
- `blinded`：死亡时仍处于致盲中，且这次致盲时长 ≥ `min_blind_seconds`；附剩余时长、投掷者、是否队友闪的。

### 交火中的闪光 `engagement.player_blind` / `opponent_blind`

双方分别记录 `BlindInfo`：只考虑时长 ≥ `min_blind_seconds` 且致盲期间与交火窗口 `[start_tick, end_tick]` 重叠的记录；窗口前开始但窗口内仍生效的也算，窗口开始时已结束的不算。没有相关致盲时为 None。

- `blinded_at_event`、`remaining_at_event_s`：击杀 / 死亡那一 tick 是否仍被闪、剩余秒数（保留两位小数）；已恢复时剩余时间为空。
- `max_duration_s`：所有相关致盲中最长的总时长，不是窗口内的重叠时长。
- `thrower`、`thrower_ambiguous`：事件时仍被闪，选剩余时间最长的记录；都已恢复时选总时长最长的记录。投掷者不确定时为空。
- `thrower_role` 相对于目标玩家：`self` 本人，`teammate` 本人的队友，`opponent` 本次交火的对手，`enemy` 对手的其他队友；投掷者或队伍关系未知时为空。

有原生 `player_blind` 时直接使用解析结果；ESL 由 `flash_duration` 重建，同 tick 多颗闪可能无法确定投掷者（fields.md 陷阱 17）。这些字段记录窗口内被闪的事实，不据此判定致盲是死亡原因；现有 `death.blinded`、`died_blind` 口径不变。

### 跳转
`jump_command` = `demo_gototick <交火开始前 jump_lead_seconds 秒的 tick>`。**命令写法尚未在 CS2 中实测。**

## 整场枪法统计 `aim_stats`（Leetify 口径）

名称和口径沿用 Leetify（`docs/metrics_research.md`），样本数低于 `min_samples` 时数值为空。

| 指标 | 定义 | 与 Leetify 的差别 |
|---|---|---|
| `time_to_damage_ms` | 每次遭遇（同一回合对同一敌人，伤害间隔超过 3 秒算新的一次）从看见到第一次伤害，≥ 1 秒的排除，取中位数 | "看见"用地图几何；按遭遇统计 |
| `crosshair_placement_deg` | 同上的遭遇中，从看见到第一次伤害准星转过的角度，取中位数 | 同上 |
| `counter_strafing_pct` | 步枪、看见敌人时、未下蹲的射击中，速度 < 34% 武器最高速度的比例；开镜状态取开枪前一 tick，缺数据时取开枪 tick（fields.md 陷阱 23） | "看见"用地图几何 |
| `spray_accuracy_pct` | 步枪连续 3 发以上（相邻两枪间隔 ≤ 0.2 秒）的扫射中，看见敌人时开的枪命中敌人的比例 | 同上；"连续"的间隔是我们定的 |
| `spotted_accuracy_pct` | 看见敌人时开枪的命中率（同一 tick 对敌人造成伤害即命中） | 同上 |
| `headshot_accuracy_pct` | 枪械命中中命中头部的比例，不含 AWP | 无 |

步枪：AK-47、M4A4、M4A1-S、Galil、FAMAS、AUG、SG 553。

**旧算法的全量核对记录**（2026-10-15，24 场 demo 的全部 240 人次，无报错，每人六项都有数值；下表尚未按新版视野重算）。每人一场的数值，按来源取中位数（括号内为四分位 p25–p75）：

| | ESL 职业（30） | 5E（130） | 完美（80） |
|---|---|---|---|
| Time to Damage | **375 ms**（354–406） | 484 ms（453–523） | 500 ms（453–564） |
| Crosshair Placement | **3.9°**（2.8–4.6） | 4.5°（3.6–5.7） | 5.0°（3.9–6.5） |
| Spotted Accuracy | **34.3%** | 29.6% | 28.4% |
| Headshot Accuracy | **21.2%** | 18.8% | 14.8% |
| Spray Accuracy | 33.6% | 31.8% | 29.6% |
| Counter-Strafing | 81.1% | 79.2% | 80.0% |

职业选手六项都更好。Time to Damage、Crosshair Placement 区分度最明显（四分位几乎不重叠）；Counter-Strafing、Spray Accuracy 在单场层面差别很小，需要多场汇总才能看出趋势。每人每场的样本数中位数：遭遇约 20 次、看见敌人时的射击约 170 发、步枪急停射击约 70 发。

旧版 6 点算法的“约 79% 能算出视野指标、9.5% 没看见”统计已不适用于新版。018 在 4 场 demo 的 631 次阵亡（排除穿墙、穿烟）中，`not_seen` 从旧算法的 73 次降到 33 次，其中 26 次锚点附近有几何视线但对手不在屏幕内；另有 32 次为 `lost_sight`、3 次为 `first_hit_unseen`。这组数只描述该四场的阵亡样本，不当作全部决定性瞬间的覆盖率。

## 职业基准 v2

清单在 `baselines/pro_demos.toml`，生成入口为 `scripts/build_baseline.py`。使用与分析用户相同的解析、特征和致盲过滤流程；覆盖率低于 `parsing.min_target_coverage` 的玩家不入样本，某份 demo 失败时整份跳过并打印原因，来源与实际人次写入基准文件。Mirage p1/p2 是同图两段，按两份样本处理，不称为两张不同地图。

当前 v2 来自 ESL Pro League S24 的 5 组对局、15 份 demo，共 150 人次；全部成功，覆盖率为 0 的热身非选手不计入。按 018 的新视野算法重算后，整场 TTD 职业中位数为 391 ms，准星移动中位数为 3.88°；具体分布和每项有效样本数见 `baselines/pro_sample.json`。上方 30 人次表格是旧算法的早期核对记录。

- `metrics` 保留每人每份 demo 的整场枪法分布，原 `compare()` 接口兼容。
- `weapons` 按交火第一枪的武器归组；没开枪时从手持显示名反查规范名，查不到则跳过。只含击杀、死亡且有交火的数据，不含回合结束后、误杀、bot 和世界伤害；交火少于 `baseline.min_engagements`（默认 30）的武器不输出。
- 每把武器记录交火数，TTD、准星移动、距离的 p50/p75/p90 和 n；分位数使用线性插值。TTD 排除 ≥ 1 秒以及烟雾、被闪白的样本，准星排除烟雾和被闪白；首枪停稳率、命中率分别排除 None，并各自记录分母 `first_shot_stopped_n` / `first_shot_hit_n`。
- `spray` 取上述交火中的子弹，按每发实际使用的自动武器归组；`recoil_index` 从 0 开始，分段起点来自配置，默认 0、1、3、6、10、15，最后一段不封顶。每段记录 from/to、shots、hit_pct，空段命中率为 None，缺失连射序号的子弹不计入。
- `weapon_threshold()` 和 `spray_segments()` 提供读取接口，旧基准、缺失武器或指标返回 None / 空列表。
- `problem_rates` 使用同一次生成的新武器/压枪阈值，对每名职业选手逐图运行问题检测；仅统计实际已实现规则，包含默认停用项及亮点供后续校准，不含待开发项和 `other`。只计 target 不是 `match` 的命中，回合数为全部人次的在场回合数（`rounds_played`）之和，每十回合频率保留两位小数；至少命中一次的人次占比保留一位小数。

按武器的逐次交火分布和连射分段命中率用于后续问题前提条件，不等同于 Leetify 的整场六项统计。Leetify 未公开这些同口径职业分布，也未公开是否采用我们的致盲过滤方式。样本来自有限的同一赛事对局，只作为参考，不作为所有职业选手的总体标准。

## 问题检测

`features/problems.py` 登记 `docs/problem_catalog.md` 的全部 25 个编号，实际检测以任务 005 的明确表格为准，共 19 个编号、20 种检测（压枪含单次交火和整场）。命中只表示满足必要条件，并不自动认定为失误；AI 从命中里选取重点并讲解，由结构化检查器核对。

- 交火武器直接复用基准的认定函数。枪法与距离规则分别使用同武器职业 p75 / Glock 距离 p90；缺失武器或阈值不会套用其他武器，计入 `skipped`。没有基准仍检测通用规则。
- TTD 排除烟雾、首次可见时被闪白、缺失数据和达到最大等待时长的样本；死亡还要求对手有 TTD 且更短。其他规则按任务表分别检查首枪、接触类型、人数标签、补枪、死亡道具、致盲归属及亮点。
- 单次压枪只取交火武器的后段子弹，逐发匹配职业连射段命中率后求平均；混合武器不合并。整场则复用基准 `spray_stats`，按每发实际武器、相同发数段分别比较，默认由 `spray_match_enabled=false` 关闭。两者都要求足够子弹，差值使用百分点；等于差值阈值不命中。移动扫射排除首枪，停稳数据不完整时不推断。
- `[problems]` 集中保存阈值和停用列表。默认停用 `died_without_seeing`、`turned_into_flash`、`weapon_not_ready`、`spray_while_moving`、`awp_unscoped_shot`。视野误判已在 018 明显减少，但 `died_without_seeing` 仍待产品负责人根据新频率审查，暂不启用；移动扫射、AWP 未开镜及整场压枪待数据校准；被闪后阵亡、拉栓期间交火按目录降为背景，事实卡照常显示。`awp_moving_shot` 由通用移动首枪规则覆盖。目录待开发项、`awp_close_range_duel`、`other` 仅登记，不检测；AWP 未开镜规则仅检测死亡。检测条件和原有阈值不变。
- 每条 `ProblemHit` 保留 target、交火武器、判断所用的 evidence 和 thresholds（数值与来源）；子弹序列和道具列表因证据字典只允许标量，编码为 JSON 字符串。`ProblemReport` 同时记录职业来源、停用编号及缺少数据的计数；不满足条件和样本不足不计作缺少数据。
- `problem_frequency()` 对已实现、启用的非亮点问题计算本场次数，零命中也列出，排除 `match` 目标。职业频率从基准原始 `hits / rounds` 计算，预期次数 λ 为职业频率乘以本场在场回合数（`MatchMoments.rounds_played`：总回合数减去中途掉线、重连前不在场的回合，fields.md 陷阱 24）；多出次数为本场次数减 λ，偶然概率为泊松分布 P(X ≥ 本场次数 | λ)。偶然概率 < `frequency_max_chance`（默认 0.05）且多出次数 ≥ `frequency_min_excess`（默认 2）才突出；职业频率缺失或为零时不判突出。倍数仅作展示。突出项在前，各组按多出次数降序排序。
- `analyze` 无需 `--ai` 即在 `<输出根目录>/<玩家名>_<SteamID64>/<比赛名>/` 生成（按 steamid 归档：已有以 `_<SteamID64>` 结尾的玩家文件夹时沿用，同一个人在不同平台的名字不同也归到一处） `moments.json`、`facts.md` 和 `problems.md`。频率表展示本场、预期、多出、偶然概率和倍数，突出项加粗；多出次数大于 0 但不突出的标为“次数较少，需要更多比赛确认”，多出次数小于或等于 0 的标为“不高于职业水平”，不作为问题提出。其后列出命中证据、停用项和 skipped。重新运行会把旧 `report.md` 与 `submission.json` 按文件修改时间移入 `历史版本/`。背景项登记于 `CONTEXT_ONLY`，不单独报为问题；不改变事实卡和原始指标。

## 多场汇总

`summary` 从指定 demo 或 `data/demos` 中找到的同一玩家 demo 重新解析，不依赖单场输出；同一个比赛标识只保留一份，优先原始文件，无法读取或解析的场次记录原因后跳过。比赛按标识排序，成功处理的比赛依次编号 `M1`、`M2`，跨场瞬间可写为 `M3:R9-death`。

汇总问题频率只计各场非 `match` 目标的命中，总次数除以在场回合数之和；职业预期、泊松偶然概率和突出判断与单场共用同一计算。`matches_with_hits` 是至少命中一次的场数，`matches_above_pro` 是该场次数严格高于该场按职业频率预期次数的场数，`by_map` 记录各地图命中次数。整场枪法六项分别取各场非空数值的中位数并注明参与场数，不把不同场次的子弹合并重算。程序结果写入玩家文件夹的 `汇总/problems.md`、`汇总/summary.json`；AI 汇总诊断留待任务 013。

## 结构化诊断与报告

`diagnose()` 先生成问题命中和频率，再把“问题频率”“检测到的问题”放入逐次瞬间列表之前。AI 可用 get_moment/get_round 核对细节，最终通过 submit_report 提交 overview、findings、patterns、highlights 和 training_plan；不以自由文本报告作为完成结果。

- “检测到的问题”开头固定说明出枪慢和准星问题只在打中过对手的交火中可判断，不能据击杀/死亡的分布推断心理压力；TTD 是看见到首次命中，频率按每十回合整体比较。
- finding 最多关联两个代表性 target，同一问题编号只能有一条 finding；pattern 可再列出该问题的全部相关瞬间，分析共同原因。
- finding、pattern、highlight 的分析可引用程序从整场枪法、职业对比、问题频率（含预期次数、多出次数和偶然概率百分比）及比分收集的数字；逐次瞬间的数字仍只由本条 targets 提供。overview 继续使用摘要数字。检查器把配置中的 25、50、75、90 等分位标签识别为名称；数值与事实的差距小于文字最后一位的一个单位时视为匹配。
- 敏感说法（`[[checker.phrases]]`）默认要求至少一个 target 满足谓词；`scope = "all"` 要求本条 targets 中的瞬间全部满足，用于“这两次死亡”“两次击杀”这类统称（只有漏补枪回合记录时不适用，那里的阵亡是队友的）。“站位偏散/离队友远”“离队友近”核对与队友的距离：死亡瞬间取最近存活队友距离，漏补枪记录取与阵亡队友的距离，门槛为 `teammate_far_distance`、`teammate_near_distance`。地图名按 `[checker.map_aliases]` 核对，含官方中文名。

- 解析失败或内容违规会按字段路径返回中文错误；调用流程还在本地核对工具 schema 已声明的编号枚举、数量、长度和 targets 格式，以兼容没有强制 schema 的服务。默认允许两轮修改；第三次仍不合格时，删除有违规的整条 finding/pattern/highlight，保留原文、路径及原因。没有可检查对象或删除后一条 finding 都不剩时抛出 DiagnosisError。查询错误不计入结论修正次数，原服务的拒绝、截断和异常处理保留。
- 总览违规时由程序按目标玩家比分、击杀数和死亡数生成替换句，并明确标注“AI 总览未通过检查，已替换”。删条和替换后再次检查最终对象，通过后才排版。
- `report/render.py` 将程序事实卡、检测证据与阈值、突出频率、比分和跳转命令，与 AI 的分析及训练建议分别展示；other 标明“AI 判断，无程序依据”。旧的自由文本引用检查模块已删除，结构化检查器用于拒绝文本编号的正则保持原样。
- `submit_report` 只有一个顶层包装对象时，如 `{"report": {提交字段...}}`，程序识别并检查内层内容，原始参数和拆开的键名仍写入审计。连续两次真正的空提交会收到“提交内容为空，请提交完整对象”，不占用修正次数；第三次起按普通格式错误计数。诊断保留最终 submission、每轮 violations_history、dropped、ProblemReport、FrequencyRow、usage、tool_calls、raw_calls 和 repairs。CLI 保存 `report.md` 及 `submission.json`，后者包含提交、违规历史、删除/替换记录及每次 submit_report 的原始参数和解析结果，便于核对。

## 标签

| 标签 | 含义 |
|---|---|
| `opening_kill` / `opening_death` | 本回合第一个击杀 |
| `trade_kill` / `traded` / `not_traded` | 补枪 / 被补回 / 未被补 |
| `clutch_1vN` | 瞬间发生时自己是队伍最后一人，对面 N 人 |
| `man_advantage` / `man_disadvantage` | 瞬间前存活人数多于 / 少于对面 |
| `post_plant` | 已下包 |
| `after_round_end` | 胜负判定之后 |
| `shot_first` / `opponent_shot_first` / `shot_same_tick` | 交火中谁先开枪（近似） |
| `no_shots_fired` | 死亡前这次交火中没有朝对手开枪 |
| `died_blind` / `unused_utility` | 被闪致死 / 死时道具没用 |
| `opponent_blind` / `blind_in_fight` | 对手 / 自己在交火窗口内被闪，事件时可能已恢复 |
| `saw_opponent_first` / `opponent_saw_first` | 谁先看见谁（地图几何视野） |
| `peeked_held_angle` / `held_angle` | 接触时自己主动拉出、对手架点 / 自己架点、对手拉出 |
| `died_without_seeing` | 死亡时看不到击杀者（被背身、侧身或穿点打死） |
| `flash_assist` / `damage_assist` | 关键助攻的类型 |
| `teamkill` / `victim_is_bot` / `world_damage` | 误杀队友 / 击杀 bot / 非玩家伤害致死 |

## 漏补 `missed_trades`
不是目标玩家自己的击杀 / 死亡，单独列出：队友阵亡时目标玩家存活、与阵亡队友的直线距离 ≤ `missed_trade_max_distance`、窗口内在雷达上发现过击杀者，但没在窗口内击杀他。距离不考虑视线，"发现"是雷达状态（约 0.5 秒更新一次），**只是近似**，给 AI 作参考。

## 已知局限
- 实际扣血的总和比记分板的伤害统计（`m_iDamage`）系统性地低约 1%（每人每场 7–27 点）。试过几种算法都对不上，原因未查明；单次交火的伤害按事件中的剩余血量计算，是自洽的。
- 交火中开枪的目标、谁先开枪、瞄准偏差、视野都基于估计的眼睛 / 身体高度，没有 hitbox。
- 视野只有静态地图几何：烟雾只用于排除，门、可破坏物体不考虑；awpy-data 的地图网格版本可能与 demo 录制时的地图版本不同（输出中记录了 `visibility_data_version`）。
- 职业基准只覆盖清单中的有限赛事样本，尚不含路人分位及完整职业基准库。

## 本地模型上下文检查

使用 `[ai].provider = "local"` 且 `[ai.local].context_check = "ollama"` 时，生成报告前向本机 Ollama `/api/show` 查询所选模型的 `num_ctx`。系统提示词、摘要、用户指令和工具定义的总字符数乘以 `chars_per_token`，再加输出上限 `max_tokens` 与 `context_margin_tokens`；超过 `num_ctx` 时不请求模型。每轮返回后核对 `usage.prompt_tokens`：若超过 `num_ctx - max_tokens`，或比上一轮减少，则停止并提示更换更大上下文的模型。查询工具调用达到 `max_tool_calls` 后，后续轮次只提供 `submit_report`。`context_check = "none"` 会跳过 Ollama 专用检查。
