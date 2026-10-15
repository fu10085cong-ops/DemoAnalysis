# 009 结构化诊断：接入调用流程、改写提示词、排版报告

状态：完成

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

- [x] `tests/test_ai.py` 用假客户端（**不调用真实 API**）覆盖：
  - 第一次就提交合格 → 生成报告，`repairs == 0`；
  - 第一次有违规、第二次合格 → `repairs == 1`，错误结果中包含违规说明；
  - 一直不合格 → 达到上限后删除违规条目，`dropped` 有记录，报告仍然生成；
  - 删完后一条 finding 都不剩 → `DiagnosisError`；
  - 总览不合格被替换；
  - 不调用 `submit_report` 就结束 → 收到要求提交的消息；
  - 提交参数结构错误；
  - 先调用 `get_moment` 再提交；
  - `refusal`、`max_tokens`、`error` 的处理保持不变。
- [x] `tests/` 中新增 `render_diagnosis` 的测试：各节都出现；`other` 有标注；事实卡、依据、跳转命令正确。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] **不要运行 `--ai`。** 真实调用由 Claude 在征得用户同意后执行。完成报告中写明：用假客户端，基于 5E Mirage 真实数据和 `tests/fixtures/mirage_submission_valid.json` 排版出来的报告，保存在 `data/output/` 下的文件名。

## 范围外（不要做）

- 不改问题检测、检查器的规则和阈值。发现检查器误杀时写进遗留问题。
- 不做网页。
- 不调用真实的大模型 API。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `tools/digest.py`：在瞬间列表前加入问题频率及按问题分组的命中与中文依据，突出项明确标记；新增共用比分统计，供摘要、程序替换总览和报告使用。保留原比赛、枪法、瞬间和漏补信息。
  - `ai/diagnose.py`：先生成 ProblemReport / FrequencyRow，再提供查询工具和 submit_report。结构错误、内容违规、没有提交及同轮重复提交都会得到中文错误；正常查询错误不计入结论修改次数。保留模型拒绝、截断、异常和最大轮数处理。
  - 调用流程在本地核对 submit_tool 已有 schema 的枚举、数量、长度、targets 格式与去重约束，防止未强制 schema 的兼容服务提交空 targets 或空 findings；约束全部来自现有工具定义，不增加检测或检查器规则。
  - 默认允许两轮修改。达到上限后按原始路径删除整条违规 finding/pattern/highlight；跨组 other 超限保留顺序靠前且无其他违规的允许数量。总览违规时由程序生成比分和击杀/死亡数替换句。删除/替换后再次校验，未通过或一条 finding 都不剩时抛 DiagnosisError。
  - Diagnosis 去掉 raw_text/citations，新增最终 submission、problems、frequency、violations_history、dropped 和程序比分；保留报告、摘要、服务/模型、usage、tool_calls、repairs。DroppedItem 保存原始路径、原内容、违规原因和 removed/replaced 动作，便于区分删条与总览替换。
  - `ai/prompts.py`：重写为结构化工作方式、优先突出项、同类归入 patterns、情境/原因/更好处理的讲解要求、具体训练和严重度定义；保留原指标口径、距离单位、拉栓时间、接触速度及近似性说明。给出明确标为虚构的 finding 示例，移除旧引用和自由文本报告结构指令。
  - `report/evidence.py`、`report/render.py`：共用中文检测依据；最终报告包含程序比分、模型名、突出频率、002 事实卡、依据与阈值、AI 分析/训练、规律、亮点、训练计划、去重的跳转命令、职业来源及删除数量。回合 target 使用漏补事实，match 使用整场命中的数值，other 标明 AI 判断、无程序依据。
  - `cli.py`：保存新排版的 `_report.md`，同时保存含 submission、violations_history、dropped 的 `_submission.json`；统计文案改为结论修正。
  - `tools/checker.py`：仅将从旧 citations 模块导入的 CITATION 正则原样迁移到本模块；没有改变其规则。确认无剩余代码依赖后删除 `tools/citations.py`，并替换其旧诊断/引用测试。
  - `config/default.toml` 的 ai.max_repairs 改为 2；`docs/features.md` 同步结构化流程和输出说明。
  - `tests/test_ai.py` 改用结构化提交的假客户端测试，保留服务适配、缓存、thinking/reasoning、工具错误和停止原因验证，补充所有任务要求的修改/删除路径、全局 other 超限、重复提交、格式边界与最大轮数。`tests/test_render.py` 覆盖完整排版、事实卡、依据、整场数据、跳转去重、删除数量、提示词口径及 CLI 双输出。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：**482 passed, 3 skipped**。诊断和排版专项共 43 项通过；`git diff --check` 通过。未新增依赖。
- 真实 demo 验证：
  - 使用指定 5E Mirage `g173-n-20261014223216271995270_de_mirage.dem`，玩家 `76561199711881069`。只解析 MatchData，复用已有的 28 回合、37 个瞬间的 MatchMoments 和当前职业基准，提交文本直接取自 `tests/fixtures/mirage_submission_valid.json`。
  - 注入 FakeAnthropic 假客户端，模拟三次提交，同一固定样例经过两轮修改机会后，删除其中已在 008 降为背景/停用的 weapon_not_ready。最终保留 **2 条 finding、1 条 pattern、1 条 highlight**，repairs=2，dropped=1；最后一份对象再次通过格式和内容校验。
  - 删除记录原路径为 `findings[2]`，问题为 weapon_not_ready，原目标 R9-death。原因包含当前 schema 不允许该编号、context_only 和 disabled_problem；这是 008 后预期的审核结果，未修改历史 fixture 或检查规则。
  - 报告包含程序比分 **12:16**、模型标记 `fake-claude-no-api`、出枪慢 1.81 倍和准星没提前放好 1.54 倍的突出表、真实事实卡/阈值和游戏跳转命令。
  - 输出文件：
    - `data/output/g173-n-20261014223216271995270_de_mirage_76561199711881069_structured_fake_report.md`
    - `data/output/g173-n-20261014223216271995270_de_mirage_76561199711881069_structured_fake_submission.json`
    - `data/output/g173-n-20261014223216271995270_de_mirage_76561199711881069_structured_fake_digest.md`
  - **没有运行 --ai，没有调用真实大模型 API，没有读取真实 .env**；CLI 输出测试将凭据加载替换为空操作，demo 原文件只读。
- 遗留问题 / 需要确认的地方：
  - 用户已确认：每次回复只调用一次 submit_report，收到失败结果后允许重新提交；max_repairs=2 表示两次修改机会，第三次仍有违规才删条。始终无法形成可检查对象时抛错；有历史可检查对象时使用最后一份进行删除/替换。
  - 总览替换单独记录为 replaced，不计入“有 N 条结论已删除”的 removed 数量；替换句本身明确写明 AI 总览未通过检查。
  - 数量/长度等格式错误也允许两轮修正。若是 training_plan 等全局字段的格式错误，无法通过删除 finding/pattern/highlight 修复，最终会明确抛错，不输出仍有违规的报告。
  - 未发现需要修改检查器数值或敏感说法规则的误杀。数字集合核对及敏感说法谓词仍沿用 007 的首版语义边界。
  - 固定样例与假客户端验证了接入、检查和排版；真实模型的讲解质量需 Claude 在获得用户同意后验证。此次未做网页、未改检测条件/阈值、未提交。

## 审查意见（Claude 填写）

**审查通过（2026-10-16）。** 提示词保留了全部指标口径，讲解和准确性要求具体；修正、删除、替换路径都有测试；排版结构符合设计，已降为背景的 weapon_not_ready 被检查器正确拦下删除。482 个测试通过。讲解质量要靠真实模型验证，由 Claude 在用户同意后执行。
