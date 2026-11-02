# Math Research Agent

让数学论文与研究想法，经过理解、探索和形式化，变成可检查、可追溯、可复用的数学证据。

MRA 的最终目标是一个覆盖论文推理核验、猜想探索和知识积累的数学研究 Agent，
长期面向包括数学四大期刊级别在内的高难度研究问题。这是产品愿景，
不是当前求解能力或科研成果的承诺。

> **状态更新：2026-11-02。** 当前交付是本机研究工作台，已跑通人工整理到真实 Lean 验证的闭环。
> PDF 全文自动形式化、通用自主求解与持续自我改进尚未完成，具体差距见下表。

## 最终产品要做什么

- **核验论文推理**：输入源文件或 PDF，识别定义、命题、前提、证明步骤与跨章节依赖；忠实形式化并逐步核验，报告已闭合部分、理论缺口和待澄清问题，定位回原文。
- **探索数学猜想**：从不完整的研究想法出发，澄清对象与假设，规划多条研究路线，检索引理，搜索证明或反例，并在预算内根据真实反馈继续探索。
- **积累并改善研究能力**：保存经过验证的定理、证明和失败经验，支持检索与复用；用独立评测判断策略或模型升级是否有效，保留版本与回滚能力。

预期的完整工作流程如下；**流程图描述目标架构，不表示各环节均已自动化。**

```mermaid
flowchart LR
    A[论文 / 数学想法] --> B[原文解析与问题澄清]
    B --> C[完整命题与推理依赖]
    C --> D[形式化与语义对照]
    D --> E[引理检索 / 证明与反例搜索]
    E --> F[真实 Lean 检查与证据门禁]
    F --> G[原文对应报告与知识积累]
    G --> E
    G -. 独立评测后改进 .-> D
```

## 已实现与尚未实现

**✅ 已实现**：所列范围内已有可用实现与验证记录。**🟨 部分实现**：已有基础能力，最终目标仍有缺口。
**⬜ 未实现**：尚未交付该目标能力；接口、页面或设计文档不算实现完成。

| 能力 | 状态 | 当前范围与尚缺部分 |
| --- | --- | --- |
| 论文导入与原文对照 | ✅ 已实现 | HTML/LaTeX 优先，PDF 文本其次，英文 OCR 兜底；可修正、对照原页、保存版本及导出。公式和页码对应仍需核对。 |
| 原文整理为完整数学命题 | 🟨 部分实现 | 已有跨段来源、对象、定义、量词、全部前提、结论及依赖的人工整理与审核；自动语义提取尚未实现。 |
| 自然语言到形式化 | 🟨 部分实现 | 已有结构化候选、受控形式语言与语义确认机制；整篇论文自动生成忠实形式化尚未实现。 |
| 真实形式验证 | ✅ 已实现 | 支持范围内的候选通过真实 Lean 检查、数据重放及证据核查；不接受模型自报“证明成功”。不支持任意 Lean 代码直接执行。 |
| 证明搜索与反例搜索 | 🟨 部分实现 | 已有部分受控策略、原命题或完整否定的检查；通用模型驱动的自主搜索、修复和复杂难题求解尚未完成验证。 |
| 全文逐步推理审查 | 🟨 部分实现 | 已有论证义务、依赖、研究转交和局部证据回链；自动覆盖整篇论文及核验作者全部推导尚未实现。 |
| 数学知识与证明复用 | 🟨 部分实现 | 已有固定 Mathlib 源码检索、自有成果入库、受控复用和失效检查；尚未覆盖任意数学知识及自动经验学习。 |
| 研究工作台 | ✅ 已实现 | 本机页面、研究路线与缺口记录、版本历史、任务状态及证据查看可用；记录任意领域问题不等于可以验证任意命题。 |
| 真实模型接入 | 🟨 部分实现 | 接口、配置模板与调用流程已有实现；当前未配置模型，尚无真实模型端到端效果评测。论文自动语义生成也未接通。 |
| 持续自我改进 | ⬜ 未实现 | 经独立评测证明的自动能力提升、模型训练与升级回滚闭环尚未交付；已有知识积累和测试不等于已实现自我改进。 |
| openJiuwen 编排适配 | ⬜ 未实现 | 已有框架独立业务工具接口；尚未完成 openJiuwen SDK/运行时集成与实际编排验收。 |
| 团队协作与在线服务 | ⬜ 未实现 | 当前为本机工作区；账户认证、团队权限、多租户隔离和生产部署尚未交付。 |

数学能力会逐步扩展至数论、代数、分析与 PDE 等方向。当前每条验证仍受已实现的形式语言、
可用引理与准备好的环境限制，不能据此宣称“数学所有方面都可以解决”。

## 现在可以实际体验什么

从[论文导入](http://127.0.0.1:8765/documents)开始，在[数学义务](http://127.0.0.1:8765/obligations)
中人工整理完整命题并核对语义，进入[通用研究](http://127.0.0.1:8765/studio)编辑受控候选，
显式提交真实验证，再将证据关联回原文。[全文推理](http://127.0.0.1:8765/articles)
用于组织论证义务与依赖，不表示已有全文自动验证。

本机已用论文 *Cauchy–Schwarz Regularizers* 的完整 Proposition 1 跑通新检查与独立进程重放，
覆盖非负性、零点集合双向等价和零向量情形。这是人工整理的一条命题验证，
不代表全文核验、模型自动求解或新的数学发现。

`PROVED` / `REFUTED` 需要真实检查工件及语义对齐记录。搜索失败、超时或执行错误不能当作反驳；
命题成立不等于作者的每一步推导均正确，局部通过不代表全文通过。
历史证据与当前复核结果分别展示，具名语义确认仍是本地声明，不是独立专家认证。

## 开始使用

需要 Linux/WSL、Python 3.10+。在项目根目录执行：

```bash
python3 ops/scripts/prepare_document_runtime.py --with-ocr
python3 -m mra serve --port 8765
```

打开 <http://127.0.0.1:8765/documents>。仅导入 HTML/LaTeX 可省略运行环境准备；
PDF/OCR 准备针对 Ubuntu 22.04 amd64。PDF 上限 16 MiB、60 页，源文件上限 16 份、UTF-8 合计 1 MiB。
详细操作见[论文导入](docs/protocols/document-intake-v1.md)和[数学义务](docs/protocols/document-obligations-v1.md)说明。

**无需先配置模型，即可导入、人工整理和使用已有受控验证流程。** 模型按需另行配置，
未配置时不会用样例冒充真实输出；配置后的生成请求会将相关输入发送给所选模型服务，
见[模型配置说明](docs/protocols/ideas-v1.md)。

源码克隆不含本机 Lean 环境、用户论文、研究账本或历史证明工件。
真实验证需另行准备环境；高级分析、微积分、PDE 等 profile 还需各自采用记录，尚未全部提供公开的一键准备流程。

<details>
<summary>基础 Lean / Nat 环境准备</summary>

需要 Git、tar、curl、libzstd 及可用的非特权 Linux namespace：

```bash
python3 ops/scripts/bootstrap_runtime.py
python3 ops/scripts/extract_lean.py
python3 ops/scripts/bootstrap_mathlib.py
python3 ops/scripts/prepare_mathlib_cache.py
python3 ops/scripts/seal_runtime.py
```

固定 Lean 4.35.0、Mathlib `56a005855ea17e0b0958f73ec801ae140b0a1679`。

</details>

## 开发与文档

```bash
python3 ops/scripts/check_source.py
python3 ops/scripts/run_tests.py --suite source
# 只查看测试范围、所需环境和延后项目，不执行测试：
python3 ops/scripts/run_tests.py --suite prepared --list
# 准备本地 PDF/OCR 后，可增加真实提取测试：
MRA_DOCUMENT_RUNTIME=1 PYTHONPATH=.:tests python3 -m unittest test_document_intake test_document_http -v
```

默认 CI 运行源码合同；需要封存环境的合同会逐条列为延后，显式启用的集成测试列为跳过，
都不计作通过。准备好基础 Lean 后可运行 `MRA_REAL_LEAN=1 python3 ops/scripts/run_tests.py --suite lean-smoke`；
该入口检查四项真实 Lean/重放用例，任一跳过也不算验收成功。高级合同仍需各自采用记录，
完整历史实验还可能依赖本地账本和资源限制。JSON 报告保存在 `artifacts/`，
具体范围见[测试分层说明](docs/protocols/test-suites-v1.md)。软件测试、真实 Lean 检查与模型效果分别报告。

- [产品蓝图](Math_Research_Agent_Development_Blueprint.md)与[开发约定](CONTRIBUTING.md)
- [模型配置](docs/protocols/ideas-v1.md)与[框架独立工具](docs/protocols/core-tools-v1.md)
- [设计决策](docs/adr/)、[验证记录](docs/evaluation/)、[工件说明](artifacts/README.md)
- [论文提取依赖与许可](docs/dependencies/document-ingestion-v1.md)、[许可清单](docs/security/license-inventory.md)
- [早期完整说明与阶段记录](README.history.md)（其中“当前”均指当时阶段）

自有代码的对外发布许可证仍待项目所有者决定。运行时依赖遵守各自许可；
当前产品主要用于本机研究，尚未开放多租户服务。
