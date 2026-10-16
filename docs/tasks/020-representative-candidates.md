# 020 代表例子由程序给出候选，补"下包前"规则

状态：待做（在 019 完成之后做）

## 目标

1. finding 的代表性交火只能从程序排出的前 N 名候选中选。避免选到勉强过线的弱例子：本地报告曾把只比职业阈值慢 8 毫秒的 R1 当作"出枪慢"的主要例子，另一个慢 344 毫秒的反而没讲。
2. "下包前"之类的说法，要有数据支持。闪光助攻的分析曾写成"下包前"，实际当时已经下包。

产品负责人已确认：程序给出前 3 名候选，AI 从中选择。

## 背景（必须读）

- `AGENTS.md`
- `src/cs2coach/summary/digest.py`：`_deviation`、`representative_hits`（多场已有的排序规则，单场复用）
- `src/cs2coach/tools/digest.py`：`build_digest` 中"检测到的问题"一节
- `src/cs2coach/tools/checker.py`：`_check_entries`；`PREDICATES` 及 `[[checker.phrases]]` 的处理
- `config/default.toml`：`[checker]`、`[summary] representatives_per_problem`、`[[checker.phrases]]` 中 `planted` 一条

## 要做的事

### 1. 单场候选

- 把 `_deviation` 和排序规则移到单场、多场都能用的位置，例如 `features/problems.py` 或新模块，避免两份代码。
- 排序规则沿用 `representative_hits`：
  - 漏补：先看有没有被别人补回，再看距离；
  - 其余：按阈值偏离从大到小；
  - 并列时按时间先后。
- 单场摘要"检测到的问题"中，每个问题的命中按这个顺序列出，前 N 个标"〔候选〕"。N 由新配置 `[checker] finding_candidates = 3` 决定。
- 检查器：finding 的每个 target 必须在该问题的前 N 名中，否则报 `not_representative`，说明里列出前 N 名的编号。
  - 命中不超过 N 个时，都算候选；
  - pattern 和 highlight 不受限；
  - `match` 和 `other` 不受限。

### 2. 多场同样执行

多场摘要已经只列出 `representatives_per_problem` 个代表。检查器也要执行同一条规则：finding 的 target 必须在这些代表之中。

### 3. "下包前"规则

- 新谓词 `not_planted`：
  - 瞬间：`m.context.bomb_planted is False`；
  - 漏补记录：`r.bomb_planted is False`。
- 新增一条 `[[checker.phrases]]`：
  - 匹配"下包前""没下包""未下包""包还没下"；
  - 至少一个 target 满足 `not_planted`。

## 验收标准

- [ ] 测试：
  - 排序正确：偏离大的在前，漏补规则不变；
  - 摘要中标出候选；
  - 单场 finding 选了第 4 名报 `not_representative`，选前 3 名通过；
  - 命中只有 2 个时都可以选；
  - 多场同样执行；
  - pattern 和 highlight 不受影响；
  - "下包前"在已下包时报错，未下包时通过；漏补记录也适用；
  - 原有的 `planted` 规则不变。
- [ ] 多场原有测试全部通过：排序代码移位置后，结果不变。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 不调用 DeepSeek 或 Claude。

## 范围外（不要做）

- 不改检测阈值、频率规则和事实卡。
- 不改 019 的占位符规则。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
