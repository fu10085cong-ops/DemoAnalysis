# NNN 任务标题

状态：待做
难度：A / B / C（见 `docs/tasks/README.md` 的"难度分级"）
执行：Codex（思考强度 high / medium）/ Kimi / Claude 直接实现
分支：`task/NNN-简称`

## 目标

（一两句话：做完后能得到什么，为什么要做）

## 背景（必须读）

只列函数名、配置段和章节，不写"通读某文档"。

- `文件::函数` 或 `文档#章节`：为什么要读

## 要做的事

1. …

## 接口与数据

（函数签名、数据结构、配置键、输出格式）

## 预写测试（B 类必填）

Claude 已写好的测试文件：`tests/test_xxx.py`。实现的目标是让它们通过；**不要修改这些测试**，认为测试有错时停下来写进完成报告。

## 验收标准

- [ ] 测试：`tests/test_xxx.py` 覆盖……
- [ ] 开发时只跑相关测试：`.venv/bin/python -m pytest -q tests/test_xxx.py`
- [ ] 完成前跑一次全部测试：`.venv/bin/python -m pytest -q`（Windows：`.venv/Scripts/python.exe -m pytest -q`），报告只贴最后一行
- [ ] （如需要）真实 demo：只跑这一条命令……，只贴这几行……，预期……

## 范围外（不要做）

- …

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
