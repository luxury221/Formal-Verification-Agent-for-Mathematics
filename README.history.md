# Math Research Agent

本仓库包含产品源码、测试、自有示例、准备工具和设计文档。首次克隆不包含本机运行环境、用户论文、研究账本或历史证明工件；历史验收链接可能指向本地保留文件，见 [artifacts说明](artifacts/README.md)。环境准备见下方“从零准备环境”，开发规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。 当前完整测试集含依赖已封存运行环境的用例；仅克隆源码后直接运行全部测试或默认 CI 仍可能因缺少环境快照、profile adoption 而失败，不能据此声称完整回归已通过。

<!-- MRA-ARTICLE-READER-V2-20261101 -->

2026-11-01 · [全文推理工作台](http://127.0.0.1:8765/articles#article-3b58c561897f42d6a097eb1fab8637b4)的默认后台证据读取支持中途取消：独立进程仍调用原验证器完整核对，每图300秒读取预算，超时或输出超限只记证据暂不可用。界面新增当前图、已用时间和进度条；关闭服务会中断未提交观察，显式新建任务后才重跑。

本次实际点击取消到页面终态约3.49秒；另一作业128.67秒完成13图，13条历史引用匹配、0条异常。该单次观察不代表读取普遍提速或实时取消保证；原文修订43、数学判断和旧证据均保留，全文仍未完成。相关221项执行216通过/5跳过，浏览器8项通过，初轮旧关闭断言失败保留。见[本轮验收](artifacts/article-reader-v2-20261101-01/REPORT.md)、[后台协议](docs/protocols/article-evidence-rechecks-v1.md)和[ADR0097](docs/adr/0097-interruptible-article-provider-reads.md)。以下保留历史阶段。

<!-- MRA-ARTICLE-AUDIT-V1-20261101 -->

2026-11-01 · [全文推理工作台](http://127.0.0.1:8765/articles#article-3b58c561897f42d6a097eb1fab8637b4)新增可取消、刷新续查的持久后台证据复核；结果独立显示为历史观察。Studio 新增人工子题与候选入口，支持实际转交后的明确限定结果回收；保留完整原题与全部条件。论文公式本地排版并可查看原始TeX，证明/分析页同页导航与过期响应已修复。

真实网页流程新转交的二维见证通过 Lean 和独立回放并回收为限定finding；原文修订43/13条关联，原题及全文仍UNKNOWN。相关200项为195通过/5跳过；真实取消、刷新恢复和13图历史复核已验。原始503及绑定等待超时保留，底层读取仍慢，取消不能抢占当前读取。模型/openJiuwen未配置，不代表全文自动验证完成。见[本轮验收](artifacts/article-audit-v1-20261101-01/REPORT.md)、[后台协议](docs/protocols/article-evidence-rechecks-v1.md)、[回收协议](docs/protocols/article-research-return-v2.md)和[ADR0096](docs/adr/0096-observable-article-evidence-workflow.md)。以下保留历史阶段。

2026-10-30 · [研究项目](http://127.0.0.1:8765/research)新增可逆归档、恢复、搜索和容量管理，解决100个旧项目后无法新建论文/Studio任务的问题。活跃上限仍100，原研究内容及证明链接保留；归档项目可通过直链继续访问。

正式库现有100活跃、1归档、101保留，原100项目正文/摘要不变；已从论文 Proposition 2 的完整原题新建[研究任务](http://127.0.0.1:8765/studio#study-391dd3dc997846a3b86f45096f286b28)，保持UNKNOWN。相关191项回归、16条实际CLI和7项浏览器交互通过；历史证据经原验证器复核仍为9项有效、14项待处理，另300块未分类，全文未完成。本轮正式服务新Lean与模型调用均0。见[操作](docs/protocols/project-capacity-v1.md)、[ADR0093](docs/adr/0093-project-capacity-without-evidence-migration.md)、[验收](artifacts/project-capacity-20261030-01/REPORT.md)。以下保留历史状态。

2026-10-30 · 当前增量：[证明工作台](http://127.0.0.1:8765/proof#research-38a103e83213432b895bdb70d804110c/main/proof3flow-76c1b13e6a5746cbad9b7d98a6737da2)支持取得的全局最小值、最小二乘、精确求导和零向量分支，默认 `proof-language-v3`，历史 v1/v2 仍按原版本读取。最小值同时要求取得证人与对全部实数参数的下界，驻点本身不授予极小值结论。

论文公式26、27、28、零向量/交换段落及完整 Lemma 1 共5项通过真实 Lean 和独立数据回放，已具名对齐并关联原文；错误极小值对照被拒绝，保持 UNKNOWN/E0。相关161项测试通过。显式复核后9项已列义务有效、14项待处理，另有300块未分类，全文仍未完成，模型未配置。现有研究项目已达100上限，本轮复用原论文项目并直接关联证据，没有新增 Studio 任务；项目归档/容量管理待补。见[协议v3](docs/protocols/attained-minima-v3.md)、[ADR0092](docs/adr/0092-attained-minima-and-least-squares.md)、[本轮验收](artifacts/least-squares-20261030-01/REPORT.md)。下文保留历史状态。

2026-10-30 · [全文推理工作台](http://127.0.0.1:8765/articles#article-3b58c561897f42d6a097eb1fab8637b4)已连接论文义务、研究任务、正式验证器和证据回收。新增依赖分层图、优先处理清单、原子批量整理、任务状态与可读报告导出，旧论文直接可读。

本轮用真实论文完成了“创建研究任务 → 完整候选 → 新 Lean 检查与独立回放 → 对应确认 → 关联回原文”的全过程。公式24的重复验证只用于框架验收，公式25已建立待研究任务；整篇论文仍未验证完成，模型未配置。见[操作](docs/protocols/article-workflow-v1.md)、[设计](docs/adr/0089-article-research-roundtrip.md)、[验收](artifacts/article-framework-20261030-01/REPORT.md)。以下保留历史交付记录。

2026-10-30 · 新增[全文推理工作台](http://127.0.0.1:8765/articles)：保存固定版本 HTML，按章节定位原文，整理完整义务、前提及依赖，关联已有真实验证节点，并显式复核证据。局部结论与作者的完整推理分别记录；本版尚不能认证整篇论文。

本轮用 *Cauchy–Schwarz Regularizers* v3 建立全文论证清单，已有局部 Lean 证据按原范围引用；没有调用后端模型或执行新 Lean。见[操作与限制](docs/protocols/article-reasoning-audit-v1.md)、[设计](docs/adr/0088-version-bound-article-obligations.md)、[验收记录](artifacts/article-reasoning-20261030-01/REPORT.md)。以下保留此前交付记录。

2026-10-30 · 新增[通用研究中的 Mathlib 检索](http://127.0.0.1:8765/studio)：对8624个固定源码文件建立230480条具名声明候选索引，可查名称、英文和常见中文概念，展开确切来源并引用到当前步骤。有效引用进入后续模型研究上下文；撤回、换路线或源码失效会排除引用，研究文字保持UNKNOWN/E0。模型仍按此前安排稍后配置。

本轮相关回归142通过/10跳过，4项质量检查和6项真实浏览器检查通过；7组真实源码检索示例均有返回。检索不构成适用性验证或新的证明成绩。见[操作](docs/protocols/premise-search-v1.md)、[设计](docs/adr/0084-source-premises-in-research-studio.md)、[本轮证据](artifacts/premise-search-20261030-01/README.md)。下方保留先前交付与历史记录。

2026-10-30 · 当前交付：[通用数学研究工作台](http://127.0.0.1:8765/studio)。任意领域可保存完整原题、定义与条件，建立依赖路线、记录多轮推演/缺口、导出记录，并把完整候选具名交给数域、微积分或经典 PDE 验证器。模型规划和研究接口已接入，真实模型按用户安排稍后配置；领域标签不代表该领域任意问题可解。

本轮从新入口交接的完整实数估计已真实检查并在不同进程重放，取得 PROVED/E4，反馈回写原记录。研究文字保持 UNKNOWN/E0。相关回归 138 项为128通过/10跳过；初轮两个旧真实测试争用后台工作锁，停旧服务后单独复测通过。未重跑全项目，也不把历史失败改成通过。

服务入口 `/studio` 已更新；既有 PDE 完整能量推导保留。操作见[协议](docs/protocols/research-studio-v1.md)，决策见[ADR 0083](docs/adr/0083-cross-domain-research-studio.md)，结果与限制见[验收证据](artifacts/studio-20261030-01/README.md)。

以下保留历史阶段记录；其中“当前”指其当时状态。

2026-10-30 · 当前交付：[PDE 研究工作台](http://127.0.0.1:8765/pde)支持编辑完整推导、合并预览、保存、具名执行真实 Lean 检查及独立进程重放，保留全部条件、原始四步陈述及证据导出。也可沿用逐节点检查。

本轮完整经典 Poisson 能量恒等式取得 PROVED/E4；同条件下改成 1=0 的候选被真实拒绝并保持 UNKNOWN/E0。中间陈述未分别验证，显示 UNKNOWN/E0。71 项相关回归为61通过/10跳过，四项质量检查和五项实际浏览器检查通过，源码在验收期间未变。

进入工作台后选择“一维 Poisson · 完整能量推导”查看本轮记录。列表仅读任务元数据；打开具体记录仍完整复核，首次读取可能等待来源审核。模型仍待本地配置。此为已知经典恒等式验证，不代表一般弱 PDE 或原创难题求解；旧顺序四节点验收和旧 scanner 失败记录保留。见[操作协议](docs/protocols/pde-project-research-v1.md)、[设计](docs/adr/0082-expanded-pde-final-target.md)及[本轮证据](artifacts/pde-delivery-20261030-01/README.md)。

以下为各历史阶段记录，其“当前状态”以本节为准。

<!-- MRA087 -->

2026-10-29 · MRA087 当前状态；下文旧阶段原样保留。

新增可编辑经典PDE语言、确切来源应用、独立账本和CLI。28新契约及12质量命令通过，真实账本专项与14条CLI通过。最新相关346项293通过/52跳过/1项旧目录新增竞态失败，原失败保留，旧scanner未修；不声称全项目回归全绿。

pde-language-v1支持明确实数/函数、导数/可积性、闭区间、边界与新目标，实际导入当前E4证明数据并对新目标独立重放。8历史正目标E4，主动损坏恢复控制后6当前有效。当前显式CLI，原/analysis网页未切换；非一般弱PDE或原创研究。见[协议](docs/protocols/pde-language-v1.md)与[验收](docs/evaluation/pde-language-results.md)。

<!-- MRA086 -->

2026-10-29 · MRA086 当前状态；下文旧阶段原样保留。

新增事件监视扫描器与独立 pde-energy-watched-v2。46契约通过；相关317项266通过/51跳过；12质量命令通过；真实全链与成功重放后丢失监视的发布门禁均通过；14条CLI通过。旧MRA085两项竞态失败保留，旧实现没有修复，本次旧测试通过不改变历史结论。

新的可选运维CLI每次仍核全字节，并通过Linux inotify与最终目录重枚举检测扫描期间变化。复用原不可变一维Poisson数学树，独立身份/证据目录；不切换原网页或旧执行器。5正目标历史E4，主动损坏恢复控制后1当前有效E4；无新PDE语言、模型调用或原创成果。见[验收](docs/evaluation/watched-integrity-results.md)与[协议](docs/protocols/watched-integrity-v2.md)。

<!-- MRA085 -->

2026-10-29 · MRA085 当前状态（下文旧阶段保留为历史记录）。

真实Poisson能量链专项通过（939.626秒）、14条CLI与12质量命令通过；相关协议回归270项为218通过/50跳过/2项旧streaming竞态失败，原失败及可复现时间戳诊断保留。

新增一维经典Poisson积分/能量链运维CLI及真实引理证明数据复用，5历史正目标E4；主动损坏恢复控制后1当前有效E4。原/analysis网页保持，尚未接新PDE结构化输入。不是一般PDE、存在唯一性、原创或模型成绩；模型未配置、调用0。见[验收](docs/evaluation/pde-energy-results.md)。


<!-- MRA084 -->

2026-10-29 · MRA084 当前状态（下文旧阶段保留为历史记录）。

257项相关回归208通过/49跳过；12质量命令通过；单独真实图/HTTP专项通过（857.806秒），14条实际CLI通过；实际Edge桌面/390px浏览器14项通过。

新增[分析研究工作台](http://127.0.0.1:8765/analysis)：完整微积分目标图、逐节点具名真实检查、原问题独立门禁、取消/撤销/导出。前置仅调度，不导入引理证明项。本轮五图七个已记录终态检查，5历史节点E4；损坏恢复/撤销控制后1当前节点E4、0当前原问题E4，成功原问题曾分别取得自己的E4。模型未配置，无PDE或原创研究成绩。见[验收](docs/evaluation/calculus-research-results.md)。

<!-- MRA083 -->

2026-10-29 · MRA083 当前状态（下文旧阶段保留为历史记录）。

204项相关回归164通过/40跳过，质量10命令与真实工件复核通过（数学目标含未完成）；11个历史E4检查，三项实际恢复控制后8个当前E4；未完成项['quantified-derivative', 'composed-derivative']。新增带显式可导/可积、测度、端点与a.e.条件的结构化微积分CLI；原内积语言保持。无新网页、PDE或原创研究声明。见[验收](docs/evaluation/calculus-language-results.md)。

<!-- MRA082 -->

2026-10-29 · MRA082 当前状态（下文旧阶段保留为历史记录）。

184项相关回归145通过/39跳过，质量10命令与单独真实专项通过。九正目标9个历史E4，恢复控制后7个当前E4；五数学负对照和两政策控制UNKNOWN，两额外安全重放拒绝。新增固定导数/区间积分隔离验证CLI，明确可导/可积、测度、端点与a.e.语义；尚无通用微积分AST或PDE能力。见[验收](docs/evaluation/calculus-foundation-results.md)。

<!-- MRA081 -->

2026-10-29 · MRA081 当前状态（下文旧阶段内容保留作历史记录）。

本轮两样本未证明稳定提速：第一组新版更快，第二组旧版更快。新profile仅作为显式可选实验入口，未替换原默认执行。164项相关回归，127通过/37跳过；质量10命令及单独真实专项通过。同资源两目标交错对照和真实损坏恢复/取消控制已收集，共7完整检查/12阶段，当前3个E4检查。新增独立流式扫描分析CLI，每次仍核全部实际字节；原A1语言和数学目标保持。性能仅为两个已知样本的观察，无PDE能力声明。见[验收](docs/evaluation/analysis-streaming-results.md)。

<!-- MRA080 -->

2026-10-29 · MRA080 当前状态（下文旧阶段内容保留作历史记录）。

新增受控实分析CLI：显式实数、实内积空间和函数/λ输入，完整条件确认后真实检查与独立重放。最终30秒计划的12个已知正例中11个完成真实检查/不同PID重放，未完成：pointwise-hypothesis；未完成者保持UNKNOWN。损坏恢复与取消控制通过；当前未提供导数/积分或PDE能力。见[协议](docs/protocols/analysis-language-v1.md)与[验收](docs/evaluation/analysis-language-results.md)。

<!-- MRA079 -->

新增独立实分析基础实验：三个固定内积正例通过真实检查/独立重放，错误条件、类型及不受许可公理被拒绝；损坏恢复不复活。当前仅运维CLI、Type 0已知目标，下一步扩展结构化实数/向量/函数输入。见[协议](docs/protocols/analysis-foundation-v1.md)与[验收](docs/evaluation/analysis-foundation-results.md)。

<!-- MRA078 -->

新增显式可选的精简递归诊断CLI structure-recursion-compact-*（schema v2）。在原1MiB限额下，两份已见失败程序由日志溢出恢复为完整候选拒绝反馈，正例仍通过真实检查与独立重放；旧v1和网页保持。后续数学方向优先[分析与偏微分方程](docs/product/analysis-pde-roadmap.md)。见[本轮验收](docs/evaluation/compact-recursion-diagnostics-results.md)。


<!-- MRA077 -->

新增目标优先的跨任务引理匹配：在 /typed-research/lemmas 的「跨任务复用」输入完整新目标，查找当前已证导出、自动填写实参及全部条件证明，再预览、具名执行真实导入和独立重放。六个作者可见衍生目标中旧无导入0/6、新匹配3/6；搜索fuel不同且部分基线触及日志限额，不能外推一般能力。见[验收](docs/evaluation/export-lemma-matching-results.md)。


<!-- MRA076 -->

新增统一引理研究工作台 /typed-research/lemmas：局部义务规划→完整程序检查→独立引理导出→跨任务显式复用，保留完整条件/来源及真实双阶段证据。桌面与390px实际Edge14项通过；旧结构图仍v3，新流程独立。模型未配置。见[验收](docs/evaluation/lemma-research-workbench-results.md)。

<!-- MRA075 -->

新增独立局部引理导出与跨任务证明数据复用：保留全部原条件和来源关系，两个引理重新检查到E4，新目标真实导入并独立重放。CLI需显式执行，网页仍为v3。详见 [验收](docs/evaluation/structure-lemma-library-results.md)。

<!-- MRA074 -->

新增有界等式义务规划CLI：先证明内层完整等式，再传至共享外层项上下文，自动串联最多两个经检查的局部引理。八题作者可见对照旧v1为0/8、新v2为5/8；网页仍v3，非通用引理发现。见[协议](docs/protocols/structure-obligation-proposals-v2.md)与[验收](docs/evaluation/structure-obligation-planning-results.md)。

<!-- MRA073 -->

新增受控递归证明CLI：支持固定Nat算术改写、先证明再调用的局部引理，最终原目标不增加假设。六题作者可见对照旧v4为1/6、新程序为4/6；次数相加与交换复合均通过真实Lean及独立重放。最多两个手工局部引理，网页仍v3。见[协议](docs/protocols/structure-recursion-program-v1.md)与[验收](docs/evaluation/structure-recursion-program-results.md)。

<!-- MRA072 -->

新增显式可选的结构策略v4 CLI：优先符号闭合与不同归纳方向，对递归／量词子项作捕获避免的自由参数匹配。六题作者可见诊断中v3为2/6、v4为3/6；新增通过的是使用原假设中的递归等式，两道旧障碍仍UNKNOWN。网页仍用v3，旧版本与分数保留。见[协议](docs/protocols/structure-strategy-proposals-v4.md)与[验收](docs/evaluation/structure-strategy-v4-results.md)。

<!-- MRA071 -->

新增公开来源函数迭代领域对照CLI：固定Mathlib陈述出处、完整改写条件和曝光记录，旧v1／当前v3同预算比较八题。实际两组各5/8 E4，次数相加／交换复合及删条件对照保持UNKNOWN；不改旧成绩，不计盲测或原创能力。见[协议](docs/protocols/function-dynamics-evaluation-v1.md)与[验收](docs/evaluation/function-dynamics-evaluation-results.md)。

<!-- MRA070 -->

新增证据暂忙自动恢复：数学研究工作台遇到明确锁暂忙时有界重读 GET，等待期间隐藏旧判断、清除旧执行确认；切换语境后丢弃旧读取。全部 POST 仍只发送一次。重新审计既有完整 E4，不重新执行数学检查；本轮相关回归349项通过／67项跳过。见[协议](docs/protocols/evidence-read-recovery-v1.md)和[验收](docs/evaluation/evidence-read-recovery-results.md)。

<!-- MRA069 -->

新增“匹配已证引理”：在完整原目标下自动匹配当前已证前置的实参与全部条件，展示嵌套调用与尚未闭合条件。选择只带入草稿，须重新预览、具名执行真实证明数据导入及独立重放；建议恒UNKNOWN/E0，原图判断独立。见[协议](docs/protocols/structure-lemma-suggestions-v1.md)和[验收](docs/evaluation/structure-lemma-suggestions-results.md)。

<!-- MRA068 -->

结构候选升级为v3：能对符合条件的原目标参数提出单参数或共同泛化，显示程序与保守排除信息。单初态／双初态重复变换任务取得真实完整E4，缺少后继条件保持UNKNOWN；旧v1／v2及历史成绩保留。产品保存改为完整目录原子发布，修复后台扫描把准备中新图误判失效的竞态。见[协议](docs/protocols/structure-strategy-proposals-v3.md)与[验收](docs/evaluation/structure-generalization-results.md)。

<!-- MRA067 -->

工作台候选策略升级为递推索引排序 v2：依据完整目标中实际使用的递推选择归纳方向，并显示排序依据；旧版接口和六题评测结果保留。重命名且交换加法参数的变体经真实候选／独立重放通过，缺交换律仍 UNKNOWN。见[新版协议](docs/protocols/structure-strategy-proposals-v2.md)与[本轮结果](docs/evaluation/structure-strategy-ast-results.md)。

<!-- MRA066 -->

新增固定预算结构策略诊断CLI：structure-strategy-evaluation-prepare／run／report。冻结完整候选后具名真实执行，两组每题最多2次、每次共享20秒；六题实测固定程序2/6、生成候选4/6，保留全部UNKNOWN、来源／曝光及成本。属于作者可见功能诊断，非独立研究成绩；现有网页和生成器未改动。见[协议](docs/protocols/structure-strategy-evaluation-v1.md)与[结果](docs/evaluation/structure-strategy-evaluation-results.md)。

<!-- MRA065 -->

结构与归纳节点新增“提出候选程序”：在完整原条件下生成有界改写／Nat归纳程序，显示当前真实失败反馈。选择仅带入草稿，须预览、另存并具名真实核验；候选恒UNKNOWN/E0。新增 structure-strategy-plan／structure-strategy-node CLI，模型继续待配置。见[协议](docs/protocols/structure-strategy-proposals-v1.md)与[验收](docs/evaluation/structure-strategy-proposals-results.md)。

<!-- MRA064 -->

新增 structure-reuse-evaluation-* CLI：固定六题幺半群／同态／自然数幂对照，导入3/6、展开4/6，来源成本与失败／UNKNOWN全分母保留。作者可见机制诊断，无模型或原创能力成绩；现有工作台更明确区分原图与组合判断。见[协议](docs/protocols/structure-proof-reuse-evaluation-v1.md)及[结果](docs/evaluation/structure-proof-reuse-evaluation-results.md)。

<!-- MRA063 -->

结构证明复用已接入同一数学研究工作台与后台单 worker：可核对完整载体、运算、定义、全部原条件和调用实参，具名提交新的组合检查、取消、撤销及导出完整证据。原图与组合判断分别记录；模型继续待本地配置。见[协议](docs/protocols/structure-proof-reuse-web-v1.md)与[本轮验收](docs/evaluation/structure-proof-reuse-web-results.md)。

<!-- MRA062 -->

新增显式结构完整证明数据复用 CLI：structure-reuse-*。在同一结构图的完整载体／运算／定义及全部原条件下，显式应用当前已证引理，支持多级组合与函数合同等式；原冻结导入执行器真实核验数据，再独立重放完整结果。新组合E4与原图判断分别报告，尚未接网页／后台队列。见[协议](docs/protocols/structure-proof-composition-v1.md)与[验收](docs/evaluation/structure-proof-reuse-results.md)。

<!-- MRA061 -->

新增结构与归纳网页操作：在同一[数学研究工作台](http://localhost:8765/typed-research)切换“数域与定义／结构与归纳”，完整核对载体、运算、递推定义、全部结构定律及目标，再逐节点具名交给原单worker真实检查。支持完整导出、取消、撤销和丢响应后的原ID人工恢复；原问题须自己的E4，结构前置尚不导入证明项。见[网页协议](docs/protocols/structure-research-web-v1.md)与[验收](docs/evaluation/structure-research-web-results.md)。下文编号描述各历史阶段，不表示旧限制仍是当前功能。

<!-- MRA060 -->

新增同项目显式结构／归纳研究图 CLI：structure-research-*。共享载体、运算、递推定义和闭合结构定律，每节点保留完整原目标；同态保持自然数幂有自己的真实归纳检查和独立重放。支持具体前置门禁、取消／撤销及父提交前退出的保守恢复。前置只控制调度，网页／后台队列尚未消费新协议。见[协议](docs/protocols/structure-project-research-v1.md)及[验收](docs/evaluation/structure-project-research-results.md)。

<!-- MRA059 -->

新增显式代数结构与真正自然数归纳的 structure-* CLI：可声明 Type 载体、运算、全部定律和 Nat.rec 定义，再指定具名重写、归纳变量及泛化变量。非交换幺半群幂加法、乘法同态和三角数已通过真实检查与独立重放；错误证明保持UNKNOWN。仅CLI，现有网页尚不消费新协议。见[协议](docs/protocols/explicit-structures-v1.md)及[验收](docs/evaluation/explicit-structures-results.md)。

<!-- MRA058 -->

MRA058 完成有理数与显式混合数域的固定六题程序复用对照：import 4/6、inline 5/6，差异来自归档不可用题；完整条件和同一源程序保留，错误条件仍UNKNOWN。计入来源构建，不支持总体提速或原创研究能力结论。见[结果](docs/evaluation/typed-proof-reuse-evaluation-results.md)。 新增 typed-reuse-evaluation-prepare/run/report CLI。

<!-- MRA057 -->

类型研究页现已接通真实证明复用：选择当前已证来源，显式填写实参与全部条件证明，预览并具名提交，由单后台worker实际导入和独立重放。支持多级复用、完整报告导出、取消和撤销传播；组合E4与上方原图判断分开。见[网页协议](docs/protocols/typed-reuse-web-v1.md)和[验收](docs/evaluation/typed-reuse-web-results.md)。

<!-- MRA056 -->

新增类型化引理实际证明数据复用 CLI：`typed-reuse-catalog/preview/submit/work/report`。完整图目标不变，显式实参及全部条件证明，支持 Nat/Int/Rat 闭合定义与多级真实内核导入、独立重放和撤销传播。新组合结果与原图判断分开；本轮尚未接网页/HTTP后台队列。见[协议](docs/protocols/typed-proof-composition-v1.md)和[验收](docs/evaluation/typed-proof-reuse-results.md)。
<!-- MRA055 -->

新增 [类型与定义研究工作台](http://localhost:8765/typed-research)：在同一研究项目／研究线下编辑共享 Nat／Int／Rat 定义与完整任务图，预览所有目标，逐节点具名执行真实 Lean 与独立重放，查看分开的执行状态／数学判断，取消、撤销及导出。定义签名与原文直接展示，完整结构化数据可展开。前置只控制调度与绑定具体证据，尚未导入其证明项；原问题仍须自己的完整 E4。模型未配置，不自动理解任意自然语言。见[协议](docs/protocols/typed-project-research-v1.md)和[验收](docs/evaluation/typed-project-research-results.md)。下文各编号保留历史阶段范围。

<!-- MRA054 -->

新增显式数学类型与定义的 CLI：typed-catalog、typed-preview、typed-submit、typed-work、typed-report。可输入 Nat／Int／Rat 混合变量、显式类型转换、非递归数值函数与命题定义，再对含全部定义／前提的完整目标执行真实 Lean 检查和独立重放。分数精确处理；Nat 截断减法和 Rat 总除法（a/0=0）明确区分。当前为独立接口，旧网页和研究图不自动接收这些目标。见[使用协议](docs/protocols/typed-definitions-v1.md)及[验收](docs/evaluation/typed-definitions-results.md)。

<!-- MRA053 -->

新增只读诊断 `python3 -m mra runtime-audit`：完整核对运行环境内容并记录范围、字节数及耗时，不执行 Lean，始终 UNKNOWN/E0。真实剖析定位到环境扫描成本；实验显示多线程更慢，缓冲单线程仅有约 5.16% 的局部扫描差，因此保留原数学验证路径。见[协议](docs/protocols/runtime-audit-v1.md)和[实测](docs/evaluation/runtime-audit-results.md)。

<!-- MRA052 -->

新增完整目标的证明复用配对评测 CLI：`reuse-evaluation-prepare`、`reuse-evaluation-run`、`reuse-evaluation-report`。固定五道作者可见输入，同预算比较导入真实前置证明与展开同一程序，冷启动包含前置构建和独立重放。实际两组各 3/5 E4；导入组冷启动成本更高，本轮不支持总体性能提升的结论。见[协议](docs/protocols/proof-reuse-evaluation-v1.md)及[验收](docs/evaluation/proof-reuse-evaluation-results.md)。这是机制诊断，模型调用为零；新报告通过 CLI 导出，网页保留既有入口。

<!-- MRA051 -->

新增 [研究批次](http://127.0.0.1:8765/campaigns)：预审原任务全部节点的完整证明程序与总预算后，混合复用原版/新版完整组合 E4，逐节点实际导入前置证明数据，再对完整目标真实检查与独立重放。提供网页、HTTP、CLI，支持预算停止、取消、中断恢复、完整导出和撤销级联。运行状态与数学结论分开；原问题必须有自己的完整证据。见[协议](docs/protocols/research-campaign-v1.md)与[验收](docs/evaluation/research-campaign-results.md)。仍为受控 Nat 作者程序；一般数学定义、自主规划、真实模型与独立收益评测待后续。下文保留各阶段历史。

<!-- MRA050 -->

新增组合成果库 `/proof-library` 与传递复用 `/library-compositions`：具名确认私有存储权利后，保存完整目标、所有原前提、真实检查工件与祖先，再通过真实内核导入和独立重放继续组合。来源撤回或损坏使后继持续停用。见[协议](docs/protocols/proof-library-v1.md)与[验收](docs/evaluation/proof-library-results.md)。当前仍为受控Nat手工证明，新协议尚未接入研究图或证明独立求解收益。

<!-- MRA049 -->

`/research` 新增组合证据面板：把已有真实组合E4与节点的完整目标和具体前置精确关联，支持具名确认、撤销、级联停用、完整导出及带入目标开始组合。原路线和既有续研保留各自判断；关联本身不启动Lean，也不自动解锁原调度器。见[协议](docs/protocols/research-compositions-v1.md)与[验收](docs/evaluation/research-compositions-results.md)。下文为各阶段历史记录；一般数学类型、成果再归档、真实模型效果和独立预算收益仍待后续。

<!-- MRA048 -->

新增 `/compositions` 引理组合工作台：有效私有Mathlib E4来源、完整Nat公式表单、可视化证明树、每次实例化条件预览、具名确认、真实候选与独立重放、历史/导出/取消/撤销。沿用MRA047数学核心，网页后台worker已接入第15个主库。研究图关联、组合成果再入库、一般数学定义与真实模型效果仍待后续。见[ADR039](docs/adr/0039-private-composition-workbench.md)和[验收](docs/evaluation/lemma-composition-web-results.md)。

<!-- MRA047 -->

新增私有引理证明项组合核心与CLI：显式提供完整实参与条件证明，将有效归档的NDJSON证明数据在隔离名称下重新内核检查，用于新Nat目标，再完成独立数据重放。具名确认、撤销/损坏传播、取消与中断保持。网页与研究图尚未接入；不是独立求解收益评测。见[协议](docs/protocols/lemma-composition-v1.md)与[验收](docs/evaluation/lemma-composition-results.md)。

<!-- MRA046 -->

研究页增加来源请求协调：四个研究面板按序读取，暂时忙碌的来源最多自动重读两次；切换语境丢弃过期读取，全部POST保持手动确认与单次发送。逐步推演增加失败重读入口。见[协议](docs/protocols/research-loading-v1.md)及[验收](docs/evaluation/research-loading-results.md)。

<!-- MRA045 -->

新增[依赖证据续研](docs/protocols/research-continuation-v1.md)：/research可保存原任务的独立续研，当前已证前置解锁后继的具名真实检查。每节点仍完整验证/独立重放，前置撤销传播；支持取消、来源核对、撤销和导出。依赖只作调度，不注入假设或导入证明项，原路线记录保留。见[ADR036](docs/adr/0036-evidence-dependent-research-continuation.md)与[验收](docs/evaluation/research-continuation-results.md)。

<!-- MRA044 -->

新增[研究推演证据关联](docs/protocols/research-evidence-v1.md)：在 /research 打开完整已证推演，核对完整原式后具名关联到原节点，支持来源审计、撤销与完整导出。关联复用既有E4，不执行新的Lean或重放；原路线结论单独保留，不自动满足路线前置。见[ADR035](docs/adr/0035-named-research-evidence-links.md)与[验收](docs/evaluation/research-evidence-results.md)。

<!-- MRA043 -->

新增[受控逐步推演](docs/protocols/proof-steps-v1.md)：同一研究节点可先读取真实初始目标，再逐步选择固定 tactic，从历史开放状态另建分支，保留失败记录。每次重建完整前缀；开放目标恒为UNKNOWN/E0，完整证明仍须类型/公理审计与独立重放。推演不自动晋升原任务或知识库，模型未配置。见[ADR034](docs/adr/0034-controlled-proof-prefix-branches.md)与[验收](docs/evaluation/proof-steps-results.md)。

<!-- MRA042 -->

新增[同项目研究路线](http://127.0.0.1:8765/research)：在所选项目和研究线内保存完整问题/引理图，统一引理组合、固定策略、反例与真实 Lean 目标观测。先预览、保存不执行、逐次具名确认，再完成真实候选检查和独立重放；失败与中断保留。引理齐全不自动证明原问题。固定 gcd 程序可展示实际1→1→2→0目标，仍非一般交互式证明搜索；模型未配置。主库仍十四个，备份新增任务目录和子账本。见[协议](docs/protocols/research-flow-v1.md)、[ADR033](docs/adr/0033-project-research-routes-and-lean-goals.md)与[验收](docs/evaluation/research-flow-results.md)。

新增[Lean 策略实验](http://127.0.0.1:8765/strategies)：对同一个完整 Nat 目标尝试固定 `omega` 或受控 gcd／互素化简。先预览全部条件、策略和预算，具名确认后执行真实 Lean 与独立证明数据重放；失败保持 UNKNOWN。提供反馈、历史、导出、取消、撤销与重新核对。单次战术尝试仍非一般证明状态搜索；模型未配置。见[协议](docs/protocols/lean-strategies-v1.md)、[ADR032](docs/adr/0032-fixed-lean-strategy-experiments.md)与[实测](docs/evaluation/lean-strategies-results.md)。新增独立策略账本后共十四个主业务库，备份需同时保留运行工件。

新增[有限反例探索](http://127.0.0.1:8765/number-theory)：保留完整猜想与前提，寻找具体 Nat 赋值。数值结果只是 UNKNOWN/E0；导出候选、审查原命题的完整否定并具名确认后，才执行真实 Lean 与独立重放。只有否定得到有效 E4 证据时原猜想标为 REFUTED。见[协议](docs/protocols/counterexamples-v1.md)、[ADR031](docs/adr/0031-reviewed-finite-counterexamples.md)与[实测](docs/evaluation/counterexample-results.md)。

新增[数论搜索对照](http://127.0.0.1:8765/evaluations#search-comparison)：16 道作者可见的完整目标，不提供参考证明，比较单节点引用与多步组合。先准备并查看所有候选，再具名确认真实 Lean/独立重放；失败与未知保留在完整分母。浏览器只读，支持逐题记录和导出。见[协议](docs/protocols/search-comparison-v1.md)、[ADR030](docs/adr/0030-reviewed-search-comparison.md)和[实测](docs/evaluation/search-comparison-results.md)。

新增[有界数论证明搜索](http://127.0.0.1:8765/number-theory)：输入完整前提与结论即可组合现有 22 条受控引理，不必先填写证明项，也无需配置模型。搜索候选保持 UNKNOWN/E0；明确带入后重新预览、具名确认，再交给真实 Lean 与独立重放。搜索深度、步数、子项和时间均受限，失败不能作为反例。见[使用协议](docs/protocols/number-theory-search-v1.md)、[ADR029](docs/adr/0029-bounded-number-theory-search.md)和[验收](docs/evaluation/number-theory-search-results.md)。

将数学命题交给真实 Lean 检查，并保存确切命题、工具链、语义对齐和证据工件。已完成 P0 验证、形式化候选协议、审核状态、自然数算术评测基础、本地参考/证书任务队列及浏览器工作台。新增自然数猜想入口：自由输入原文、生成有界形式化候选、具名确认、有限证明/反例计划与反馈修复，再经真实 Lean 及独立进程重放。兼容 API 适配器已提供；按用户选择暂不配置真实模型，因此目前不能报告自动求解能力或模型分数。

开发根目录：`/home/blacklions/workspace/Linux/数学形式化验证Agent`。Windows 入口：`\\wsl.localhost\Ubuntu-22.04\home\blacklions\workspace\Linux\数学形式化验证Agent`。D 盘原目录保留备份，项目记忆只保存在 `/mnt/d/数学形式化验证Agent/memory/`。

新增固定预算模型策略评测：`python3 -m mra model-benchmark --plan` 可在未配置模型时查看计划；配置后运行 `python3 -m mra model-benchmark`，每题一次模型提案、一次真实 Lean 检查，并记录耗时、已知用量和工件。该模式提供作者形式化目标，不测自然语言翻译；真实模型分数尚未产生。详见 [评测使用说明](docs/protocols/model-evaluation-v1.md)。

新增 [Mathlib 功能评测](http://127.0.0.1:8765/evaluations#ml-bench-d14c00d3f4db42cf838bc6668d4a6505)：8道作者编写且已查看的结构化输入题，包含正确证明、错误证明、遗漏前提及未开放定义。固定预算执行后，3题真实 Lean 与独立重放通过、3题证明拒绝、2题定义阻塞，后5题保持未知。页面支持完整题目、分组、预算命令、逐题诊断、历史与导出；37.5%仅为作者输入检查通过率，模型求解率为空。CLI先用 `python3 -m mra mathlib-benchmark --plan` 核对，再具名确认执行；见[协议](docs/protocols/mathlib-evaluation-v1.md)、[ADR027](docs/adr/0027-authored-mathlib-diagnostics.md)及[验收](docs/evaluation/mathlib-evaluation-results.md)。

## 完整前端工作区

新增 [gcd 与互素实验](http://127.0.0.1:8765/number-theory#mathlib-741f76300d1b47e99633fd9642e60fc2)：可表达最大公因数、互素以及它们与整除的组合命题，提供22条受控引用与6份可编辑示例。完整前提、目标和证明项预览后具名确认，再进行真实Lean与独立重放；历史、导出、取消及撤销均已接通。入口也位于原Mathlib实验顶部，沿用桌面/手机设计。77项相关回归和实际Edge10项通过；见[协议](docs/protocols/number-theory-v1.md)、[ADR028](docs/adr/0028-controlled-gcd-and-coprimality.md)及[验收](docs/evaluation/number-theory-results.md)。新结果独立保存，旧评测结论保留原协议，模型仍稍后配置。

统一前端现覆盖十一个入口：研究总览、猜想探索、研究项目、论文审查、知识图谱、Mathlib 实验、形式验证、评测中心、研究记录、产品全景及设置。采用暖白/墨绿视觉、数学 SVG、共享搜索与桌面/手机布局，无新增前端运行依赖。

新增 [Mathlib 引用实验](http://127.0.0.1:8765/mathlib)：自然数整除定义、九条完整类型的受控引用、显式组合证明项、具名核对、真实 Lean 检查与独立重放。提供整除组合、Mathlib 等价式、截断减法与失败对照四份可编辑样例；历史、取消、撤销、重新核对与导出均使用真实本地账本。协议和 CLI 见 [mathlib-lab-v1](docs/protocols/mathlib-lab-v1.md)，验收见 [mathlib-lab-results](docs/evaluation/mathlib-lab-results.md)。目前是人工结构化输入；Mathlib 结果现可具名纳入同一研究项目与研究线，节点支持版本、前置、重新带回检查与持久撤销传播。Mathlib真实E4成果也可私有入库、按原文/完整公式检索、撤销/移除并带回重新检查，模型继续稍后配置。见 [研究节点协议](docs/protocols/research-mathlib-v1.md) 与[验收](docs/evaluation/research-mathlib-results.md)。

启动后先看 <http://127.0.0.1:8765/>，整体构造见 <http://127.0.0.1:8765/workflow>。猜想与形式验证继续使用真实后端；论文页已新增人工标注的自然数单定理真实审查，阅读布局示例仍明确标注预览，知识页已接入单定理审查的私有入库、检索与撤销传播。评测页只读真实计划和历史，设置页只在浏览器生成配置内容。详见 [页面与数据说明](docs/protocols/workspace-ui-v1.md)、[ADR-015](docs/adr/0015-unified-research-workspace.md) 和 [前端验收](docs/evaluation/frontend-design-results.md)。

## 私有知识库

在论文审查结果中点“存入私有知识库”，或访问 <http://127.0.0.1:8765/library>。先查看真实 PROVED/E4 义务，确认完整公式、来源与本机私有保存权利，再保存条目和证明文件。图谱、列表与检索读取真实账本；撤销步骤会使引用它的条目失效，命题的独立证明继续独立管理。

当前接纳人工标注 Nat/Int 单定理的已证明义务，入库本身不运行模型或新 Lean 检查。资料默认不共享、不训练；来源或工件改变会停用条目，不自动恢复。移除清理知识副本，原审查仍保留。详见[知识库协议](docs/protocols/knowledge-v1.md)、[ADR-017](docs/adr/0017-private-provenance-knowledge.md)与[实测结果](docs/evaluation/knowledge-results.md)。Mathlib实验结果已通过独立私有归档协议接入本页，见[归档协议](docs/protocols/mathlib-knowledge-v1.md)；一般 Lean 包和训练仍待后续。

## 私有 Mathlib 引理实例化

在知识图谱的真实 Mathlib 归档详情点“实例化引用”，显式填写新 Nat 变量及所有原变量的替换项。系统同时替换完整前提、目标和原证明输入；先核对前后完整命题，再保存待检查实例。带回 Mathlib 实验后重新预览、具名确认并完成真实 Lean 与独立重放，最后明确关联完全相同的新检查结果，才显示已证明。

实例支持检索、全局搜索、完整来源与替换详情、JSON 导出、撤销/移除及来源失效传播。保存或代入本身保持 UNKNOWN/E0，来源归档的原 run 不能代替独立新检查。演示：<http://127.0.0.1:8765/library#ml-reuse-bd42c4a9011f4756a9c5188654f59e12>。77项相关主回归、实际Edge12项/11张截图、15条CLI通过；见[实例化协议](docs/protocols/mathlib-reuse-v1.md)、[ADR026](docs/adr/0026-explicit-mathlib-lemma-reuse.md)和[验收](docs/evaluation/mathlib-reuse-results.md)。当前范围是现有 Nat 整除定义与受控引用，仍需人工结构化输入；没有一般证明项导入、自动搜索或原创难题求解收益证据。

## 多引理研究空间（首期）

打开 `http://127.0.0.1:8765/research`，新建研究项目，保存原问题、定义笔记、子问题和候选引理。分支与版本快照保留不同路线；公式使用显式Nat/Int完整量词与前提。节点可带入单定理真实验证，再具名关联完全相同的历史E4来源。前置未验证则阻塞，撤销、修改或源工件变化会传播失效；主问题仍需自己的证明。

研究空间提供研究结构与确切证据管理；新增受控多节点执行见文末及 [执行协议](docs/protocols/research-runs-v1.md)。图中定义仍为文本语境；知识实例化见文末，受控Mathlib定义和证明引用见独立实验入口，一般定义及跨域图绑定待后续。见[管理协议](docs/protocols/research-space-v1.md)、[管理实测](docs/evaluation/research-space-results.md)和[长期路线](docs/product/research-capability-roadmap.md)。

## 整数域与长期研究方向

单定理审查现在可显式选择自然数 ℕ 或整数 ℤ，输入负数、整数取负和整数减法，并用负整数见证核验反例。六份作者示例覆盖两种域；域切换需重新准备和确认。知识归档保留数学域与完整条件。猜想模型生成目前仍限定Nat。

本轮更新受信编译器和审查实现；旧审查及知识保留原始证据，但旧协议结论停用，需重新确认和检查，不能自动视为新版认证。见[整数验收](docs/evaluation/integer-results.md)、[ADR-019](docs/adr/0019-typed-integer-verification.md)。

长期目标是支持重大原创数学研究。后续路线包括定义与引理管理、多分支研究任务、文献与知识引用、证明状态搜索及专家复核，见[研究能力路线](docs/product/research-capability-roadmap.md)。当前算术功能不代表已能解决四大期刊级研究问题。

## 单定理双通道审查

新增表单支持新建、带入上方文本草稿、行号预览、检查策略、引用校验和标注导出。变更会清除旧确认；不自动提取公式。见 [ADR-018](docs/adr/0018-guided-paper-annotations.md) 与[表单验收](docs/evaluation/annotation-editor-results.md)。

打开 <http://127.0.0.1:8765/papers#single-theorem-review>，选择作者示例，或在表单中填写原文、前提、结论与步骤，可直接输入 `n + 2 > n` 等公式；也可切换 JSON 编辑。准备并逐项确认原文行号、公式和步骤引用，再启动真实 Lean 检查。命题本身与作者标注的证明步骤分别给出判断，可定位具体缺口、读取检查工件、取消、撤销确认及导出记录。未验证步骤不能被后续步骤当作可信引理。

本阶段不需要模型，支持人工标注的受限 Nat 单定理，尚未接入自动命题提取、PDF 解析和一般论文审稿。三个独立编写的例子展示完整证明、真命题但错误原证明、缺条件的真实反例。详见 [审查协议](docs/protocols/paper-review-v1.md)、[ADR-016](docs/adr/0016-annotated-single-theorem-review.md) 和 [实测结果](docs/evaluation/paper-review-results.md)。CLI 可先运行 `python3 -m mra paper-review-plan --input evals/paper-review-v1/true-claim-bad-proof.json` 查看计划，具名确认后再执行。

## 运行

在 WSL Ubuntu 22.04 内执行：

```bash
cd '/home/blacklions/workspace/Linux/数学形式化验证Agent'
python3 -m mra cases
python3 -m mra environment
python3 -m mra check nat_add_zero
```

没有语义对齐确认时，即便 Lean 检查通过，也只授予 `E3_KERNEL_CHECKED`，数学判断保持 `UNKNOWN`。核对 `cases` 显示的原始陈述、量词、定义域与实际形式化后，可用具名记录确认该映射：

```bash
python3 -m mra check nat_add_zero --reviewer '你的名字'
python3 -m mra check false_all_nat_zero --reviewer '你的名字'
```

第一个案例证明 `∀ n : Nat, n + 0 = n`；第二个案例证明 `¬ (∀ n : Nat, n = 0)`，反例是自然数 1。确认针对单一版本的命题，不代表整篇论文或自然语言翻译器已获认证。

```bash
python3 -m mra benchmark
MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v
python3 ops/scripts/check_source.py
```

`artifacts/p0-benchmark.json` 保存基准结果，`artifacts/runs/<案例-id>/` 保存生成的 Lean 源、请求、完整环境内容清单、标准输出/错误、检查回执、对齐记录和报告。基准使用自有样例的具名作者对齐声明，不能解释为用户或外部数学专家确认。`python3 -m mra report artifacts/runs/<目录>` 可查看历史报告。

候选检查成功后，`replay/` 保存证明数据、受信检查模板、策略和第二个进程的日志。该进程不导入候选模块，使用封存基础库和同一 Lean 内核重新检查本地声明、精确目标及公理。两阶段共享原 Worker 墙钟预算；缺少或失败的重放不能获得 E3/E4。范围和验收见 [ADR-009](docs/adr/0009-data-only-kernel-replay.md)、[重放协议](docs/protocols/kernel-replay-v1.md) 与 [验证记录](docs/evaluation/independent-replay-results.md)。

## 受限证明证书

```bash
python3 -m mra certificate-target nat_add_zero
python3 -m mra check-certificate nat_add_zero --input evals/certificates/add-zero.json
python3 -m mra check-certificate nat_add_zero --input evals/certificates/add-zero-lambda.json --reviewer '你的名字'
python3 -m mra check-certificate false_all_nat_zero --input evals/certificates/not-all-zero.json --reviewer '你的名字'
```

`certificate-target` 提供原文、精确目标和版本指纹，不启动 Lean。调用者提交 JSON 表达式证书，由受信模板直接构造内核表达式，再完成两阶段检查；证书不作为 Lean 源、tactic 或命令执行。前两个例子使用不同证明项核验同一个目标，最后一个证明“所有自然数都为零”的完整否定。省略审核者时仍为 UNKNOWN/E3。

首期只启用 `nat_add_zero` 和 `false_all_nat_zero`；目标不能由证书改写。原始与规范化文件均限 64 KiB，表达式限 2,048 节点、深度 64。同步 CLI/API 和持久任务共用相同证书协议，尚未接入模型评测。格式、边界与结果见 [证书协议](docs/protocols/kernel-certificates-v1.md)、[ADR-010](docs/adr/0010-bounded-kernel-certificates.md) 和 [验收记录](docs/evaluation/kernel-certificate-results.md)。

## 持久参考与证书任务

```bash
python3 -m mra task-submit nat_add_zero --request-id add-zero-v1
python3 -m mra task-submit-certificate nat_add_zero --input evals/certificates/add-zero.json --request-id certificate-add-zero-v1
python3 -m mra task-work --task-id <返回的 task-id>
python3 -m mra task-report <task-id>
python3 -m mra task-cancel <task-id>
```

提交只入队；相同请求 ID 和内容不会重复执行。单次 worker 持久记录真实阶段，取消会终止运行中的检查进程组。中断后 `task-recover` 保留尝试历史但不给出数学结论；`task-retry <task-id> --attempt-id <最新失败尝试> --request-id <重试请求ID>` 会重新检查，初次加重试最多 3 次。排队或取消保持 UNKNOWN/E0；检查通过而没有具名对齐仍为 UNKNOWN/E3。

证书入队时将规范化正文和摘要保存到数据库，原始输入文件此后可以删除；重试仍核验同一份证书、目标与预算。JSON 空白和键顺序变化保持幂等，改变证明项或切换参考/证书模式会与原请求 ID 冲突。执行前重新核对冻结合同；崩溃后必须重新完成两阶段检查，旧成功报告不会自动发布为任务结论。

任务账本位于 `artifacts/tasks/tasks.sqlite3`，工件仍位于 `artifacts/runs/`。历史报告会核对记录的工件摘要，证书篡改也会使顶层结果保持 UNKNOWN/E0；这不能当成当前环境重新认证。当前支持批准的仓库参考和两个固定目标的证书、单个 Linux 本地 worker；详见 [任务协议](docs/protocols/tasks-v1.md)、[ADR-011](docs/adr/0011-durable-certificate-tasks.md) 与 [证书任务验收](docs/evaluation/certificate-task-results.md)。最初的任务验收保留在 [参考任务记录](docs/evaluation/task-state-results.md)。

## 本地浏览器工作台

在上述 WSL 项目根目录启动：

```bash
python3 -m mra serve --port 8765
```

浏览器打开 `http://127.0.0.1:8765/` 进入研究总览；原固定目标验证页面位于 `/verify`。在形式验证页可以核对原文、确切检查表达式和假设，选择仓库参考或粘贴受限 JSON 证书，提交后查看真实阶段、尝试、用时、数学判断、证据等级和历史工件。服务自带单个后台 worker，与 CLI 使用同一任务账本；已有排队任务也会顺序处理。Ctrl+C 停止服务并等待当前有限尝试结束，页面可单独请求取消。

首期新提交只开放上述两个固定目标。没有语义确认时，真实检查通过仍为 UNKNOWN/E3；核对映射并填写姓名后，可提交新任务重新检查以取得 E4，原 E3 任务保持不变。姓名是本地对齐声明，模型调用和费用为 0。作者证书示例已明确标注来源。

页面支持任务 ID 查询、历史工件摘要核对，以及按原目标、证书和预算显式重试。工件变化会抑制历史数学结果；历史报告不是当前环境的新认证。监听地址固定为回环地址，写操作要求同源 Origin 和本次会话 token；这是本机开发工作台。协议及边界见 [HTTP 协议](docs/protocols/http-workbench-v1.md)、[ADR-012](docs/adr/0012-local-http-workbench.md) 与 [工作台验收](docs/evaluation/http-workbench-results.md)。

## 形式化候选预览与审核

新版自然数猜想入口为 `http://127.0.0.1:8765/ideas`。可保存新的自然语言命题，配置模型后生成候选；确认确切公式后最多尝试三个证明/反例计划。支持自然数加、乘、截断减法、比较及一阶量词，执行策略限定为受信的 omega/simp/decide/见证反例，未开放任意模型 Lean 源。配置模板为 `config/model.example.json`，本机配置为忽略版本控制的 `config/model.local.json`，密钥只读指定环境变量。模型未配置时保持 WAITING_FOR_MODEL，不会使用本地样例冒充生成。协议与配置步骤见 [猜想闭环](docs/protocols/ideas-v1.md)，设计见 [ADR-013](docs/adr/0013-bounded-natural-arithmetic-ideas.md)。

以下原有 CLI 仍是独立的样例预览入口：

以下命令不需要 Lean 安装，使用精确匹配的自有样例，输出会标明 `producer.kind=fixture`：

```bash
python3 -m mra formalize --input evals/formalization/clear-nat.json
python3 -m mra formalize --input evals/formalization/ambiguous.json
python3 -m mra formalize --input evals/formalization/unsupported.json
```

输出列出原文及版本、候选 Lean 陈述、定义域、量词、假设、澄清问题和内容指纹，并保存在 `artifacts/formalization/`。清楚的样例产生待审核候选；歧义输入为 `NEEDS_CLARIFICATION`；未收录输入为 `UNSUPPORTED / UNKNOWN`。

`formalization-report` 查看工件、当前审核状态和完整账本历史；`review-formalization` 记录具名接受/拒绝；`revoke-formalization` 撤回确切审核。撤销不会恢复更早接受，`formalize --revise` 会持久化父关系，并使该源所有旧版本审核变为 `SUPERSEDED`。`--event-id` 支持中断后的幂等重试，旧工件用 `register-formalization` 注册后重新审核。完整命令与响应字段见 `docs/protocols/formalizer-v1.md` 和 ADR-006。接受候选仍是 `UNKNOWN / E0`，不会启动 Lean；真实验证继续通过上述 `check` 命令。

审核账本位于 `artifacts/formalization/review-state.sqlite3`，JSON 审核文件是历史导出；备份须同时保留数据库和草稿目录。审核者姓名仍是本地声明，尚未接入身份认证。验证结果见 `docs/evaluation/review-state-results.md`；最初的 Formalizer 协议验收保留在 `docs/evaluation/formalizer-results.md`。

当前自然数猜想增量新增25项专项全部通过、实际Edge 7项操作通过；最终253项测试覆盖均已通过。初次完整回归252项通过、1项受旧后台服务抢占队列干扰失败；停止旧服务后，相同源码下该项独立真实Lean复验通过。初次失败及复验均保留，不能称初次回归全绿。真实模型按用户选择尚未配置。详见 [猜想闭环验收](docs/evaluation/idea-workflow-results.md)。

历史 HTTP 工作台阶段回归：228 项通过、0 失败、0 跳过，915.476 秒；在既有 209 项基础上增加 19 项 HTTP 测试（14 项协议/SQLite，5 项真实 Lean 集成）。实际 Edge 浏览器 10 项检查全部通过，保存 6 张截图；另验证 serve CLI 的启动、页面读取和正常退出。20 项模型协议 mock 单独标注，真实模型调用为 0。证据见 [HTTP 工作台验收](docs/evaluation/http-workbench-results.md)，旧阶段记录保存在 [证书任务验收](docs/evaluation/certificate-task-results.md)。

## 自然数算术评测

首期领域为 Nat 上的加法、乘法、后继、顺序与截断减法。18 条自有参考用例按命题族分为开发、回归、预留各 6 条，训练集为空。查看协议和原文输入不需要 Lean：

```bash
python3 -m mra dataset
python3 -m mra dataset --split development
python3 -m mra reference-benchmark
```

`reference-benchmark` 使用候选和重放两阶段共用的固定 30 秒 Lean Worker 预算，默认只检查回归组，输出报告路径及真实检查工件。每题只有一次候选尝试，报告另列实际 Lean Worker 数，验证模式纳入协议指纹。它运行作者提供的参考证明，报告 `reference_pass_rate`；真实模型调用为 0，`model_solve_rate=null`。模型生成协议另固定 30 秒、2,048 输出 token、一次尝试，并通过协议指纹绑定数据、拆分和预算。

预留组在仓库中可见，作者已经查看并验证参考解，不是独立盲测。明确需要检查全部参考时：

```bash
python3 -m mra dataset --split all --include-reserved
python3 -m mra reference-benchmark --split all --include-reserved
```

数据许可、去重/拆分边界与曝光说明见 ADR-007；验证结果见 `docs/evaluation/nat-arithmetic-results.md`。参考评测仍只执行确切仓库案例；两个黄金目标的证书入口不改变数据拆分，也不开放任意 Lean 源。

## 从零准备环境

需要 Linux/WSL、Python 3.10+、Git、tar、curl、系统 libzstd，以及可用的非特权 user/mount/network/PID namespace。下载只发生在准备阶段；Lean Worker 无网络、看不到宿主 home 和 D 盘，输入与工具链只读，并限制 CPU、地址空间、文件大小、文件描述符与线程数。

```bash
python3 ops/scripts/bootstrap_runtime.py
python3 ops/scripts/extract_lean.py
python3 ops/scripts/bootstrap_mathlib.py
python3 ops/scripts/prepare_mathlib_cache.py
python3 ops/scripts/seal_runtime.py
```

工具链固定为 Lean 4.35.0 和 Mathlib commit `56a005855ea17e0b0958f73ec801ae140b0a1679`，配置在 `lean_workspace/`。Lean 官方二进制先校验 release SHA-256；Mathlib 及其八个依赖使用确切 commit 的源归档。准备缓存时临时采用本地 Lake 路径条目，随后按原始字节恢复上游 manifest，保证缓存哈希输入一致。

上述五步准备基础 Nat 运行环境。分析、微积分、PDE 和扩展证明语言的独立 profile 还需要各自的采用记录与准备步骤，目前尚未全部整理为可从零执行的公开脚本；首次克隆不能直接复现本机全部高级历史验证。未准备相应 profile 时应保留“环境不可用/未知”，不要从历史报告恢复验证授权。

若 WSL 下载连接慢，可在官方 Cache 已生成 URL 计划后，从 Windows 项目路径运行 `python ops/scripts/fetch_cache_plan.py`，再在 WSL 执行 `python3 ops/scripts/prepare_mathlib_cache.py --unpack-only`。该回退只下载官方缓存计划中的文件。

环境封存会重新比较官方 Lean 归档、源码归档和缓存解包内容，再建立本机内容基线。`environment`、证明执行和证据门禁都会校验实际文件，内容或清单变化时拒绝复用证据。更新依赖/启动器/案例/配置后，先检查变更并恢复或重新准备依赖，再显式运行 `python3 ops/scripts/seal_runtime.py --replace`；验证过程不会自动更新基线。每次校验读取整个安装树，需要额外 I/O。覆盖范围与宿主可信边界见 `docs/adr/0004-runtime-integrity.md`。

## 当前可信边界

- 运行状态与五类数学判断分开；证明被 Lean 拒绝时为 `UNKNOWN`，超时保留 `TIMEOUT`。
- 候选阶段做 theorem/type 与本模块传递公理审计；独立进程再次重放证明数据、内核核对精确目标及全部提供的本地声明公理。`sorry`、`admit`、隐藏占位及新增公理均受门禁约束。
- 内部检查器签发回执；证据门禁拒绝伪造 PASS、修改的工件、错误命题版本、错误环境、过期对齐与策略变更。
- 环境指纹绑定实际 Lean/Mathlib/Cache 文件及关键宿主 ELF 依赖；源/缓存依赖 HTTPS 获取回执，整个宿主系统仍属于可信基础。
- 参考/证书入口的目标、方向、前提和版本须匹配仓库记录；证书仅允许七种纯表达式构造。新猜想入口由有界公式树生成目标、由四种受信策略生成全部源码；模型无法提交任意 Lean 源。新增声明在另一进程使用封存基础库重放，不递归导出基础库定义。重放使用同一 Lean 内核实现；公开多租户边界仍未开放。
- 回执密钥仅存在当前 Python 后端实例内存中，持久化报告用于审计；历史报告重新认证应执行新检查。生产签名、不可变对象存储和完整依赖撤销属于后续工作。
- 已有可选兼容 API 传输，真实模型尚未配置；协议模拟及真实 Lean 分开验收。启用配置后原文及检查反馈发往指定模型服务；当前未上传用户论文，未下载模型权重或训练数据。自有代码的对外发布许可证尚待项目所有者决定。

详见 `docs/adr/`、`docs/security/license-inventory.md`、`docs/development/issues.md` 和 `docs/evaluation/`。CI 配置已提供；本地测试结果与远端 CI 运行状态分别记录。

## 按依赖检查多个研究节点

打开 `/research`，先保存研究图，再点击“规划本轮检查”。选择最多八个 Nat/Int 数学节点，设置每项策略和总预算，逐项核对原文、完整公式和前置，填写确认者后提交。检查复用真实 Lean 与独立重放，面板显示节点状态和已预留配额。结果可以逐条核对并绑定回研究图。

依赖控制执行顺序，不自动成为 Lean 假设；前置未证明时后继跳过，主问题仍需自己的完整证明。总配额耗尽时保留已经完成的有效结果。取消会撤销本轮全部来源；中断恢复保留工件和已用预算，需要重新预览确认，不能从残留文件直接发布证明。模型仍可稍后配置，本功能的真实模型调用为零。

协议及 CLI：[`docs/protocols/research-runs-v1.md`](docs/protocols/research-runs-v1.md)；决策：[ADR-021](docs/adr/0021-controlled-research-runs.md)。该图执行范围仍为受限算术；受控 Mathlib 定义和两类知识实例化已在独立入口交付，后续优先按领域扩展证明接口与混合图执行，不能将调度能力等同于重大原创问题的求解能力。

## 将已验证引理实例化

打开 `/library`，选择可用且带有最外层全称量词的条目，点击“引用并实例化此引理”。明确新参数和代入项，填写新命题原文，保存后核对来源、全部条件、完整新公式和预算，再具名确认并提交真实 Lean 验证。前端提供实例历史、状态核对、取消和 JSON 导出；成功结果可明确存入私有知识库，并从来源审查链接绑定研究节点。

例如将整数引理 `∀n, n - 1 < n` 的 `n` 代入 `x + 2`。系统保留完整逻辑结构，为新命题独立检查及重放；原引理的历史证明仍留在来源。来源撤销/移除/损坏会使实例及其生成来源失效。没有作者证明时，其判断仍为 UNKNOWN。当前尚未导入归档证明项、扩展一般 Mathlib 定义或测量知识复用的求解收益。

协议与 CLI：[knowledge-instances-v1](docs/protocols/knowledge-instances-v1.md)；决策：[ADR-022](docs/adr/0022-verified-knowledge-instantiation.md)。模型仍可稍后配置。

## Mathlib 私有成果归档

在 Mathlib 真实E4结果中点击“存入私有知识库”，核对完整原文、命题、证明输入、版本及文件清单，具名确认本机保存权利后归档。知识页新增 Mathlib 档案区，支持关键词/完整公式/库引用检索、全局搜索、停用筛选、来源审计、导出、撤销与移除。

“带回 Mathlib 重新检查”恢复全部原始请求，清除旧确认，需要新预览、具名确认与真实双阶段检查。入库和查询本身只复核历史证据；不导入旧证明项。来源或副本损坏会永久停用；恢复文件不复活，原始实验独立保留。

演示：<http://127.0.0.1:8765/library#ml-knowledge-29fc28e7a3cf6550ec67011a0e67a33b>。71项主回归、修正视觉后的29项重复界面回归及实际Edge14项通过，9张桌面/390px截图已目视。见[ADR025](docs/adr/0025-private-mathlib-knowledge.md)、[协议](docs/protocols/mathlib-knowledge-v1.md)、[验收](docs/evaluation/mathlib-knowledge-results.md)。
# 分析证明组合（2026-10-30）

新增独立版本的局部引理、受控定理应用、显式等式改写及代数推导，入口 [分析证明组合](http://127.0.0.1:8765/proof)。真实论文公式25已使用相同完整目标通过平台 Lean 与独立回放，并关联回原文；原有 v1 证据保留。该轮示例论文有2个有效数学义务，全文仍未完成；后续进展见下节。模型继续稍后配置。

操作见 [证明组合协议](docs/protocols/proof-composition-v1.md)，决策见 [ADR 0090](docs/adr/0090-versioned-analysis-proof-composition.md)，验收以 [本轮报告](artifacts/proof-composition-20261030-01/REPORT.md) 为准。


## 逻辑证明、存在证人与等号条件（2026-10-30）

同一 [证明工作台](http://127.0.0.1:8765/proof) 新增明确版本的存在证人/消去、析取及命题分情况、双向等价、顺序已证引理与显式化简。历史v1图继续使用原验证器；Studio、论文回收和CLI共用严格版本路由，不混用版本或自动迁移证据。

真实论文 *Cauchy–Schwarz Regularizers* 的CS等号义务和完整命题1已通过平台Lean与独立回放，并关联回原文。形式化保留原文不全为零的双系数定义及零向量分支，没有新增非零假设。另有零向量反例证书和故意删去零分支的错误证明控制；它们不表示原论文被反驳。

[论文工作台](http://127.0.0.1:8765/articles#article-3b58c561897f42d6a097eb1fab8637b4) 显式复核后有4项已列义务当前有效、19项待处理。326原文块中300项尚未分类，23项义务不是穷尽清单，全文仍未认证。模型继续未配置；形式化对应为具名本地核对，并非独立专家认证。

操作见 [逻辑证明协议v2](docs/protocols/logical-proof-composition-v2.md)，设计见 [ADR 0091](docs/adr/0091-logical-proof-witnesses-and-version-routing.md)，最终验证与所有开发失败见 [本轮报告](artifacts/proof-logic-20261030-01/REPORT.md)。

## 有限维 ℓp 与框架独立工具（2026-11-01）

证明工作台新增独立 `proof-language-v4`：维数1–8的显式实向量、点积、ℓp和式及实幂。正实数p的幂律、有理实幂和Hölder等号的二维限定存在见证已有真实平台Lean检查及独立回放；初次失败和错误证明负对照保留。p>0的幂律不代表p<1时满足范数公理。限定见证已以 `counterexample_specialization` 关联回原文命题2，复核链接为CURRENT，原命题仍为UNKNOWN/E0，不授予原论文或全文REFUTED。文章revision42有12条链接、9项当前有效义务、14项待处理、300文本块未分类，全文未完成。见[协议v4](docs/protocols/finite-lp-v4.md)、[ADR0094](docs/adr/0094-finite-lp-proof-language-v4.md)、[正式见证工件](artifacts/holder-equality-20261030-01/platform-20261101-02/result.json)及[原文回收结果](artifacts/holder-equality-20261030-01/article-binding-20261101/result.json)；该轮原provider直接复核和离线聚合不代表HTTP性能问题已修，历史版本继续由原验证器读取。

新增11个框架独立JSON业务工具及单次CLI，可供后续编排器调用检索、预览、审核后转交、提交、状态、证据和取消。原业务store保留完整语义确认、幂等、预算与证据权威，工具成功不等于数学证明。门面默认不启用业务写操作，不执行worker；没有新增适配器数据库。真实模型仍未配置，openJiuwen尚未接入或安装。参数与边界见[core-tools-v1](docs/protocols/core-tools-v1.md)和[ADR0095](docs/adr/0095-framework-independent-core-tools.md)。
