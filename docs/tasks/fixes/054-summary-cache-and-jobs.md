# 054 多场汇总提速：缓存只看分析相关配置，`summary` 支持 `--jobs`

状态：待做
难度：B（改 2–3 个文件，照现有写法扩展，测试已预写）
执行：MiMo（经 Codex 驱动），在独立工作目录 `C:\Users\David\DemoAnalysis-054` 中
分支：`task/054-summary-cache-jobs`

## 目标

多场汇总有分析缓存，但缓存键包含**整份配置**，改任何一段展示用的配置（雷达、阶梯、报告页文字等）都会让 21 场全部重新分析（约 7 分钟）。另外 `summary` 是逐场串行的，而基准、审计、页面检查早已支持 `--jobs`（048）。本任务：

1. 缓存键只包含会影响单场分析结果的配置段；
2. `summary` 支持 `--jobs`，默认与其他脚本一样取 `tools.jobs` 与 CPU 核数的较小值。

## 背景（必须读）

- `src/cs2coach/cache.py`：`cache_key`（现在把整个 `cfg` 放进键）、`cached`
- `src/cs2coach/summary/aggregate.py::build_summary`：逐场的 `analyse()` 闭包、跳过与打印逻辑
- `src/cs2coach/parallel.py`：`map_ordered`、`default_jobs`、`Outcome`（048 的写法）
- `src/cs2coach/cli.py`：`_build_parser` 中 `summary` 子命令的参数、`_summary` 调用 `build_summary` 的地方
- 参照：`scripts/build_tier_baseline.py::main` 如何用 `map_ordered` 和 `on_done` 打印进度

## 要做的事

1. **`cache.py`**：新增模块级常量

   ```python
   # 只影响报告展示、不影响单场分析结果（MatchMoments、问题检测、比分）的配置段；不放进缓存键。
   # 新增配置段默认放进缓存键：宁可多算一次，也不能读到过期结果。
   DISPLAY_SECTIONS = ("training", "summary", "report", "audit", "ladder", "radar", "review", "checker", "ai", "tools")
   ```

   `cache_key` 放进键的配置改为 `{k: v for k, v in cfg.items() if k not in DISPLAY_SECTIONS}`，其余部分（demo 路径、大小、修改时间、member、player、代码哈希）不变。

2. **`aggregate.py::build_summary`**：
   - 签名加 `jobs: int | None = None`；为 None 时用 `default_jobs(cfg)`
   - 把 `analyse()` 闭包改成**模块级函数**（多进程需要可 pickle），在其中完成"解析 → 检测 → 比分"并读写缓存；参数用 `(path, member, steamid, cfg, baseline, use_cache)` 这类可 pickle 的值
   - 模块顶部 `from cs2coach.parallel import default_jobs, map_ordered`（测试会替换 `aggregate.map_ordered`，所以必须通过模块属性调用）
   - 去重（`seen`）在分发前完成；**结果按输入顺序**处理：成功的加入 `games`，失败的记入 `skipped`，`reason` 用 `Outcome.error` 原文
   - 进度打印：完成时打印"完成 <match_id>：<地图>，在场 N 回合"或"跳过 <match_id>：<原因>"（与现在的文字一致），由 `on_done` 在主进程打印
   - `jobs <= 1` 时行为与现在完全一致（`map_ordered` 已保证串行时不建进程池）

3. **`cli.py`**：`summary` 子命令加 `--jobs`（`type=int`，默认 None，帮助文字照 `build_tier_baseline.py` 的写法："并行进程数（默认取配置 tools.jobs 与 CPU 核数的较小值）"），传给 `build_summary`。

4. **文档**：`docs/features.md#多场汇总` 一句话说明缓存键不含展示类配置段、`summary --jobs`。

## 预写测试（不得修改）

`tests/test_summary_cache_jobs_054.py`。Claude 已确认：改动前 14 项失败、14 项通过；失败的全部是因为功能未实现（缓存键仍含展示段、`aggregate` 没有 `map_ordered`、CLI 没有 `--jobs`），通过的是保护性用例（分析类配置一变缓存必须失效；去掉展示类配置后分析照常运行）。认为测试有错时停下来写进完成报告，不要改测试。

## 运行测试的方法（独立工作目录专用）

工作目录 `C:\Users\David\DemoAnalysis-054` 没有自己的 `.venv`，用主目录的 Python，并让它导入本目录的代码：

```powershell
$env:PYTHONPATH = "C:\Users\David\DemoAnalysis-054\src"
C:\Users\David\DemoAnalysis\.venv\Scripts\python.exe -m pytest -q tests/test_summary_cache_jobs_054.py -p no:cacheprovider --basetemp C:\Users\David\DemoAnalysis-054\.pytest-tmp
```

完成前跑一次全部测试（同样的方式，去掉文件名）。本目录没有 `data/`，依赖真实 demo 或局域网共享的测试（如 `tests/test_platform_check.py`）失败属已知情况，在报告中注明即可。

## 验收标准

- [ ] `tests/test_summary_cache_jobs_054.py` 全部通过
- [ ] 全部测试通过（除上述已知的环境类失败），报告只贴最后一行
- [ ] `git diff --stat` 只涉及 `cache.py`、`summary/aggregate.py`、`cli.py`、`docs/features.md`、本任务单
- [ ] 真实 demo 验证由 Claude 在主目录完成：`summary --jobs 1` 与 `--jobs 6` 的 `summary.json` 逐字节相同；改 `[radar]` 后再跑一次应全部命中缓存

## 范围外（不要做）

- 不改代码哈希的范围（`_SOURCE_DIRS`、`_SOURCE_FILES`）
- 不改分析、检测、报告页的任何逻辑
- 不改 `analyze`（单场）命令
- 不访问本工作目录以外的路径（主目录的 `.venv` 只用来运行 Python）；不运行 `--ai`

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
