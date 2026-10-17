# 040 "看见对手却没开枪"：手持道具不判；补充游戏内确认问题

状态：完成
难度：C
执行：Claude 直接实现

## 问题与改动

- 039 审计抽样中，有 1 条是死时手持烟雾弹。他不是看见后犹豫，而是没切枪，属于另一种问题。规则加上条件：交火武器必须是枪。没开枪时，交火武器由手持武器的显示名反查，见 `baseline.engagement_weapon`。
- `[review.questions]` 新增 `saw_but_no_shot`，用于游戏内确认：对手是否清楚地出现在屏幕里，并且有时间开枪。
- 测试：`tests/test_saw_but_no_shot_039.py::test_holding_utility_is_not_hesitation`。

## 待办

8 条样本的游戏内确认（复盘包 `data/review/2026-10-17-noshot/`），由产品负责人在 Windows 上完成。这条规则完全依赖几何视野，需要确认"几何上看见了"在游戏里是否真的看得见。
