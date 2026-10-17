# 044 报告页 HTML（单场与多场汇总）

状态：完成
难度：B（照草图把 043 的数据渲染成一个页面）
执行：Codex（思考强度 medium）
分支：`task/044-report-page-html`

## 目标

`analyze` 和 `summary` 在 `report_data.json` 旁边多写一个 `report.html`。双击就能在浏览器里打开，离线可用。版式照 `docs/design/report_page_mockup.html`（草图里的数据是假的，只参考结构和样式）。

页面从上到下：
1. 标题与一句话结论；
2. 能力雷达；
3. 做得好的方向；
4. 问题汇总；
5. 高光回合。

## 背景（必须读）

- `docs/design/report_page_mockup.html`：版式、CSS、雷达绘制、展开行、复制按钮都可以直接沿用。
- `src/cs2coach/report/page_data.py::ReportPage`（043 新增）：页面的唯一数据来源。
- `src/cs2coach/cli.py::_analyze`、`_summary`：写出 `report_data.json` 的位置。

## 要做的事

1. 新增 `src/cs2coach/report/page_html.py`：
   ```python
   def render_page(page: ReportPage) -> str
   ```
   - 生成**单个自包含的 HTML 文件**：CSS、JS 内联；不引用任何外部资源（不用 Google Fonts，也不用 CDN），字体用系统字体栈（`"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif`），等宽字体用 `Consolas, ui-monospace, monospace`。
   - 页面数据以 `<script type="application/json" id="page-data">` 嵌入，由内联 JS 读取后渲染。嵌入前把 `</` 转义成 `<\/`，防止名字里带标签时破坏页面。
   - JS 渲染代码放在 `src/cs2coach/report/page_template.html` 里（模板文件，用 `importlib.resources` 读取），Python 只替换数据占位。所有插入页面的文字都要做 HTML 转义：JS 用 `textContent`，或者写一个 escape 函数。
2. 功能对照草图：
   - 雷达图：6 个轴来自 `page.axes`；实线是 `tiers[i].you`，虚线是所选的 comparison。两个下拉框分别是"虚线对比"（列出 `comparisons`）和"目标分段"（列出 `tiers`，默认选中 `default_tier`）。值为 None 的轴画在 0，并在旁边的数值列表里显示"—"。
   - `tiers` 为空时，雷达区显示一行说明：`还没有构建分段基准：python scripts/build_tier_baseline.py --all`，其余区块照常显示。
   - 做得好的方向和问题汇总：每行显示颜色点（severity）、名称、维度、条形图（`you` 与 `target` 的竖线）、`you / target 单位`。切换目标分段时，问题行的 `target` 改用该分段的 `problem_targets`，severity 在前端用同样的规则重算：比例取 `page.problem_bad_ratio`，不要写死。问题汇总默认展开第一项，做得好的方向全部折叠。
   - 展开后：先显示 `text`，再显示具体回合表，列为比赛、回合、时间、局面、发生了什么、游戏内跳转。跳转列显示 `jump` 命令，复制按钮复制 `"{playdemo}; {jump}"`。剪贴板失败时，按钮文字改为"请手动复制"。
   - 高光回合：用卡片展示；为空时显示"这段时间没有挑出高光回合"。
   - 支持亮色和暗色两套主题（照草图的 `prefers-color-scheme` 写法），手机宽度下不出现横向滚动。
3. `cli.py`：在 `report_data.json` 之后写 `report.html`，并加进 `_archive_reports` 的归档列表。终端最后打印一行 `报告页：<路径>`。

## 验收标准

- [x] `tests/test_page_html.py`：
  - 用构造的 `ReportPage`（单场和汇总各一个）渲染，断言输出里有 `id="page-data"`，且嵌入的 JSON 能解析回同样的内容；
  - 名字里含 `</script><b>` 时，输出中不出现未转义的 `</script><b>`；
  - 不含 `http://` 和 `https://`（证明没有外部资源）；
  - `tiers` 为空时，输出里包含 `build_tier_baseline.py --all` 的提示文字。
- [x] `cli` 测试：用现有的假数据流程断言 `report.html` 写出了，并且被归档。
- [x] 开发时只跑相关测试文件；完成前跑一次全部测试，报告只贴最后一行。
- [x] 真实 demo：用 043 验收时的同一条 `analyze` 和 `summary` 命令，打开生成的 `report.html`，截图贴进完成报告，单场和汇总各一张。如果截不了图，就写明在 Chrome 中检查过这几项：
  - 雷达能切换对比线和分段；
  - 问题行能展开；
  - 复制按钮可用。

## 范围外（不要做）

- 不改 `report_data.json` 的结构（有需要就停下来提出）。
- 不把 AI 报告放进页面；`report.md` 和 `basic_report.md` 保持不变。
- 不做本地服务器（localhost 网页留到后面），这次只生成静态文件。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
  - 新增 `report/page_html.py`、`report/page_template.html`：通过资源模板嵌入安全 JSON，内联 CSS/JS，系统字体、亮暗主题、Canvas 雷达、分段/对比切换、可展开的具体回合与复制按钮；手机宽度下表格改为纵向展示。
  - `cli.py`：单场和汇总生成 LF 换行的 `report.html`，重跑归档旧页面，终端最后打印报告页路径。
  - 新增 `tests/test_page_html.py`；更新 `test_cli_output.py`、`test_summary.py` 和 `test_facts.py` 的文件列表、HTML 归档及终端输出断言。无新增依赖，新文件均为 LF。
  - 本任务单和任务索引：更新验收与待审查状态。
- 测试结果（最后一行）：`955 passed, 3 skipped, 1 error in 45.73s`
- 真实 demo 验证（如有）：
  - 使用 043 相同的 `data/eval/eval_set.toml` 第一个 case，运行 `analyze <demo> --player <player>`；未加 `--ai`，单场退出码 0，HTML 已在本机 Chrome 打开并检查。
  - 同一个 player 运行 `summary --player <player>`，退出码 0，汇总 21 场、跳过 0 场；汇总 HTML 已在 Chrome 打开并检查。两份 HTML 中的 JSON 均与旁边的 `report_data.json` 完全一致。
  - Chrome 合成数据交互检查通过：对比线切换、目标分段切换、缺失轴显示“—”、使用 `problem_bad_ratio` 重算严重度、第一条问题默认展开、优势默认折叠、键鼠展开及复制失败提示。复制内容通过假剪贴板校验，未覆盖系统剪贴板。
  - Chrome 调试协议指定 390px 视口并启用暗色主题，确认单场和汇总页面均无横向溢出。截图将玩家名及含名字的描述替换为“（已匿名）”，数值与布局保留；原始报告保存在本地 `data/task044/reports/`。
  - 单场截图：![单场报告（已匿名）](../../../data/task044/match.png)
  - 汇总截图：![汇总报告（已匿名）](../../../data/task044/summary.png)
  - 手机暗色截图：[单场 390px](../../../data/task044/match-mobile-dark.png)、[汇总 390px](../../../data/task044/summary-mobile-dark.png)
- 遗留问题 / 需要确认的地方：
  - 全量测试唯一错误是已知的 `tests/test_platform_check.py` 收集失败（沙箱无法读取 `data/eval/platform` 局域网共享目录），未处理。测试临时目录、缓存改放在工作区，使用 `--continue-on-collection-errors` 让其余测试执行完毕。
  - 无检测规则、指标口径、JSON 结构或现有 Markdown 报告改动。

## 审查意见（Claude 填写）

（2026-10-18，Windows）**通过**。全部测试 958 通过。页面没有外部资源，也没有 `innerHTML` 和 `eval`；嵌入的 JSON 转义了 `</`。截图与草图结构一致。审查时 Claude 改了数字格式：雷达旁的百分位原来显示成 64.33，改为取整；其他数值 10 以上保留 1 位小数，以下保留 2 位。真实数据重新生成了单场、两份汇总共 3 个页面。
