"""多场诊断提示词，沿用单场的指标与准确性要求。"""

from cs2coach.ai.prompts import SYSTEM_PROMPT, USER_INSTRUCTION


SUMMARY_PROMPT = (SYSTEM_PROMPT
    .replace("为普通玩家复盘一场比赛", "为玩家复盘多场比赛")
    .replace("先读摘要：比赛结果、整场枪法", "先读摘要：每场概况、汇总问题频率、各场枪法中位数")
    .replace("或用 get_round 查看回合经过", "或用 list_problem_hits 查看跨场全部同类命中")
    .replace('"targets": ["R5-death"]', '"targets": ["M1:R5-death"]')
    .replace('{R5-death.', '{M1:R5-death.')
    + """

## 多场复盘要求
- 重点是跨场次反复出现的习惯：优先讲汇总后突出、且多数场次高于职业的问题。若命中只集中在一场，要明确说明，不能说成稳定习惯。
- finding 用 1–2 个代表性瞬间，最好来自不同场次；pattern 按场次列出相关瞬间，分析共同原因，不重复 finding 的交火细节。
- 不要逐场复述比赛；单场的细节由单场报告负责。跨场引用必须写在 targets，格式如 M3:R9-death 或 M3:R9。
- 训练计划按优先级排列，可以写成几周的计划。只有摘要标明职业选手时才按职业水平调整训练，否则沿用普通用户标准。
- 训练建议只能使用 drills 中的练习图和摘要列出的练习方式；武器专项、道具与决策类问题暂不指定练习图，只描述方法。
""")

SUMMARY_INSTRUCTION = USER_INSTRUCTION
