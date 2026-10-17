# 030 启用"没看见对手就被打死"，只算对手在屏幕外

状态：完成（Codex 额度紧张，改动很小，由 Claude 直接实现，2026-10-17）
执行机器：Mac，分支 `task/030-enable-died-without-seeing`

## 目标

`died_without_seeing` 一直停用，原因是早期视野算法有误判。018 修正视野之后，经过两项验证：
- 审计 18/20 正确，另 2 条是被燃烧瓶烧死，已由 027 的 `killed_by_utility` 排除；
- 产品负责人在游戏内确认 3/3，对手确实在屏幕外。

产品负责人 2026-10-17 同意按 Claude 的建议启用。

## 改动

1. `config/default.toml`：从 `problems.disabled` 中移除 `died_without_seeing`。
2. `features/problems.py`：命中条件增加 `engagement.opponent_off_screen is True`，即双方有几何视线、但对手不在本人屏幕内。
   - 审计中 197 条命中里有 189 条属于这种情况；
   - 另外 8 条"完全没有视线却被打死"多半是几何误差，不再判。
3. 测试：用例改成带 `opponent_off_screen`；新增"启用后默认会判、没有视线时不判、穿墙不判"的测试；默认停用清单的测试同步更新。
4. 文档：`docs/problem_catalog.md` 更新这条规则的可靠度和职业/业余频率；`docs/features.md` 更新停用清单的说明。
5. 职业基准重建（`problem_rates` 中 `died_without_seeing` 改为只计屏幕外）。

## 说明

职业约每 10 回合 0.36 次，业余约 0.39 次，频率接近，所以这条规则很少会成为"突出问题"。它的价值在于具体是哪一次，以及是否反复在同一位置发生。后者留给以后的战术分析，作为"同一区域反复被侧后方击杀"的规律检测。

## 审查意见

由 Claude 实现，没有经过 Codex。以后如果有人对这条规则提出疑问，从 `docs/eval_log.md` 的"检测规则审计""游戏内确认"两节查依据。
