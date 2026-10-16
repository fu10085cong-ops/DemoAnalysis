# 015 多场汇总首轮实测后的修正

状态：完成

## 目标

2026-10-16 用 DeepSeek 生成了 3 份多场汇总报告：梁孙超、卢文笙、妮妮孩孩major冠军。Claude 逐条核对后，事实准确性整体很好，但发现了以下几类具体漏洞。本任务修正它们。

| # | 现象 | 原因 |
|---|---|---|
| 1 | AI 说梁孙超的两次漏补"都发生在 T 方**已下包的残局**"，实际上当时包都没下。卢文笙的报告里，AI 想给一次漏补写"2v2"，被检查器拦下 | 漏补记录 `MissedTrade` 没有回合情境（阵营、人数、是否下包、回合时间），AI 只能猜 |
| 2 | "已下包"没被拦下 | "下包"不在敏感说法对照表里 |
| 3 | 妮妮孩孩major冠军的报告多处写"回合**还剩**约 72 秒" | `round_time_s` 是冻结期结束后**已经过去**的秒数，给 AI 的字段说明里没有写清楚 |
| 4 | 梁孙超汇总选出的两个漏补代表例子不典型：一次后来被别人补掉了，一次是手枪局 | 漏补没有可以和阈值比较的数值，代表性命中只能按时间顺序选 |
| 5 | 汇总报告标题是"Jayleo"，但玩家文件夹叫"梁孙超" | 标题取的是编号最前那场的名字 |
| 6 | 总览里写的"455 杀 273 死"被判为 `number_not_found` | 摘要里只有每场的数字，没有合计 |

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `src/cs2coach/features/models.py`：`MissedTrade`、`Context`
- `src/cs2coach/features/moments.py`：`find_missed_trades`、`context()`、`bomb_planted()`
- `src/cs2coach/report/facts.py`：`missed_trade_facts`
- `src/cs2coach/tools/digest.py`：漏补记录在摘要中的写法
- `src/cs2coach/tools/queries.py`、`src/cs2coach/summary/queries.py`：`get_moment` 的字段说明
- `src/cs2coach/tools/checker.py`：`PREDICATES`、回合 target 的取数、人数核对
- `config/default.toml`：`[[checker.phrases]]`
- `src/cs2coach/ai/prompts.py`、`src/cs2coach/summary/prompts.py`
- `src/cs2coach/summary/digest.py`：代表性命中的选择、开头
- `src/cs2coach/summary/render_report.py`：标题
- `src/cs2coach/summary/aggregate.py`：`PlayerSummary.names`

## 要做的事

### 1. 漏补记录补上回合情境

- **`MissedTrade` 新增字段**，都取**队友阵亡那一 tick**、从**目标玩家视角**计算，默认值保持旧数据可读：
  - `side`；
  - `alive_own`、`alive_enemy`：队友阵亡**之后**双方的存活人数，口径与 `Context` 一致；
  - `round_time_s`；
  - `bomb_planted`。
- **事实卡**：`missed_trade_facts` 在现有句子前加一句，例如 `第 34 秒，T 方，下包前，队友阵亡后 3v4。`
- **摘要**：单场摘要中的漏补记录也写出这些情境。
- **检查器**：回合 target（`R5` / `M3:R5`）的数据中加入这些字段：
  - 人数核对：这一回合漏补记录中的人数；
  - 允许的数字：这些字段的值；
  - 谓词：可以作用在漏补记录上，见第 2 项。

### 2. 新增敏感说法"下包"

- 新增谓词 `planted`：瞬间的 `context.bomb_planted` 为 True，或者漏补记录的 `bomb_planted` 为 True。
- 对照表新增：`下包后`、`已下包`、`包已下`、`下包的残局` 这类说法 → `planted`。
- **否定说法不触发**，例如"下包前""没下包""还没下包""未下包"。正则要处理这一点，并写测试。

### 3. 回合时间的口径

- 在单场和多场 `get_moment` 的字段说明中写明：`context.round_time_s` 是**冻结期结束后已经过去的秒数**，不是剩余时间。
- 提示词的准确性要求中加一条：回合时间只能说"回合第 N 秒"或"开局 N 秒"，不要换算成"还剩多少秒"。剩余时间与是否下包、比赛设置有关，数据里没有。
- 检查器的对照表新增：`回合还剩` → 新谓词 `never`，始终不满足。
  - 违规说明写明：`round_time_s 是已经过去的秒数，请写成"回合第 N 秒"`；
  - **不要**影响"致盲还剩 0.8 秒"这类说法，所以只匹配"回合还剩"。

### 4. 漏补的代表性例子

- 汇总摘要选漏补的代表性命中时：
  - 先选**没被其他队友补掉**的（`killer_traded_by_other` 为 False）；
  - 再按**距离从近到远**；
  - 仍然尽量来自不同场次。
- 写成函数里的特例即可，其他问题的选择规则不变。

### 5. 汇总报告的标题

- 标题和摘要开头用**出现场数最多**的名字；场数相同时，取编号最小那一场的名字。
- 其他名字仍然列在"曾用名"中。

### 6. 合计数字

- 汇总摘要的开头加一行合计：总击杀、总死亡、总助攻、胜 / 负 / 平的场数。
- 这些数字加入整个汇总的允许数值。

## 验收标准

- [ ] 测试：
  - `MissedTrade` 新字段的计算：人数是队友阵亡之后的；下包前后的 `bomb_planted`；
  - 事实卡和单场摘要中出现漏补的情境；
  - 检查器：
    - 漏补 target 的人数不符时报错，相符时通过；
    - "已下包"在没下包的瞬间或漏补上报错；"下包前""没下包"不报错；
    - "回合还剩 43 秒"报错，"致盲还剩 0.8 秒"不报错；
  - 漏补代表性例子的选择顺序；
  - 标题用出现场数最多的名字，以及场数相同时的规则；
  - 合计数字可以在总览中引用。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 真实数据（**不加 `--ai`**，输出到临时目录）：
  1. 梁孙超的汇总：贴出摘要中漏补的代表性命中，以及标题；标题应为"梁孙超"；
  2. 5E Dust2（`g173-n-20261014233811572950094_de_dust2`）中梁孙超的单场：贴出事实卡中第 9、10 回合的漏补卡片；
  3. 摘要可以用 `build_summary_digest` 直接生成，不需要调用 AI。

## 范围外（不要做）

- 不改问题检测、频率规则。
- 不调用真实的大模型 API。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：`features/models.py`、`features/moments.py` 给漏补记录补齐队友阵亡时目标玩家的阵营、阵亡后人数、已过回合时间和下包状态，旧记录仍可读取；`report/facts.py`、`report/evidence.py`、`tools/digest.py` 让事实卡、单场摘要、跨场代表命中及查询结果显示同一局势；`tools/checker.py`、`config/default.toml` 核对漏补人数、下包后说法并拦截“回合还剩”，保留否定说法；`tools/queries.py`、`summary/queries.py`、`ai/prompts.py` 说明回合时间口径；`summary/aggregate.py`、`summary/digest.py`、`summary/render.py`、`summary/render_report.py`、`cli.py` 调整代表性漏补排序、主名字、标题与合计数字；相关测试文件补齐回归覆盖。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：540 passed，3 skipped。`git diff --check` 无空白错误。未调用真实大模型 API。
- 真实 demo 验证：梁孙超（`76561199736861630`）8 场数据重新解析后，在临时目录生成 `summary-digest.md` 和 `summary-report.md`：`C:\Users\David\AppData\Local\Temp\cs2coach-015-validation`。报告标题为“**多场复盘：梁孙超 · 8 场**”；摘要开头为“玩家：梁孙超。用过的名字：Jayleo、梁孙超。”，合计为 **115 杀、115 死、23 次关键助攻；7 胜、1 负、0 平**。漏补的 3 个代表命中如下（均未被其他队友补掉，并尽量来自不同场次）：

  ```text
  M8:R9：第 59 秒，T 方，下包前，队友阵亡后 3v4；离阵亡队友 57.3 单位。
  M2:R2：第 16 秒，T 方，下包前，队友阵亡后 4v5；离阵亡队友 63.7 单位。
  M4:R10：第 12 秒，T 方，下包前，队友阵亡后 4v5；离阵亡队友 102.8 单位。
  ```

  指定 5E Dust2（`g173-n-20261014233811572950094_de_dust2`）单场在临时目录生成 `facts.md`，第 9、10 回合漏补卡片：

  ```text
  ### 〔R9〕漏补枪
  - 第 59 秒，T 方，下包前，队友阵亡后 3v4。
  - 队友 DAWAN67 阵亡时离你 57 单位，你在补枪时间窗口内没有击杀击杀他的对手。

  ### 〔R10〕漏补枪
  - 第 10 秒，T 方，下包前，队友阵亡后 4v5。
  - 队友 喑海 阵亡时离你 216 单位，你在补枪时间窗口内没有击杀击杀他的对手。
  ```

- 遗留问题 / 需要确认的地方：无。

## 审查意见（Claude 填写）

**审查通过（2026-10-16）。** 漏补记录带上了回合情境；代表性漏补都是没人补上、距离近的例子；标题改为"梁孙超"；合计数字（115 杀 115 死、7 胜 1 负）与逐场数据一致；"下包"规则正确处理了否定说法，"回合还剩"只匹配这四个字。540 个测试通过。
