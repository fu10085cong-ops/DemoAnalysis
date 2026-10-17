# S02 对战平台数据对照：把平台的局内数据固定成回归检查

状态：完成（结构性任务，说明见 `docs/tasks/README.md` 的"三类任务"）

## 目标

2026-10-17 用一场完美 demo 对照平台截图，发现了两处口径问题：补枪窗口 3 秒对 5 秒、道具伤害该用有效伤害。这类问题只有拿外部数据对照才看得出来，以后改代码时也可能悄悄改坏。

本任务做一个**可以反复运行的对照工具**：平台数据录入成文件，脚本解析对应的 demo，用我们的口径算出同样的指标，逐人逐项比较，超出容差就报错。以后每多一场带截图的比赛（完美或 5E），只要多录一个文件，对照就自动覆盖到它。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`、`docs/tasks/README.md`
- `docs/tasks/features/025-report-summary-card-lobby-table.md`、`docs/tasks/fixes/026-trade-window-spray-seen-range-words.md`：其中"已验证的口径"一节和补枪验收表
- `src/cs2coach/features/lobby.py`、`src/cs2coach/features/models.py`：`LobbyLine`
- `src/cs2coach/features/moments.py`：`traded` / `not_traded` 标签，`DeathState.unused_grenades`
- `src/cs2coach/parsing/match.py`：`blinds`、`grenades` 两张表
- `config/default.toml`：`[moments]` 的 `min_blind_seconds`
- **平台数据文件**：`data/eval/platform/9220525551542050188_0.toml`，已由 Claude 按截图录入。含真实信息，只读，不入库

## 已验证的对照结果（Claude 2026-10-17 用临时脚本算出，本任务要把它固化）

| 平台指标 | 我们的口径 | 结果 |
|---|---|---|
| 首杀成功次数和比例 | 每回合第一个击杀 | 10/10 |
| 闪光助攻 | 击杀记录中 `assistedflash` 为真且助攻者是本人 | 10/10 |
| 全局道具伤害 | 对敌人的 `hegrenade` / `inferno` / `molotov` / `incgrenade` 伤害，用 `dmg_effective` 合计 | 10/10 |
| 死亡时浪费道具平均价值 | 每次死亡时身上没用掉的投掷物，按价格表求和后取平均 | 10/10（误差不超过 1 美元） |
| 补枪击杀成功 | 5 秒窗口，按报仇的队友人数计 | 9/10 |
| 队友补枪成功（被补回） | 自己死亡后 5 秒内击杀者被队友击杀 | 9/10 |
| 平均闪白敌人次数 | 每颗闪光弹致盲敌人的人次，只算致盲 ≥ 1 秒的 | 平均误差 0.03 |
| 命中率 | 枪械命中敌人的伤害事件数 ÷ 枪械开枪数 | 9/10 误差在 1 个百分点以内 |
| 精准爆头率 | 爆头命中 ÷ 命中（我们不算 AWP） | 误差在 3 个百分点以内 |
| 反应时间、伤害时间、预瞄、急停、压枪、精准度 | 我们按地图几何判断"看见"，平台更晚 | **口径不同，只看排序相关，不比数值** |

价格表：闪光 200、烟雾 300、手雷 300、燃烧瓶 400、燃烧弹 600、诱饵 50。这组价格和平台 10/10 一致。

## 要做的事

### 1. 给 `LobbyLine` 补上平台有、我们还没有的 4 个字段

计算和现有字段放在一起，口径见上表：

- `traded_deaths: int`：自己的死亡中带 `traded` 标签的次数；
- `wasted_utility_avg: float | None`：死亡时没用掉的投掷物平均价值，没有死亡时为 None；
- `enemies_flashed_per_flash: float | None`：本人扔的闪光弹致盲敌人的人次 ÷ 闪光弹数，致盲时间不少于 `moments.min_blind_seconds` 才算，没扔闪光时为 None；
- `teammates_flashed_per_flash: float | None`：同上，对象换成队友，不含自己。

价格表放进配置 `[economy] grenade_prices`，并通过配置校验。

**这 4 个字段只加进数据，本任务不改报告的体检卡和同场表。**

### 2. 平台数据文件的格式

格式以 `data/eval/platform/9220525551542050188_0.toml` 为准，示例模板写进 `config/platform_check.example.toml`（虚构数据）：

- 文件头：`platform`、`demo`（路径）、可选 `demo_member`、`captured`（录入日期）。
- `[[compare]]`：每个要比较的指标一条，包括：
  - `platform_field`、`ours`（`LobbyLine` 字段名，可以带 `aim.` 前缀）；
  - `kind`：`exact`、`tolerance` 或 `info`；
  - `tolerance`：只用于 tolerance；
  - `min_match_share`：至少多少比例的玩家要对上，默认 1.0；
  - `note`：说明口径。
- `[[player]]`：每名玩家一条，`steamid`、`name`，以及平台上的各项数值。没有的值不写。

### 3. 对照脚本 `scripts/platform_check.py`

- 用法：`.venv/bin/python scripts/platform_check.py data/eval/platform/<文件>.toml [更多文件]`，不传参数时检查 `data/eval/platform/` 下的全部 `.toml`。
- 每个文件只解析一次对应的 demo，用 `lobby_lines` 算出全部玩家；目标玩家随便取一名即可，因为同场数据和目标无关。
- 按 `[[compare]]` 逐项比较，在终端输出 Markdown 表：
  - 每个指标一行：对上的人数、总人数、是否通过、最大偏差；
  - `info` 类只输出排序相关系数（Spearman），不判通过与否；
  - 没通过的指标，下面列出对不上的玩家，用 steamid 后 4 位标识，并显示双方数值。
- 有任一 `exact` 或 `tolerance` 指标对上的比例低于 `min_match_share` 时，退出码为 1，否则为 0。
- 平台数据里有、`LobbyLine` 里没有的玩家（或者反过来），要明确列出来，不能静默忽略。

### 4. 测试

- `tests/test_platform_check.py`，用合成数据：
  - 比较逻辑：exact、tolerance、`min_match_share`、info 只算相关系数；
  - 玩家缺失时会列出；
  - 退出码。
- 4 个新字段各有合成用例：
  - 致盲 0.9 秒不算、1.0 秒算；
  - 致盲队友、致盲自己分开处理；
  - 没有死亡时 `wasted_utility_avg` 为 None。
- **真实数据测试**：`data/eval/platform/` 下有文件、对应 demo 也存在时，运行对照，断言全部通过；不存在时跳过。写法参照 `tests/test_real_demo.py`。

## 接口与数据

- `LobbyLine` 新增 4 个字段，默认值要保证旧缓存、旧测试能正常反序列化。
- 新增配置 `[economy] grenade_prices`。
- 对照工具不改任何检测、报告行为。
- 平台数据文件留在 `data/` 下，不入库；仓库里只放虚构的示例模板。

## 验收标准

- [ ] 测试按第 4 步完成，全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] `.venv/bin/python scripts/platform_check.py` 对现有的那份文件退出码为 0，并把输出贴进完成报告，用 steamid 后 4 位，不贴名字。按上面的已验证结果，exact 和 tolerance 指标应该全部通过。如果有指标没通过，先确认是实现问题还是文件里的 `[[compare]]` 写得不合理，**不要擅自改平台数据**，写进完成报告。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改体检卡、同场表和报告。
- 不改检测规则和口径。对照中发现的新偏差写进完成报告，由 Claude 判断。
- 不录入新的平台数据：由产品负责人提供截图，Claude 录入。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：`features/models.py`、`features/lobby.py` 增加四项同场数据，旧缓存可按默认值反序列化；`config/default.toml`、`config.py` 增加并校验六种道具价格；`scripts/platform_check.py` 实现单文件一次解析、逐项对照、Spearman 排序相关、缺失玩家提示与退出码；`config/platform_check.example.toml` 提供虚构模板；`tests/test_lobby.py`、`tests/test_config.py`、`tests/test_platform_check.py` 覆盖合成用例和真实样本；`docs/features.md` 记录新增指标口径。未增加依赖，未改平台数据、报告或检测规则。
- 测量结果：改动前没有可重复执行的平台对照工具；现在对 1 场、10 名玩家的 15 项指标逐项检查，其中 9 项 `exact` / `tolerance` 全部达到各自的 `min_match_share`，6 项 `info` 输出排序相关。两项补枪各 9/10，其余判定项各 10/10。后续新增 TOML 文件即可纳入无参数对照。
- 测试结果：`.venv/bin/python -m pytest -q`：712 passed，3 skipped（16.76 秒）；`git diff --check` 通过；`.venv/bin/python scripts/platform_check.py` 退出码 0。测试和脚本均未调用付费或本地模型 API。
- 真实 demo 验证：使用只读 `data/demos/perfectworld_demo/9220525551542050188_0.zip`，对照只读 `data/eval/platform/9220525551542050188_0.toml`。命令 `.venv/bin/python scripts/platform_check.py` 输出如下（未输出玩家姓名）：

  ```markdown
  ## 9220525551542050188_0.toml

  | 指标 | 对上/有值 | 结果 | 最大偏差 / Spearman |
  |---|---:|---|---:|
  | opening_kills | 10/10 | 通过 | 0 |
  | flash_assists | 10/10 | 通过 | 0 |
  | utility_damage | 10/10 | 通过 | 0 |
  | trade_kills | 9/10 | 通过 | 1 |
  | traded_deaths | 9/10 | 通过 | 1 |
  | wasted_utility_avg | 10/10 | 通过 | 0.75 |
  | accuracy_pct | 10/10 | 通过 | 1.63 |
  | headshot_pct | 10/10 | 通过 | 2.3 |
  | enemies_flashed_per_flash | 10/10 | 通过 | 0.0777778 |
  | teammates_flashed_per_flash | 10/10 | 信息 | Spearman 0.631 |
  | damage_time_ms | 10/10 | 信息 | Spearman 0.250 |
  | crosshair_deg | 10/10 | 信息 | Spearman 0.503 |
  | counter_strafe_pct | 10/10 | 信息 | Spearman 0.512 |
  | spray_pct | 10/10 | 信息 | Spearman 0.652 |
  | precision_pct | 10/10 | 信息 | Spearman 0.390 |
  ```
- 遗留问题 / 需要确认的地方：补枪击杀、被补回各有 1 人与平台相差 1 次，和任务单已验证的偏差一致；队友闪白与视野相关指标仍只供排序参考，由 Claude 判断口径差异。当前平台文件未修改。

## 审查意见（Claude 填写）

**审查通过（2026-10-17）。**
- 对照结果和 Claude 之前用临时脚本算的完全一致，包括各项最大偏差和 6 个排序相关系数；
- Claude 自己运行 `platform_check.py`，退出码为 0；平台数据文件没有被改动；
- 4 个新字段的口径符合任务单：致盲阈值读配置，价格表在配置中，自己闪到自己不计入；
- 真实数据测试在缺少数据文件或 demo 时自动跳过；712 个测试通过。

**以后的用法**：产品负责人提供新比赛的平台截图，Claude 录入到 `data/eval/platform/`，不带参数运行 `platform_check.py`，就会对照全部文件。

