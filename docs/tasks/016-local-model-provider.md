# 016 接入本地模型（Ollama）

状态：完成

## 目标

让单场诊断和多场汇总可以用本机 Ollama 上的模型生成报告：免费，数据不离开电脑。这也是架构原则 4 的实际检验："AI 模型可替换，便宜的模型也要能用好"。

## 背景（必须读）

- `CLAUDE.md`（架构原则 4）、`AGENTS.md`
- `src/cs2coach/ai/providers.py`、`ai/openai_compat.py`：现有的 OpenAI 兼容接口实现，目前按 DeepSeek 写死了一些参数
- `src/cs2coach/ai/diagnose.py`（共用的诊断循环）、`summary/diagnose.py`
- `config/default.toml` 的 `[ai]`、`[ai.deepseek]`
- `src/cs2coach/cli.py`

Claude 已在本机实测（2026-10-16，RTX 4070 Ti Super 16 GB，Ollama 0.34.0）：

- **Ollama 的 OpenAI 兼容接口**：`http://localhost:11434/v1`，`api_key` 随便填。用 `openai` SDK 调用 `chat.completions.create`，工具调用正常：`finish_reason == "tool_calls"`，参数是合法的 JSON。
- **思考内容**放在 message 的 `reasoning` 字段（`model_extra["reasoning"]`），**不是** DeepSeek 的 `reasoning_content`。
- **Ollama 默认只有 4096 token 的上下文，超出部分会被静默截断，不报错。** 已经新建了模型 `qwen3-14b-40k`（`FROM qwen3:14b`，`num_ctx 40960`）。原生接口 `POST http://localhost:11434/api/show`（body：`{"model": "..."}`）返回的 `parameters` 文本里有 `num_ctx 40960`。
- **实测 token 数**（Qwen3 分词）：汇总摘要约 6,300（妮妮孩孩major冠军 21 场）；单场摘要约 15,000（完美 Dust2，30 回合、56 个瞬间）；提示词约 2,000；`submit_report` 的工具定义约 1,500。中文按字符估算，约 0.76 token / 字。
- **速度**：输入约 2,000 token / 秒，生成约 60 token / 秒。

## 要做的事

### 1. 配置

在 `config/default.toml` 新增 `[ai.local]`，每项带中文注释：

- `base_url = "http://localhost:11434/v1"`；
- `model = "qwen3-14b-40k"`；
- `api_key_env = ""`：为空时不需要密钥，SDK 传一个占位值；
- `max_tokens`：本地模型的输出上限，包括思考过程，默认 8192；
- `reasoning_field = "reasoning"`：思考内容所在的字段名，DeepSeek 为 `reasoning_content`；
- `context_check = "ollama"`：启动前向 Ollama 查询上下文长度；设为 `"none"` 时跳过，用于其他本地服务；
- `context_margin_tokens`：为输出预留的余量；
- `chars_per_token = 0.76`：用于估算 token 数；
- `max_tool_calls`：本地模型的查询次数上限，避免上下文被撑爆，默认 8。

`[ai.deepseek]` 补上 `reasoning_field = "reasoning_content"`。DeepSeek 专用的参数（`thinking`、`reasoning_effort` 的 `extra_body` 写法）改成**按配置决定是否发送**：本地模型不发送。**DeepSeek 的现有行为必须不变**，用现有测试保证。

### 2. 防止上下文被静默截断（最重要）

- **开始前**：`context_check == "ollama"` 时，调用 `/api/show` 取得 `num_ctx`。查不到时报错，提示用户新建带 `num_ctx` 的模型。
  - 估算第一轮的输入量：系统提示词 + 摘要 + 用户指令 + 工具定义，用字符数 × `chars_per_token`。
  - 估算值 + `max_tokens` + `context_margin_tokens` > `num_ctx` 时，**直接报错，不调用模型**。报错信息中写明估算值、上下文长度和建议（例如换一个上下文更大的模型）。
- **每一轮之后**：
  - 用返回的 `usage.prompt_tokens` 检查：超过 `num_ctx - max_tokens` 时，停止并报错；
  - 如果 `prompt_tokens` **比上一轮还少**，说明 Ollama 截断了历史，立即报错。
- **工具调用次数**达到 `max_tool_calls` 时，提示模型"查询次数已用完，请直接提交"，并且这一轮不再提供查询工具，只保留 `submit_report`。

### 3. 思考内容

`openai_compat` 按 `reasoning_field` 读取思考内容，按现有逻辑回传。本地模型是否需要回传，按实际测试的结果决定，并在完成报告中说明。

### 4. 命令行

`analyze` 和 `summary` 都新增 `--provider {deepseek,claude,local}`，覆盖配置中的 `[ai].provider`。用量的打印格式不变；本地模型额外打印总耗时。

### 5. 文档

- `README.md`：本地模型的用法，包括新建大上下文模型的 Modelfile 写法、需要的 Ollama 环境变量（`OLLAMA_FLASH_ATTENTION=1`、`OLLAMA_KV_CACHE_TYPE=q8_0`），以及"跑报告时显存要空出 12–14 GB"；
- `docs/features.md`：上下文检查的规则。

## 验收标准

- [ ] 测试（假客户端，monkeypatch 掉 `/api/show` 的请求）：
  - 本地 provider 不发送 DeepSeek 专用参数；DeepSeek 的现有测试全部不改且通过；
  - 从 `reasoning` 字段读取思考内容；
  - 上下文检查：
    - 估算值超出时，在调用模型之前报错；
    - `/api/show` 查不到 `num_ctx` 时报错；
    - 某一轮的 `prompt_tokens` 超过上限时报错；
    - `prompt_tokens` 比上一轮少时报错；
  - 达到 `max_tool_calls` 后，只提供 `submit_report`；
  - `--provider` 覆盖配置。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] **真实本地运行**（本地模型免费，**允许真实调用 Ollama**；**不要调用 DeepSeek 或 Claude**）：
  ```
  python -m cs2coach summary --player 76561199703310441 --ai --provider local --out <临时目录>
  ```
  完成报告中写明：
  - 总耗时、每轮的 `prompt_tokens`；
  - 修正次数、删除的条目、`violations_history` 的摘要；
  - 是否成功生成 `report.md`，并贴出总览和第一条 finding。

  如果因为上下文或查询次数失败，如实报告失败的原因和数据，不要为了通过而改阈值。

## 范围外（不要做）

- 不改提示词的内容、检查器的规则和问题检测。
- 不比较本地模型和 DeepSeek 的质量（Claude 在审查后另做）。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：`config/default.toml` 新增 `[ai.local]` 和 DeepSeek 的 `reasoning_field`；`ai/providers.py`、`ai/openai_compat.py` 接入本地服务、按配置发送 DeepSeek 专属参数、读取对应思考字段，并在 `/api/show` 预检及每轮核对上下文；`ai/diagnose.py`、`summary/diagnose.py` 共用查询次数限制和逐轮 token 记录；`cli.py` 为单场、多场增加 `--provider`，本地调用不读取 `.env`，额外打印耗时和每轮 token；`README.md`、`docs/features.md` 写明用法及上下文规则；`tests/test_local_provider.py` 用假 HTTP/假模型覆盖上述保护。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：546 passed，3 skipped。原有 DeepSeek 测试未修改并通过；`git diff --check` 无空白错误。本机 Ollama 的实际回复在 `model_extra["reasoning"]` 中带有思考内容；保留回传该字段，真实三轮对话正常完成。
- 真实 demo 验证：运行 `summary --player 76561199703310441 --ai --provider local --out C:\Users\David\AppData\Local\Temp\cs2coach-016-validation`，21 场、在场 438 回合，成功生成 `妮妮孩孩major冠军_76561199703310441\汇总\report.md` 与 `submission.json`。AI 阶段总耗时 **108.9 秒**，三轮 `prompt_tokens` 为 **9902、16578、19578**；调用工具 3 次，修正 2 次，最后删除 1 条 `patterns[0]`。`violations_history` 各轮分别为 13、5、3 项：首轮有 target 数量/格式、无命中及数字错误；次轮仍有无命中和数字错误；末轮仅剩该 pattern 的 3 项数字错误，删除后报告生成。完整日志在 `C:\Users\David\AppData\Local\Temp\cs2coach-016-run.log`。

  总览原文：

  > 妮妮孩孩major冠军在21场比赛中表现总体稳健，取得18胜2负1平的战绩，击杀455次、死亡273次、关键助攻69次。但存在两个突出短板：出枪慢（TTD中位数469ms，高于职业375ms）和准星预判偏差（Crosshair Placement中位数4.26°，高于职业3.83°）。问题集中在de_dust2（68%）和de_inferno（38%），且在21场中19场出现出枪慢问题。

  第一条 finding 原文：

  ```json
  {
    "problem": "slow_reaction",
    "targets": ["M20:R23-kill1", "M12:R4-death"],
    "severity": "medium",
    "analysis": "在de_dust2 23回合（M20）和de_inferno 4回合（M12）中，首次命中耗时达969ms，超过职业同武器75分位值近一倍。例如M12回合4，双方5v4且经济相近，对手TTD为641ms，自己慢了328ms，给了对手先伤机会。更好的处理是接敌前将准星放至可能露头位置，控制移动完成首枪。",
    "training": "在创意工坊地图《Deagle Duel》进行预瞄练习，每组30次共3组。首枪目标切换用时需控制在300ms内，下次复盘检查首次命中耗时。"
  }
  ```

- 遗留问题 / 需要确认的地方：第一条 finding 把 `M12` 的地图写成 `de_inferno`，真实数据及程序事实卡均为 `de_dust2`。现有检查器不核对分析文字中的地图名；本任务范围外，留给 Claude 审查后决定是否立新任务。未调用 DeepSeek 或 Claude。

## 审查意见（Claude 填写）

**审查通过（2026-10-16）。** 本地模型接入正确；上下文保护有效：开始前先查 `num_ctx` 并估算输入，每轮检查 token 数，发现截断立即报错；DeepSeek 的现有测试不改且通过。546 个测试通过。

Claude 对本地报告的核对（千问 14B，与 DeepSeek 版对比）：内容只剩 1 条关键问题和 1 条亮点，至少有 4 处事实或口径错误：
- 地图写错；
- 把次数当成百分比（"de_dust2（68%）"）；
- 把慢的 TTD 说成"仅 812ms"；
- 编造了创意工坊地图名。

结论：正式报告仍然用 DeepSeek。暴露的检查器漏洞（地图名、百分比口径、练习图名）另行处理。

