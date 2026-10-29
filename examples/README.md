# 当前与历史运行材料

当前实现的证据都在 `review-v2/`。根目录其他报告属于上一版本，保留用于审计，不用于宣称当前代码的成功率。

| 文件 | 含义 |
| --- | --- |
| [review-cases.json](review-cases.json) | 原16项加8项真实反例，预期及资料路径 |
| [review-v2/acceptance-summary.json](review-v2/acceptance-summary.json) | 24项逐项通过及每项来源 |
| [review-v2/live/report.json](review-v2/live/report.json) | 最新整批23/24，一次网络错误 |
| [review-v2/live-retry/report.json](review-v2/live-retry/report.json) | 网络错误项复测1/1 |
| [review-v2/live-initial/report.json](review-v2/live-initial/report.json) | 本轮首次23/24，摘要代替原文问题 |
| [review-v2/mock/report.json](review-v2/mock/report.json) | 16个显式离线流程通过；无语义能力证明 |
| [review-v2/unit-tests.log](review-v2/unit-tests.log) | 当前108项源码测试 |
| [review-v2/installed-tests.log](review-v2/installed-tests.log) | 普通安装后的108项测试 |
| [review-v2/environment.json](review-v2/environment.json) | 当前环境和源文件/数据哈希 |
| [review-v2/system-prompt.txt](review-v2/system-prompt.txt) | 当前真实模型系统提示 |
| [cases.json](cases.json) | 原16项场景输入，离线evaluate按ID选择场景 |
| [context.input.json](context.input.json) | ask --input所需的纯问题/历史JSON |

每个运行目录包含 `<id>.input.json`（验收配置）、`<id>.output.json`（实际结果）与 `<id>.trace.jsonl`（事件）。验收配置经 evaluate 运行，不能直接当成 ask 的纯问题输入。完整解读见[评估文档](../docs/03-evaluation.md)。
