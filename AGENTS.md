# AGENTS.md（给 Codex 等编码代理）

本项目由两个 AI 协作开发：**Claude 负责和用户沟通、定方案、写任务单、审查改动；你（GPT / Codex）负责按任务单写代码。**

## 必读

1. `CLAUDE.md`：项目规则（产品目标、架构原则、技术约定、demoparser2 陷阱）。**对你同样适用**，与本文件冲突时以 CLAUDE.md 为准。
2. 当前任务单：用户指定的 `docs/tasks/<features|fixes|structural>/` 下的任务单。格式和状态说明见 `docs/tasks/README.md`。
3. 任务单"背景"里列出的文档和代码（常见：`docs/features.md` 指标定义，`docs/fields.md` 数据陷阱，`docs/parsing.md` 解析层输出格式）。

## 工作规则

- **只做任务单范围内的事。** 发现范围外的问题，写进完成报告的"遗留问题"，不要顺手改。
- **有歧义就停下来提问。** 任务单不清楚、与文档或现有代码矛盾、验收标准做不到时，不要猜：把问题写进完成报告，状态改为"待审查"。
- **不要提交。** 不执行 `git commit`、`git push`，不切换或新建分支，不改 git 配置。改动留在工作区，由 Claude 审查、用户确认后提交。
- **必须跑测试。** 完成前运行全部测试并全部通过：
  - Windows：`.venv/Scripts/python.exe -m pytest -q`
  - macOS / Linux：`.venv/bin/python -m pytest -q`
- **完成后填写任务单末尾的"完成报告"**（改了哪些文件、为什么这样做、测试结果、遗留问题），把状态改为"待审查"。
- 审查后状态变为"需修改"时，按"审查意见"逐条处理，在完成报告中逐条回应。

## 环境注意

- 虚拟环境 `.venv` 已经装好。**不要从 PyPI 重装或升级 demoparser2**：PyPI 版有已确认的 bug，这里装的是 `scripts/build_demoparser.py` 编译的修复版（见 CLAUDE.md）。新增依赖须写进 `pyproject.toml`，并在完成报告中说明。
- **不要运行 `--ai` 或任何真实的大模型 API 调用**（会产生费用）。AI 层的测试一律用假客户端。
- **不要读取、打印或修改 `.env`**（里面是 API 密钥）。
- `data/` 下的 demo 只读。解析一场 demo 约 40 秒，需要真实 demo 验证时，在完成报告中写明用了哪些 demo、结果如何。

## 代码风格

- 和周围代码保持一致：中文注释和 docstring，注释密度、命名、写法照着同目录的文件来。
- 路径用 `pathlib`，文本读写显式 `encoding="utf-8"`。
- **阈值一律放进 `config/default.toml`。** 配置是严格校验的：新键必须先在默认配置中声明。
- 每个指标计算和决定性瞬间识别都要有单元测试，造数据用 `tests/helpers/synthetic.py`。
- 指标口径改动要同步更新 `docs/features.md`；数据层的新发现写进 `docs/fields.md`。
