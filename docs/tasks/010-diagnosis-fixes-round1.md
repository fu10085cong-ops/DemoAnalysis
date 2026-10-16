# 010 结构化诊断第一轮实测后的修正

状态：完成

## 目标

2026-10-16 用 DeepSeek 对 3 名玩家生成了结构化报告。准确性明显提升：Claude 逐条核对了第一份报告，AI 引用的具体事实全部正确。但实测发现 4 个问题，本任务修正它们。

实测数据在 `data/output/` 下：

| 玩家 | 报告 | 提交记录 |
|---|---|---|
| 玩家04 | `g173-n-20261014223216271995270_de_mirage_76561190000001002_report.md` | 同名 `_submission.json` |
| 玩家06 | `9215177492731139980_0_76561190000001001_report.md` | 同名 `_submission.json` |
| 76561190000001003 | `9215177492731139980_0_76561190000001003_report.md` | 同名 `_submission.json` |

`_submission.json` 中的 `violations_history` 记录了每一轮被退回的原因。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/tasks/009-structured-diagnosis.md`：现有流程
- `src/cs2coach/ai/diagnose.py`、`ai/prompts.py`、`tools/digest.py`、`tools/checker.py`、`tools/submission.py`
- 上面 3 份 `_submission.json`

## 要做的事

### 1. 检查器：分析文字中允许引用整场数据

**现象**：AI 在分析里写"整场 Time to Damage 500ms，慢于职业中位数 375ms""你的频率是职业的 1.81 倍"，被判为 `number_not_found`。AI 只好删掉这些有用的对比（见第一份报告 `violations_history` 第 1 轮）。

**原因**：摘要中的数字目前只允许出现在 `overview` 里。

**改法**：定义"整场数值"，在 `findings`、`patterns`、`highlights` 的 `analysis` 中也允许使用。整场数值只包括摘要中**与具体瞬间无关**的部分：

- 整场枪法统计及其职业对比（`aim_stats`、`compare()` 的结果：职业中位数、p25、p75、"优于 N%"）；
- 频率对比表（本场次数、本场和职业每 10 回合的频率、倍数）；
- 比分和回合数。

**不要**把所有瞬间的数字都放进来，否则"把别的瞬间的数字安到这个瞬间上"这类错误就检查不出来了。

实现方式：整场数值由程序从数据结构直接收集，不要从摘要文本里抽取，并作为参数传给 `check_submission`。`overview` 保持现在的规则（允许摘要中的全部数字）。

### 2. 空提交：查明原因，并且不占用修正次数

**现象**：3 份报告中有 2 份，第一次调用 `submit_report` 时所有字段都缺失（`violations_history` 第 0 轮为 `invalid_submission`，"缺少必填字段"）。

**改法**：
1. `_submission.json` 新增 `raw_calls`：每次调用 `submit_report` 的原始参数字符串（`ToolCall.raw_args`）和解析后的结果，用来以后排查。
2. 查看 `providers.py` 和 `openai_compat.py` 中 DeepSeek 工具调用参数的解析，确认空参数的来源：是模型确实传了空对象，还是参数被截断、解析失败，或者走了别的分支。**完成报告中写明结论**。如果是代码的问题，就修复。
3. 参数为空对象 `{}` 或缺少全部顶层字段时，回复"提交内容为空，请提交完整对象"，**不计入** `repairs`。为防止死循环，连续空提交超过 2 次后仍按现有规则计入。

### 3. 同一个问题不要讲两遍

**现象**：第一份报告的 `findings` 中，"出枪慢"列了 5 个瞬间；`patterns` 中又列了 7 个瞬间。读起来是重复的。

**改法**：
1. `findings` 中每条的 `targets` 最多 2 个，即代表性的交火。上限放进配置 `[checker].max_finding_targets = 2`，并写进工具 schema。
2. 新增检查 `duplicate_problem`：同一个问题编号不能同时出现在两条及以上的 `findings` 中。同一问题可以同时出现在一条 finding（讲代表性交火）和一条 pattern（讲反复出现的原因）中，但 pattern 的 `analysis` 不能重复 finding 的具体交火细节。后半句只在提示词里要求，不做程序检查。
3. 提示词中说明分工：finding 讲 1–2 次代表性交火的细节；pattern 讲共同原因，列出所有相关瞬间。

### 4. 在摘要中写明检测口径，防止 AI 被口径误导

**现象**：第一份报告的总览写"出枪慢和准星问题主要发生在击杀而非死亡，说明这些坏习惯在低压力回合被掩盖了"。实际原因是口径：死亡时，只有在阵亡前打中过对手才有 TTD，所以"出枪慢"天生更多出现在击杀中。AI 把这个口径当成了规律。

另外，AI 把 TTD 当成"开第一枪的时间"："第一枪拖了 859ms"，实际是看见到**命中**。

**改法**：在摘要"检测到的问题"一节的开头，加一段**由程序生成的固定说明**：

- `slow_reaction`、`poor_crosshair_placement`：只能在**打中过对手**的交火中判断。死亡时如果没打中对手，就无法判断，所以这两类问题在击杀中出现得更多，**击杀和死亡的比例不说明问题**；
- TTD 是看见到**第一次命中**的时间，分成"看见→开枪"和"开枪→命中"两段，不要说成"开第一枪用了 X 毫秒"；
- 频率按每 10 回合计算，与职业比较的是整体频率，不按击杀和死亡分开。

提示词"准确性要求"中加一条：不要根据击杀和死亡的分布推断心理状态或压力，例如"低压力时掩盖了坏习惯"。数据不支持这类推断。

## 验收标准

- [x] 测试：
  - 检查器：`analysis` 中引用整场 TTD、职业中位数、频率和倍数可以通过；引用**其他瞬间**的数字仍然报 `number_not_found`；
  - 空提交不计入 `repairs`；连续空提交超过 2 次后计入；`raw_calls` 被记录；
  - findings 的 targets 超过 2 个时，schema 和检查器都拒绝；`duplicate_problem` 的触发和不触发；
  - 摘要中出现口径说明，提示词包含新增的准确性要求。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 用 3 份 `_submission.json` 中第 1 轮被退回的提交做回归：
  - 当时因为引用整场数据被拒的数字，现在应该通过；
  - 引用其他瞬间数字的，仍然被拒。

  如果提交记录里没有保存当时的原始提交，就根据 `violations_history` 中的消息构造等价的测试用例。
- [x] **不要运行 `--ai`。** 实测由 Claude 在征得用户同意后执行。

## 范围外（不要做）

- 不改问题检测的规则和阈值。
- 不改事实卡和报告排版。跳转命令表是正常的，不需要改。
- 不加新的问题类型。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `tools/digest.py` 从 AimStats、`compare()`、FrequencyRow 和程序比分提取整场数字，只收集整场指标，不读取逐次瞬间；在“检测到的问题”开头加入固定口径说明。
  - `tools/checker.py` 接收整场数字，供 findings/patterns/highlights 的 analysis 使用；overview 仍按原摘要核对。finding 的 targets 超过配置上限时报错，同一编号在多条 finding 中出现时对后续条目标记 `duplicate_problem`。
  - `ai/diagnose.py` 保存每次 submit_report 的原始参数字符串及解析结果；若只有一个顶层键且其值是带提交字段的对象，先拆开再检查，raw_calls 另记 `unwrapped` 键名。完整对象可直接通过，部分对象走正常结构错误；两个顶层键或真正空对象不拆。前两次连续空提交要求补全且不计修正次数，第三次起按现有规则计数。`cli.py` 将 raw_calls 写入审计 JSON。
  - `config/default.toml` 将 max_finding_targets 从 5 改为 2；`ai/prompts.py` 明确 finding/pattern 分工及不能从击杀死亡分布推断心理压力；`docs/features.md` 同步诊断和审计口径。
  - `tests/test_checker.py`、`test_ai.py`、`test_submission.py`、`test_render.py` 覆盖整场数字与其他瞬间的隔离、重复编号、目标上限、空提交计数、单层包装完整/缺字段/双顶层键、DeepSeek 参数解析、raw_calls 与固定说明，并调整与新重复规则冲突的旧断言。
- 测试结果：专项测试 139 项通过；全量 `.venv/Scripts/python.exe -m pytest -q`：**497 passed, 3 skipped**。`git diff --check` 通过。没有新增依赖。
- 真实 demo 验证（如有）：读取 `data/output/` 中三份既有 `_submission.json` 的退回记录及对应 `_moments.json`，没有重新解析 demo。旧审计未保存首轮原始提交，按三份记录中的整场数字 469/375、656/375、25.7/36.2 和频率 2.5/1.81 构造等价回归；这些整场引用可通过，另一瞬间独有的 933 毫秒仍被拒绝。没有运行 `--ai`，没有调用真实大模型 API。
- 遗留问题 / 需要确认的地方：两份历史记录的首轮对象缺少全部五个必填字段，且含额外 `report` 字段。`openai_compat._parse_args` 对合法 JSON 对象原样返回、非法或非对象返回 None；历史错误说明当时已解析成对象，符合整份结论包在 `report` 中的情况。旧审计未保存 `raw_args`，无法复原那两次的原始字符串；新增 raw_calls 可供后续实测核对。实测报告内容未在本任务改动。
- 第 1 轮审查意见回应：已实现单层对象拆包，键名不限；完整对象当轮直接通过，不增加 repairs。内层缺字段按正常结构错误返回；两个顶层键不拆包。审计保留原始和解析后参数，并记录 `unwrapped`。增加 Claude 和 DeepSeek 假客户端测试覆盖以上路径。

## 审查意见（Claude 填写）

**第 1 轮（2026-10-16）：需修改（只有一处）。** 4 项都已正确实现：整场数值只来自整场统计、职业对比、频率和比分，没有混入各瞬间的数字；口径说明、finding 的目标上限、`duplicate_problem` 和 `raw_calls` 都已到位。491 个测试通过。

你对空提交的排查很关键：首轮提交里有一个多出来的 `report` 字段，五个必填字段都缺失。这说明模型把整份结论**包进了一层 `{"report": {...}}`**，并不是真的空提交。现在的处理是让模型重新提交，模型要把整份结论重写一遍，多花一轮输出（约 1–3 万 token）。

**请修改**：`submit_report` 的参数如果**只有一个顶层键**，并且它的值是一个对象、包含 `ReportSubmission` 的全部必填字段，就直接拆开这一层使用，键名不限，如 `report`、`submission`。拆开后照常检查，不计入修正次数，也不要求重新提交。在 `raw_calls` 中记录 `unwrapped: "<键名>"`。

其他情况保持现在的规则：真正的空对象，或者缺字段且无法拆开的。

测试：
- `{"report": {完整合法对象}}` → 一次通过，`repairs == 0`，`raw_calls` 记录了 `unwrapped`；
- `{"report": {缺字段的对象}}` → 拆开后按正常的结构错误处理；
- 两个顶层键 → 不拆开。

**第 2 轮（2026-10-16）：通过。** 单个顶层键包裹的提交会被拆开使用，并在 `raw_calls` 中记录键名；497 个测试通过。
