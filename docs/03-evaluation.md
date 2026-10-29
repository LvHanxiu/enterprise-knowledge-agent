# 评估、复核与实际结果

## 当前版本结论

本轮针对用户提出的“是否完全符合题目、是否过度防御”重新审查并修正。上一版的主要交付和基本场景成立，但辅助事实索引与固定搜索门槛会拒绝合法答案，因此不能将上一版笼统描述为没有这些问题。当前版本取消了这些门槛，并增加真实反例验收。

- 源码自动化测试：**108/108**，见[实际日志](../examples/review-v2/unit-tests.log)。
- 普通 wheel 安装后的自动化测试：**108/108**，见[安装测试日志](../examples/review-v2/installed-tests.log)。安装到独立虚拟环境，在源码目录之外启动 `knowledge-agent` 成功，资源不再依赖源码层级。
- 显式 Mock 场景：**16/16**，见[流程报告](../examples/review-v2/mock/report.json)。这只证明工具与状态流程，不证明问题理解或真实模型决策。
- 真实模型逐项验收：**24/24**，见[带来源的汇总](../examples/review-v2/acceptance-summary.json)。最新[整批为23/24](../examples/review-v2/live/report.json)，`tool_limit` 因 `network_error` 中断，独立[复测1/1通过](../examples/review-v2/live-retry/report.json)。不将跨批复测称为单批全部通过。

环境为 Windows、Python 3.13.9，运行和测试仅使用标准库。模型为真实 HTTP 接入的 `qwen-plus`。系统提示、源文件/资料哈希与环境记录见 [environment.json](../examples/review-v2/environment.json) 和 [system-prompt.txt](../examples/review-v2/system-prompt.txt)。

## 题目要求逐项核对

| 题目要求 | 当前实现与证据 |
| --- | --- |
| 解决员工制度/产品问答 | 报销、VPN、年假、产品导出等合成 FAQ |
| 问题与可选历史 | CLI 位置参数、JSON 输入、`history`；真实 context 场景 |
| 带 ID、版本日期的材料 | 必须字段仅 `doc_id`、`version_date`、`content`；无事实索引也可使用 |
| 文档检索与原文读取 | `search` 与 `read` 两个真实本地工具 |
| 模型选择动作→工具→观察→再决策 | 每轮请求包含工具观测和预算，真实 Trace 及供应商响应 ID 可核对 |
| 回答、引用、缺口、任务状态 | 结构化输出含 answer/citations/gaps/status/clarification |
| 明确答案/冲突/资料外/改写/上下文/失败 | 原16项及新增8项真实验收 |
| 冲突不得擅选 | 模型核验正文的范围、条件和替代关系，无法消解时引用双方、报告冲突并请用户确认 |
| 内容替换后答案与动作变化 | 无索引30/15冲突、仅正文改45、总部/分公司范围三类实际运行 |
| 至少3组实际输入/输出/Trace | 当前保存24组真实与16组显式Mock；正常、资料不足和工具失败均覆盖 |
| 至少2条不同执行路径 | 正常、冲突、检索不足、失败恢复等实际模型路径，见下方 |
| 步数或工具调用上限 | 两种预算均实现，上限后为incomplete，不冒充完成 |
| 工程与文档交付 | 源码、测试、数据、examples、五份文档、依赖/环境模板、Git commit |
| 提交GitHub URL | 用户明确允许本次仅本地交付，URL后补；本地Git/ZIP/bundle完整 |
| 界面、部署、多Agent生产运行 | 题目明确非必需，本次未扩展这些范围 |

## 去掉了哪些过度防御

1. 删除“所有历史截断检索必须以原查询补全”的全局限制。模型能够从宽泛检索转到精确检索，工具保留 `truncated` 提示，决定是否继续搜由模型做。
2. 删除“引用必须覆盖预先索引的 facts”的要求。正文中新增加的完整真实句子无需人工建索引即可引用。
3. 删除按相同 fact key 和不同 value 强制判冲突，以及无索引就禁止报告冲突的逻辑。总部30天和分公司15天可以同时有效；原文30/15天且范围不明时则由模型报告冲突。
4. `facts`、`keywords` 均可省略，中文文档 ID 可以使用。过时的可选事实提示被忽略并显示 `index_warnings`，不阻断新正文。删除无业务依据的10000份文档/50000字符、预算上限100等额外硬限。
5. 删除 Mock 中的中文词法、指代、产品版本和事实聚合等伪语义猜测。现在只有显式场景，不把固定流程包装成自主模型判断。
6. 删除吞掉任意 `Exception` 的兜底。预期服务/工具错误有明确状态，编程缺陷应暴露，便于定位。
7. 将模型 JSON 动作结构校验集中在 Agent，适配器不再对合法 JSON 数组提前终止，模型可以收到协议错误后修正。

核心循环与模型模块由704行减为582行。增加的场景 JSON 是公开测试配置；其预置动作明确标记为 Mock。协议类型、完整原文引用、预算、必要输入验证、服务错误处理、密钥保护仍保留。IPv4连接顺序处理有上一轮真实连接延迟证据，故保留，而不是为了减少行数重新引入已验证的问题。

另外修复了畸形验收配置 `[{}]` 导致未处理 KeyError，以及普通安装后丢失数据/场景/测试资源的问题。

## 当前24项真实验收

| 用例 | 验收要求 | 实际状态 | 步数/工具 | 结果 |
| --- | --- | --- | --- | --- |
| [normal](../examples/review-v2/live/normal.output.json) | 明确答案和可核验引用 | answered | 3/2 | 通过 |
| [conflict](../examples/review-v2/live/conflict.output.json) | 相同规则30/15天冲突，不能凭版本日期擅选 | conflict | 5/3 | 通过 |
| [outside](../examples/review-v2/live/outside.output.json) | 资料外问题必须承认缺口 | insufficient | 5/4 | 通过 |
| [paraphrase](../examples/review-v2/live/paraphrase.output.json) | 改写后的相似问题 | conflict | 4/3 | 通过 |
| [context](../examples/review-v2/live/context.output.json) | 结合上下文处理追问，历史回答不能当作知识库证据 | answered | 3/2 | 通过 |
| [tool_recovery](../examples/review-v2/live/tool_recovery.output.json) | 读取工具临时失败后模型基于错误调整动作 | answered | 4/3 | 通过 |
| [tool_failure](../examples/review-v2/live/tool_failure.output.json) | 工具持续失败要明确缺口或未完成，不编造答案 | insufficient | 5/4 | 通过 |
| [missing_scope](../examples/review-v2/live/missing_scope.output.json) | 企业版信息缺失，不能用标准版上限代答 | insufficient | 5/4 | 通过 |
| [missing_rule](../examples/review-v2/live/missing_rule.output.json) | 已找到相关文档但具体规则缺失，不得以无关事实代答 | insufficient | 5/4 | 通过 |
| [clarification](../examples/review-v2/live/clarification.output.json) | 没有主题和历史时应追问 | needs_clarification | 1/0 | 通过 |
| [single_policy](../examples/review-v2/live/single_policy.output.json) | 仅有D01且无冲突时按D01回答 | answered | 3/2 | 通过 |
| [changed_policy](../examples/review-v2/live/changed_policy.output.json) | 替换文档内容后答案随原文变化 | answered | 3/2 | 通过 |
| [step_limit](../examples/review-v2/live/step_limit.output.json) | 达到模型步数上限明确未完成 | incomplete | 1/1 | 通过 |
| [tool_limit](../examples/review-v2/live-retry/tool_limit.output.json) | 达到工具调用上限明确未完成 | incomplete | 1/0 | 通过 |
| [product](../examples/review-v2/live/product.output.json) | 产品FAQ答案、限制和引用 | answered | 3/2 | 通过 |
| [leave](../examples/review-v2/live/leave.output.json) | 不同业务领域明确答案 | answered | 3/2 | 通过 |
| [no_facts_conflict](../examples/review-v2/live/no_facts_conflict.output.json) | 无事实索引时根据两份普通原文识别冲突，不依赖facts键值 | conflict | 4/3 | 通过 |
| [content_only_change](../examples/review-v2/live/content_only_change.output.json) | 只修改原文为45天，旧30天事实提示失效时仍根据新原文回答 | answered | 3/2 | 通过 |
| [unindexed_sentence](../examples/review-v2/live/unindexed_sentence.output.json) | 新增OA提交入口原文未写入facts，仍可正确引用回答 | answered | 3/2 | 通过 |
| [scoped_policies](../examples/review-v2/live/scoped_policies.output.json) | 相同事实键但总部和分公司适用范围明确不同，应按用户范围回答 | answered | 3/2 | 通过 |
| [materials](../examples/review-v2/live/materials.output.json) | 只询问报销材料时引用D05，不能被无关期限冲突阻断或混入期限 | answered | 3/2 | 通过 |
| [explicit_reference](../examples/review-v2/live/explicit_reference.output.json) | 句首有这个但已明确VPN主题时直接检索回答，不做无谓追问 | answered | 3/2 | 通过 |
| [cash_out](../examples/review-v2/live/cash_out.output.json) | 缺少年假折现政策时明确资料不足，不以申请提前天数代答 | insufficient | 3/2 | 通过 |
| [raw_product_scope](../examples/review-v2/live/raw_product_scope.output.json) | 普通文档没有scope或facts字段时从原文识别企业版范围和10000条上限 | answered | 3/2 | 通过 |

断言检查状态、必须出现/不得出现的内容、引用ID、成功工具动作、引用来自成功读取的原文、缺口与执行预算。自然语言语义的验收使用真实模型，单元测试只验证机械约束和协议行为，不强行要求程序按字段替模型作业务判断。

## 真实动态执行路径

- [normal](../examples/review-v2/live/normal.trace.jsonl)：`search → read → finish`，结果 `answered`。
- [conflict](../examples/review-v2/live/conflict.trace.jsonl)：`search → read → read → finish → finish`，结果 `conflict`。
- [outside](../examples/review-v2/live/outside.trace.jsonl)：`search → search → search → search → finish`，结果 `insufficient`。
- [tool_recovery](../examples/review-v2/live/tool_recovery.trace.jsonl)：`search → read → read → finish`，结果 `answered`。
- [scoped_policies](../examples/review-v2/live/scoped_policies.trace.jsonl)：`search → read → finish`，结果 `answered`。
- [no_facts_conflict](../examples/review-v2/live/no_facts_conflict.trace.jsonl)：`search → read → read → finish`，结果 `conflict`。
- [content_only_change](../examples/review-v2/live/content_only_change.trace.jsonl)：`search → read → finish`，结果 `answered`。

上述每条结果都包含 `model_responses` 和 token 用量；Trace 保存动作参数、观测、简短决策依据，不要求或保存内部思维链。工具故障后的动作来自下一次真实模型调用。资料是合成数据，密钥未写入工程或日志。

## 本轮发现与复测

第一轮扩大验收为 [23/24](../examples/review-v2/live-initial/report.json)。8个新增反例全部通过；原有 `missing_rule` 场景虽然正确表示资料不足，却仅根据搜索摘要判断全文缺失，没有执行 read。补充通用提示：已有相关候选时先读取正文，不能用摘要代替全文。保持验收期望不变后，该项通过。

最终批次仅剩一次网络中断，针对该项复测通过，原始失败保留。之前版本的16项运行、OpenAI超时、Qwen首次13/16和HTTP400批次仍保存在 `examples/`，但不代表当前实现；历史源代码可在前一提交 `2f5c7e89` 查看。当前证据统一位于 `examples/review-v2/`。

## 可复制命令

```shell
python run.py test
python run.py ask "报销期限是多少天？" --mode mock --mock-scenario conflict
python run.py evaluate --mode mock --output-dir examples/local/mock
python run.py evaluate --mode live --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --api-key-env DASHSCOPE_API_KEY --cases examples/review-cases.json --output-dir examples/local/live
```

真实运行需要预先配置有效 `DASHSCOPE_API_KEY`。`ask --mode mock` 必须提供场景；原16项可通过离线流程运行，新增8项用于真实语义验收，不补写模拟“智能”规则迎合这些问题。

## 用时与已知边界

上一轮本地交付及打包的墙钟区间约45.7分钟，记录仍可在历史提交查看。本轮首次可核对时间为 2026-10-28 21:15:03，本报告整理时为 2026-10-28 21:52:08（Asia/Shanghai），该区间约 37.1 分钟；不把这称为提交人未经记录的人工工时。最终打包时间在目录旁的 delivery.json。

当前已覆盖题目所有必需实现与验收项，GitHub URL按用户要求后补。通过有限合成案例不等于任意未来输入都一定正确：词法检索可能漏召回，模型可能误判跨段语义，服务可能超时。程序保留原文证据与失败状态，评审可替换资料复核。没有为消除这些无限风险重新加入固定业务判断、生产框架或不必要功能。
