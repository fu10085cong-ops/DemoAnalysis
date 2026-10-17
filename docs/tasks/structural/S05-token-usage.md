# S05 模型用量：逐轮记录、修正缓存统计、固定内容移到可跨报告缓存的位置

状态：待做
难度：B（不改模型调用逻辑；只改用量统计和提示词的拼接位置）
执行：Codex，思考强度 medium
分支：`task/S05-token-usage`

## 目标

现在一份单场报告的输入约 10 万 token，多场汇总约 25 万。可是看不出钱具体花在哪一轮，缓存统计也有错。本任务做三件事：

1. **把每一轮的用量记下来**，作为以后每项省 token 优化的测量工具（结构性任务要求先能测量）。
2. **修正缓存统计**：现在只读 DeepSeek 专用的 `prompt_cache_hit_tokens`。小米 MiMo 用的是 OpenAI 标准字段 `prompt_tokens_details.cached_tokens`，所以报告里显示"缓存命中 0"。Claude 2026-10-17 实测，两个相同的请求，第二个 4,957 个输入 token 中有 4,928 个命中缓存。
3. **把每场都一样的说明文字从比赛摘要挪进系统提示词**。服务端缓存按前缀匹配，挪过去之后，不同比赛、不同玩家的报告之间也能命中缓存。

Claude 量过的现状（完美 Dust2 单场）：比赛摘要 14,800 字符，其中占位符说明约 2,700 字、练习图白名单约 400 字；系统提示词 4,000 字；提交工具定义 4,600 字；查询工具定义 1,600 字。

## 背景（必须读）

- `src/cs2coach/ai/providers.py::Usage`
- `src/cs2coach/ai/openai_compat.py`：`send` 里构造 `Usage` 的那一段，以及 `self.messages` 的组装
- `src/cs2coach/ai/claude.py`：Claude 的用量字段（`cache_read_input_tokens` 等）
- `src/cs2coach/ai/diagnose.py`：`DiagnosisResult` / 循环里累计用量和 `prompt_tokens_history` 的地方
- `src/cs2coach/cli.py`：写 `submission.json` 的地方（搜索 `prompt_tokens_history`），以及打印"输入 … tokens（其中缓存命中 …）"的那一行
- `src/cs2coach/tools/slots.py::slot_guidance`、`src/cs2coach/tools/drills.py::drill_guidance`、`src/cs2coach/tools/digest.py::build_digest`
- `src/cs2coach/summary/digest.py`、`src/cs2coach/summary/prompts.py`：多场汇总的对应位置
- `src/cs2coach/tools/checker.py`：**检查器会读取摘要文本**（`check_submission(..., digest, ...)`）。移动内容之前，先确认检查器依赖摘要里的哪些部分
- `scripts/eval_batch.py`：评测汇总表

## 要做的事

1. **`Usage` 新增 `reasoning_tokens`**：
   - 来自 `completion_tokens_details.reasoning_tokens`，没有时为 0；
   - 缓存命中改为：`prompt_cache_hit_tokens`（DeepSeek）和 `prompt_tokens_details.cached_tokens`（OpenAI 标准）**取有值的那个**，都没有时为 0；
   - Claude 的统计保持现状。
2. **逐轮用量**：
   - 每次请求的 `Usage`（输入、缓存命中、输出、思考）按顺序记录下来；
   - 所有服务都写进 `submission.json`，键名 `usage_turns`，现在只有本地模型才写；
   - 原来的 `prompt_tokens_history` 保留，兼容旧格式。
3. **命令行输出**：总用量那一行加上轮数和思考 token，例如"5 轮；输入 102,439 tokens（其中缓存命中 78,000）、输出 15,110 tokens（其中思考 11,000）"。
4. **评测汇总表**：`eval_batch.py` 的汇总加上每个 case 的轮数、缓存命中率、思考 token 占输出的比例。
5. **固定内容挪进系统提示词**：
   - `drill_guidance` 的全部内容，以及 `slot_guidance` 中**每场都一样**的部分（字段表、显示样式、写法规则），从摘要里移到系统提示词末尾；
   - 本场才有的部分，例如本场的计数值，留在摘要里，标题保持"## 占位符"；
   - 单场和多场汇总都要改；
   - **必须保证检查器的结果不变**：如果检查器要用到被移走的文字，就把系统提示词也传给它，或者改为直接读取同一个数据源。不要削弱任何检查。
6. **系统提示词保持完全固定**：不能含有日期、玩家名、地图名等每场不同的内容，否则会破坏缓存。加一个测试：两场不同的比赛生成的系统提示词完全相同。

## 验收标准

- [ ] 单元测试：
  - **缓存统计**：用假客户端分别返回 DeepSeek 字段、OpenAI 标准字段、两者都没有三种情况，缓存命中和思考 token 都正确；
  - **逐轮用量**：`usage_turns` 的条数等于请求轮数，累计值等于总用量；
  - **系统提示词**：两场不同比赛（合成数据）生成的系统提示词逐字相同；单场和多场各一个；
  - **检查器**：用现有的回归样例（`tests/fixtures/mirage_*`），确认检查结果在改动前后相同。
- [ ] **离线对比**，不调用模型：对完美 Dust2 那一场，贴出改动前后的系统提示词字符数和摘要字符数。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] **不调用任何付费 API**。真实的改动前后对比由 Claude 用评测集来做。
- [ ] `privacy_scan.py` 退出码为 0；完成报告中玩家名替换成"（已匿名）"。

## 范围外（不要做）

- 不减少轮数、不改思考强度、不压缩"决定性瞬间"列表：这些会影响报告质量，等测量工具好了再分别做 A/B 实验。
- 不改检查规则本身、提示词里的写作要求、提交格式。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 离线对比：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
