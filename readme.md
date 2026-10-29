# 企业知识问答 Agent

帮助员工查询企业制度与产品资料，返回基于原文的答案、文档引用、资料缺口和任务状态。输入支持自然语言问题与可选对话历史，知识资料均为合成数据。

仓库地址：[LvHanxiu/enterprise-knowledge-agent](https://github.com/LvHanxiu/enterprise-knowledge-agent)。项目采用 Python 标准库实现，提供 CLI、真实模型接入、离线流程 Mock、自动化测试和实际运行记录。

## 环境与安装

- Python 3.10 或以上；已保存的本地实测环境为 Windows、Python 3.13.9。
- 从源码运行和执行测试无需第三方依赖，`requirements.txt` 为空是有意设计。
- 真实模型模式需要有效 API 密钥及可访问的兼容 Chat Completions JSON mode 的服务；离线 Mock 不需要密钥。

```shell
git clone https://github.com/LvHanxiu/enterprise-knowledge-agent.git
cd enterprise-knowledge-agent
python --version
```

也可以下载仓库 ZIP 并解压，在包含 `run.py` 的目录执行后续命令。

可选安装方式：

```shell
python -m pip install .
knowledge-agent --help
```

构建安装包需要 `setuptools>=68`，运行与测试没有第三方依赖。

## 无密钥快速验证

```shell
python run.py ask "报销期限是多少天？" --mode mock --mock-scenario conflict
python run.py test
python run.py evaluate --mode mock --output-dir examples/local/mock
```

第一条命令应返回 `conflict`，引用 D01 的 30 天与 D02 的 15 天，说明无法确定适用制度。第二条命令运行自动化测试，第三条命令运行 16 组离线场景并保存报告、输入、输出与 Trace。

Mock 是显式场景流程桩：`ask --mode mock` 必须指定 `--mock-scenario`。它不理解任意自然语言，不证明真实模型决策效果。真实模型失败时不会自动降级为 Mock。

## 真实模型运行

已保存的真实验收记录使用 `qwen-plus`。在本机设置 `DASHSCOPE_API_KEY`，例如 PowerShell：

```powershell
$env:DASHSCOPE_API_KEY = "替换为你自己的 API 密钥"
```

随后执行：

```shell
python run.py ask "首次使用企业 VPN 怎么操作？" --mode live --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --api-key-env DASHSCOPE_API_KEY --output examples/local/answer.json --trace examples/local/answer.trace.jsonl
```

运行完整真实模型验收集：

```shell
python run.py evaluate --mode live --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --api-key-env DASHSCOPE_API_KEY --cases examples/review-cases.json --output-dir examples/local/live
```

默认模式为 `live`，默认模型为 `gpt-4.1-mini`，默认地址为 `https://api.openai.com/v1`，默认密钥变量为 `OPENAI_API_KEY`。可使用 `--model`、`--base-url`、`--api-key-env` 覆盖。

也可将 `.env.example` 复制为 `.env` 并填写配置，再给命令增加 `--env-file .env`。程序不会自动加载 `.env`；已有系统环境变量优先于文件，命令行模型与地址优先于环境变量。真实密钥和 `.env` 不应提交到仓库。

## 输入、输出与执行边界

`ask` 接受位置参数问题，或使用 `--input` 读取包含 `question` 和可选 `history` 的 JSON。历史消息由 `role` 与 `content` 组成，支持 `user`、`assistant` 两种角色。

离线上下文示例：

```shell
python run.py ask --input examples/context.input.json --mode mock --mock-scenario context
```

输出包含 `answer`、`citations`、`gaps`、`status`、`clarification`、执行次数和 Trace。引用提供文档 ID、版本日期与原文片段。状态含义如下：

| 状态 | 含义 |
| --- | --- |
| `answered` | 已根据读取的原文回答 |
| `conflict` | 证据存在未消解冲突，需要确认适用制度 |
| `insufficient` | 检索或读取后仍缺少回答依据 |
| `needs_clarification` | 需要用户补充问题条件 |
| `incomplete` | 执行预算不足，未完成核验 |
| `error` | 模型服务或适配器发生预期错误 |

默认最多执行 12 个模型步骤、8 次工具调用，可通过 `--max-steps` 和 `--max-tool-calls` 调整。工具预算耗尽后若模型仍请求工具，或模型步骤达到上限而未结束，返回明确的 `incomplete`。

知识库文件可用 `--data` 替换。每份文档必须包含 `doc_id`、`version_date`、`content`；标题、关键词和辅助事实提示可省略。辅助提示不能替代正文，过时提示不阻断新正文回答。详见 [data/README.md](data/README.md)。

## 动态决策与引用处理

每轮将问题、对话历史、工具观测和剩余预算交给模型，由模型选择 `search`、`read`、`clarify` 或 `finish`。程序执行工具，再将结果交给下一轮模型。模型可以补充检索、读取更多原文、处理工具失败、追问或停止。

程序校验动作结构和引用真实性：引用必须来自成功读取的文档，并与原文完整句子一致。是否存在业务冲突、适用范围或明确替代关系由模型结合正文判断，不根据版本日期或辅助字段擅自选择答案。

真实执行路径示例：

- [正常回答](examples/review-v2/live/normal.trace.jsonl)：`search → read → finish`。
- [资料外问题](examples/review-v2/live/outside.trace.jsonl)：`search → search → search → search → finish`。
- [工具失败恢复](examples/review-v2/live/tool_recovery.trace.jsonl)：`search → read（失败）→ read → finish`。

Trace 包含动作、工具参数、结果摘要及简短决策依据，不记录模型内部思维链。

## 验收结果与评审入口

| 内容 | 已保存的实测结果或入口 |
| --- | --- |
| 源码测试 | [108/108 通过](examples/review-v2/unit-tests.log) |
| 普通安装后的测试 | [108/108 通过](examples/review-v2/installed-tests.log) |
| 离线流程 | [16/16 通过](examples/review-v2/mock/report.json)，仅验证流程 |
| 真实模型逐项验收 | [24/24 经复测通过](examples/review-v2/acceptance-summary.json) |
| 真实模型最新整批 | [23/24](examples/review-v2/live/report.json)，一个网络错误用例[独立复测 1/1 通过](examples/review-v2/live-retry/report.json) |
| 验收输入与预期 | [examples/review-cases.json](examples/review-cases.json) |
| 环境、源文件和数据哈希 | [environment.json](examples/review-v2/environment.json) |
| 实际输入、输出与 Trace 索引 | [examples/README.md](examples/README.md) |
| 用户、目标、范围和成功标准 | [01-requirements.md](docs/01-requirements.md) |
| 调用流程、动态决策和技术取舍 | [02-solution.md](docs/02-solution.md) |
| 预期、实测、失败、用时和限制 | [03-evaluation.md](docs/03-evaluation.md) |
| AI 辅助内容、修改与验证 | [04-ai-usage.md](docs/04-ai-usage.md) |
| 难点、复盘和后续计划 | [05-retrospective.md](docs/05-retrospective.md) |

验收覆盖明确答案、文档冲突、资料外问题、改写问题、上下文追问、工具失败、资料替换以及执行上限。最新整批结果与独立复测分别保留，不将跨批通过描述为单批全部通过。

每个场景保存 `<id>.input.json`、`<id>.output.json` 和 `<id>.trace.jsonl`。场景的 `.input.json` 是 `evaluate` 验收配置，不应直接传给 `ask --input`；后者示例为 [context.input.json](examples/context.input.json)。新运行结果建议写入 `examples/local/`，以保留已有验收证据。

## 项目结构

```text
README.md
run.py
src/knowledge_agent/
tests/
data/
examples/
docs/
.env.example
requirements.txt
pyproject.toml
.github/workflows/test.yml
```

## 发布说明与已知限制

本工程已发布到本页所列 GitHub 仓库。部分开发阶段文档中的“仅本地交付”“GitHub URL 后补”描述已过时，发布状态以本 README 为准。文档提到的旧本地提交 `2f5c7e89` 未发布到本仓库，不能在本仓库中据此获取旧源码；保留的旧运行记录用于说明开发过程，当前验收证据统一位于 `examples/review-v2/`。无需依赖本地交付阶段的 ZIP、Git bundle 或仓库外的 `delivery.json` 运行项目。

实际用时的记录区间及边界见评估文档；这些是可核对的开发过程记录，不冒充提交人个人手工工时。

本次交付采用题目允许的 CLI 方式，未实现界面、线上部署、多 Agent 生产调度、企业权限管理或真实企业资料接入。有限合成案例通过不能保证所有未来问题均正确；词法检索可能漏召回，模型可能误解语义，外部服务可能超时。真实模型需要单独配置服务，离线 Mock 不提供通用问答能力。

最终提交时应提供题号、仓库 URL，以及包含本 README 和全部交付材料的最终 commit SHA。SHA 可从 GitHub 对应提交页面复制，或在同步后的仓库运行 `git rev-parse HEAD` 获取。
