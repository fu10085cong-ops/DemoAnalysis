# R02 行为标签人工复核：盲审、混淆矩阵与阈值校准

状态：进行中（第 1 轮复盘包已生成，等待产品负责人在游戏内判定）
难度：C（工具由 Claude 直接实现；判定由产品负责人完成）
执行：Claude（工具、抽样、统计）+ 产品负责人（游戏内判定）

## 目标

路线图第 5.5 节第 4 步与验收标准第 3 条：R01 的初版主标签（保持 / 前压 / 后撤 / 横向调整 / unknown）是基于导航路径距离的规则，阈值是工程假设。本任务用人工盲审检验规则对不对，算出混淆矩阵，并据此校准阈值。**标签经复核可信之前，不做行为与结果的比较，不写规律卡。**

## 工具

`scripts/research_review.py`（测试：`tests/test_research_review.py`；配置：`[research.review]`）：

- `build --source <导出名> --name <复盘包名> --seed <种子>`：按主标签分层抽样（默认保持、前压、后撤、横向调整各 3 个，unknown 8 个，其中 2 个是 1 秒内结束的窗口），每层顶级与二三线赛事交替，同一回合最多一个窗口，顺序打乱；生成 `data/research/review/<名>/` 下的 `review.toml`、盲审清单 `checklist.md`、控制台别名 `cs2coach_research.cfg`
- `install <名> --csgo <game/csgo 目录>`：把 demo（压缩包内的先解压）复制到 `replays/research/`，cfg 复制到 `cfg/`
- `score <名>`：读取 `review.toml` 中已填的 `result`，与程序标签对比，输出一致率、混淆矩阵和不一致窗口的位移、路径距离变化、窗口秒数，写入 `score.md`

## 旧 demo 的版本兼容

CS2 更新后旧 demo 会提示"Demo 文件与当前游戏版本不兼容"。2026-10-19 查明：检查的是 demo 文件头（第一条 `DEM_FileHeader`，未压缩）的 `network_protocol`（protobuf 字段 2），值等于游戏 `steam.inf` 的 `PatchVersion` 去掉点（1.41.9.1 → 14191）。实机验证：FURIA vs Aurora（14188）改成 14191 后能正常加载播放。`install` 因此会把**复制到 CS2 目录的副本**的版本号改成当前游戏版本（只在编码长度相同时原地改 2 个字节），`data/` 中的原始 demo 不改。差距较大的版本（14141 → 14191）是否也能正常播放，待第 1 轮实机确认；如出现花屏、人物错乱，改用 2D 回放。

## 2D 回放页（游戏内回放的替代）

改版本号只对相邻版本有效：14188 → 14191 能播放；14141 改了网络协议版本和构建号后仍"不兼容"（2026-10-19 实机），说明游戏还有更深的版本检查。现成的办法只有运行旧版 CS2（CS Demo Manager + `download_depot`，单个旧版本 30–40 GB）。因此改用不依赖游戏版本的 2D 回放：

- `scripts/research_replay.py <复盘包名> [--jobs 2]`：解析复盘包涉及的 demo，取每个窗口 T 阵亡前 `lead_seconds` 到之后 `window_seconds + replay_tail_seconds` 的全部玩家位置与朝向（`replay_fps` 帧 / 秒），以及击杀、该 CT 的开枪、投掷物，生成单文件 `replay.html`（模板 `scripts/research_replay.html`）
- 页面：Mirage 雷达图上动画回放（CT 蓝、T 橙，要看的 CT 白圈 + 视角方向 + 轨迹），播放 / 调速 / 拖动，判断时段时间轴为绿色；盲审，答案存浏览器本地，"导出结果"下载 JSON
- `research_review.py import <名> <JSON>` 把答案写回 `review.toml`；测试 `tests/test_research_replay.py`

## 第 1 轮（`r02-1`）

- 来源：`data/research/r01-v3`（45 场比赛、2845 个窗口），种子 20261019；20 个窗口来自 16 场比赛
- 产品负责人：打开 `data/research/review/r02-1/replay.html`，逐条看 T 方首个阵亡后 5 秒内指定 CT 的移动，选择 `保持 / 前压 / 后撤 / 横向调整 / 看不出`，导出结果（游戏内复盘因版本不兼容不可用）
- 之后：Claude 运行 `score r02-1`，按混淆矩阵和不一致样本调整 `[research.labels]` 阈值（必要时调整规则本身），再抽第 2 轮检验；记录每轮结果

## 记录

（每轮的一致率、主要错误类型与阈值调整写在这里）
