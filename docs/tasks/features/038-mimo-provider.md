# 038 接入小米 MiMo 模型服务

状态：完成
难度：C
执行：Claude 直接实现

## 做了什么

- `config/default.toml` 新增 `[ai.mimo]`：
  - `base_url = "https://api.xiaomimimo.com/v1"`，凭据放在环境变量 `MIMO_API_KEY`（`.env`）；
  - 默认模型 `mimo-v2.6-flash`。
- `ai/providers.py`：`make_provider` 新增 `mimo` 分支，复用 `OpenAICompatProvider`；不发 DeepSeek 的 `thinking` 参数。
- `cli.py`、`scripts/eval_batch.py`：`--provider` 增加 `mimo`。
- 测试 `tests/test_local_provider.py::test_mimo_factory_and_missing_key`。

## 已核实（2026-10-17）

- 用 `GET /v1/models` 列出的文本模型：`mimo-v2.6-flash`、`mimo-v2.6-pro`、`mimo-v2.6-pro-ultraspeed`、`mimo-v2.5`、`mimo-v2.5-pro`；另有语音识别和语音合成模型。
- 用一个最小的工具调用请求测试过：`mimo-v2.6-flash` 和 `mimo-v2.6-pro` 都能正确返回 `tool_calls`，回复里带 `reasoning_content`。
- 官方价格页是脚本渲染的，抓不到内容，价格没有核实。

## 首次实测

单场报告（完美 Dust2，`mimo-v2.6-flash`）：
- 工具调用 6 次，结论修正 2 次；
- 输入 102,439 tokens，**缓存命中 0**，输出 15,110 tokens。

MiMo 的接口没有返回缓存命中数，可能不支持前缀缓存，所以每次都要按完整输入计费。
