# R04 AI 与程序分歧复盘包

状态：完成
难度：B
执行：Codex（思考强度 medium）。Kimi 不用：依赖的脚本还没提交，worktree 里没有
分支：在当前工作目录直接改（`parsing-layer`），不要提交

## 目标

AI（DeepSeek 文字、MiMo 看图）对复盘包逐条给出了判断，程序也有判断。把**AI 与程序大类不一致**的条目做成一个裁定包：公开程序的判断依据和每个 AI 的判断与理由，由产品负责人看回放给最终答案。以后新样本没有人工答案时，用这个包优先人工检查最可能判错的条目。

## 背景（必须读）

- `scripts/research_stance.py::adjudicate`：现有裁定包（人工 vs 程序）。新功能照它的写法；其中生成程序依据（`reveal` 里"程序的判断……""交战在……""移动……""威胁危险度……"那几行）的代码**抽成一个函数共用**，`adjudicate` 的输出不能变
- `scripts/research_stance.py`：`BIG`（细类 → 大类）、`main()` 的子命令写法
- `scripts/research_review.py::_write_review`：写 `review.toml`
- `scripts/research_vision.py::compare`：AI 结果文件的读法
- AI 结果文件：`data/research/review/<包>/text/<名>.jsonl`、`data/research/review/<包>/vision/<名>.jsonl`，每行一个 JSON，用到 `id`、`label`（细类中文名或 null）、`confidence`、`evidence`、`error`

## 要做的事

1. `research_stance.py` 新增子命令 `ai-adjudicate`：
   ```
   python scripts/research_stance.py ai-adjudicate --pack gold-1-v7dev --source st-6b \
       --ai text/t2 --ai vision/v2 --name gold-1-v7dev-ai [--split dev|holdout|all，默认 all]
   ```
   - `--ai` 可重复，值是 `<子目录>/<名>`，对应 `<包>/<子目录>/<名>.jsonl`；显示名用 `文字 t2`（text）、`看图 v2`（vision），其他子目录直接用子目录名
   - 选条目：至少一个 AI 的大类与程序大类不同。AI 的 `label` 为 null（失败）不算分歧
   - 排序：先放"所有给出答案的 AI 都与程序不同、且彼此大类相同"的条目，再放其余；组内按 id 的数字排序（g2 < g10）
   - `--split`：按源包 `answer_key.csv` 的 `split` 过滤；源包没有 `answer_key.csv` 时忽略过滤
2. 每条 `reveal`：
   - 第一行：`分歧类型：两个 AI 都反对程序` 或 `分歧类型：只有 文字 t2 反对程序`（按实际 AI 个数和名字写）
   - 程序依据：与 `adjudicate` 完全相同的那几行（不含"你之前的判断"）
   - 每个 AI 一行：`文字 t2：激进·主动寻求对枪（高）——<evidence>`
   - **不显示人工以前的答案**：这个包要能用于没有人工答案的样本。条目里也不要带 `previous` 字段
3. 头部 `note`：`AI 与程序的判断不一致。页面公开了程序的依据和每个 AI 的理由；请看回放给出最终答案，备注写清楚谁对、为什么`；`labels` 照 `adjudicate`
4. 测试：在 `tests/` 下新建 `test_research_ai_adjudicate.py`，用 `tmp_path` 造最小的包（2–4 条 review.toml 条目 + windows.csv + 两个 jsonl），覆盖：选择规则（含 null 不算分歧）、排序、reveal 第一行、不含人工答案；再加一条测试确认抽函数后 `adjudicate` 输出不变（可用同一组假数据前后对比 `reveal`）

## 验收标准

1. `pytest tests/test_research_ai_adjudicate.py` 通过；全部测试通过（报告只贴最后一行）
2. 真实数据（Claude 会复跑）：上面那条命令输出 **10 条**，顺序是 g20、g31、g35、g52、g53、g59（两个 AI 都反对程序），然后 g46、g49、g60、g69
3. 再运行 `python scripts/research_replay.py gold-1-v7dev-ai --jobs 2` 能生成 `replay.html`，每条显示橙框里的 reveal
4. `adjudicate` 对 `gold-1-v7dev` / `st-6b` 的输出与改动前一致（改动前先跑一次存下 review.toml 做对比，生成到临时名字，比较后删掉临时包）

## 完成报告

- 改动：`scripts/research_stance.py` 抽出程序依据共用函数并新增 `ai-adjudicate`；`tests/test_research_ai_adjudicate.py` 覆盖选择、排序、隐藏人工答案及旧输出格式。
- 相关测试：`2 passed in 0.61s`；全部测试：`1330 passed, 4 skipped in 47.93s`。
- 验收 2：生成 10 条；实际顺序为 g20、g31、g35、g52、g53、g59、g46、g49、g60、g69。g46 的程序大类为激进，看图 AI 也是激进，按“所有 AI 都与程序大类不同”的排序规则应在第二组；与验收指定的第一组顺序冲突，待确认。
- 验收 3：`replay.html` 生成成功，10/10 个窗口有数据，页面包含 10 条 reveal。
- 验收 4：改动前后 `adjudicate` 的 `review.toml` SHA-256 一致（均 9 条）。
- 遗留问题：请确定 g46 以排序规则还是验收顺序为准；当前实现按排序规则。
