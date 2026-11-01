# Mathlib 受控引用实验验收

日期：2026-10-27。MRA032 / ADR023。状态：已完成本地首期验收。活动代码位于WSL项目；记忆仅保存在D盘。

## 已交付范围

`/mathlib` 支持自然数整除定义与九条具名库引用，其中三条来自Mathlib的等价定理。完整变量、前提、结论和证明项采用有界结构化数据，显式填写隐式Nat参数及证明实参。宿主生成确切Lean陈述，真实候选编译与独立数据重放检查完整目标及公理闭包。已导入库常量由封存基线解析，不递归重建整个Mathlib。

四份自有功能样例覆盖整除链与加法组合、双向整除等价式、截断减法边界，以及遗漏前提的失败对照。前端包含目录/类型/出处、原文及完整公式预览、名称映射、预算、具名确认、实时状态、取消、撤销、重新核对、历史与JSON导出。API和CLI使用相同计划与账本。实验结果独立保存，不隐式转换为旧算术图节点或知识条目。

可用演示：<http://127.0.0.1:8765/mathlib#mathlib-b6965e62d69c4ae89df918d2b6db00d7>。完整命题是：对任意自然数a、b、c，若a整除b且b整除c，则a整除b+c。确切宿主命名、证明项、具名对齐、两个真实进程及工件摘要见 `artifacts/mathlib-lab-final-execution.json`。最终状态COMPLETED / PROVED / E4；这是人工结构化功能证明，不是模型生成或新数学发现。

## 检查结果

| 范围 | 结果与证据 |
| --- | --- |
| 主回归 | 84项全部通过，0失败/错误/跳过，411.745秒；新增24项=15项结构/账本/HTTP/CLI+9项真实Lean测试方法，一些方法覆盖多份证明；其余为受影响旧HTTP、界面、CLI、实例化和重放回归。见 `mathlib-lab-validation.json` / `mathlib-lab-tests.log` |
| 真实数学与信任边界 | 九条引用均以显式实参通过真实Lean及重放；组合证明、Mathlib等价式、零边界、合取/等价应用、遗漏前提拒绝、真实1秒超时、错误目标的内核桥接拒绝、重放期间取消/撤销、工件损坏永久停用、实际退出73后的中断恢复均覆盖 |
| 最终界面回归 | 浏览器发现同页hash切换不更新详情后，仅修改mathlib.js；后续21项界面/HTTP回归全部通过，0失败/错误/跳过，2.873秒。此组与主回归重叠，不能相加声称105项不同测试。见 `mathlib-lab-final-ui-validation.json` |
| 实际浏览器 | 现有Edge实际12项流程通过：定义/目录、完整前提与清确认、真实组合证明、导出、减法等价式、失败保持未知、未知定义阻塞、具名撤销、重新确认与新检查、取消、390px重载、11页面响应布局；6张最终截图已目视。预期的未知定义HTTP400单列，其他JS/console/CSP错误0。见 `mathlib-lab-browser-qa.json` / `mathlib-lab-visual-qa.json` |
| 质量 | Ruff64个Python文件、10个JS语法、11项公式转换、85项Python/JSON源门禁全部通过；见 `mathlib-lab-quality.json` |
| 历史保留 | 15个受保护源码/政策/封存文件逐字节不变。本轮备份中12条ACTIVE知识记录（含开发测试条目）全部保留，7项ACTIVE研究绑定保留；此前三节点run和知识实例run仍为COMPLETED，各节点PROVED/E4可用。见 `mathlib-lab-preservation.json` |
| 交付服务 | 8765正常运行，全部28条页面/资源与源码逐字节一致；8个业务账本加原形式化语义审核库，共9个SQLite库integrity_check均ok且无QUEUED/RUNNING。实际PID/cgroup、模型配置与worker状态见 `mathlib-lab-final-runtime.json`。模型仍未配置，真实调用0；无QA8780服务或开机自启 |

主回归前后源码稳定。主回归后唯一产品源码修改为前端hash导航监听，后续21项及完整实际浏览器重验覆盖它；后端、测试、模板和原封存均未改变。最终交付清单再次核对当前源码与最终回归摘要。

## 保留的早期记录

1. 初始目录探测误用了不存在的`Nat.dvd`常量名，真实Lean返回非零；改为明确的`Dvd Nat`实例定义，来源与语义已核对。原探测在 `mathlib-lab-stage/probe.*`。
2. 首轮四例检查中，两个正例通过、减法例TIMEOUT、失败对照按预期REJECTED；没有把这轮当成全通过。默认导出器递归展开库证明依赖，后改为独立的新增声明导出片段；预算、公理审计和旧封存不变。原运行在 `mathlib-lab-smoke.json`；隔离诊断在 `mathlib-lab-subtraction-diagnostic/`，修正验证在 `mathlib-lab-export-verification.json`。
3. 第一次浏览器在7项检查后发现hash导航详情未同步，原失败及截图保留于 `mathlib-lab-browser-before-hash-navigation.json` / 非final截图。修复后完整12项通过，最终截图使用`mathlib-lab-final-`前缀。错误导航期间撤销的是失败实验记录，原已证明记录没有被改写。

所有早期记录保留原状态；失效结果不会自动晋升。测试与浏览器运行产生的具名核对者均标为开发/验收声明，不代表真实用户身份认证。

## 限制与后续

这是受控自然数整除与人工证明项实验；未接入任意Mathlib模块、有理数、集合、函数、归纳搜索、自动文献或原创性评估。旧研究图/知识库绑定、全局记录搜索和一般定义需要版本化跨域适配。最多200次实验，候选与重放共享1–30秒worker预算；完整性扫描另计时，因此墙钟耗时会更长。没有通用求解率、持出收益或数学四大期刊级研究能力声明。

复用既有Lean4.35.0、固定Mathlib及LeanExport的Apache-2.0版本、Python标准库与现有Edge/Node；无新增依赖、下载、权重、真实模型调用、训练、提交、推送或发布。协议见 [mathlib-lab-v1](../protocols/mathlib-lab-v1.md)，决策见 [ADR023](../adr/0023-controlled-mathlib-terms.md)，最终证据索引见 `artifacts/mathlib-lab-delivery-check.json`。
