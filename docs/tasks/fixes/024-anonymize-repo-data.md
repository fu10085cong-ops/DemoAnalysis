# 024 仓库内真实玩家信息匿名化

状态：完成

## 目标

仓库里有真实的业余玩家信息：测试 fixture、测试代码和文档中有真实的 SteamID64 和游戏名，其中有的像真实姓名。仓库公开之前必须把它们换成虚构值。本任务只处理**当前工作区的文件**，git 历史的处理见"范围外"。

职业选手的 SteamID 和名字是公开信息，**保留不改**。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/product.md` 的"数据与隐私"
- `docs/tasks/fixes/017-checker-maps-percent-drills.md` 的审查意见：fixture 里含真实信息的提醒
- `tests/fixtures/`：全部 6 个文件
- `tests/helpers/synthetic.py`：现有的虚构 steamid，例如 `76561198000000001`
- `src/cs2coach/baselines/pro_sample.json`：职业选手的 steamid，这份清单里的都不用改
- `data/eval/eval_set.toml`：本地评测清单，不入库，只读。里面的 case 名和 player 是需要匿名的真实玩家，可用来核对有没有漏掉

## 要做的事

1. **确定要替换的 steamid**：在下面的范围里找出所有 `7656119\d{10}`。不需要替换的有三类：
   - `pro_sample.json` 中出现的职业 steamid；
   - 已经是虚构值的，即 `76561190000000000` ~ `76561190000009999` 和 `7656119800000000x`；
   - `src/cs2coach/parsing/match.py` 中的常量 `_STEAMID64_BASE`。

   范围：`tests/`、`docs/`、`config/`、`scripts/`、`src/`，以及根目录的 `README.md`、`AGENTS.md`、`CLAUDE.md`。
2. **确定要替换的名字**：
   - fixture 中，非职业 steamid 对应的玩家名，包括所有出现名字的字段，例如 `player_name`、`opponent_name`、队友名、`names`；
   - `data/eval/eval_set.toml` 的 case 名中，平台和地图之后的玩家名；
   - 用这两份清单在上面的范围里全文搜索，所有出现的地方都替换。
3. **替换规则**：
   - 同一个真实值在所有文件中替换成同一个虚构值，保持数据之间的对应关系；
   - steamid 用 `76561190000001001` 起递增。这段号不是有效的 Steam 账号，不会撞上真人；
   - 名字用"玩家01""玩家02"……，按第一次出现的顺序编号；
   - **映射表不要写进仓库**，也不要贴进完成报告。完成报告只写替换了多少个 steamid、多少个名字、涉及哪些文件。
4. **文件名**：fixture 文件名本身不含真实信息，不用改名。如果发现文件名里有真实信息，改名并同步更新引用它的测试。
5. **新增防回归测试** `tests/test_privacy.py`：扫描第 1 步的范围（排除 `pro_sample.json`），断言其中的每个 `7656119\d{10}` 要么属于 `pro_sample.json` 的职业清单，要么属于第 1 步列出的虚构号段。
6. **文档**：`docs/` 下引用真实玩家的说明，例如 `eval_log.md`、`status.md` 中的 case 名，换成同一套虚构名，并在 `docs/eval_log.md` 开头的"怎么做"里加一句：仓库内的玩家名已匿名，与本地评测清单的 case 名不一致是预期的。

## 接口与数据

- 只改文件内容，不改任何代码逻辑。
- 唯一的新代码是 `tests/test_privacy.py`。

## 验收标准

- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。替换后，依赖具体名字或 steamid 的断言要随之更新，不能删掉测试。
- [ ] `tests/test_privacy.py` 通过；把一个真实 steamid 临时写回任一 fixture 后，它会失败。在完成报告中说明验证过程，不要贴出那个 steamid。
- [ ] 用 `data/eval/eval_set.toml` 中的玩家名和 steamid 在第 1 步的范围里搜索，结果为 0。完成报告写搜索命令和"0 条"。
- [ ] 017 的回归测试，即基于 `016_local_*.json` 的那些，替换后仍然报出原来那三类违规。

## 范围外（不要做）

- **不改 git 历史**：不运行 `git filter-repo`、`git rebase` 等命令，不提交。历史中仍有真实信息，公开前怎么处理（重写历史或新建一个只含当前快照的仓库）由产品负责人决定。
- 不改 `data/` 下的任何文件：它们不入库。
- 不改职业选手的 steamid 和名字。
- 不改报告生成逻辑。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：在 `tests/fixtures/`、测试代码、`scripts/eval_batch.py` 和 `docs/` 中统一替换 9 个真实 SteamID、13 个真实玩家名，涉及 30 个原有文件；职业选手保持原值。`docs/eval_log.md` 的“怎么做”增加匿名化说明。新增 `tests/test_privacy.py` 和 `scripts/privacy_scan.py` 扫描任务指定范围；依赖具体身份值的测试断言同步更新。未改 `data/`、git 历史或代码逻辑。
- 测试结果：`.venv/bin/python -m pytest -q`：644 passed，3 skipped；017 回归 `tests/test_checker_017.py`：7 passed，仍覆盖 `map_mismatch`、`raw_number`、`unknown_drill` 三类违规。曾临时把历史真实 SteamID 写回一个 fixture，单跑 `tests/test_privacy.py` 退出码 1，随后恢复 fixture；正常测试通过。按本地 `data/eval/eval_set.toml` 读取非职业 case 的 `player` 和 case 名最后一段，在 `tests/ docs/ config/ scripts/ src/ README.md AGENTS.md CLAUDE.md` 的文本文件里全文搜索：0 条。搜索命令为临时 Python 脚本：`tomllib.loads(Path('data/eval/eval_set.toml').read_text(encoding='utf-8'))` 取值，逐个 `Path.read_text(encoding='utf-8')` 检查；映射未写入仓库。
- 真实 demo 验证：无需重新解析；匿名化后的 fixture 与 017 回归通过。
- 遗留问题 / 需要确认的地方：git 历史仍有原始信息，公开前由产品负责人决定重写历史或建立仅含当前快照的新仓库。

- 审查意见逐条回应：用本地两类业余比赛的 23 场 demo 完整名单补查自由文本；共收集 157 个非职业玩家名、148 个 SteamID。额外发现 8 个名字，在 8 个原有文件里替换 28 处（原扫描命中 21 行），此前的 5 个虚构名编号保持不变，合计匿名化 13 个名字。新增 `scripts/privacy_scan.py`，从 demo 读取名单，仅在终端列出命中文件与行号，不写映射或名单文件。最后运行 `.venv/bin/python scripts/privacy_scan.py`：`已扫描 23 场 demo，收集 157 个非职业玩家名、148 个 SteamID。身份信息命中：0 处。`

## 审查意见（Claude 填写）

**需修改（2026-10-16）。** steamid 已经全部替换，`test_privacy.py` 能拦住真实 steamid，017 回归仍然通过。但名字没有替换干净。

1. **自由文本里的人名没有替换。** 完成报告说只替换了 5 个名字，但 fixture 和文档里出现的远不止这 5 位玩家：
   - `tests/fixtures/mirage_checker_data.json` 的摘要文本（"击杀 …""对手 …"）里还有 2 个真实名字；
   - `docs/tasks/` 的 013、015、018 号任务单的举例里还有 3 个，例如事实卡原文"对手 … 在 …"、误判清单。

   **原因**：名字清单只取了 fixture 中的名字字段和评测清单的 case 名，没有包括比赛里的全部玩家，也就是队友和对手。
2. **请这样收集名字清单**：
   - 用解析层读出 `data/demos/5e_demo`、`data/demos/perfectworld_demo` 下全部 demo 的完整玩家名单，包括名字和 steamid，排除 `pro_sample.json` 里的职业选手；
   - 第 1 步的扫描范围里，这些名字出现的地方全部替换。还是同一套"玩家NN"编号，已经替换过的保持不变。
   - 解析全部 demo 大约要十几分钟。完成报告里写明扫描了多少场、收集到多少个名字、替换了多少处。
3. **新增 `scripts/privacy_scan.py`**：
   - 用第 2 步的方法从 `data/demos` 收集非职业玩家的名字和 steamid，扫描第 1 步的范围，在终端列出命中的文件和行号。
   - 公开仓库前，以及以后新增 fixture 时都可以再跑一遍。它读的是本地 demo，所以不放进 pytest。
   - 输出只在本地终端显示，名字清单不写入任何文件。
   - 完成报告里贴出最后一次运行的结果：0 处命中。

**复审通过（2026-10-17）。**
- 名字清单改为取自 23 场 demo 的完整玩家名单，补换了自由文本里的 8 个名字；
- Claude 自己运行 `scripts/privacy_scan.py`，结果是 0 处命中；用上次发现的那几个名字单独搜索，也是 0 条；
- `test_privacy.py` 用来防止真实 steamid 被重新写回仓库。

**仍待产品负责人决定**：git 历史里还有真实信息。公开前要么重写历史，要么新建一个只含当前快照的仓库。
