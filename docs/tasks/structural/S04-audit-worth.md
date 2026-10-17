# S04 审计判定加第二项"值不值得提醒"

状态：完成
难度：B（照现有写法扩展，改 1–2 个文件）
执行：Kimi
分支：`task/S04-audit-worth`（独立工作目录 `../democheck-033`）

## 目标

现在审计只判"事实对不对"（`verdict`）。但事实成立，不代表教练会把它当成失误来提醒，比如下包 3 秒后就阵亡，身上的火根本来不及扔。

本任务在判定文件里加第二项 `worth`，报告里再输出一张"有用率"表：事实成立并且值得提醒的比例。第三轮审计会用到它。

## 背景（只读这些）

- `src/cs2coach/audit.py`：`review_page`、`update_labels`、`wilson`、`precision_report`
- `tests/test_audit.py`：`record()` 的写法
- **预写测试**：`tests/test_audit_worth.py`。Claude 已经写好，**不要修改**；认为测试有错，停下来写进完成报告。

## 要做的事

1. **`update_labels`**：新条目在 `verdict` 后面加一行 `worth = ""`。已有条目一字不动，现有测试已经保证这一点。
2. **`review_page`**：每条的判定行加上"值得提醒：________（yes / no，只在 correct 时填）"。
3. **`precision_report`**：在"错误原因"一节之后，新增一节 `## 有用率（事实成立且值得提醒）`。
   - 表头：`| 问题 | 层 | 已判定 | 有用 | 无用 | 未填 | 有用率 | 95% 区间 |`
   - 三类的定义：
     - **有用**：`verdict = correct` 且 `worth = yes`；
     - **无用**：`verdict = wrong`，或者 `verdict = correct` 且 `worth = no`；
     - **未填**：`verdict = correct` 但 `worth` 为空或缺失。未填的不计入有用率；`unsure` 完全不计入。
   - "已判定" = 有用 + 无用 + 未填。
   - 分层和加权与准确率表完全相同：可疑层、不可疑层，再按两层在全部命中中的比例加权，区间用 Wilson。可以把准确率表的计算抽成一个公用函数，两张表共用。
   - 只列至少有一条 `worth` 已填写的问题。全部都没填时，这一节只写一行：`暂无“值不值得提醒”判定。`
   - 校验：`worth` 只能是 `""`、`yes`、`no`；`worth` 非空、但 `verdict` 不是 `correct` 时，抛出 `ValueError`。
4. **准确率表本身的输出不变**，预写测试会检查这一点。
5. **文档**：
   - 在 `docs/tasks/structural/S01-detector-audit.md` 末尾补一段"判定口径：第二项 worth"，说明上面三类的定义；
   - 在 `docs/tasks/README.md` 的任务索引里加上 S04 这一行（类型：结构性）。

## 验收标准

- [ ] `PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q tests/test_audit_worth.py tests/test_audit.py` 全部通过。
- [ ] 完成前跑一次全部测试：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q`，报告只贴最后一行。
  - **必须带 `PYTHONPATH=src`**，否则导入的会是主目录里的代码。
- [ ] 不要用真实审计数据运行 `audit_report.py`：它会改写共享目录里的 `precision.md`。真实数据由 Claude 验证。

## 范围外（不要做）

- 不改 `scripts/review_pack.py` 和其他审计脚本。
- 不改检测规则和 `config/`。
- 不读、不改 `data/` 下的任何文件。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
  - `src/cs2coach/audit.py`：`update_labels` 新条目在 `verdict` 后追加 `worth = ""`（已有条目字节不动）；`review_page` 判定行加"值得提醒：________（yes / no，只在 correct 时填）"；`precision_report` 在判定循环里校验 `worth`（只允许 `""`/`yes`/`no`，非空时 `verdict` 必须是 `correct`，否则 `ValueError`）并按问题、分层累计有用/无用/未填，在"错误原因"后新增 `## 有用率（事实成立且值得提醒）` 一节——分层、加权、Wilson 区间与准确率表同一套口径（未重构准确率表，只复用 `wilson`，保证准确率表输出不变）；全部未填时只输出"暂无"值不值得提醒"判定。"。
  - `docs/tasks/structural/S01-detector-audit.md`：末尾补"判定口径：第二项 worth"。
  - `docs/tasks/README.md`：任务索引加 S04 行。
- 测试结果（最后一行）：`757 passed, 4 skipped in 10.24s`（`PYTHONPATH=src ... -m pytest -q`；目标文件 `tests/test_audit_worth.py tests/test_audit.py` 45 passed）
- 遗留问题 / 需要确认的地方：未填的判定行/标签里旧的 `labels.toml` 没有 `worth` 键，读取按缺失即空处理，旧数据无需迁移；未运行 `audit_report.py`（按要求真实数据由 Claude 验证）。

## 审查意见（Claude 填写）

**审查通过（2026-10-17，Claude）。** 这是 Kimi 的第一个任务，逐行审查过：
- 三类的定义、校验、分层加权都和任务单一致；
- 预写测试没有被改动；
- 用 `2026-10-17` 的真实判定只读运行 `precision_report`，准确率表和现有 `precision.md` 完全相同；还没有 worth 判定时，有用率一节显示"暂无"。

**可以改进**：有用率表的加权代码是从准确率表复制的，没有抽成公用函数。不影响正确性，以后改加权方法时要两处一起改。

测试：Claude 复跑 757 passed、4 skipped。比主目录多 1 个 skip，原因是工作目录里没有 `data/`。

