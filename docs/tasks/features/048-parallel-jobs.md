# 048 逐场并行：基准、审计收集、页面检查加 `--jobs`

状态：待做
难度：B（新增一个小模块，4 个脚本把循环体提成函数；已预写测试）
执行：Kimi（`C:\Users\David\.kimi-code\bin\kimi.exe`，在 Windows 上第一次使用，Claude 逐行审查）
分支：`task/048-parallel-jobs`（独立工作目录 `C:\Users\David\DemoAnalysis-048`）

## 目标

规则回归、基准重建都是一场一场按顺序处理的。实测平均只用到约 3.2 个核（机器是 16 线程、32 GB 内存），一次回归要几十分钟。

改成按比赛多进程并行，默认 6 个进程。每个进程约占 1.3 GB 内存，6 个约 8 GB。**结果必须和串行时完全一样**：按输入顺序合并，任何一场失败都不影响其他场。

## 背景（必须读）

- `tests/test_parallel.py`、`tests/helpers/parallel_fns.py`：**预写测试，不要修改**。如果认为测试有错，停下来写进完成报告。
- 4 个脚本的主循环：
  - `scripts/audit_collect.py::main`：每场已经有现成的函数 `collect_one(path, member, cfg, calibration, baseline)`；
  - `scripts/build_baseline.py::main`：`for i, entry in enumerate(entries, 1)` 那一段；
  - `scripts/build_tier_baseline.py::main`：`for index, (path, member) in enumerate(entries, 1)` 那一段；
  - `scripts/page_check_all.py::main`：同上。
- `scripts/rule_regression.py::main`：它调用 `build_baseline.main([])` 和 `audit_collect.main(["--name", ...])`。

## 要做的事

1. **新增 `src/cs2coach/parallel.py`**：
   ```python
   @dataclass
   class Outcome:
       value: Any            # 成功时的返回值，失败时为 None
       error: str | None     # 失败时为 "异常类型: 消息"

   def default_jobs(cfg) -> int          # min(cfg["tools"]["jobs"], os.cpu_count() or 1)
   def map_ordered(fn, items, jobs: int, args: tuple = (), on_done=None) -> list[Outcome]
   ```
   - 对 `items` 中的每一项调用 `fn(*item, *args)`，按**输入顺序**返回结果。
   - `jobs <= 1` 时在本进程里依次执行，**不创建** `ProcessPoolExecutor`；否则用 `concurrent.futures.ProcessPoolExecutor(max_workers=jobs)`。
   - `ProcessPoolExecutor` 要在模块顶层导入，测试会替换它。
   - 每一项的异常都要捕获，转成 `Outcome(None, "类型: 消息")`，其他项照常进行。
   - `on_done(index, outcome)` 在每一项完成时调用一次，在主进程里执行，可以用来打印进度。完成顺序可以和输入顺序不同。
2. **配置**：`config/default.toml` 新增：
   ```toml
   [tools]
   # 逐场处理的脚本同时运行的进程数；每个进程约占 1.3 GB 内存
   jobs = 6
   ```
   在 `config.py` 里校验：必须是正整数，0、负数、布尔值都报 `ConfigError`。
3. **4 个脚本**：
   - 都加上 `--jobs N` 参数，默认 `default_jobs(cfg)`；
   - 把"处理一场"的代码提成**模块级函数**，因为子进程只能导入模块级函数；
   - 在 `main` 里用 `map_ordered(该函数, 每场的参数, jobs, args=(...))` 处理全部比赛，按输入顺序合并结果；
   - 必须用模块里的全局名字 `map_ordered` 调用（`from cs2coach.parallel import map_ordered`），测试会替换它。
   - 子进程里不要 `print`。成功或失败的提示，在主进程里通过 `on_done` 或合并结果时打印，内容和现在保持一致。
   - `page_check_all.py`："玩家NN"的编号要在合并结果时**按输入顺序**分配，不能在子进程里分配，否则每次运行编号会变。`main` 改为 `main(argv: list[str] | None = None)`。
   - `build_baseline.py`、`build_tier_baseline.py`：职业基准和分段基准最终写出的 JSON，必须和 `--jobs 1` 时**逐字节相同**。
4. **`rule_regression.py`**：
   - 加 `--jobs N`，默认 `default_jobs(load_config())`；
   - 不加 `--skip-baseline` 时调用 `build_baseline.main(["--jobs", str(jobs)])`；
   - 调用 `audit_collect.main(["--name", args.name, "--jobs", str(jobs)])`。
5. 在 `docs/tasks/README.md` 的"节省额度的约定"中加一句：重建基准、跑回归时默认 6 进程并行，用 `--jobs 1` 退回串行。

## 运行方式

在独立工作目录中执行。测试用主目录的 venv，但把 `PYTHONPATH` 指向工作目录的 `src` 和工作目录本身：

```
$env:PYTHONPATH = "C:\Users\David\DemoAnalysis-048\src;C:\Users\David\DemoAnalysis-048"
C:\Users\David\DemoAnalysis\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_parallel.py
```

工作目录里没有 `data/`（没有 demo），所以**不做真实 demo 验证**，由 Claude 合并后在主目录验证。

## 验收标准

- [ ] `tests/test_parallel.py` 全部通过（不得修改这个文件）。
- [ ] 全部测试通过（命令同上，去掉文件名），报告只贴最后一行。
- [ ] 4 个脚本加 `rule_regression.py` 的 `--help` 里都有 `--jobs`。
- [ ] 新文件和修改过的文件都是 LF 换行，不含真实玩家名或 SteamID。

## 范围外（不要做）

- 不改检测、特征、解析层的任何代码。
- 不改 `cs2coach analyze` 和 `summary`：汇总的逐场并行以后再说。
- 不在仓库目录里留下临时目录。如果 pytest 需要临时目录，用 `--basetemp` 指到系统临时目录。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
