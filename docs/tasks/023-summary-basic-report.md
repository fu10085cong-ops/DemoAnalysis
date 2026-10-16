# 023 多场汇总的基础版报告（L0）

状态：完成

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

- 改动的文件及原因：新增 `src/cs2coach/summary/basic.py`，复用单场 L0 的问题选择、固定说明和练习图，多场代表交火沿用 `representative_hits`；`src/cs2coach/summary/render_report.py` 抽出公共跨场事实卡；`src/cs2coach/cli.py` 每次 summary 生成并归档基础报告。新增 `tests/test_summary_basic.py`，更新 CLI 归档断言和 AI 报告快照。
- 测试结果：`.venv/bin/python -m pytest -q`：644 passed，3 skipped。用原版渲染函数与新版对同一合成提交逐字比较，结果相同；完整 AI 报告 SHA-256 快照已加入测试。
- 真实 demo 验证：从本地评测清单读取“多场汇总”的 player，执行无 `--ai` 的 `summary --out "$TMPDIR/cs2coach-023"`，退出码 0；汇总 23 场，生成 234 行的 `basic_report.md`。基础版 9 个代表瞬间均出现在历史 AI 报告中，9 个都有跳转命令；候选顺序与 `representative_hits` 一致。下面是前 60 行，玩家名按 024 匿名化要求替换（临时目录中的原始报告未改动）：

```markdown
# 基础复盘（多场）：玩家01 · 23 场

曾用名：玩家01 · 地图：de_ancient 1 场、de_dust2 12 场、de_inferno 6 场、de_mirage 3 场、de_nuke 1 场 · 在场 479 回合 · 击杀 497、死亡 305、关键助攻 71 · 程序生成，不含 AI 分析

## 整场枪法

| 指标 | 各场中位数 | 职业中位数 | 优于职业样本 | 参与场数 |
|---|---:|---:|---:|---:|
| Time to Damage | 484ms | 391ms | 8% | 23 场 |
| Crosshair Placement | 4.66° | 3.71° | 31% | 23 场 |
| Counter-Strafing | 89.2% | 82.3% | 83% | 23 场 |
| Spray Accuracy | 33.6% | 35.4% | 41% | 23 场 |
| Spotted Accuracy | 32.8% | 34.25% | 39% | 23 场 |
| Headshot Accuracy | 21.4% | 22.6% | 46% | 23 场 |

## 突出问题

| 问题 | 总次数 | 职业预期 | 多出的次数 | 偶然概率 | 倍数 | 出现场数 / 高于职业的场数 | 主要地图 |
|---|---:|---:|---:|---:|---:|---:|---|
| **出枪慢** | 174 | 67.08 | 106.92 | 0.0% | 2.59× | 23 / 23 | de_dust2 88 次、de_inferno 46 次、de_mirage 24 次、de_ancient 8 次、de_nuke 8 次 |
| **准星没提前放好** | 137 | 79.69 | 57.31 | 0.0% | 1.72× | 23 / 20 | de_dust2 69 次、de_inferno 40 次、de_mirage 20 次、de_ancient 4 次、de_nuke 4 次 |
| **压枪差** | 22 | 9.89 | 12.11 | 0.1% | 2.23× | 13 / 13 | de_dust2 17 次、de_inferno 3 次、de_mirage 1 次、de_nuke 1 次 |

## 出枪慢

**为什么是问题**：从看见对手到第一次打中他用时偏长，对手会先拿到打中你的机会。慢的原因可能是看见后才去找准星、开枪前还在调整，或者前几发没打中。

**怎么改**：接敌前先把准星放到对手会出现的位置和高度，看见人后用最少的修正打出第一发；第一发打出前先停稳。

**练习**：Aim Botz、死斗

**代表交火**

#### 〔M2:R20-kill2〕M2 · 9210110943532552076_0 · de_dust2

- 你在 BombsiteB，对手 玩家02 在 BombsiteB，距离 12 米。
- 你手持 AWP，对手手持 M4A1-S；本回合装备价值 7450 对 5800。
- 交火前血量：你 83，对手 4。
- 你用 AWP 击杀了对手。
- 你先看见对手，领先 0.66 秒。
- 对手先开枪，领先 1.31 秒。
- 接触时你的速度 104、对手 6 单位/秒：有一方处在减速或急停的过渡状态，无法判断谁在架点。
- 你开了 1 枪，命中 1 枪；第一枪命中，开第一枪时已停稳。
- 从看见对手到第一次造成伤害用了 922 毫秒（看见→开枪 922 毫秒，开枪→命中 0 毫秒）。
- 从看见对手到第一次造成伤害，准星转过了 26.8°。
- 这是一次补枪：队友阵亡 1.4 秒后，你击杀了击杀他的对手。

程序依据：TTD 922 毫秒，高于职业同武器 75 分位 406 毫秒。

#### 〔M23:R16-kill1〕M23 · g201-20260930195112244070473_de_inferno · de_inferno

- 你在 BombsiteB，对手 玩家03 在 Banana，距离 14 米。
- 你手持 MP7，对手手持 AK-47；本回合装备价值 3900 对 5000。
- 交火前血量：你 100，对手 31。
- 你用 MP7 击杀了对手。
- 对手第一次看见你时正被闪白，不计算对手的反应时间。
- 接触时你的速度 217、对手 57 单位/秒：有一方处在减速或急停的过渡状态，无法判断谁在架点。
- 你开了 6 枪，命中 2 枪；第一枪未命中，开第一枪时未停稳。
- 从看见对手到第一次造成伤害用了 891 毫秒（看见→开枪 406 毫秒，开枪→命中 484 毫秒）。
- 从看见对手到第一次造成伤害，准星转过了 10.3°。
```
- 遗留问题 / 需要确认的地方：无。

- 审查意见逐条回应：`jumps` 为空时省略整个“附录：跳转命令”小节；漏补回合单独成报告的测试同时断言没有标题和空表头。

## 审查意见（Claude 填写）

**基本通过，只有一处小问题（2026-10-16）。** 结构、选题、代表交火、共用事实卡、CLI 归档都符合要求。AI 版输出不变，有快照测试保证。用真实数据重跑：23 场，234 行，没有残留占位符。

1. **跳转命令表为空时也会输出表头**：代表交火都是漏补回合时，没有任何瞬间，`## 附录：跳转命令` 会只剩一个空表头。请照单场 `render_basic_report` 的做法，`jumps` 为空时整节省略，并补一个测试。

**复审通过（2026-10-17）。** 跳转命令为空时整节省略，并有测试覆盖。
