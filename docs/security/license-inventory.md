# 许可与依赖清单

2026-11-02：CI 测试报告留存采用固定 `actions/upload-artifact` v4.6.2（MIT），
仅用于 GitHub 临时运行器上传指定 JSON 报告，保留 14 天。版本、许可、用途与限制见
[ci-checks-v1](../dependencies/ci-checks-v1.md)。产品运行环境与 Lean/Mathlib 采用记录不变。

2026-11-01：论文导入新增独立、可选的本地 PDF/OCR 工具环境。固定版本、
用途、许可及保留的 wheel/Debian 通知见
[document-ingestion-v1](../dependencies/document-ingestion-v1.md)。不用于数学
证据签发，不采用本机已有的 AGPL PDF 库；Lean/Mathlib 采用记录未更改。

<!-- MRA087 -->

2026-10-29 · MRA087 当前状态；下文旧阶段原样保留。

新增可编辑经典PDE语言、确切来源应用、独立账本和CLI。28新契约及12质量命令通过，真实账本专项与14条CLI通过。最新相关346项293通过/52跳过/1项旧目录新增竞态失败，原失败保留，旧scanner未修；不声称全项目回归全绿。

新身份复用既有Lean4.35.0和固定Mathlib56a005855ea17e0b0958f73ec801ae140b0a1679不可变树，明确导入IntegrationByParts与已缓存InnerProductSpace.Basic。八个依赖许可文件摘要现场复核，host许可沿用watched记录，新增下载/安装0。用途为受控经典PDE任务/来源重放；非一般PDE或模型能力声明。见[依赖复审](../../artifacts/pde-language-20261029-01/dependency-review.json)。

<!-- MRA086 -->

2026-10-29 · MRA086 当前状态；下文旧阶段原样保留。

新增事件监视扫描器与独立 pde-energy-watched-v2。46契约通过；相关317项266通过/51跳过；12质量命令通过；真实全链与成功重放后丢失监视的发布门禁均通过；14条CLI通过。旧MRA085两项竞态失败保留，旧实现没有修复，本次旧测试通过不改变历史结论。

新增采用的是已安装CPython ctypes/_ctypes（PSF）、libffi8 3.4.2-4（MIT）和glibc 2.35-0ubuntu3.15公共inotify API（既有LGPL通知）。CPython/stdlib为3.10.12-1~22.04.18；kernel为6.6.87.2-microsoft-standard-WSL2。无新安装、下载或第三方实现复制；7新增宿主路径及原依赖均纳入新profile实际摘要。版本/用途/限制/系统许可原文与摘要见[依赖复审](../../artifacts/watched-integrity-20261029-01/dependency-review.json)及独立adoption/acquisition。不是发行镜像的完整SBOM。

<!-- MRA085 -->

2026-10-29 · MRA085 当前状态（下文旧阶段保留为历史记录）。

真实Poisson能量链专项通过（939.626秒）、14条CLI与12质量命令通过；相关协议回归270项为218通过/50跳过/2项旧streaming竞态失败，原失败及可复现时间戳诊断保留。

新增采用同一固定Mathlib的IntegrationByParts有限模块，Lean4.35.0/Mathlib56a0058及八项依赖沿用已登记许可，adoption在使用前记录。独立普通复制树，官方2761缓存中2692摘要复用、69新增4360425字节；现有curl/cache/leantar只用于准备。无新供应商SDK、权重、训练、Euler代码或外部论文材料；获取摘要不当上游签名。见[ADR0077](../adr/0077-poisson-energy-proof-data.md)。


<!-- MRA084 -->

2026-10-29 · MRA084 当前状态（下文旧阶段保留为历史记录）。

257项相关回归208通过/49跳过；12质量命令通过；单独真实图/HTTP专项通过（857.806秒），14条实际CLI通过；实际Edge桌面/390px浏览器14项通过。

本轮无新第三方依赖、下载、模型权重或外部论文材料；复用已记录Lean4.35.0/固定Mathlib、Python标准库、既有开发用Node/Ruff/Edge。新任务图、HTTP、CLI及网页为本项目实现；未引入Euler非商业代码。下一阶段仅本地读取现有Mathlib分部积分/Sobolev源码，尚未采用新模块或下载其缓存；实际扩环境前另记版本、许可、用途及闭包。见[验收](../evaluation/calculus-research-results.md)。

<!-- MRA083 -->

2026-10-29 · MRA083 当前状态（下文旧阶段保留为历史记录）。

calculus-language-v1复用原Lean4.35.0/固定Mathlib与八依赖许可；adoption在使用四import范围前记录，其中InnerProductSpace.Basic来自既有A0缓存。没有新复制、下载、解包、第三方实现、权重或训练利用。既有本地SHA256及供应链限制不变。见[ADR0075](../adr/0075-typed-calculus-language.md)。

<!-- MRA082 -->

2026-10-29 · MRA082 当前状态（下文旧阶段保留为历史记录）。

calculus-foundation-v2复用已登记Lean4.35.0/固定Mathlib与八依赖许可，adoption在使用前记录版本/文本摘要/用途/限制；独立树补三imports的官方2692缓存计划，1999摘要复用/693新下载。官方Cache源码逐字对照归档，既有static-curl仅准备时复用；获得摘要不是上游签名。无新第三方实现/权重/论文/训练用途授权或模型调用。见[ADR0074](../adr/0074-isolated-calculus-foundation.md)。

<!-- MRA079 -->

2026-10-29 analysis-foundation-v1 复用同一 Lean 4.35.0 / Mathlib 固定 commit 与八项已登记依赖，独立复制 Mathlib 并补充 InnerProductSpace.Basic 的官方缓存。版本、实际许可文本摘要、用途及限制在独立 adoption.json 中；保留 Lean/Mathlib Apache-2.0、lean4-cli MIT 与其余依赖通知。既有 Cache/static-curl/leantar 只用于环境准备；额外 cache 工具取得的二进制摘要入 acquisition.json，沿用原供应链核查限制，不把本地获取摘要当上游签名。Windows 下载使用既有 Python 标准库，不安装新 SDK；不暴露检查 Worker 的网络。八个固定已知探针仅内部功能验收，不是训练集或原创成果。见 [ADR0071](../adr/0071-isolated-analysis-foundation.md)。

<!-- MRA045 -->

MRA045（2026-10-27）依赖续研使用项目独立Python标准库/fcntl/SQLite及原生网页，复用冻结执行器、Lean4.35.0与固定Mathlib；无新增依赖、模型、外部实现、数据、下载、训练或Euler代码。见[ADR036](../adr/0036-evidence-dependent-research-continuation.md)。

<!-- MRA044 -->

MRA044（2026-10-27）具名证据关联使用项目独立Python标准库、既有fcntl/SQLite及原生网页，复用登记过的Lean4.35.0/固定Mathlib证据；无新依赖、外部实现、数据、模型、下载、训练或Euler代码。见[ADR035](../adr/0035-named-research-evidence-links.md)。

<!-- MRA043 -->

MRA043（2026-10-27）受控前缀与分支使用项目独立Python标准库/fcntl/SQLite及原生HTML/CSS/JS，复用已登记Lean4.35.0和固定Mathlib，不引入新依赖、外部实现、模型、数据、下载或训练。固定目标读取源码沿用冻结实现，完整证明沿用既有审计和数据重放。见[ADR034](../adr/0034-controlled-proof-prefix-branches.md)。

<!-- MRA042 -->

MRA042（2026-10-27）同项目研究路线和固定目标观测由项目独立 Python 标准库/fcntl/SQLite 及原生 HTML/CSS/JS 实现；复用 Lean4.35.0 和已登记固定 Mathlib。getGoals/ppGoal 仅读取本地固定程序的实际目标，最终定理仍经原内核和独立重放。无新增依赖、模型、外部数据、下载、训练或 Euler 实现；作者可见功能案例不作持出集或训练授权。见[ADR033](../adr/0033-project-research-routes-and-lean-goals.md)。

MRA041（2026-10-27）固定 Lean 策略适配器与页面为项目独立 Python 标准库/SQLite 和原生 HTML/CSS/JS，沿用既有 Lean 4.35.0 与 Mathlib commit `56a005855ea17e0b0958f73ec801ae140b0a1679`（Apache-2.0）。用途是完整受限 Nat 目标的 `omega` 与六条具名规则化简，经既有内核审计及独立重放验收。六份作者可见示例仅内部功能检查，不是持出集或训练数据。无新增依赖、模型、数据集、下载或 Euler 实现；原许可限制继续适用。见[ADR032](../adr/0032-fixed-lean-strategy-experiments.md)。

MRA040（2026-10-27）有限反例流程使用项目独立 Python 标准库、原生 HTML/CSS/JS 和既有 Lean 4.35.0/固定 Mathlib。用途为小范围 Nat 数值实验及完整否定的内核检查和独立重放；使用内核可检查的 decide，不采用 native_decide。无新第三方依赖、模型、外部数据或 Euler 代码；复用作者可见案例仅用于内部功能验收，不授权训练。

MRA039（2026-10-27）搜索对照由项目独立 Python 标准库/fcntl/SQLite 和原生 HTML/CSS/JS 实现，无新增第三方依赖。复用已登记 Lean 4.35.0、固定 Mathlib（Apache-2.0）及本机 Edge/Node。16 个结构化目标为作者编写且已查看，project-owned-internal，仅内部功能回归，training_use=false；不引入外部数据、论文、权重或 Euler 受限实现，不因内部案例而授予训练或公开再分发权限。

MRA038（2026-10-27）有界数论搜索为项目独立 Python 标准库及原生 HTML/CSS/JS 实现，复用已登记的 Lean 4.35.0、固定 Mathlib（Apache-2.0）和本机 Edge/Node。用途为本地候选生成与真实验证；无新依赖、下载、模型权重、外部论文或训练数据，未采用 Euler 实现。规则测试与示例均由项目作者编写且已查看，仅作内部功能验收，不作独立持出集或训练许可声明。

上游许可核查日期：2026-10-19；本地工具用途与宿主基线更新：2026-10-24。原始来源、确切 commit/模型 revision、文件哈希见 `upstream/inventory.json`、`model-inventory.json`、`mathlib-dependencies.json`；正文保存在 `upstream/`。本清单记录开发采用决定，不代替最终商用合同审查。

## 当前采用

| 组件 | 版本/范围 | 许可/来源 | 本轮决定 |
| --- | --- | --- | --- |
| Python | WSL 系统 3.10.12；标准库 | PSF 及系统随附通知；未引入 pip 运行依赖 | 本地验证、队列及回环HTTP工作台；http.server/threading/secrets均为既有标准库，仅作受信单用户本地服务 |
| SQLite / sqlite3 | 本机 SQLite 3.37.2；Python 3.10.12 标准库封装 | SQLite 引擎为 public domain；封装随 Python；核对 `/usr/share/doc/libsqlite3-0/copyright`，Debian 打包文件另有 GPL-2+ | 用于本地审核/任务事务、源版本、尝试与事件幂等；fcntl/flock 使用既有 Python 标准库及 Linux 内核；不复制系统二进制，不提供认证/签名；宿主动态库受信，发行镜像前纳入 SBOM |
| Lean 4 | v4.35.0 / commit `eb6cafb7290e8bd5f2937522d8d804f58f02bafc`；官方 Linux release | Apache-2.0；随发行包的 LICENSE/第三方 LICENSES | 采用，保留通知；release SHA-256 在 `lean_workspace/environment.json` |
| LeanExport / Lean.Replay / Expr 与内核 API | 同一 Lean 4.35.0 官方归档；NDJSON 格式 3.1.0；Basic/Parse/Json/Replay 源均带 Apache-2.0 声明 | 沿用上述 Lean 归档及通知，原始源已纳入运行快照 | 采用公开表达式构造、Json、addDeclCore、导出/解析/内核 replay API；用于受限数据证书及独立进程复验；不复制第三方实现、不新增下载，不启用 bundled 外部 checker；同一内核实现 |
| Mathlib4 | `56a005855ea17e0b0958f73ec801ae140b0a1679` | Apache-2.0 | 采用，固定依赖清单，当前导入 `Mathlib.Data.Nat.Basic` |
| Mathlib 依赖 | plausible、LeanSearchClient、importGraph、ProofWidgets4、aesop、Qq、batteries | 各固定 commit LICENSE 正文为 Apache-2.0；逐项哈希在 `mathlib-dependencies.json` | 固定 Mathlib 环境的组成部分；未启用远程检索 API 或 Web widget 服务 |
| lean4-cli | `c24fe6078c630e89b489bffe59b3e20ac30d12c8` | MIT | Mathlib 构建依赖，保留通知 |
| 系统执行工具 | Ubuntu 22.04 的 unshare、tar、curl、libc、libzstd；systemd-run 仅本地验收内存 scope | 系统随附 GPL/LGPL/BSD/curl 等许可通知；未将系统二进制复制进源码树 | 使用系统环境，不作为独立产品发行；发行镜像前补齐镜像 SBOM。10月22日自动更新后，核对 glibc 2.35-0ubuntu3.15 等包校验与更新日志，显式重封存8项变化的宿主文件；Lean/Mathlib/策略不变，证据见 `artifacts/http-workbench-seal.json` |
| Mathlib cache 工具 | 固定 Mathlib Cache 源；随 Lean 的 leantar；Cache 自动取得的 static-curl 7.88.1 | 继承 Mathlib/Lean 发行通知；curl/static-curl 的独立供应链核查列在风险项 | 仅环境准备时使用；不暴露于检查 Worker 的网络 |
| 开发格式工具 | 既有 Windows Ruff 0.12.0 | MIT，开发工具，未打包进运行环境 | 用于格式化和 lint；运行与测试不依赖它 |
| Node.js 开发工具 | 既有 Windows v24.16.0 | Node.js MIT及随安装第三方通知；使用内置fs/child_process/WebSocket/fetch | JavaScript语法检查及DevTools浏览器验收脚本；不进入Python/Lean运行依赖，不复制二进制，不安装npm包 |
| Microsoft Edge 开发浏览器 | 10月19日既有 Windows 155.0.4283.62；10月24日 Browser.getVersion 实测 156.0.4314.42 | Microsoft专有浏览器许可及安装通知，未复制或再分发 | 仅本机无头页面交互/截图验收，使用独立项目验收配置目录及DevTools；产品使用标准浏览器能力，不依赖Edge专有接口；浏览器由宿主自动更新，非项目安装/分发 |
| CI checkout | actions/checkout `11bd71901bbe5b1630ceea73d27597364c9af683` | MIT，固定源链接 `https://github.com/actions/checkout/blob/11bd71901bbe5b1630ceea73d27597364c9af683/LICENSE` | CI 配置采用；远端工作流尚未运行 |
| 黄金测试数据 | `evals/golden_cases.json` | 本项目自有合成样例，内部开发用途 | 无外部论文/训练集混入；manifest 记录 provenance、consent、split |
| 内核证书示例 | `evals/certificates/` 三份 JSON，对应两个既有黄金目标 | 本项目作者构造的开发资产，project-owned-internal；引用已批准 Lean/Nat 引理 | 仅开发/验证，无模型生成或训练用途授权，不授予公开再分发许可；不纳入 Nat 拆分/模型分数 |
| 自然数算术评测参考 | `nat-arithmetic-v1`；18 条陈述/形式化/参考证明；确切摘要见 `evals/datasets_manifest/nat-arithmetic-v1.json` | 项目作者自有合成数据，project-owned-internal；Lean/Mathlib 引理只按已采用环境引用 | 仅开发与评测，training_use=false；无外部 benchmark、私有论文或模型生成；不授予训练及公开再分发许可；预留组作者已查看并做参考验证 |

Mathlib 的八项依赖源码和归档哈希保存在本地 `.runtime/mathlib-sources.json`；该目录不提交。第三方许可正文保留在可审计文档中，自有代码尚未决定对外发布许可证。

## 候选组件

| 项目 | 实际核查 | 本轮决定 |
| --- | --- | --- |
| Euler | LICENSE 为 PolyForm Noncommercial 1.0.0；LICENSING/NOTICE 另说明上游 Apache 与原创部分的范围 | `restricted`；仅参考公开文档，不导入、不复制其实现到核心；商用复用需明确授权 |
| Danus | Apache-2.0；Python >=3.10，核心无 pip 依赖 | `candidate`；仅隔离候选目录下的源码审计及核心 API smoke；未导入产品核心 |
| PyPantograph | LICENSE/pyproject 均为 Apache-2.0，Python >=3.11 | `candidate`；当前 3.10 环境不安装，后续测 REPL 的工具链兼容与资源行为 |
| LeanDojo-v2 | pyproject 声明 MIT，但 LICENSE 正文为 Apache-2.0；Python >=3.11，含 GPU/分布式依赖 | `needs_review`；元数据冲突需澄清，未安装 |
| ProofFlow | LICENSE 为 MIT；README 要求 Python3.12+，pyproject 为 >=3.8；还需模型和 Lean Server 配置 | `candidate`；未安装，P1/P2 时验证最小实际调用 |
| DeepSeek-Prover-V2 | GitHub 有自定义 LICENSE-MODEL；所查 HF 7B/671B 的 LICENSE 文件为 MIT，README 又指向 Model Agreement | `restricted_pending_scope_review`；代码、权重及特定 checkpoint 的条款存在范围差异，未下载权重，不认定已获商用批准 |
| Goedel-Prover-V2 | 仓库与所查 HF 8B/32B 卡片声明 Apache-2.0，但所查根目录未取得独立 LICENSE 全文 | `needs_review`；记录卡片 revision，未下载权重，不据徽标自动批准部署 |
| miniF2F/PutnamBench/其他论文数据 | 未引入，未审查实际数据版本授权 | `not_adopted`；禁止把“公开可访问”视为训练或再分发许可 |

历史固定目标阶段没有接入模型服务。2026-10-24 新增可选 Chat Completions 兼容协议传输，独立实现，仅使用既有 Python 标准库 urllib/subprocess/signal/SQLite，不新增供应商 SDK 或运行依赖。用户选择稍后配置模型，本轮只访问本机协议模拟服务；无模型权重、外部训练数据或用户论文上传。实际供应商、部署版本、服务条款和所用模型权利须按后续配置记录，协议兼容性不等于模型资产许可。

2026-10-24 增加固定预算策略评测及18条参考到Nat AST的项目作者映射，仅开发与评测，无训练/再分发授权变化。复用既有Python/SQLite/Lean/Mathlib，不新增供应商SDK、组件或模型资产；真实模型仍未配置。

2026-10-25 增加 `evals/paper-review-v1/` 三份人工标注单定理文本及 JSON，由项目独立编写，来源与用途见该目录 README。仅为内部功能验收，不是外部论文、模型输出、训练集或通用能力基准；不授予训练及公开再分发许可。审查复用既有标准库、SQLite、Nat 编译器与 Lean 验证，无新增运行依赖或第三方实现。

2026-10-25 增加私有知识索引与证明归档，复用既有 Python/SQLite/Lean 环境与项目自有单定理验收文本；不新增依赖、外部论文、模型资产或训练数据。逐次入库要求本地保存权利声明，sharing_allowed/training_allowed始终false；本地保存不授予上游源或用户资料的公开再分发/训练权利。证明副本仅本机内部验收与可审计留存，外部分发需另核许可。

2026-10-26 整数扩展仅使用既有标准库与已固定Lean/Mathlib核心；无新依赖、模型或外部论文下载。evals/integer-review-v1三份文本/公式由项目作者编写，仅功能回归和内部演示，不是第三方论文或模型能力基准，不自动授予训练或公开共享权利。

2026-10-26 research-space-v1 为自有标准库实现，沿用已固定Lean/Mathlib及现有浏览器；无新依赖、模型、论文下载或训练。研究图结构示例为作者编写的功能示例，不能当数学原创性或模型能力证据。

2026-10-26 controlled-research-run-v1 使用现有标准库 SQLite/time 和原真实检查/重放流程，浏览器验收继续使用已登记的本机 Edge/Node。无新依赖、外部代码、模型权重、论文或训练数据；调度与算术演示均为项目自有功能验收，不授予上游资产新的权利，也不代表模型能力。

2026-10-26 verified-knowledge-instance-v1 由项目独立实现，复用现有 Python 标准库、SQLite、已固定 Lean/Mathlib 与原验证策略；浏览器验收仍为已登记的本机 Edge/Node。无新增依赖、外部实现、外部论文或权重；本地引用、实例化与归档不授予共享/训练权利，不新增真实模型调用。

2026-10-27 mathlib-nat-divisibility-v1 复用已登记的 Lean 4.35.0 / LeanExport（Apache-2.0）、Mathlib 56a005855ea17e0b0958f73ec801ae140b0a1679（Apache-2.0）、Python 标准库和现有 Edge/Node。用途限本地定义/受控证明引用、真实候选检查和独立新增证明项重放；库常量从封存基线解析，不重建整个库。没有下载、复制外部实现、新增运行依赖、权重或论文；四份实验样例由项目编写，仅功能验收，不代表原创性，也不授予训练或公开再分发权利。模型调用仍为0。

2026-10-27 research-mathlib-v1 为自有Python标准库/SQLite适配器及原生HTML/CSS/JS界面，复用已登记Lean/Mathlib和现有Edge/Node。用途是本机研究图与既有真实证明的严格绑定及撤销传播，无新依赖、外部实现、论文、权重、下载或训练，真实模型调用0。演示图为作者功能验收，不授予分享或训练权利；未采用Euler实现。

2026-10-27 private-mathlib-knowledge-v1 为项目自有Python标准库/SQLite/fcntl实现及原生HTML/CSS/JS界面；复用已登记的Lean/Mathlib和现有Edge/Node，无新依赖、下载、权重、外部论文、外部实现、模型调用或训练。归档保存真实实验工件及版本/许可来源，具名确认仅声明本机保存权利，不授予共享或训练权利；没有采用Euler实现。

<!-- MRA035 -->

2026-10-27 private-mathlib-reuse-v1 为项目自有 Python 标准库/SQLite 与原生 HTML/CSS/JS 实现；复用已登记 Lean 4.35.0、Mathlib 固定版本（Apache-2.0）和本机 Edge/Node。用途是本机私有归档的显式实例化、重新真实检查及证据关联；无新依赖、下载、外部实现、模型权重、论文或训练数据，模型调用0。来源保留既有版本/许可说明；本地保存和替换不授予分享、训练或对外再分发权利；未采用 Euler 实现。

<!-- MRA036 -->

2026-10-27 mathlib-authored-evaluation-v1 由项目独立实现，复用既有 Python 标准库/SQLite/fcntl、已登记 Lean 4.35.0 / Mathlib 固定版本及本机 Edge/Node；用途为固定预算功能评测与本地报告。`evals/mathlib-diagnostics-v1/cases.json` 的8道结构化输入由项目作者编写、已查看且仓库可见，project-owned-internal，仅内部开发/评测，training_use=false；不是外部论文、独立持出集或模型生成数据，不授予训练与公开再分发许可。无新依赖、外部实现、下载、模型权重或训练，模型调用0；未采用 Euler 实现。

<!-- MRA037 -->

2026-10-27 mathlib-nat-gcd-v1 使用已登记Lean 4.35.0 core的Init/Data/Nat/Gcd.lean、Coprime.lean及固定Mathlib环境（Apache-2.0），用途为本机gcd/互素类型、显式库引用、真实候选与独立重放。实现由项目自有编译器/标准库SQLite适配器与原生HTML/CSS/JS组成，复用本机Edge/Node，无新增依赖、下载、外部实现、权重、论文或训练。六份示例和新增引用组合请求由作者编写且已查看，仅内部功能验收，不自动授予训练或公开再分发权利。模型调用0，未采用Euler实现；引用不改变既有库许可与用户资料权利。

<!-- MRA054 -->

2026-10-28 typed-definitions-v1 复用已登记 Lean 4.35.0 / commit eb6cafb7290e8bd5f2937522d8d804f58f02bafc（Apache-2.0）的 Init/Data/Rat、Init/GrindInstances/Ring/Rat、Init/Grind/Ordered/Rat 及核心 rfl/omega/grind；固定 Mathlib commit 56a005855ea17e0b0958f73ec801ae140b0a1679（Apache-2.0），导入范围仍 Mathlib.Data.Nat.Basic。用途为本机显式类型、透明非递归函数／命题定义与真实证明数据重放。源码及olean已在既有完整封存中，无新增下载／依赖／Euler代码／模型／外部论文／训练。Python标准库／SQLite及既有Ruff只作原用途；九份作者可见示例仅内部功能验收，不构成盲测、模型成绩或训练授权。实际接口限制和验收见 ADR045。
