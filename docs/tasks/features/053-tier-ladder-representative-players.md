# 053 段位阶梯：按代表人物建分段基准，多场汇总改为阶梯位置

状态：待做
难度：A（分段基准结构变化，新增阶梯计算，多场报告页的雷达、优点、结论都改口径）
执行：Codex（思考强度 high）
分支：`task/053-tier-ladder`（基于 `task/052-category-panel`）

## 目标

现在的 5E 分段按"文件夹里整场 10 人都算这个段位"统计，三档几乎分不开（`docs/status.md` 2026-10-19 待办）。改为：**每个分段由若干名段位明确的代表人物定义，每场只统计这个人。** 多场汇总不再在 8 个人里算百分位，而是显示玩家在**段位阶梯**上的位置：

> 入门标准（5E B 段）= 1 → 5E A 段 = 2 → 5E S 段 = 3 → 职业 = 4

2026-10-19 产品负责人确定：B 段作为"新手入门标准"；每个分段人数标准一致（8 人、每人约 10 场）；报告默认对比 A 段；**多场汇总**的雷达改画阶梯位置，"做得好的方向"和"最弱的维度"按阶梯判断。**单场报告不变**（仍按分段的单场分布算百分位）。

## 背景（必须读）

- `docs/status.md` 第一节"2026-10-19 待办"：代表人物方案的来由和约定
- `config/tiers.toml`、`scripts/build_tier_baseline.py`（`parse_one_tier_demo`、`build_tier_baseline`、`demo_entries`、`main`）
- `src/cs2coach/features/radar.py`（`player_metrics`、`percentile`、`axis_values`、`median_axes`）
- `src/cs2coach/report/page_data.py`：`_build_page`（多场雷达 = 逐场维度取中位数）、`_rows`（优点按逐场百分位中位数筛选）、`_verdict`、`build_summary_page`（052 的类别面板）
- `src/cs2coach/features/panel.py`：`PanelCounts`、`panel_values`、`PANEL_METRICS`（`kind` 水平 / 风格）
- `src/cs2coach/summary/aggregate.py::aggregate_matches`（`aim_overall` 按逐场取中位数）
- `src/cs2coach/report/page_template.html`（雷达绘制、分段切换）
- `src/cs2coach/summary/discover.py`（`summary` 默认扫描 `data/demos`）

## 已有数据（Claude 已准备）

- 5E B 段 8 名代表人物：`data/tiers/5e_b_players.toml`（`[[player]]`：`name`、`steamid`、`dir`），demo 在 `data/demos/tiers/5e_b/<昵称>/`。**名单和 demo 都在 `data/`，不进仓库**
- 有效 demo 77 场：月蓝海、马虎的柔晓博各有 1 个压缩包损坏（`zipfile.is_zipfile` 为假），九落秋雨有 2 场里他用了另一个 SteamID（按规则会被跳过，剩 7 场）
- A、S 段的代表人物以后再收集；在那之前 `5e_a`、`5e_s` 仍用旧的文件夹口径

## 要做的事

1. **分段配置** `config/tiers.toml`：
   - 每个分段可选 `level`（阶梯位置，整数）与 `players_file`（代表人物名单路径）。设定：`5e_b` → `name = "入门标准（5E B 段）"`、`level = 1`、`players_file = "data/tiers/5e_b_players.toml"`（替换原来的 `dirs`/`exclude`）；`5e_a` → `level = 2`；`5e_s` → `level = 3`；`pro` → `level = 4`；`amateur_tmp` 不设 `level`（不在阶梯上）
   - `config/default.toml` 新增 `[ladder]`：`min_player_matches = 3`（每人至少几场才计入人均值）、`reach_quantile = 0.25`（见第 4 点）

2. **按代表人物构建** `scripts/build_tier_baseline.py`：
   - 有 `players_file` 时：逐人扫描其 `dir`（递归、跳过解压缓存和损坏的压缩包，沿用现有提示），每场只为该 SteamID 计算 `find_moments` 和 `lobby_line`；**demo 中没有该 SteamID 时跳过这一场并提示**；少于 `min_player_matches` 场的人不计入人均值（单场分布照常计入）
   - 没有 `players_file` 的旧分段：照旧统计全部玩家，另外**按 SteamID 分组**得到人均值（只取场数 ≥ `min_player_matches` 的人；职业分段同一选手多场，可以得到人均值；旧 5E 文件夹分段大多不够，人均值可能为空）
   - 名单文件、SteamID、昵称、路径**都不能写进输出**；输出只含数值

3. **基准输出新增 `per_player`**（现有字段 `metrics`、`problem_rates`、`panel` 保留，口径不变）：

   ```json
   "per_player": {
     "n_players": 8,
     "matches": [10, 7, ...],            // 每人计入的场数，不含身份，按大小排序
     "metrics": {
       "time_to_damage_ms": {"values": [...], "standard": 540.0, "q25": 520.0, "q75": 575.0},
       ...
     }
   }
   ```

   - 人均值的口径**必须与多场汇总对玩家本人的口径相同**：雷达四项 = 这个人各场 `player_metrics` 的中位数（"典型单场"，与 `aggregate_matches`、`_build_page` 一致）；类别面板的指标 = 这个人各场 `PanelCounts` 合并后 `panel_values`（与 052 一致）。最好直接复用同一个函数，不要另写一套
   - `standard` = 人均值的中位数；`q25`/`q75` = 人均值的 25%、75% 分位（与 `baseline.py` 现有分位数函数相同的算法）；`values` 排序后保存
   - 覆盖的指标：雷达四项 + `PANEL_METRICS` 中 `kind == "水平"` 的指标。风格类不进阶梯

4. **阶梯计算** 新模块 `features/ladder.py`：

   ```python
   class LadderPoint(BaseModel):
       level: int; tier_id: str; name: str; standard: float; q25: float | None; q75: float | None

   class LadderResult(BaseModel):
       position: float | None      # 画在雷达上的位置；None 表示段位间没有明显差别，不画
       reached: LadderPoint | None # 达到的最高一档；一档都没达到为 None
       next: LadderPoint | None    # 下一档
       ordered: bool               # 各档标准是否按"越来越好"排列

   def ladder_points(metric: str, tiers: dict[str, dict]) -> list[LadderPoint]: ...
   def ladder_position(value: float | None, points: list[LadderPoint], higher_is_better: bool) -> LadderResult: ...
   ```

   - **位置**：数值等于某档标准时，位置就是该档的 `level`；两档之间按数值线性插值；低于最低一档或高于最高一档时，按相邻一段的斜率外推，限制在 `[最低 level − 0.5, 最高 level + 0.5]`
   - **顺序检查**：相邻两档的标准如果不是"越高档越好"（例如 A 段的急停比 S 段还高），`ordered = False`，`position = None`，页面上写"段位间没有明显差别"，不画在雷达上
   - **达到某档**：从最高档往下找，第一个"数值不差于该档人均值中较差的那一侧分位"的档位，就是 `reached`。越高越好的指标看 `q25`，越低越好的看 `q75`（`reach_quantile` 控制，默认 0.25）。某档没有 `q25`/`q75`（人数不足）时用 `standard`
   - 只用设了 `level` 且有 `per_player` 的分段；可用的档位少于 2 个时，`position` 为 None，多场页面回退到现在的百分位显示（旧基准文件也走这条路）

5. **多场报告页**（`build_summary_page`，单场 `build_match_page` 不改）：
   - **雷达**：数值改为各维度的 `position`；参考线改为各档标准（入门 = 1、A = 2、S = 3、职业 = 4 的同心线），去掉"本场平均 / 本场最佳"之类单场才有的对比线；刻度显示档位名称而不是百分位
   - **每个维度 / 指标旁的文字**：`已达到 5E A 段（标准 1.15 米）· 下一档 5E S 段 1.03 米`；没达到入门：`未达到入门标准（1.30 米）`；`ordered = False`：`段位间没有明显差别`
   - **做得好的方向**：雷达维度中 `reached.level` ≥ 当前对比分段的 `level + 1` 的（和 A 比时即达到 S），按 `position` 从高到低，最多 `max_strengths` 条；例子沿用现有挑选逻辑
   - **最弱的维度**：`reached` 低于当前对比分段（或为 None）的雷达维度中 `position` 最低的；全部达到对比分段时写"四个维度都达到了 5E A 段"
   - 一句话结论里"最该练的问题"部分不变（仍按问题频率与分段 `problem_rates` 比较）
   - **类别面板**：水平类指标增加一列"阶梯"，内容同上面的文字；风格类该列留空
   - 分段切换：切换的是"当前对比分段"（影响优点、最弱、问题预期），阶梯本身不随切换变化

6. **汇总默认扫描**：`summary` 不指定 demo 时，跳过 `data/demos/tiers/` 下的分段样本（新增配置 `[summary] exclude_dirs = ["data/demos/tiers"]`）

7. **文档** `docs/features.md`：新增"段位阶梯"一节（代表人物、人均值口径、位置插值、顺序检查、达到某档的判定、页面展示）；分段雷达一节注明多场改为阶梯、单场仍为百分位

**不要重建基准文件**，由 Claude 重建（需要解析 77 场代表人物 demo 和职业 demo）。

## 接口与数据

- `config/tiers.toml`：`level`、`players_file`
- 名单文件：`[[player]]` 的 `name`（仅用于提示）、`steamid`（字符串）、`dir`
- 基准 JSON：`per_player`（见上）
- `features/ladder.py`：`LadderPoint`、`LadderResult`、`ladder_points`、`ladder_position`
- 多场 `report_data.json`：每个雷达维度和类别面板的水平类指标附带 `ladder`（`position`、`reached`、`next`、`ordered`）；结构由实现方定，写进完成报告

## 玩家看到什么

- 多场报告的雷达：四个维度各画一个点，位置是"你在入门 / A / S / 职业阶梯上的哪里"，同心线是各档标准。图例写明"线 = 各段位代表玩家的标准水平"
- 每个维度旁一句话：达到哪一档、该档标准是多少、下一档标准是多少；单位与现有一致
- "做得好的方向"= 已经达到比对比分段高一档的维度；"最弱的维度"= 还没达到对比分段的维度里最低的
- 分段名称：`入门标准（5E B 段）`、`5E A 段`、`5E S 段`、`职业`
- 段位标准是"该段位代表玩家（每人多场）的中位水平"，页面底部一句话说明代表玩家人数和每档的总场数（例如"入门标准：8 名玩家、77 场"）
- 单场报告看不到任何变化

## 验收标准

- [ ] 新增 `tests/test_tier_ladder_053.py`，至少覆盖：
  - 名单构建：两名代表人物各 3 场，其中一场 demo 里没有该 SteamID → 跳过并提示；只有 2 场的人不进人均值、但单场分布计入；输出里没有 SteamID、昵称、路径
  - 人均值口径：雷达指标 = 各场 `player_metrics` 的中位数；面板指标 = 合并计数后的值；`standard`/`q25`/`q75` 正确
  - 旧分段按 SteamID 分组得到人均值，场数不足的不计
  - `ladder_position`：正好等于某档标准、两档之间插值、低于最低档和高于最高档时外推并限幅、越低越好的方向、`ordered = False`、可用档位少于 2 时为 None
  - 达到某档：数值介于该档 `q25` 与 `standard` 之间（越高越好）→ 算达到；比 `q25` 还差 → 不算
  - 多场页：雷达值为阶梯位置；和 A 比时达到 S 的维度进"做得好的方向"；未达到 A 的维度中位置最低的是"最弱"；旧基准（没有 `per_player`）回退到百分位
  - 单场页结果与改动前一致
  - `summary` 默认扫描跳过 `data/demos/tiers/`
- [ ] 开发时只跑相关测试：`.venv/Scripts/python.exe -m pytest -q tests/test_tier_ladder_053.py tests/test_page_data.py tests/test_page_check.py tests/test_panel_052.py`
- [ ] 完成前跑一次全部测试：`.venv/Scripts/python.exe -m pytest -q`，报告只贴最后一行（`tests/test_platform_check.py` 在沙箱里读不到共享目录属已知问题，注明即可）
- [ ] `python scripts/page_check_all.py` 违规为 0，或逐条说明原因（如页面一致性规则需要按阶梯调整，说明改了哪条、为什么）

## 范围外（不要做）

- 不重建基准文件；不改单场报告
- 不改问题检测、阈值、职业武器阈值（`pro_sample.json`）
- 不收集或处理 A、S 段的代表人物数据
- 不做类别面板的分级（S/A/B 等级）
- 不调用真实 AI

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
