# 021 评测集：固定样例批量生成报告

状态：完成

## 目标

改提示词、规则或换模型之后，能用同一批比赛重新生成报告，量化比较错误率，不再靠零散的个例判断。生成由脚本完成；逐份核对由 Claude 人工进行，结果记在评测记录里。

## 背景（必须读）

- `AGENTS.md`
- `src/cs2coach/cli.py`：`_analyze`、`_summary`、`--provider`、`--model`、输出目录与归档
- `src/cs2coach/ai/diagnose.py`、`summary/diagnose.py`：`Diagnosis` 中的 usage、repairs、violations_history、dropped

## 要做的事

### 1. 评测清单

- 清单放在 `data/eval/eval_set.toml`。`data/` 已被 git 忽略，清单含真实 steamid，不入库。
- 仓库中提供示例 `config/eval_set.example.toml`，用占位的 steamid 说明格式：
  ```toml
  [[case]]
  name = "完美 Dust2 16:14"
  kind = "analyze"               # analyze 或 summary
  demo = "data/demos/perfectworld_demo/9215177492731139980_0.zip"
  player = 76561190000000000
  ```

### 2. 批量脚本 `scripts/eval_batch.py`

```
python scripts/eval_batch.py [--set data/eval/eval_set.toml] [--provider ...] [--model ...] [--label 说明]
```

- 逐个运行清单中的 case，复用 CLI 的同一套代码，不另写一套。
- 输出到 `data/output/评测/<日期时间>_<label>/<case 名>/`，不覆盖各玩家文件夹中的正式报告。
- 汇总表 `summary.md`，每个 case 一行：
  - 模型；
  - 各轮违规数，例如 "11→2→0"；
  - 违规代码分布；
  - 删除和替换的条数；
  - 工具调用次数；
  - 输入（缓存命中）/ 输出 token；
  - 耗时；
  - 报告路径；
  - 留空的"人工核对：错误数"一列，由 Claude 填写。
- 单个 case 失败时记录原因，其余继续。
- 开始前打印将要运行的 case 数和模型。没有 `--yes` 时，等待用户输入 y 确认；真实调用会产生费用。

### 3. 评测记录

新建 `docs/eval_log.md`，说明怎么记录：每次评测一节，包括日期、改动内容、模型、汇总表链接、人工核对的错误数和类型。先写好格式说明，内容由 Claude 后续填写。

## 验收标准

- [ ] 测试：
  - 清单解析；
  - 用假的诊断函数跑两个 case，其中一个失败：失败的被记录，另一个继续；
  - `summary.md` 各列正确；
  - 输出目录不覆盖正式报告；
  - 没有 `--yes` 时需要确认。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 用本地模型（Ollama，免费）跑一个只含 1 个 case 的清单，贴出 `summary.md`。不要调用 DeepSeek 或 Claude。

## 范围外（不要做）

- 不做自动判定报告对错。人工核对由 Claude 完成。
- 不改诊断流程和检查器。

---

## 完成报告

GPT 额度用完，本任务由 Claude 完成（2026-10-16）。

- **改动的文件及原因**：
  - `scripts/eval_batch.py`（新建）：清单校验，CLI 参数与手动运行相同，逐个运行，单个 case 出错只记录原因，汇总写入 `summary.md`，运行前确认；
  - `config/eval_set.example.toml`；
  - `docs/eval_log.md`：记录方法，以及错误怎么分类、怎么计数；
  - `cli.py`：审计文件新增 `run` 字段（模型、修正次数、工具调用、token），汇总表从这里读取；`tests/test_render.py` 同步；
  - 测试：`tests/test_eval_batch.py`。
- **真实评测集**：`data/eval/eval_set.toml`，共 5 个 case：
  - 完美 Dust2 玩家01；
  - 5E 玩家02；
  - 5E 玩家03；
  - ESL ZywOo；
  - 玩家01的多场汇总。

  另有 1 个 case 的验收清单 `local_one.toml`。都不入库。
- **测试结果**：`pytest -q`：583 passed, 3 skipped。
- **本地验收**：`--provider local`，只跑 1 个 case，成功。各轮违规数 14→5→2，删除 1 条，工具调用 3 次，输入 70004 / 输出 4504 token，用时 171 秒。

## 审查意见（Claude 填写）

由 Claude 实现，没有经过另一方审查。
