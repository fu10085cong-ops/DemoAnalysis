# 052 类别面板：指标计算、段位均值与多场汇总展示

状态：待做
难度：A（新模块，涉及回合胜负、阵营、残局状态推演，跨 moments / 基准 / 汇总 / 报告页）
执行：Codex（思考强度 high）
分支：`task/052-category-panel`

## 目标

参照完美世界数据面板的结构，按类别（火力、枪法、补枪、突破、道具、残局、狙击）算出指标，和段位均值对比，展示在多场汇总的报告页上，每个类别下挂上已有的问题检测和亮点。**本任务只做"算对、能看到"**，分级（S/A/B…）和雷达式图形以后再定。

## 背景（必须读）

- `docs/features.md#类别面板`：**全部指标的口径定义，以它为准**（通用约定、各类别表格、类别下的问题检测）
- `docs/product.md#类别面板：参照完美的数据面板`：为什么这么做、不做什么
- `src/cs2coach/features/lobby.py::lobby_line`：现有同场统计的写法（击杀、助攻、闪光、道具伤害），口径要与它一致
- `src/cs2coach/features/moments.py::find_moments`、`_tags`（`opening_kill` / `opening_death` 的判定）、`_assist`（`flash_assist`）
- `src/cs2coach/features/models.py`：`MatchMoments`、`LobbyLine`、`TradeChance`
- `src/cs2coach/features/trades.py::trade_chance_skip`（漏补率口径）
- `scripts/build_tier_baseline.py::build_tier_baseline`（分段基准输出格式）
- `src/cs2coach/summary/aggregate.py`、`src/cs2coach/report/page_data.py::build_summary_page`、`report/page_html.py`、`report/page_template.html`
- `docs/parsing.md` 的 `kills`、`damages`、`blinds`、`grenades`、`economy`、`rounds` 各节：字段名以它为准，**不要凭记忆假设 demoparser2 的字段**

## 已验证的数据事实

- `kills`：`headshot`、`weapon`、`assister`、`assistedflash`、`is_enemy_kill`、`in_freeze_time`、`after_round_end`、`attacker_team_num`、`victim_team_num`
- `damages`：统计伤害用 `dmg_effective`；道具伤害的 `weapon` 为 `hegrenade`、`inferno`
- `blinds`：`tick`、`victim`、`thrower`、`duration`、`thrower_ambiguous`、`victim_team_num`、`thrower_team_num`
- `grenades`：`type == "flashbang"` 为闪光弹爆开，有 `thrower`、`tick`、`round`
- `economy`：每回合每人一行，`grenades` 为购买时间结束时身上的投掷物（规范名列表，重复保留）；购买时间内已阵亡的人 `inventory` 为空
- `Round`：`winner`（`Team`）、`freeze_end_tick`（可能为空，用 `start_tick` 兜底）、`end_tick`
- 2026-10-18 用 5E B/A/S 36 场算过：首杀对枪参与率按整场合并约 20%、首杀对枪胜率 50%（零和），可作为真实 demo 的合理性检查

**需要实现方先用脚本验证、写进完成报告的一点**：闪光弹爆开（`grenades`）与它造成的致盲（`blinds`）如何对应。预期是同一 `thrower`、`blinds.tick` 等于或紧跟爆开 tick；请在一场 5E、一场完美 demo 上统计能对上的比例和 tick 差的分布，按实际结果确定匹配规则（例如容差几个 tick），写在 `docs/features.md#类别面板` 的道具一节。

## 要做的事

1. **计数模型** `features/panel.py`：

   ```python
   class PanelCounts(BaseModel):
       """单人单场的原始计数（分子、分母和用时样本），多场与段位都按合并计数再算比例。"""
       rounds: int = 0
       rounds_won: int = 0
       ...  # 覆盖 docs/features.md#类别面板 每个指标所需的分子分母
       reaction_ms: list[float] = []  # 用时类指标保存全部样本，合并后取中位数

   def panel_counts(match: MatchData, sid: int, mm: MatchMoments, cfg: dict) -> PanelCounts: ...
   def panel_values(counts: list[PanelCounts], cfg: dict) -> dict[str, float | None]: ...
   ```

   - 每回合阵营取冻结期结束时的 `team_num`；掉线回合（`mm.absent_rounds`）不计
   - 残局：按击杀顺序推演双方存活人数，开局名单取冻结期结束时存活的玩家；每回合每人最多记一次，N 取成为最后存活者那一刻的对方人数
   - 击杀、助攻、闪光助攻、首杀与 `mm.moments` 的标签保持一致（可直接从 `mm` 取，避免两套口径）
   - `MatchMoments` 新增 `panel: PanelCounts | None = None`（有默认值，旧缓存可读），在 `find_moments` 末尾计算，这样分段基准、多场汇总、缓存都能拿到

2. **指标登记** `PANEL_METRICS`：每项记录 `id`、类别、中文名、单位、换算倍数、方向（越高越好 / 越低越好）、类型（水平 / 风格）、分子分母字段、最小样本配置键。名称和类型按 `docs/features.md#类别面板` 原样填写。

3. **配置** `config/default.toml` 新增 `[panel]`：各指标的最小分母（建议：按回合的 20 回合；首杀对枪 10 次；残局 5 次；1v1 3 次；闪光 10 颗；狙杀 `sniper_min_kills = 5`）、`sniper_weapons = ["awp", "ssg08"]`、类别顺序与中文名、每个类别下的问题和亮点列表（照 `docs/features.md#类别面板` 最后一张表）。`config.py` 加校验。

4. **分段基准**：`build_tier_baseline` 输出新增 `"panel": {"counts": <全部样本合并后的 PanelCounts>, "values": <panel_values 结果>, "n": <人次>}`，人次只统计 `mm.panel` 不为空的。**不要重建基准文件**，由 Claude 重建。
   注意：5E 分段基准将改为"按代表人物"重建（见 `docs/status.md` 2026-10-19 待办），届时同一人的多场会合并。因此计数必须能按人、按场相加（`PanelCounts` 只放可相加的计数和样本列表，不放比例），方便以后按人合并。

5. **多场汇总**：`summary.json` 的结构化数据中加入目标玩家合并后的 `panel` 值和计数。报告页（多场）在现有区块之后新增"类别面板"：每个类别一张表，列为"指标 | 你 | 段位均值（当前对比分段） | 样本"，风格类指标在名称后标"（风格）"；样本不足显示"样本不足"；狙击类在狙杀不足时整类隐藏。每个类别下列出该类问题在汇总中的次数和段位预期（复用现有问题汇总的数据，不重新计算），亮点列出次数。单场报告页不加。

6. **文档**：把闪光匹配规则、实际实现中与 `docs/features.md#类别面板` 不一致或补充的地方写回该节。

## 接口与数据

- `features/panel.py`：`PanelCounts`、`panel_counts(match, sid, mm, cfg)`、`panel_values(counts, cfg)`、`PANEL_METRICS`
- `MatchMoments.panel: PanelCounts | None = None`
- 分段基准 JSON：`"panel": {"counts": {...}, "values": {...}, "n": int}`；旧基准没有这个键时，面板的段位均值显示"—"
- `report_data.json`（多场）：新增 `panel` 字段，结构由实现方定，写进完成报告

## 玩家看到什么

- 多场报告页新增"类别面板"区块，位于现有内容之后；7 个类别依次排列，狙击类可能隐藏
- 每个数字都是"多场合并"的值；表头注明"按 N 场合并"
- "段位均值"是当前对比分段（默认 5E A 段）全部样本合并后的值，不是中位数，表头写清
- 风格类指标不表示好坏，名称后标"（风格）"；零和指标（首杀对枪胜率、1v1 胜率）名称后标"（同场零和）"并在区块底部用一句话解释
- 和雷达区块的关系：雷达四项在面板"枪法"类里再出现一次，数值必须一致（复用同一个值，不另算）
- 不显示任何等级、总分、称号

## 验收标准

- [ ] 新增 `tests/test_panel_052.py`，用 `tests/helpers/synthetic.py` 构造比赛，至少覆盖：
  - 火力：两回合（一胜一负）各有击杀与伤害时，`kpr`、`kpr_won`、`adr_won`、`multikill_round_pct` 的值
  - 突破：首杀者与首死者都记一次参与；首杀后赢下回合计入 `opening_round_win_pct`；首伤取本回合第一次对敌伤害
  - 残局：4 名队友依次阵亡后成为 1v2，随后击杀一人赢下回合 → 记 1 次 1v2 获胜，不再记 1v1；对方同时成为 1v1 的那一方记 1 次 1v1
  - 道具：两颗闪光弹，一颗致盲敌人 1.5 秒、一颗只致盲队友 → `effective_flash_pct` 50%、`blind_time_per_round` 只算敌人；`utility_use_pct` 投出数不超过持有数
  - 狙击：AWP 与 SSG 08 的狙杀计入，狙杀少于门槛时类别隐藏
  - 掉线回合不计；多场合并按分子分母相加，而不是比例取平均（构造两场比例不同、回合数不同的例子）
  - 雷达四项在面板里的值与雷达区块一致
- [ ] 开发时只跑相关测试：`.venv/Scripts/python.exe -m pytest -q tests/test_panel_052.py tests/test_page_data.py tests/test_summary.py`
- [ ] 完成前跑一次全部测试：`.venv/Scripts/python.exe -m pytest -q`，报告只贴最后一行（`tests/test_platform_check.py` 在沙箱里读不到共享目录属已知问题，注明即可）
- [ ] 真实 demo：`.venv/Scripts/python.exe -m cs2coach summary --player <player>`（不加 `--ai`；`<player>` 为产品负责人的 SteamID64，派单时给出，不要写进仓库文件），在完成报告中贴出面板 7 个类别的数值，并逐项说明是否在合理范围（例如每回合击杀 0.5–1.0、首杀对枪参与率 15–25%、有效闪光率 30–90%）
- [ ] `python scripts/page_check_all.py` 违规为 0，或逐条说明原因
- [ ] 闪光匹配的验证结果写进完成报告

## 范围外（不要做）

- 不做分级、总分、称号，也不做每个类别的雷达图形
- 不做 WE 评分、回合残局分、回均拆火击杀
- 不改雷达四项的口径、问题检测、漏补检测
- 单场报告页不加面板
- 不重建基准文件；不调用真实 AI

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 闪光匹配验证：
- 真实 demo 验证（面板数值与合理性）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
