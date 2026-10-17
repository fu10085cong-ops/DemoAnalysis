# 028 游戏内复盘包：一条命令准备好要确认的 demo 和跳转命令

状态：待做（在 027 完成并合并之后开始）
执行机器：Mac 或 Windows 实现均可；**CS2 相关的验证只能在装了 CS2 的 Windows 电脑上做**。

## 目标

审计中有些判定必须进游戏看才能确认，例如"对手是不是在屏幕外""你能不能看到击杀者"。现在的流程太麻烦：只给出 demo 文件名，产品负责人要自己找压缩包、解压、放进 CS2 目录、打开 demo、手动输入跳转命令，看完再口头告诉 Claude 结果。

本任务做一个**复盘包工具**，把这个流程变成：

1. 一条命令生成清单，并把 demo 和配置文件放进 CS2 目录；
2. 在 CS2 控制台每条只输两条短命令：加载 demo、跳到那一刻；
3. 在清单文件里填"对 / 不对 / 看不出"；
4. 一条命令把结果写回审计判定。

以后会做 2D 回放网页，大部分确认就不用开游戏了。本任务只解决现在必须开游戏的这部分。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`、`docs/tasks/README.md`
- `docs/status.md` 的"两台电脑协作"：审计数据在两台电脑之间通过局域网共享，`data/audit` 等目录在两台电脑上的路径相同
- `src/cs2coach/audit.py`、`scripts/audit_*.py`：审计数据的格式（`hits.jsonl`、`labels.toml`），以及 027 新增的 `--name`
- `src/cs2coach/parsing/loader.py`：`resolve_demo` 解压压缩包并缓存到 `.extracted/`，复盘包直接用它得到 `.dem` 文件
- `src/cs2coach/features/moments.py`：跳转命令 `jump_command` 的生成方式，跳转提前量在 `moments.jump_lead_seconds`

## 要做的事

### 1. `scripts/review_pack.py build`：生成复盘包

- **选择条目**，两种方式二选一：
  - `--audit <审计目录名> --pick ingame`：选出 `labels.toml` 中 `needs_ingame = true`，且判定为 `unsure`、或者备注里写了"推断"的条目；
  - `--keys <文件>`：文件里每行一个 `hit_key`。
- **输出到 `data/review/<名称>/`**：
  - `checklist.md`：每条一节，写明编号（r1、r2……）、地图、demo 文件名、目标玩家名和回合、要确认的问题、Claude 原来的判定和备注，以及在 CS2 里要输入的命令；
  - `review.toml`：每条一项，包括 `id`（r1…）、`hit_key`、`result`（留空，填 `对` / `不对` / `看不出`）、`comment`（留空）；
  - `cs2coach_review.cfg`：给每条定义一个短命令，例如 `alias r1 "demo_gototick 96128"`。具体命令格式见第 4 步。
- **要确认的问题**按问题类型写进配置 `[review.questions]`，例如：
  - `died_without_seeing`："对手开枪打你的那一刻，他在不在你的屏幕里？在屏幕里填'不对'，在屏幕外填'对'"；
  - `poor_crosshair_placement`："刚看见对手时，准星离他远吗？远填'对'，基本在他身上、只是在跟枪填'不对'"；
  - `missed_trade`："队友死的那一刻，你能看到击杀者吗？能看到填'对'，看不到填'不对'"；
  - 其他问题给一个通用的问法。
  - 每个问题都要写清楚：哪种情况填"对"，即程序判对了。

### 2. `scripts/review_pack.py install`：放进 CS2

- 参数 `--csgo <CS2 的 game/csgo 目录>`。也可以不传，改用配置 `review.csgo_dir`，或者环境变量 `CS2COACH_CSGO_DIR`。
- 用 `resolve_demo` 拿到每条需要的 `.dem`，复制到 `<csgo>/<review.demo_subdir>/`，同一场只复制一次；`.cfg` 复制到 `<csgo>/cfg/`。
- 打印出每一步要在 CS2 控制台输入的内容。
- 目标文件已存在且内容相同时跳过；不同时提示，不要静默覆盖。

### 3. `scripts/review_pack.py apply`：写回判定

- 读取 `review.toml`。只处理填了结果的条目，没填的跳过并提示。
- 写回对应审计目录的 `labels.toml`：
  - `对` → `correct`，`不对` → `wrong`，`看不出` → `unsure`；
  - `reason` 写成"游戏内确认"；
  - `note` **保留原有内容**，在后面追加"；游戏内确认（日期）：结果，备注"。
- 写回前把原 `labels.toml` 备份为 `labels.toml.bak-<时间>`。
- 只改动 `review.toml` 里出现的条目，其他条目一字不动。用测试保证这一点。
- 最后打印改了哪些条目，以及每条的判定从什么变成什么。

### 4. CS2 控制台命令要做成可配置

下面几点**没有在真实 CS2 上验证过**，不要凭记忆写死，全部放进配置 `[review]`，给出默认值：

- `demo_subdir`：demo 放在 `game/csgo/` 下的哪个子目录，默认 `replays`；
- `playdemo_template`：加载命令，默认 `playdemo {subdir}/{demo_stem}`；
- `jump_template`：短命令里的内容，默认 `demo_gototick {tick}`；
- 可选的 `follow_template`：切到目标玩家视角，默认留空。CS2 是否支持、写法是什么待验证。

**验证由产品负责人在 Windows 上完成**：按 `checklist.md` 操作第一条，确认 demo 能加载、跳转正确。如果命令写法不对，只改配置，不改代码。

### 5. 文档

- 在 `docs/status.md` 加一段"游戏内复盘"的使用步骤；
- 在 `README.md` 的 Windows 部分，补上 CS2 的 `game/csgo` 目录在哪里、怎么找。

## 接口与数据

- 新增 `scripts/review_pack.py`，包括 `build`、`install`、`apply` 三个子命令。
- 新增配置 `[review]`、`[review.questions]`，在默认配置中声明，并通过配置校验。
- 输出在 `data/review/<名称>/`，不入库。
- 跨平台：路径一律用 `pathlib`，Windows 路径里有空格时也要能用。

## 验收标准

- [ ] 测试，用临时目录和合成数据：
  - `build`：两种选择方式、`checklist.md` 和 `review.toml` 的条目一致、`.cfg` 中命令和跳转 tick 正确；
  - `install`：同一场 demo 只复制一次；已有相同文件时跳过；不同时提示，不覆盖；
  - `apply`：结果映射、note 追加、备份；**未出现的条目一字不动**；没填结果的跳过；
  - 配置模板替换正确。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`，Windows 上是 `.venv/Scripts/python.exe -m pytest -q`。
- [ ] **真实数据**：用 `--keys` 生成审计中待游戏内确认的 8 条，`hit_key` 见下方，贴出 `checklist.md` 的前两条，不贴玩家名以外的个人信息。
- [ ] **Windows 实机验证**：由产品负责人完成，结果写进完成报告的"遗留问题"，Codex 不需要做。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

待游戏内确认的 8 条（审计目录 `2026-10-17`）：

```
died_without_seeing-…19725a43
died_without_seeing-…d21c888f
died_without_seeing-…b218689e
slow_reaction-…76d0fb65
poor_crosshair_placement-…262fffc2
poor_crosshair_placement-…bd19c252
missed_trade-…e792061e
missed_trade-…2d56e57c
```

完整的 `hit_key` 以 `labels.toml` 为准，按末尾 8 位匹配。Claude 会在开始前把完整清单放到 `data/review/keys-2026-10-17.txt`。

## 范围外（不要做）

- 不做 2D 回放网页，另开任务。
- 不修改 CS2 的其他文件，例如 autoexec 和其他 cfg。
- 不自动启动 CS2，也不往游戏里注入任何东西。项目只做赛后分析，不碰游戏客户端。
- 不改检测规则。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
