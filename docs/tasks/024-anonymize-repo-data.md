# 024 仓库内真实玩家信息匿名化

状态：待做

## 目标

仓库里有真实的业余玩家信息：测试 fixture、测试代码和文档中有真实的 SteamID64 和游戏名，其中有的像真实姓名。仓库公开之前必须把它们换成虚构值。本任务只处理**当前工作区的文件**，git 历史的处理见"范围外"。

职业选手的 SteamID 和名字是公开信息，**保留不改**。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/product.md` 的"数据与隐私"
- `docs/tasks/017-checker-maps-percent-drills.md` 的审查意见：fixture 里含真实信息的提醒
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

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
