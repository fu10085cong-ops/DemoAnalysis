# 047 报告目录页：所有报告集中到一个可点击的 index.html

状态：待做
难度：B（新增一个模块，CLI 加一次调用；已预写测试）
执行：MiMo（`C:\Users\David\bin\codex-mimo.ps1`，第一次试用，Claude 逐行审查）
分支：`task/047-report-index`（在独立工作目录中执行，见下方"运行方式"）

## 目标

现在每份报告各自躺在 `data/output/<玩家>_<steamid>/<比赛>/report.html`，要在文件夹里一层层找。这次在输出根目录生成一个 `index.html`，列出所有玩家的所有报告，点击即可打开。这也是 MVP"本地网页"形态的入口。

## 玩家看到什么

- 按玩家分组，最近有新报告的玩家排在前面。
- 每组内多场汇总排第一，单场按更新时间从新到旧排列。
- 每条报告显示四项：标题（来自 `report_data.json` 的 `title`）、类型（"多场汇总"或"单场"）、更新时间（`report.html` 的修改时间，格式为 `YYYY-MM-DD HH:MM`）、一句话结论（默认分段的 `verdict`；没有分段数据时不显示这一行）。
- 一份报告都没有时，显示"还没有报告。运行 python -m cs2coach analyze <demo> --player <steamid> 生成第一份。"
- 不显示 steamid。

## 背景（必须读）

- `tests/test_index_page.py`：**预写测试，不要修改**。实现的目标就是让它们全部通过。如果认为测试有错，停下来写进完成报告。
- `src/cs2coach/report/page_html.py`、`page_template.html`：配色变量和亮暗主题的写法照抄，让目录页和报告页看起来是同一套。
- `src/cs2coach/cli.py`：`_analyze` 和 `_summary` 中打印 `报告页：...` 的位置（约 170–178 行和 335–343 行）。

## 要做的事

1. 新增 `src/cs2coach/report/index_page.py`：
   ```python
   @dataclass
   class ReportEntry:
       player_name: str     # 文件夹名最后一个 "_" 之前的部分
       steamid: int         # 文件夹名最后一个 "_" 之后的数字
       kind: str            # report_data.json 的 kind："match" 或 "summary"
       title: str
       verdict: str         # tiers 中 id == default_tier 的那项的 verdict；没有则为 ""
       href: str            # 相对输出根目录的路径，用正斜杠，例如 "玩家甲_111/汇总/report.html"
       updated: float       # report.html 的修改时间（时间戳）

   def collect_reports(root: Path) -> list[ReportEntry]
   def render_index(entries: list[ReportEntry]) -> str
   def write_index(root: Path) -> Path      # 写 root / "index.html"，UTF-8、LF 换行，返回路径
   ```
   - 只扫描 `root/<玩家文件夹>/<比赛文件夹>/` 这两层；玩家文件夹名必须以 `_<数字>` 结尾。同时有 `report_data.json` 和 `report.html` 的文件夹才算一份报告。`评测`、`_开发验证`、`历史版本` 这些目录因为层级或命名不符合要求，自然会被排除，不需要单独写判断。
   - `report_data.json` 解析失败时跳过这一份，不中断。
   - `render_index`：生成单个自包含的 HTML，不引用任何外部资源。所有文字用 `html.escape` 转义；`href` 用 `urllib.parse.quote(href, safe="/")` 编码。每条报告是一个 `<a class="report" href="...">` 元素。要有 `<title>`，亮暗主题照报告页的写法。
2. `cli.py`：
   - 模块顶部 `from cs2coach.report.index_page import write_index`；
   - 新增 `_refresh_index(root: Path) -> None`：调用 `write_index(root)`，然后打印 `报告目录：<路径>`；
   - `_analyze` 和 `_summary` 写完 `report.html` 之后都调用 `_refresh_index(args.out)`。

## 运行方式（给 Claude）

在独立工作目录中执行，避免和主目录里正在做的 046 冲突：

```
git worktree add ../DemoAnalysis-047 task/047-report-index
```

测试用主目录的 venv，但把 `PYTHONPATH` 指向工作目录的 `src`：

```
$env:PYTHONPATH = "C:\Users\David\DemoAnalysis-047\src"
C:\Users\David\DemoAnalysis\.venv\Scripts\python.exe -m pytest -q tests/test_index_page.py
```

## 验收标准

- [ ] `tests/test_index_page.py` 全部通过（不得修改这个文件）。
- [ ] 全部测试通过（在工作目录中运行，`PYTHONPATH` 同上），报告只贴最后一行。
- [ ] 新文件为 LF 换行，不含真实玩家名或 SteamID。

## 范围外（不要做）

- 不做本地服务器，不做搜索和筛选。
- 不改报告页本身。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
