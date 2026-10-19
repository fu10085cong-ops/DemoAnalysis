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

## 第 1 轮（`r02-1`）

- 来源：`data/research/r01-v3`（45 场比赛、2845 个窗口），种子 20261019；20 个窗口来自 16 场比赛
- 产品负责人：CS2 控制台 `exec cs2coach_research`，每条 `p编号` 加载、`r编号` 跳到 T 方首个阵亡前 2 秒，切到清单指定的 CT 选手，看之后 5 秒，在 `review.toml` 填 `保持 / 前压 / 后撤 / 横向调整 / 看不出`
- 之后：Claude 运行 `score r02-1`，按混淆矩阵和不一致样本调整 `[research.labels]` 阈值（必要时调整规则本身），再抽第 2 轮检验；记录每轮结果

## 记录

（每轮的一致率、主要错误类型与阈值调整写在这里）
