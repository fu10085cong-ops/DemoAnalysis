# R05 研究数据目录分类：按名字查找子文件夹

状态：完成
难度：B
执行：Codex（思考强度 medium）
分支：在当前工作目录直接改（`parsing-layer`），不要提交

## 目标

`data/research/review/`（30 个复盘包）和 `data/research/stance/`（30 次判定结果）太乱，要分到子文件夹里，例如 `review/gold/gold-1`、`review/old/r02-1`、`stance/old/st-3a`。分类后**所有命令里的名字不变**（仍写 `gold-1`、`st-6b`），程序自己找到它在哪个子文件夹。本任务只改代码，搬文件夹由 Claude 做。

## 背景（必须读）

- `scripts/research_review.py::_pack_dir`、`_name`：现有的复盘包路径
- 下面"要改的地方"列出的每一行：现在都是 `root / "data" / "research" / "review" / <名>` 或 `REVIEW / <名>`、`... / "stance" / <名>`、`OUT / <名>` 这种直接拼接

## 要做的事

1. 新建 `src/cs2coach/research/paths.py`：
   ```python
   def research_dir(kind: str, name: str, root: Path) -> Path:
       """kind 是 "review" 或 "stance"。按顺序：
       1. name 里带 "/"（如 "old/r02-1"）→ base / name，不查找
       2. base / name 存在 → 返回它
       3. 在 base 的一级子文件夹里找 base/<分类>/name（跳过以 "." 或 "_" 开头的分类）：恰好一个 → 返回；多个 → 抛 ValueError，列出所有位置
       4. 都没有 → 返回 base / name（新建的包、新的判定结果放在顶层）
       base = root / "data" / "research" / kind
       """
   ```
   分类文件夹本身的名字（如 `gold`、`old`）不能被当成包：第 2 步只认含有 `review.toml`（review）或 `windows.csv` / `facts.csv`（stance）的目录；不含这些文件的同名目录跳过、继续第 3 步
2. 把下列位置改成调用 `research_dir`（`root` 参数照原来的传，模块级 `ROOT` 的照用 `ROOT`）：
   - `scripts/research_review.py::_pack_dir`
   - `scripts/research_stance.py`：所有 `"review" / ...`（含 `f"{pack_name}-adj"`、`exclude_packs` 循环里的 `old`、`compare_pack` 的 `exclude_pack`）、所有 `"stance" / <source>`、`OUT / name`（`extract`、`label` 写出时：名字已存在就写回原处，不存在就建在顶层）
   - `scripts/research_vision.py`、`scripts/research_text_ai.py`：所有 `REVIEW / <包>`（含 `f"{pack}-adj"`）、`"stance" / source`、`"stance" / extract`
   - `scripts/research_replay.py`、`scripts/research_firstkill.py`、`scripts/research_ai.py` 里拼 `"review"` 或 `"stance"` 包路径的地方（`grep -n '"review"\|"stance"' scripts/*.py` 逐个看）
   - **不要改**：复盘包条目里的 `archive = "stance/st-2/archive/..."`（相对 `data/research` 的路径，st-1、st-2 不会搬）；`scripts/review_pack.py`（`data/review`，不是研究目录）
3. 测试 `tests/test_research_paths.py`（`tmp_path`）：顶层命中；子文件夹命中；带 "/" 直接拼；两个子文件夹重名 → ValueError；都没有 → 顶层；名字和分类文件夹同名但不含 `review.toml` → 继续往子文件夹找；`.`、`_` 开头的分类被跳过
4. 已有测试不能坏（`tests/test_research_ai_adjudicate.py` 等用 `tmp_path` 造的包在顶层，行为应该不变）

## 验收标准

1. `pytest tests/test_research_paths.py` 通过；全部测试通过（报告只贴最后一行）
2. `grep -n '"research" / "review" /\|"research" / "stance" /\|REVIEW / \|OUT / ' scripts/*.py` 只剩定义 `REVIEW`、`OUT` 常量的行和 `research_dir` 内部（或者常量已删）
3. 手工检查（在 `tmp_path` 或临时目录做，**不要动真实的 `data/`**）：把一个包放到 `review/gold/x`，`python scripts/research_stance.py score --pack x --source <某个>` 能找到。真实数据的验证由 Claude 搬完文件夹后做

## 完成报告

新增 `src/cs2coach/research/paths.py`，按有效标记文件查找顶层或一级分类中的复盘包和判定结果；新增 `tests/test_research_paths.py` 覆盖查找规则及分类后的 `score`。更新 `research_review.py`、`research_stance.py`、`research_vision.py`、`research_text_ai.py`、`research_replay.py`、`research_firstkill.py`、`research_ai.py` 的路径调用。复盘包内的 `archive` 相对路径未改，回放读取时按 stance 名字定位。

相关测试：`81 passed in 1.99s`。全部测试最后一行：`1341 passed, 4 skipped in 49.88s`。

验收 2 的 `rg` 搜索无匹配（退出码 1）；仅保留 `REVIEW`、`OUT` 常量定义。临时目录中执行 `research_stance.py score --pack x --source s`（包在 `review/gold/x`、判定在 `stance/old/s`）退出码 0，输出“细类：一致 1/1”。未移动或修改 `data/` 下的文件；无遗留问题。
