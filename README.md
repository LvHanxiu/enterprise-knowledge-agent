# 企业知识问答 Agent

帮助员工查询合成制度与产品资料，返回可核验的原文引用、资料缺口和任务状态。模型按工具观测逐步选择检索、读取原文、补充检索、追问或结束。D01/D02 报销期限冲突时并列展示 30 天与 15 天，并请求确认适用制度；不会仅按版本日期选择答案。

工程采用 Python 标准库，提供真实模型接入、显式场景 Mock、CLI、自动化测试、验收样例和 Trace。所有知识资料均为虚构材料。Mock 的动作与业务状态由场景预置，只验证流程，不理解自然语言，也不证明真实模型决策效果；真实模型失败时不会悄悄降级为 Mock。

## 环境与快速运行

需要 Python 3.10 或更高版本。无需安装第三方运行或测试依赖，`requirements.txt` 为空是有意设计。解压后在本项目根目录执行：

```shell
python --version
python run.py ask "报销期限是多少天？" --mode mock --mock-scenario conflict
python run.py test
python run.py evaluate --mode mock --output-dir examples/local/mock
```

`ask --mode mock` 必须用 `--mock-scenario` 明确选择场景；它不会推断任意问题的含义。`evaluate --mode mock` 根据每项用例 ID 选择对应场景。通用问答使用 `--mode live`。

第一条问答应返回 `conflict`，同时引用 D01 和 D02。`test` 执行全部离线单元及集成测试；`evaluate` 执行 16 组验收场景，逐一保存输入、实际输出、Trace 和汇总报告。写入 `examples/local/` 可保留仓库已有运行记录。

也支持普通安装，数据、场景和测试资源随包安装：

```shell
python -m pip install .
knowledge-agent ask "报销期限是多少天？" --mode mock --mock-scenario conflict
knowledge-agent test
```

安装后的默认路径不依赖当前工作目录。构建需要 `setuptools`，运行与测试没有第三方依赖。

## 评审入口

| 需要查看的内容 | 入口 |
| --- | --- |
| 需求与逐项验收 | [docs/01-requirements.md](docs/01-requirements.md) |
| 模型调用流程、动态决策及取舍 | [docs/02-solution.md](docs/02-solution.md) |
| 测试预期、实际结果、失败与限制、实际用时 | [docs/03-evaluation.md](docs/03-evaluation.md) |
| AI 辅助内容与人工参与边界 | [docs/04-ai-usage.md](docs/04-ai-usage.md) |
| 难点、复盘及下一步 | [docs/05-retrospective.md](docs/05-retrospective.md) |
| 题目、仓库发布状态与 commit 获取方式 | [SUBMISSION.md](SUBMISSION.md) |
| 包含新增反例的 24 项真实验收 | [examples/review-cases.json](examples/review-cases.json) |
| 验收输入和预期 | [examples/cases.json](examples/cases.json) |
| 已保存的离线实际运行 | [examples/review-v2/mock/report.json](examples/review-v2/mock/report.json) |
| 已保存的真实模型运行 | [examples/review-v2/acceptance-summary.json](examples/review-v2/acceptance-summary.json) |
| 知识资料结构与检索边界 | [data/README.md](data/README.md) |

真实模型运行的成功或失败均以评估文档及其链接的原始记录为准，不把离线结果算成真实模型验证。

## 真实模型运行

默认模式为 `live`，默认模型为 `gpt-4.1-mini`，默认地址为 `https://api.openai.com/v1`，密钥从 `OPENAI_API_KEY` 环境变量读取。模型和地址均可覆盖，所选模型需支持 Chat Completions JSON mode。真实运行需要可访问的模型服务及有效密钥。

如果环境已设置密钥，直接运行：

```shell
python run.py ask "首次使用企业 VPN 怎么操作？" --mode live --output examples/local/live/normal.output.json --trace examples/local/live/normal.trace.jsonl
python run.py evaluate --mode live --case normal --case conflict --case outside --case tool_recovery --output-dir examples/local/live
```

也可把 `.env.example` 复制为 `.env`，在本机填写配置，再显式加载：

```shell
python run.py ask "报销期限是多少天？" --mode live --env-file .env
```

`.env` 不会自动加载；`--env-file` 才会读取。已有系统环境变量优先于环境文件，命令行的 `--model`、`--base-url` 优先于环境变量。不要将填写过的密钥文件提交到 Git；`.gitignore` 已忽略 `.env`。

| 环境变量 | 默认值或用途 |
| --- | --- |
| `OPENAI_API_KEY` | 默认密钥变量；Mock 无需配置 |
| `KNOWLEDGE_MODEL` | `gpt-4.1-mini` |
| `KNOWLEDGE_BASE_URL` | `https://api.openai.com/v1` |
| 自定义密钥变量 | 使用 `--api-key-env 变量名` 指定 |

兼容接口示例：环境中已有 `DASHSCOPE_API_KEY` 时，可选择 DashScope 的 `qwen-plus`：

```shell
python run.py ask "首次使用企业 VPN 怎么操作？" --mode live --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --api-key-env DASHSCOPE_API_KEY
python run.py evaluate --mode live --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --api-key-env DASHSCOPE_API_KEY --cases examples/review-cases.json --output-dir examples/local/dashscope
```

网络错误、超时、模型拒绝或无效模型响应都会明确返回错误。默认网络操作超时为 30 秒，可通过 `--timeout` 调整；连接候选共享连接时间预算，DNS 及整个问答的墙钟时间不保证严格等于该值。连接优先尝试 IPv4 并保留 IPv6 回退。调整超时不改变步骤及工具调用上限。

## 问题、历史与结果文件

位置参数支持直接提问；`--input` 接受含 `question` 和可选 `history` 的 JSON：

```shell
python run.py ask --mode mock --mock-scenario context --input examples/context.input.json --output examples/local/context.output.json --trace examples/local/context.trace.jsonl
```

输入格式：

```json
{
  "question": "那连续登录失败 3 次怎么办？",
  "history": [
    {"role": "user", "content": "首次使用企业 VPN 怎么操作？"},
    {"role": "assistant", "content": "需要使用企业账号并完成多因素认证。"}
  ]
}
```

`--history 文件名` 可单独提供消息数组，与位置参数问题一起使用。`--input` 不能与位置参数问题或 `--history` 同时使用。历史只帮助理解上下文，不能代替知识文档作为引用证据。

CLI 将结果 JSON 输出到标准输出；`--output` 另存完整结果，`--trace` 另存逐行 JSON 运行记录。输出目录会自动创建。

## 文档替换、故障与执行上限

对同一问题替换知识库，验证答案随真实读取内容改变：

```shell
python run.py ask "报销期限是多少天？" --mode mock --mock-scenario single_policy --data data/single_policy.json
python run.py ask "报销期限是多少天？" --mode mock --mock-scenario single_policy --data data/changed_policy.json
```

两次结果分别引用 D01 的 30 天与 45 天原文。真实模型使用默认知识库时应发现 D01/D02 冲突；离线演示该路径需明确选择 `--mock-scenario conflict`。可将 `--mode mock --mock-scenario single_policy` 换成已配置的 `--mode live` 验证真实模型。

模拟一次读取故障、持续故障，以及预算耗尽：

```shell
python run.py ask "首次使用企业 VPN 怎么操作？" --mode mock --mock-scenario normal --fail-read 1
python run.py ask "首次使用企业 VPN 怎么操作？" --mode mock --mock-scenario normal --fail-read -1
python run.py ask "报销期限是多少天？" --mode mock --mock-scenario conflict --max-steps 1
python run.py ask "首次使用企业 VPN 怎么操作？" --mode mock --mock-scenario normal --max-tool-calls 0
```

`--fail-search` 同样支持指定失败次数；`-1` 表示持续失败。失败结果会反馈给模型，失败的工具调用也计入预算。默认最多 12 次模型决策、8 次工具调用。工具预算用完后可以基于已有充分证据结束；继续请求工具则返回 `incomplete`。模型步数用完且仍未得到合法结果也返回 `incomplete`。

验收用例中的 `data`、预算及故障设置可覆盖公共配置。因此 `evaluate --data ...` 不会覆盖某个用例显式指定的文档变体。

## 输出字段与状态

| 字段 | 含义 |
| --- | --- |
| `validation_scope` | 区分真实模型决策与预置离线场景的验证范围 |
| `schema_version` | 结果结构版本 |
| `question`、`history` | 本次问题与传入历史 |
| `answer` | 基于核验原文的答案、冲突说明、追问或未完成说明 |
| `status` | 任务状态，取值见下表 |
| `citations` | 引用数组，含 `doc_id`、`title`、`version_date`、逐字原文 `quote` |
| `gaps` | 缺失资料、适用范围或执行障碍 |
| `clarification` | 需要用户补充的信息或冲突确认请求 |
| `termination_reason` | 正常结束、执行上限或模型错误原因 |
| `steps`、`tool_calls` | 实际决策步数与工具调用次数 |
| `limits` | 本次模型步数和工具调用上限 |
| `model` | 运行模式与模型名称，明确区分 `live`、`mock`、测试用 `scripted` |
| `usage` | 供应商返回的 token 用量汇总，未提供时为空对象 |
| `model_responses` | 每步供应商名称、模型名和响应 ID 白名单元数据，便于核对真实请求 |
| `started_at`、`duration_ms` | UTC 开始时间及本次耗时 |
| `trace` | 每步动作、参数、简短依据、观测摘要和实际观测结果 |

| `status` | 含义 |
| --- | --- |
| `answered` | 资料已核验并支持回答 |
| `conflict` | 已核验资料相互冲突，需要确认适用制度 |
| `insufficient` | 检索后资料不足，或工具失败导致无法取得足够证据 |
| `needs_clarification` | 缺少必要问题条件，需要追问 |
| `incomplete` | 执行预算耗尽，任务未完成 |
| `error` | 模型服务的预期错误；编程错误不被吞掉 |

Trace 不保存模型内部思维链，只保存简短决策依据和可复核外部事件。答案采用完整原文抽取，引用必须来自成功读取的文档。完整句校验仍不能证明任意跨段语义、适用范围或知识库召回完整性，具体限制见设计与评估文档。

## 退出码

| 命令 | 退出码 |
| --- | --- |
| `ask` | `0` 为正常业务结果，包括冲突、资料不足和追问；`2` 为预算耗尽；`3` 为配置、输入文件、知识库或模型错误 |
| `test` | `0` 为全部通过；`1` 为存在失败 |
| `evaluate` | `0` 为所有用例通过；`1` 为至少一项验收不通过；`3` 为运行配置或输入异常 |
| 任意命令的参数解析 | 无效命令行参数返回 `2` |

脚本集成时同时检查退出码与 JSON 的 `status`。命令行参数或初始化阶段失败可能只在标准错误输出简短错误 JSON，而没有完整结果文件。

## 目录

```text
src/knowledge_agent/   Agent、模型适配、检索、CLI
tests/                 可执行离线测试
data/                  合成知识库及替换文档
examples/              验收输入、实际输出、Trace、汇总报告
docs/                  需求、方案、评估、AI 使用及复盘
.github/workflows/     持续集成配置
run.py                 无需安装的统一入口
pyproject.toml          包元数据
requirements.txt       无第三方依赖
.env.example           环境变量模板
SUBMISSION.md          提交说明
```

完整命令参数可通过 `python run.py ask --help` 和 `python run.py evaluate --help` 查看。
