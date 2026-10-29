# 提交说明

题目：企业知识问答 Agent。截图没有提供可核对的数字题号，因此使用题目名称标识。

交付方式：本地完整工程，运行入口为 `python run.py`。用户已确认“暂时只在本地完成，后续再补充 URL”；本次没有创建或发布远程仓库，也没有伪造 GitHub 地址。

## 本地交付内容

- 完整源码、合成知识库、可执行测试、输入/实际输出/Trace、五份题目要求的说明文档。
- 本地 Git 仓库，分支为 `codex/enterprise-knowledge-agent`。
- 工程 ZIP 和可恢复提交历史的 Git bundle 在工程目录旁。
- 最终 commit SHA 记在工程目录旁的 `delivery.json`；本仓库内可运行 `git rev-parse HEAD` 获取。commit 不写入自身内容，避免自引用哈希问题。

完整运行及评审步骤见 [README.md](README.md)，实测结果与用时见 [docs/03-evaluation.md](docs/03-evaluation.md)。源码下载包不包含运行环境缓存或真实密钥。

## 后续补充 GitHub URL

在当前工程目录内执行，替换目标 URL：

```shell
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin codex/enterprise-knowledge-agent
git rev-parse HEAD
```

若使用 bundle 恢复，先在 bundle 所在目录执行：

```shell
git clone enterprise-knowledge-agent.bundle restored-agent
cd restored-agent
git rev-parse HEAD
```

提交表填写题目名称、实际 GitHub 仓库地址和最终 commit SHA。若为私有仓库，由仓库所有者为指定评审账号授予读取权限。发布前以最终 commit 运行测试；后续有修改时提交新的 commit，并更新提交表中的 SHA。

## 范围与边界

已提供题目要求的 CLI 运行方式。界面、线上部署、运行时多 Agent 和生产级运行环境均非本题必需，本次未实现。实现过程使用并行 AI 协作，不表示交付程序需要多个模型 Agent。

真实模型调用结果与离线 Mock 记录分别保存。基于合成数据、固定验收集的一轮通过不能证明所有企业问答都正确。模型可复现的是命令、输入和可核验记录，不能承诺每次工具路径完全相同。
