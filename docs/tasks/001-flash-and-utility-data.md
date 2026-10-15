# 001 交火中的闪光数据、本回合已投道具

状态：完成

## 目标

1. 补上双方在交火中是否被闪、闪光是谁扔的。用户发现报告漏掉了"对手吃闪"这类细节，原因是数据里只有"自己死时是否被闪"。
2. 记录目标玩家阵亡前本回合已经投出的道具，供以后判断"有道具没用"时参考。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/parsing.md` 中"`blinds`：致盲"和"`shots`：开枪与投掷"两节：两张表的列
- `src/cs2coach/features/models.py`：`Engagement`、`DeathState`
- `src/cs2coach/features/moments.py`：
  - `death_state()`：现有"死时被闪"的算法，新逻辑照它的口径写；
  - `_kill_or_death()`、`engagement()`：交火窗口 `start_tick`～`end_tick` 怎么来的；
  - `_tags()`
- `tests/test_moments.py`：
  - `scenario()` 用 `blinds=[{"tick", "user_steamid", "attacker_steamid", "blind_duration"}]` 构造 `player_blind`；
  - `test_short_blind_does_not_count` 等现有的被闪测试。

已验证的数据事实（不用再查）：

- `blinds` 表每行是一次致盲：
  - `tick`：致盲开始；
  - `duration`：秒；
  - `victim`、`thrower`：steamid，可能为空；
  - `thrower_ambiguous`：ESL 的 demo 没有 `player_blind` 事件，致盲由 `flash_duration` 重建，同一 tick 有多颗闪时投掷者为空、该列为 True；
  - `victim_team_num`、`thrower_team_num`。
- 某一 tick 的剩余致盲时间 = `(致盲开始 tick + duration × tick_rate − 该 tick) / tick_rate`，大于 0 表示仍在致盲中。同一个人可能有多行重叠，取剩余时间最长的那行。
- 致盲时长低于 `[moments].min_blind_seconds` 的不算，和 `death_state` 一致。
- `shots` 表包含所有 `weapon_fire`。投掷物行的 `is_grenade` 为 True，`weapon` 是规范名：`flashbang`、`smokegrenade`、`hegrenade`、`molotov`、`incgrenade`、`decoy`。

## 要做的事

1. **在 `models.py` 新增 `BlindInfo`**：

   ```python
   class BlindInfo(BaseModel):
       """交火窗口内某一方被闪的情况（只记录时长 ≥ min_blind_seconds 的致盲）。"""
       blinded_at_event: bool  # 击杀 / 死亡那一 tick 仍在致盲中
       remaining_at_event_s: float | None  # 那一 tick 剩余的致盲秒数，保留 2 位小数；不在致盲中为 None
       max_duration_s: float  # 窗口内相关致盲中最长的时长
       thrower: int | None  # 取剩余时间最长（或时长最长）那次致盲的投掷者；不确定时为 None
       thrower_ambiguous: bool  # 投掷者无法确定（ESL 重建，同 tick 多颗闪）
       thrower_role: Literal["self", "teammate", "opponent", "enemy"] | None
       # 相对于"被分析的目标玩家"：self = 目标玩家本人，teammate = 目标玩家的队友，
       # opponent = 这次交火的对手本人，enemy = 对手的其他队友；投掷者不确定时为 None
   ```

2. **`Engagement` 新增两个字段**，默认为 None，以保持兼容：
   - `player_blind: BlindInfo | None = None`
   - `opponent_blind: BlindInfo | None = None`

   计算规则：
   - 取所有满足以下两点的致盲：时长 ≥ `min_blind_seconds`；致盲期间与交火窗口 `[start_tick, 事件 tick]` 有重叠（窗口开始前就已开始、窗口内仍在生效的也算）。
   - 没有这样的致盲时，字段为 None。
   - `thrower` 取事件 tick 时剩余时间最长的那次；如果事件 tick 时都已结束，就取时长最长的那次。
   - 写成一个独立函数 `blind_info(match, p, victim, sid, opponent, start_tick, tick) -> BlindInfo | None`，放在 `moments.py`。

3. **`DeathState` 新增 `thrown_grenades: list[str] = []`**：目标玩家在本回合、阵亡 tick 之前投出的道具规范名，按时间顺序，重复的保留。来自 `shots` 表，不含 `controlling_bot` 为 True 的行。

4. **新增两个标签**，并在 `features/labels.py` 中加中文名：
   - `opponent_blind`（"对手交火时被闪"）：`engagement.opponent_blind` 不为 None；
   - `blind_in_fight`（"交火时自己被闪"）：`engagement.player_blind` 不为 None。

   已有的 `died_blind` 保持不变。

5. **`tools/digest.py` 的 `moment_line()`**：
   - 显示双方被闪的情况，例如"对手被闪（事件时剩 0.8s，队友 xxx 闪的）"，投掷者用名字显示。`moment_line` 目前拿不到名字，可以显示角色（自己 / 队友 / 对手 / 对手队友）；
   - 显示本回合已投出的道具。

6. **`tools/queries.py`**：`get_moment` 工具的说明里有字段说明，补上新字段的说明。

7. **文档**：`docs/features.md` 补充新字段的定义、口径和准确度（ESL 投掷者可能不确定）。

## 验收标准

- [x] `tests/test_moments.py` 新增测试，至少覆盖：
  - 击杀前对手被目标玩家的队友闪中，事件时仍在致盲中：`opponent_blind.blinded_at_event` 为 True，`thrower_role == "teammate"`，并有 `opponent_blind` 标签；
  - 对手在交火窗口内被闪，但事件时已恢复：`blinded_at_event` 为 False，`remaining_at_event_s` 为 None，`max_duration_s` 正确；
  - 被自己的闪闪到：`thrower_role == "self"`；
  - 致盲时长低于阈值：字段为 None；
  - 致盲在交火窗口开始前就已结束：字段为 None；
  - 投掷者不确定（`thrower` 为空、`thrower_ambiguous` 为 True）：`thrower_role` 为 None；
  - `thrown_grenades`：本回合死前投出的道具按顺序出现；死后和上一回合投出的不算。
- [x] 现有测试全部不改且通过（新字段有默认值）。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 真实 demo 验证：在一场 5E demo（`data/demos/5e_demo/.extracted/` 下任选一场）上运行：
  ```
  .venv/Scripts/python.exe -m cs2coach analyze <demo> --player <该场任一 steamid>
  ```
  完成报告中写明：所有击杀和死亡中，`opponent_blind` 和 `player_blind` 非空的数量，以及 `thrower_role` 的分布。**不要加 `--ai`。**

## 范围外（不要做）

- 不改 `death_state()` 现有的 `blinded` 等字段和它们的口径。
- 不改"死时道具没用"（`unused_utility`）标签的口径。这在问题目录里处理（`docs/problem_catalog.md`，还在审核中）。
- 不改 AI 提示词、不做结构化报告、不做事实卡（是后续任务）。
- 不改解析层（`parsing/`）。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `features/models.py` 新增 `BlindInfo`、双方可空致盲字段和默认空列表 `DeathState.thrown_grenades`，兼容旧数据。
  - `features/moments.py` 新增独立函数 `blind_info`：按时长阈值和时间重叠筛选，事件时取剩余最长的一次、全部恢复时取总时长最长的一次；角色以目标玩家为参照，未知时留空。死亡状态附加本回合死前已投道具，不改原有致盲字段算法。
  - `features/labels.py` 新增双方交火被闪标签；`tools/digest.py` 显示致盲和已投道具；`tools/queries.py` 补充字段说明；`docs/features.md` 说明定义与 ESL 重建局限。
  - `tests/test_moments.py` 新增 15 项测试：四种角色、窗口前开始的致盲、事件时已恢复、阈值和边界、重叠致盲选取、未知投掷者、原死亡致盲口径、已投道具的顺序/重复/回合/死亡/bot 过滤，以及摘要输出。现有测试未改写。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：198 passed，3 skipped（未设置真实 demo 测试环境变量）。
- 真实 demo 验证：
  - 运行 `.venv/Scripts/python.exe -m cs2coach analyze data/demos/5e_demo/.extracted/g173-n-20261014223216271995270_de_mirage/g173-n-20261014223216271995270_de_mirage.dem --player 76561199711881069`，正常生成 `_moments.json`，未加 `--ai`。
  - 目标玩家云雾散漫山野：20 次击杀、17 次死亡，共 37 个瞬间。
  - `opponent_blind` 非空 **3** 次：`opponent=1`、`enemy=1`、`self=1`；`player_blind` 非空 **2** 次：`opponent=1`、`self=1`。本场这五条记录无未知投掷者，未知分支由单元测试覆盖。
  - 17 次死亡中有 13 次 `thrown_grenades` 非空，原 demo 未修改。
- 遗留问题 / 需要确认的地方：该 demo 的两名其他玩家坐标覆盖率分别约 93%、94%，解析层按原逻辑提示警告；未改解析层。原 `death.blinded` 摘要对未知投掷者的措辞、视野与武器就绪等既有问题均保留，按范围外问题处理。没有新增依赖、配置键或 AI 提示词改动。

## 审查意见（Claude 填写）

**审查通过（2026-10-15）。**已用原始 `player_blind` 事件核对致盲数据（5E Mirage R11-death），结果一致。`_blind_line` 的措辞歧义并入 002 第 1 轮修改。
