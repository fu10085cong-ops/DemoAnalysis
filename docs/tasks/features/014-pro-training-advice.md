# 014 职业选手的训练建议

状态：完成

## 目标

产品负责人确定（2026-10-16）：**普通用户统一用同一套训练规范，只有职业选手做特殊调整**。

起因：ZywOo 的报告里出现了"每天在 Recoil Master 练压枪""在 Aim Botz 练快速锁靶"这类基础训练，对职业选手不适用。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/product.md` 待定事项中的"训练建议的标准"
- `scripts/build_baseline.py`、`src/cs2coach/baseline.py`、`src/cs2coach/baselines/pro_demos.toml`、`pro_sample.json`
- `src/cs2coach/ai/prompts.py`：提示词中的"讲解要求"，特别是关于训练的部分
- `src/cs2coach/tools/digest.py`：摘要开头
- `src/cs2coach/ai/diagnose.py`

## 要做的事

1. **职业选手名单**：
   - 生成基准时，把参与统计的选手写进 `pro_sample.json` 的新键 `players`：`[{"steamid": ..., "name": ...}]`，去重；
   - 配置新增 `[training].extra_pro_steamids = []`，可以手工补充不在基准里的职业选手。

2. **判断函数**：`is_pro_player(steamid, baseline, cfg) -> bool`：在基准的 `players` 中，或者在 `extra_pro_steamids` 中。

3. **摘要**：目标玩家是职业选手时，在摘要开头加一句固定说明：
   > 目标玩家是职业选手，本人就在职业样本中。训练建议面向职业水平：聚焦细节、对局决策和针对性的情境练习，不推荐基础的瞄准、压枪练习图。与职业样本的比较，反映的是他和其他职业选手的差距。

4. **提示词**：在训练要求中补充：只有摘要标明目标玩家是职业选手时，才按上面的方式调整训练建议；否则一律按普通用户的标准。普通用户的规则保持不变。

5. 重新生成 `pro_sample.json`。除新增的 `players` 外，原有字段的内容应与生成前一致，在完成报告中说明是否一致。

6. 设计成单场诊断和以后的多场汇总（任务 013）都能调用的形式。013 还没做，这里只需要保证 `is_pro_player` 和摘要中的说明可以被复用。

## 验收标准

- [x] 测试：
  - `is_pro_player`：在基准名单中、在补充名单中、两者都不在；
  - 基准文件没有 `players` 键（旧格式）时返回 False，不报错；
  - 职业选手的摘要有固定说明，普通用户没有；
  - 提示词包含新增的训练规则。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 重新生成基准，在完成报告中写明 `players` 的人数（应为 150 人次去重后的人数，约 25 人），并确认 ZywOo（`76561198113666193`）在名单中。
- [x] **不要运行 `--ai`。**

## 范围外（不要做）

- 不改普通用户的训练建议规则。
- 不改问题检测和频率。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `scripts/build_baseline.py` 将实际参与统计的玩家按 SteamID 去重、排序并写入 `players`；重新生成 `src/cs2coach/baselines/pro_sample.json`。
  - `config/default.toml` 新增 `[training].extra_pro_steamids`；`config.py` 校验为正整数数组，防止字符串形式的 SteamID 静默失效。
  - `baseline.py` 新增可供单场及后续多场共用的 `is_pro_player` 和 `pro_training_note`；`tools/digest.py` 在职业选手摘要开头加入训练说明，普通用户不加；`ai/prompts.py` 只在摘要标明职业选手时调整训练要求。
  - `tests/test_baseline.py`、`tests/test_config.py`、`tests/test_render.py` 覆盖名单识别、旧版基准、配置、摘要和提示词。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：**529 passed, 3 skipped**；`git diff --check` 通过。未新增依赖。
- 真实 demo 验证：运行 `.venv/Scripts/python.exe scripts/build_baseline.py` 重新生成基准，成功处理清单中的 **15 份 demo、150 人次**，失败 0 份，耗时 **419.71 秒**。`players` 按 SteamID 去重后为 **25 人**；ZywOo（`76561198113666193`）在名单中。把新基准移除 `players` 后与生成前的 Git 版本逐字段比较，**完全一致**。未运行 `--ai` 或真实大模型 API。
- 遗留问题 / 需要确认的地方：无。按用户确认，手工补充名单中的选手若不在基准样本里，摘要改用“本人不在职业样本中”的准确措辞，且不声称比较直接反映与其他职业选手的差距；基准名单中的选手使用任务单固定原句。未提交。

## 审查意见（Claude 填写）

**审查通过（2026-10-16）。** 名单 25 人，含 ZywOo；基准原有字段与生成前完全一致；只在摘要标明职业选手时才调整训练建议。手工补充、但不在样本中的选手使用"本人不在职业样本中"的措辞，比任务单考虑得更周全。529 个测试通过。
