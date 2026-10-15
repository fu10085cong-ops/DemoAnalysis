# 009 结构化诊断：接入调用流程、改写提示词、排版报告

状态：待做（**依赖 008**：需要 `problem_frequency` 和停用调整）

## 目标

把 005（问题检测）、007（提交格式与检查器）、008（频率对比）接入 AI 诊断，取代现在"AI 写自由文本、程序只检查引用"的流程：

- **"发生了什么"由程序写**：事实卡、检测依据、频率对比；
- **AI 只做判断和讲解**：挑重点、判断轻重、解释为什么在这个情境下是问题、给训练建议；
- **AI 的每条结论都要通过检查器**，不通过就退回修改；多次修改仍不通过的，删掉这一条，并记录下来。

本任务还要改写提示词，回应用户的反馈："准确性没问题，但讲得不好"。

## 背景（必须读）

- `CLAUDE.md`（架构原则 2–4）、`AGENTS.md`
- `docs/problem_catalog.md`："用途与原则"、文末"实测频率与审核结论"
- `src/cs2coach/ai/diagnose.py`：现有循环（工具调用、修正、错误处理），本任务在它的基础上改
- `src/cs2coach/ai/prompts.py`：现有提示词，其中关于指标口径的说明要保留
- `src/cs2coach/ai/providers.py`：`ChatProvider`、`ToolResult`、`Turn`
- `src/cs2coach/tools/submission.py`、`checker.py`（007）；`features/problems.py`（005、008）
- `src/cs2coach/report/facts.py`（002）、`report/problems.py`（005）
- `src/cs2coach/tools/digest.py`、`queries.py`、`citations.py`
- `tests/test_ai.py`：现有的假客户端测试

## 要做的事

### 1. 给 AI 的摘要（`build_digest`）新增两节

放在决定性瞬间列表之前：

- **"问题频率"**：`problem_frequency` 的结果，标出突出问题，写明"报告应优先讲突出问题"。
- **"检测到的问题"**：按问题分组，列出命中的 target，每条附上简短依据，例如 `R3-kill1：TTD 812ms，职业 p75 484ms`。AI 只能从这里挑选问题，`other` 除外。

`build_digest` 的签名可以加参数，传入 `ProblemReport` 和频率结果。

### 2. 诊断循环（`diagnose`）

1. 先运行 `detect_problems` 和 `problem_frequency`。
2. 工具列表：`get_moment`、`get_round`、`submit_tool(...)`。
3. AI 调用 `submit_report` 时：
   - `parse_submission` 失败 → 以错误结果返回中文说明，计一次修正；
   - `check_submission` 有违规 → 以错误结果返回违规列表（按 path 分组，每条一行），计一次修正；
   - 没有违规 → 结束循环，进入排版。
4. AI 不调用 `submit_report` 就结束回复（`stop == "end"`）→ 追加一条用户消息，要求必须调用 `submit_report` 提交，计一次修正。
5. **修正次数达到 `[ai].max_repairs` 后仍有违规**：
   - 删掉有违规的 finding、pattern、highlight 整条；
   - 总览有违规时，换成程序生成的一句话，写明比分、击杀和死亡数，并注明"AI 总览未通过检查，已替换"；
   - 删掉的内容和原因记录在 `Diagnosis.dropped` 中；
   - 删完后一条 finding 都不剩时，抛出 `DiagnosisError`。
6. `[ai].max_repairs` 默认值改为 2。
7. `Diagnosis` 中去掉 `citations`、`raw_text`，新增：
   - `submission`：最终通过检查的 `ReportSubmission`；
   - `violations_history`：每一轮的违规列表；
   - `dropped`；
   - `problems`：`ProblemReport`；
   - `frequency`。

   保留 `usage`、`tool_calls`、`repairs`。

### 3. 提示词（`prompts.py`）重写

- **保留**现有的指标口径说明：距离单位、TTD 的含义、AWP 和 SSG 的拉栓、接触速度、近似指标等。
- **工作方式**：先读摘要；需要细节时用 `get_moment` 或 `get_round` 查；最后**必须且只调用一次** `submit_report`。
- **挑选规则**：
  - 问题只能从"检测到的问题"中挑；
  - 优先讲**突出问题**；
  - 同一类问题出现多次时，放进 `patterns`，不要拆成多条 finding；
  - `other` 只用于确实重要、但目录没有覆盖的情况。
- **讲解要求**（这是用户最不满意的地方，要写具体）：
  - 每条 `analysis` 要讲清：这个情境下发生了什么，即人数、经济、回合时间、位置；为什么这样做会输掉这次交火或这个回合；更好的处理是什么。
  - 不要泛泛地说"注意站位""多练枪法"；
  - `training` 要具体到练什么、怎么练、练到什么程度，例如在哪张创意工坊地图做什么练习、练多少组。
  - `severity` 的判断：high 表示直接导致丢掉回合或残局，而且是可以避免的。
- **准确性要求**：
  - 数字只能用数据里有的；
  - 编号只放在 `targets` 中，文字里不写编号；
  - 敏感说法（没看到、被闪、干拉、架点、压枪……）只在数据支持时使用；
  - 近似指标（视野、接触类型）要说得留有余地。
- **示例**：在提示词中给出一个格式正确的 finding 示例，用虚构的数据，并注明"示例，不是本场数据"。
- 删除旧提示词中关于 `[[编号]]` 引用、裸编号的指令，以及旧的报告结构说明。

### 4. 排版（新建 `report/render.py`）

`render_diagnosis(d: Diagnosis, mm: MatchMoments, names) -> str`，输出 Markdown：

1. `# 复盘报告：{玩家} · {地图}`，以及比分、模型名；
2. **总览**：AI 写的；
3. **突出问题**：频率对比中 `prominent` 的行，由程序生成的小表；
4. **关键问题**：每条 finding 一节：
   - 标题：`### {问题中文名} · {严重程度}`，`other` 加注"AI 判断，无程序依据"；
   - **发生了什么**：每个 target 的事实卡，用 002 的函数；回合 target 用漏补枪事实；`match` 用证据中的数值写一句；
   - **程序检测依据**：该 target 的命中证据与阈值，写成一句，例如"TTD 812 毫秒，高于职业同武器 75 分位 484 毫秒"；
   - **分析**、**怎么练**：AI 写的；
5. **反复出现的问题**：问题名、涉及的瞬间编号、分析；
6. **亮点**；
7. **训练计划**；
8. **跳转命令**：所有被引用的瞬间，取 `jump_command`；
9. 末尾说明：职业样本来源（基准的 `source`）；如有删除的结论，写明"有 N 条结论未通过数据检查，已删除"。

### 5. 收尾

- `cli.py`：`--ai` 时写出 `_report.md`（新排版），另外把 `submission`、`violations_history` 和 `dropped` 写入 `_submission.json`，方便核对。
- `citations.py`：新流程不再需要。确认没有其他地方引用后删除，相关测试一并删除或改写。
- 更新 `docs/features.md` 或 README 中关于报告流程的说明。

## 验收标准

- [ ] `tests/test_ai.py` 用假客户端（**不调用真实 API**）覆盖：
  - 第一次就提交合格 → 生成报告，`repairs == 0`；
  - 第一次有违规、第二次合格 → `repairs == 1`，错误结果中包含违规说明；
  - 一直不合格 → 达到上限后删除违规条目，`dropped` 有记录，报告仍然生成；
  - 删完后一条 finding 都不剩 → `DiagnosisError`；
  - 总览不合格被替换；
  - 不调用 `submit_report` 就结束 → 收到要求提交的消息；
  - 提交参数结构错误；
  - 先调用 `get_moment` 再提交；
  - `refusal`、`max_tokens`、`error` 的处理保持不变。
- [ ] `tests/` 中新增 `render_diagnosis` 的测试：各节都出现；`other` 有标注；事实卡、依据、跳转命令正确。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] **不要运行 `--ai`。** 真实调用由 Claude 在征得用户同意后执行。完成报告中写明：用假客户端，基于 5E Mirage 真实数据和 `tests/fixtures/mirage_submission_valid.json` 排版出来的报告，保存在 `data/output/` 下的文件名。

## 范围外（不要做）

- 不改问题检测、检查器的规则和阈值。发现检查器误杀时写进遗留问题。
- 不做网页。
- 不调用真实的大模型 API。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
