# 044 报告页 HTML（单场与多场汇总）

状态：待做（**依赖 043**，043 合并后再开始）
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

- [ ] `tests/test_page_html.py`：
  - 用构造的 `ReportPage`（单场和汇总各一个）渲染，断言输出里有 `id="page-data"`，且嵌入的 JSON 能解析回同样的内容；
  - 名字里含 `</script><b>` 时，输出中不出现未转义的 `</script><b>`；
  - 不含 `http://` 和 `https://`（证明没有外部资源）；
  - `tiers` 为空时，输出里包含 `build_tier_baseline.py --all` 的提示文字。
- [ ] `cli` 测试：用现有的假数据流程断言 `report.html` 写出了，并且被归档。
- [ ] 开发时只跑相关测试文件；完成前跑一次全部测试，报告只贴最后一行。
- [ ] 真实 demo：用 043 验收时的同一条 `analyze` 和 `summary` 命令，打开生成的 `report.html`，截图贴进完成报告，单场和汇总各一张。如果截不了图，就写明在 Chrome 中检查过这几项：
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
- 测试结果（最后一行）：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
