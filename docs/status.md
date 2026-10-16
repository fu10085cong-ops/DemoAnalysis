# 当前进度与待办（交接用）

> 2026-10-16 在 Windows 上整理，准备换到 Mac 继续。**新会话、换机器后先读这一页**，再按 `CLAUDE.md` 工作。
> 这一页记录"现在做到哪、接下来做什么、有哪些约定"；产品方向的取舍以 `docs/product.md` 为准，评测数据以 `docs/eval_log.md` 为准。

## 一、现在能做什么

| 命令 | 产出 | 说明 |
|---|---|---|
| `python -m cs2coach analyze <demo> --player <steamid>` | `moments.json`、`facts.md`、`problems.md`、**`basic_report.md`** | 基础版报告（免费档，L0）：纯程序生成，不调用 AI |
| 加 `--ai`（默认 DeepSeek flash） | `report.md`、`submission.json` | AI 版单场报告；`--model deepseek-v4-pro` 临时换模型，`--provider local` 用本地 Ollama |
| `python -m cs2coach summary --player <steamid> [--ai]` | `汇总/` 下的报告 | 自动找该玩家的全部 demo；逐场分析结果有缓存（`data/cache/`） |
| `python scripts/eval_batch.py --label <说明> [--only 关键词] [--jobs 5]` | `data/output/评测/<时间>_<label>/summary.md` | 固定评测集批量生成；API 模型默认 5 个并行；会先确认（花钱） |
| `python scripts/eval_recheck.py <评测目录>` | 终端输出 | 用当前检查器重查已保存的提交，**不调用模型、不花钱**；改了检查规则后先用它 |
| `python scripts/build_baseline.py` | `baselines/pro_sample.json` | 按 `baselines/pro_demos.toml`（26 份职业 demo）重建职业基准 |
| `python scripts/tiered_experiment.py --provider local` | `data/output/评测/*_分级L0L1_*` | 分级报告实验（L0 程序 + L1 逐个交火写两三句话） |

报告准确性的机制（详见 `docs/features.md` 的"结构化诊断与报告"）：
- AI 文字里不能写数字，用占位符 `{对象.字段}`，由程序填写（019）；
- finding 的代表例子只能从程序排出的前 3 名里选（020）；
- 程序给出局面完整句 `situation`；
- 检查器核对人数优劣、守包/回防、"都……"类概括、距离说法、"每一场"、分段时间不能比阈值等；违规就退回修改，改不好就删掉这一条。

## 二、最近的进展（按时间）

- 任务单 001–021 全部完成（`docs/tasks/`）。019–021 由 Claude 完成，GPT 当时额度用完。
- 018 视野修正：部分身体可见、只在一个时刻判断导致的"没看见"误判。职业基准随之重算。
- 019 占位符、020 代表候选、021 评测集。之后根据评测结果补了：
  - 局面句、局势与概括核对、数据边界提示词；
  - 格式宽容、训练计划违规删除、"每一场"核对、距离说法、分段比阈值。
- 测试提速：
  - 分析缓存：汇总从约 4 分钟降到约 7 秒；
  - 评测并行；
  - `--only`；
  - 重查命令。
- 免费档基础版报告 `basic_report.md`。**产品负责人确定：免费档只给 L0。**
- 职业基准扩充到 26 份 demo（9 组对局、260 人次、5620 回合），新增 MP7、P250、SSG 08 阈值。
- 用新算法和 flash 重新生成了妮妮孩孩major冠军、梁孙超、卢文笙的正式报告（基准扩充之前）。

## 三、评测现状（`docs/eval_log.md`）

| 评测 | 模型 | 成功 | 错误数（5 份合计） |
|---|---|---|---|
| 019/020 之后 | flash | 5/5 | 10 |
| 局面句与边界之后 | flash | 5/5 | 1 |
| 新基准 26 份 | flash | 5/5 | 6 |
| Mac 首次（梁孙超重跑） | flash | 5/5 | 9 |
| 本地千问 14B（两次） | local | 1/5 | 成功的那份 3–4 处 |
| 分级 L0 + L1 本地 | local | 4/4 | 12 段里 6 处 |

结论：
- flash 的真实错误率约为每份 0.2–1.2 处，要看多次评测的平均；
- 剩下的错误都是"叙述"类，比如编造视角朝向、概括错，数字本身没有错；
- 本地 14B 不能用于正式报告。

## 四、等产品负责人决定的事

1. **免费版固定说明的措辞**：`config/problem_text.toml`，共 16 类问题，每类一段"为什么是问题 / 怎么改"。这些文字会原样进报告，目前是草稿。
2. **要不要启用"没看见对手就被打死"**（`died_without_seeing`）：018 修好了视野，职业频率每 10 回合 0.41 次。可以先统计它在用户 21 场里的命中再定。
3. 正式报告要不要用新基准再生成一次：约 0.6 元，差别很小，不急。
4. 产品方向（`docs/product.md` 的"待定事项"）：
   - 战术和思路复盘：R28 实验产品负责人已认可，是付费高级档的主要差异点；
   - 补枪转化率、与同场队友比较、按职责分类、进步趋势；
   - 分级报告的其余档位、研究对手。

## 五、可以直接做的事（不调用本地模型）

- 多场汇总的基础版报告：目前只有单场有 `basic_report.md`。
- "近距离"等说法的漏洞：一条 finding 引用多次交火时，只要求至少一次满足。可以考虑按句子定位是哪一次交火。
- 补职业 demo 时优先找 Inferno（只有 2 张），以及有 UMP-45 的比赛。
- 测试数据里的真实玩家名、steamid 需要匿名化（`tests/fixtures/` 中的 `mirage_*`、`016_local_*`、`summary_submission.json` 等），公开仓库之前必须做。

## 六、工作约定

- **费用**：
  - 正式报告默认用 flash，单场约 5 分钱、汇总约 1 毛、评测一套约 3 毛；v4-pro 贵约 10 倍，只在需要时用。
  - **每次真实调用前先说明大概要花多少钱**。
  - 改检查规则后用 `eval_recheck.py` 验证，不要重新生成。
  - DeepSeek 的密钥在仓库根目录的 `.env`（不入库，不要打印）。
- **与 GPT 协作**（`AGENTS.md`、`docs/tasks/`）：Claude 写任务单、审查；GPT 写代码。GPT 没额度时由 Claude 直接做完，并在任务单里注明。
- **评测核对**：每次评测后人工逐份核对，按 `docs/eval_log.md` 的分类计数并记录。
- **隐私**：`data/` 不入库（demo、输出、评测清单都有真实 steamid）；仓库公开前先匿名化测试数据。

## 七、换到 Mac 时

- **代码**：`parsing-layer` 分支推送到 GitHub 后在 Mac 上拉取。推送前确认仓库是私有的，测试数据里有真实名字。
- **环境**：按 `README.md` 的 macOS 步骤操作，`brew install uv protobuf`、Rust，然后 `python scripts/build_demoparser.py --install` 编译修复版 demoparser2。测试命令：`.venv/bin/python -m pytest -q`。第一次计算视野时会下载约 150 MB 的地图网格。
- **数据**：需要手动拷贝。只拷压缩包即可，解压目录 `.extracted/` 会自动重建。

  | 拷什么 | 大小 | 用途 |
  |---|---|---|
  | `data/demos/5e_demo/*.zip`、`data/demos/perfectworld_demo/*.zip` | 约 3.4 GB | 用户比赛：单场、汇总、评测 |
  | `data/demos/pro_demo/*.rar` | 约 5.9 GB | 职业基准；不重建基准、不跑 ZywOo 那个 case 时可以不拷 |
  | `data/eval/` | 很小 | 评测清单（含真实 steamid） |
  | `data/output/` | 约 12 MB | 已生成的报告和评测结果，可选 |
  | `.env` | — | DeepSeek 密钥，用安全的方式传 |

  - 不用拷：`data/cache/`。缓存键里有绝对路径，Mac 上会自动重新计算。
  - 不用拷：`data/demos/.extracted/`，约 5 GB，是职业压缩包搬家之前解压的旧文件，Windows 上也可以删掉。
- **已完成（2026-10-16）**：代码、依赖、`.env`、demo 数据都已就位，测试全部通过，评测跑通。第一次并行评测时地图网格被读坏的问题已经修复（见 `docs/eval_log.md` 的"mac首次"）。
- **Windows 专用的东西**：本地 Ollama 模型（`[ai.local]`）只在 Windows 的显卡上跑过；Mac 上不跑本地模型。
