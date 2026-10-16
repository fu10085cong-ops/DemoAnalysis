# 023 多场汇总的基础版报告（L0）

状态：待做

## 目标

单场分析已经会生成 `basic_report.md`，这是免费档的基础版报告，纯程序生成，不调用 AI。多场汇总（`summary` 命令）目前只有 `problems.md` 和 `summary.json` 两份表格，没有可以直接给用户看的基础版报告。

本任务给 `summary` 补上 `汇总/basic_report.md`，内容和口径照着单场的基础版来。每次运行 `summary` 都生成，加不加 `--ai` 都一样。产品负责人 2026-10-16 已确定：免费档只提供 L0。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `src/cs2coach/report/basic.py`：单场 L0，`Section`、`Example`、`problem_texts`、`choose_problems`、`build_l0`、`render_basic_report`
- `src/cs2coach/cli.py`：
  - `_summary`：多场输出写在哪里、`_archive_reports` 怎么用；
  - `analyze` 中生成 `basic_report.md` 的那几行
- `src/cs2coach/summary/aggregate.py`：`PlayerSummary`、`MatchSummary`、`SummaryFrequencyRow`、`summary.detail(number)`
- `src/cs2coach/summary/digest.py`：`representative_hits`，即多场代表交火的排序，AI 版也用它
- `src/cs2coach/summary/render.py`：`summary_frequency_table`、`summary_match_table`
- `src/cs2coach/summary/render_report.py`：`facts()` 里多场事实卡的渲染方式，包括名字来源、漏补记录、标题格式
- `config/problem_text.toml`：每类问题的固定说明（草稿）
- 参考输出：
  - 单场基础版：`data/output/评测/20261016-222717_mac首次/` 下任一单场 case 的 `basic_report.md`；
  - 多场 AI 版：同一目录下 `多场汇总_*/*/汇总/report.md`。

## 要做的事

1. 新建 `src/cs2coach/summary/basic.py`：
   - `build_summary_l0(summary: PlayerSummary, cfg: dict) -> list[Section]`：
     - 选问题：对 `summary.frequency` 调用 `choose_problems`，规则和单场相同：突出问题优先；没有突出问题时，取多出次数为正的前 3 项。
     - 每个问题的代表交火：用 `representative_hits(summary, pid, cfg["summary"]["representatives_per_problem"])`，和 AI 版的候选一致，会先覆盖不同场次。
     - 固定说明和练习图的取法照搬 `build_l0`：`problem_texts()`、`drill_labels`，以及不含 `min_aim_problems` 的练习图。
   - `render_summary_basic_report(summary: PlayerSummary, sections: list[Section], baseline: dict | None) -> str`，结构如下：
     - **标题**：`# 基础复盘（多场）：<primary_name> · <N> 场`。
     - **概况行**：曾用名、各地图场数、在场回合总数、击杀 / 死亡 / 关键助攻合计，并注明"程序生成，不含 AI 分析"。
     - **整场枪法**：各指标取各场中位数（`summary.aim_overall`），列出中位数、职业中位数、"优于职业样本"、参与场数。和职业的比较用 `compare(metric, median, baseline)`，同单场的 `_aim_table`。没有数值的指标不列。
     - **突出问题**：用 `summary_frequency_table` 只列选中的问题。没有突出问题时，写法和单场一致，说明"次数较少，需要更多比赛确认"。没有偏多的问题时写"这批比赛没有比职业频率偏多的问题。"。
     - **每个问题一节**：为什么是问题、怎么改、练习，然后是"代表交火"。
       - 每个代表交火：标题 `#### 〔M3:R9-death〕M3 · <match_id> · <map_name>`，事实卡正文，再加一行"程序依据：……"（`evidence_sentence`）。
       - 漏补回合用漏补记录的事实（`missed_trade_facts`）。
     - **附录：每场概况**：`summary_match_table`。
     - **附录：跳转命令**：列出所有代表交火，格式和 AI 版相同：`| 比赛 | 编号 | tick | 命令 |`，并提示"先在游戏里打开对应的 demo"。
     - **页脚**：同单场，提示"想要逐条分析原因和个性化训练计划，可以生成 AI 版报告（加 --ai）"。
2. **事实卡渲染不要复制粘贴**：把 `summary/render_report.py` 的 `facts()` 中"按跨场编号渲染事实卡"的部分抽成共用函数，AI 版和基础版都调用它。抽取之后，AI 版报告的输出必须逐字不变，用现有测试或新增快照测试保证。
3. **`cli.py` 的 `_summary`**：
   - 写出 `basic_report.md`，并加进 `_archive_reports` 的归档列表；
   - 打印"已写入 …/basic_report.md"，和单场一致；
   - 在 `--ai` 分支之前生成，这样 AI 失败时基础版也已经写好。

## 接口与数据

```python
# src/cs2coach/summary/basic.py
def build_summary_l0(summary: PlayerSummary, cfg: dict) -> list[Section]: ...
def render_summary_basic_report(summary: PlayerSummary, sections: list[Section],
                                baseline: dict | None) -> str: ...
```

- `Section`、`Example` 直接复用 `report/basic.py` 中的定义：`Example.target` 填跨场编号，例如 `M3:R9-death`。
- 不新增配置键。代表数量用现有的 `summary.representatives_per_problem`。

## 验收标准

- [ ] 测试，新建 `tests/test_summary_basic.py`，用合成数据，参照 `tests/test_summary.py` 的造法：
  - 有突出问题时只列突出问题；没有突出问题时取多出次数为正的前几项，并显示"需要更多比赛确认"；
  - 代表交火覆盖不同场次，顺序与 `representative_hits` 一致；
  - 漏补回合的代表用漏补记录事实；
  - 跳转命令表包含每个代表瞬间，漏补回合没有 tick，不列入；
  - 枪法表缺少某项指标时不列该行；没有职业基准时显示"—"；
  - 报告中不出现占位符 `{`、`}`；
  - CLI：`summary` 不带 `--ai` 也写出 `basic_report.md`，再运行一次时旧文件被归档。
- [ ] AI 版多场报告在抽取共用函数前后逐字一致：现有 `tests/test_summary_ai.py` 通过；如果它没覆盖事实卡，就补一个快照断言。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] **真实数据**：运行 `.venv/bin/python -m cs2coach summary --player <评测清单 data/eval/eval_set.toml 中"多场汇总"那个 case 的 player> --out "$TMPDIR/cs2coach-023"`。
  - 输出放临时目录，不要写进 `data/output/` 的正式报告目录；
  - 有分析缓存时约十几秒；缓存失效时要重新解析，约几分钟；
  - 在完成报告中贴出生成的 `basic_report.md` 的前 60 行；
  - 对照 `data/output/评测/20261016-222717_mac首次/多场汇总_*/*/汇总/report.md` 的"附录：跳转命令"：基础版的代表交火，应该都在 AI 版可选的候选范围内。
- [ ] 不调用 DeepSeek、Claude 或本地模型，**不要加 `--ai`**。

## 范围外（不要做）

- 不改 `config/problem_text.toml` 的措辞：它由产品负责人定稿。
- 不改单场 `basic_report.md` 的内容。
- 不给多场 L0 加 AI 文字，也不加 L1。
- 不改问题选择、频率和代表交火的排序规则。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
