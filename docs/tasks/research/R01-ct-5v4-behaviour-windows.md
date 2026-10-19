# R01 Mirage CT 首次 5v4：触发器、5 秒行为窗口与研究表

状态：待做
难度：A（新模块：局势触发、逐 tick 窗口抽取、导航网格路径距离、初版行为标签）
执行：Codex（思考强度 high）
分支：`research/R01-ct-5v4-windows`

## 目标

研究路线图（`docs/research_roadmap.md`）第一个最小研究闭环的**数据层**：从职业 demo 中找出"CT 首次从 5v5 变为 5v4"的时刻，为当时存活的每名 CT 抽取 5 秒行为窗口，算出可复核的特征和初版行为标签，导出研究表。每一行都能追溯到原始 demo 和 tick 区间，并带游戏内跳转命令。

本任务**只产出数据和初版标签**，不做结果比较和结论（R02 起由 Claude 与产品负责人人工复核、校准后再做）。

## 背景（必须读）

- `docs/research_roadmap.md` 第 4.1、4.2、5.2–5.5 节：分析单位、可追溯字段、`unknown` 原则、研究表字段
- `docs/research_inventory.md` 第二、七节：可复用的模块和缺口
- `src/cs2coach/parsing/match.py`：`parse_match`、`MatchData`（`ticks`、`state_at`、`player_ticks`、`kills`、`damages`、`shots`、`grenades`、`rounds`、`spotted_bit`）
- `src/cs2coach/features/visibility.py::Sight`（`sees`：几何视线 + 视野 + 烟雾 + 闪白）
- `src/cs2coach/features/panel.py::_clutch`（按冻结期名单和击杀顺序推演存活人数的现成写法）
- `src/cs2coach/features/moments.py`：`_spotted_by`（雷达发现）、`_nearest_teammate`、`commands` 的跳转命令写法（`report/page_data.py::commands`）
- `src/cs2coach/baselines/pro_demos.toml`：职业 demo 清单（`map = "de_mirage"` 的 6 条）
- `docs/fields.md` §0–§3：各平台事件差异；**不要凭记忆假设 demoparser2 或 awpy 的字段名、接口**

## 已核实的事实

- awpy 3.0.0 已安装；`~/.awpy/2000930/navs/de_mirage.nav` 存在。`awpy.map_control.NavMesh` 有 `find_area(point)`、`find_path(start, end, *, weight='distance')`、`area`、`areas`、`neighbors`；**构造 / 从文件加载的方式未确认**，实现前先写脚本验证，结果写进完成报告和 `docs/fields.md`
- Mirage 职业 demo 6 份，实际 **5 场比赛**：`falcons-vs-natus-vincere-m3-mirage-p1.dem` 与 `-p2.dem` 是同一场被拆成两段；6 份里 3 场有 Falcons。研究表必须带 `match_id`（同一场的两段相同）以便按比赛分组
- 产品负责人会陆续下载更多 Mirage 职业 demo，放在 `data/demos/pro_mirage/`（可能不存在或为空）

## 要做的事

新建包 `src/cs2coach/research/`（诊断程序不改）：

1. **`models.py`**：pydantic 模型
   - `Situation`：`demo_id`、`match_id`、`round_id`、`tick`、`map`、`trigger`（如 `"ct_first_5v4"`）、`alive_ct`、`alive_t`、`round_time_s`（冻结期结束后秒数）、`time_remaining_s`（按回合时长推算，不确定时为空）、`bomb_planted`
   - `BehaviourWindow`：`Situation` 的引用字段 + `player_id`（SteamID 字符串）、`player_name`、`start_tick`、`end_tick`、下面第 3 点的特征、第 4 点的标签、结果字段、`jump_command`、`review_status = "unreviewed"`
   - 缺数据一律为 `None`，不编造（路线图 4.1 第 5 条）

2. **触发器 `triggers.py`**：`ct_first_man_advantage(match, cfg) -> list[Situation]`
   - 每回合最多一个：本回合（冻结期结束后、回合判定前）第一次敌对击杀的**被杀者是 T**，且击杀前双方各 5 人存活 → 触发点为该击杀的 tick
   - 第一次击杀是 CT 阵亡、或击杀前已不是 5v5（掉线、冻结期死亡等）→ 本回合不触发；跳过原因计数，写进完成报告
   - 存活人数按冻结期结束时的名单与击杀顺序推演（参考 `panel._clutch`），不要用整局 10 人假设

3. **行为窗口 `windows.py`**：`behaviour_windows(match, situation, sight, nav, cfg) -> list[BehaviourWindow]`，为触发时存活的每名 CT 抽取 `[tick, tick + window_seconds]`（`[research] window_seconds = 5`）；窗口内阵亡则截止到阵亡 tick（`end_tick`）。特征：
   - **位置**：起止坐标、直线位移、起止 `last_place_name`
   - **导航路径距离**：起止时到 **T 方出生区**的沿导航网格路径距离及其变化 `nav_delta`（负数 = 更靠近 T 出生区）。T 出生区取该回合冻结期结束时 T 方 5 人位置的中心所在导航区域；找不到导航区域时为 `None`
   - **视角**：窗口内视角水平方向的累计变化（度）
   - **道具**：窗口内投出的投掷物（类型列表）
   - **接战**：窗口内开枪数、造成 / 受到的伤害、是否击杀、是否阵亡及 tick
   - **队友**：起止时最近存活 CT 队友的直线距离
   - **玩家可能掌握的敌方信息**：触发时与窗口内，本人几何可见（`Sight.sees`）的敌人数；窗口内被本队雷达发现（`spotted_by_mask`）的敌人数。**不要用敌人真实坐标作为"玩家知道的信息"**
   - **短期结果**：窗口内本人是否阵亡；窗口结束时 CT 存活数是否仍多于 T（人数优势是否保持）；**回合结果**（CT 是否赢下）
   - `jump_command`：`demo_gototick <start_tick 前 2 秒>`（沿用复盘包的写法）

4. **初版行为标签 `labels.py`**（方案 C，2026-10-19 产品负责人确定）
   - **主标签**（单选，可为 `unknown`）：`hold`（保持）/ `advance`（前压）/ `retreat`（后撤）/ `lateral`（横向调整），**方向由导航路径距离变化判断**：
     - `hold`：直线位移 < `hold_max_displacement` 且 |`nav_delta`| < `hold_max_nav_delta`
     - `advance`：`nav_delta` ≤ −`move_min_nav_delta`
     - `retreat`：`nav_delta` ≥ +`move_min_nav_delta`
     - `lateral`：直线位移 ≥ `lateral_min_displacement` 且 |`nav_delta`| < `move_min_nav_delta`
     - 其余、或 `nav_delta` 为空、或窗口内 1 秒内阵亡（窗口太短）→ `unknown`
   - **辅助标签**（多选）：`utility`（用了道具）、`regroup`（与最近队友的距离缩短超过 `regroup_min_distance` 且结束时 < `regroup_max_end_distance`）、`engaged`（窗口内开枪或受到伤害）
   - **区域参考**：`[research.regions.de_mirage]` 把地点名映射到 CT 视角的纵深等级（`deep` / `hold` / `contact`），用于给窗口起止位置打区域标签；**第一版只建表，不参与主标签判断**。表中的地点名必须来自真实 demo（见第 6 点），纵深等级由 Claude 填写，实现方留空字符串即可
   - `label_confidence`：主标签距离阈值越远越高（0–1，规则写在文档里）；`unknown` 为 0
   - 所有阈值放在 `config/default.toml` 的 `[research.labels]`，初值：`hold_max_displacement = 150`、`hold_max_nav_delta = 100`、`move_min_nav_delta = 250`、`lateral_min_displacement = 300`、`regroup_min_distance = 300`、`regroup_max_end_distance = 600`（游戏单位；**是待人工校准的工程假设**，注释写明）

5. **导出脚本 `scripts/research_export.py`**：
   - `--map de_mirage`（默认）；demo 来源为 `pro_demos.toml` 中该地图的条目，加上 `data/demos/pro_mirage/` 下的文件（目录不存在则跳过）；按 `match_id` 去重
   - `--jobs`（默认取 `tools.jobs`，现在为 2）；`--name` 输出目录名
   - 输出到 `data/research/<name>/`：`windows.csv`（每个窗口一行，列名为字段名；列表字段用 JSON 字符串）、`situations.csv`、`summary.md`（demo 数、比赛数、触发回合数、窗口数、各主标签数量、`unknown` 比例、跳过原因计数）
   - 不输出真实玩家名以外的身份信息；**职业选手名可以出现**（公开比赛），但 `data/` 不进仓库

6. **地点名清单**：脚本加 `--list-places`，列出所选 demo 中出现过的全部 `last_place_name`（按出现次数排序），写进完成报告；并把它们作为键写入 `[research.regions.de_mirage]`（值为空字符串）

7. **文档**：新增 `docs/research.md`，写清触发器、窗口、各特征的定义与已知近似（直线距离 vs 路径距离、雷达发现的更新频率等）、标签规则与阈值、`label_confidence` 的算法、研究表字段说明

## 接口与数据

- `cs2coach.research.models`：`Situation`、`BehaviourWindow`
- `cs2coach.research.triggers.ct_first_man_advantage(match, cfg)`
- `cs2coach.research.windows.behaviour_windows(match, situation, sight, nav, cfg)`
- `cs2coach.research.labels.label_window(window, cfg) -> (main, aux, confidence)`
- 配置：`[research]`（`window_seconds`）、`[research.labels]`、`[research.regions.de_mirage]`
- 输出：`data/research/<name>/windows.csv`、`situations.csv`、`summary.md`

## 验收标准

- [ ] 新增 `tests/test_research_r01.py`（合成比赛，参照 `tests/helpers/synthetic.py`），至少覆盖：
  - 触发：第一杀是 T 阵亡 → 触发；第一杀是 CT 阵亡 → 不触发；第二杀才让 CT 5v4（此前已是 4v5）→ 不触发；冻结期击杀不算
  - 窗口：5 名存活 CT 各一个窗口；窗口内阵亡时 `end_tick` 截止于阵亡
  - 导航距离可用时 `nav_delta` 的符号；导航区域找不到时为 `None` 且主标签为 `unknown`（导航网格用假对象注入，不依赖真实文件）
  - 四种主标签各一例、`unknown` 两例（缺距离、窗口过短），辅助标签的组合
  - 敌方信息只来自几何视野 / 雷达，不读敌人真实坐标做判断（构造一个敌人在墙后、坐标很近的例子，计数应为 0）
  - 短期结果：人数优势保持 / 丢失；回合结果
- [ ] 完成前跑一次全部测试，报告只贴最后一行
- [ ] **真实 demo 验证**（会占用 CPU，用 `--jobs 2`）：`python scripts/research_export.py --name r01-dev --jobs 2`，在完成报告中贴 `summary.md` 的内容，以及随机 5 个窗口的 `demo_id / round_id / player / start_tick / 主标签 / nav_delta / 位移`
- [ ] awpy `NavMesh` 加载方式与 `find_path` 返回值的验证结果写进完成报告和 `docs/fields.md`

## 范围外（不要做）

- 不做行为与结果的统计比较、不写结论或规律卡（R02 之后）
- 不改诊断程序（`features/`、`report/`、`summary/` 中已有文件只读，可以 import 复用）
- 不重建任何基准；不调用真实 AI
- 区域纵深等级不要自行填写（Claude 填）

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- awpy 导航网格验证：
- 测试结果（最后一行）：
- 真实 demo 验证（summary.md 与 5 个样例）：
- Mirage 地点名清单：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
