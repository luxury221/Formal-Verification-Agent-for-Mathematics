# 显式整数域验收

日期：2026-10-26。设计：[ADR-019](../adr/0019-typed-integer-verification.md)。研究方向：[长期路线](../product/research-capability-roadmap.md)。

## 交付与范围

新增显式Int公式/检查/审查协议、负常量和取负、有类型负反例、受信模板及独立数据侧文件。CLI/API/表单/双通道审查和私有知识归档接入整数。Nat原协议继续拒绝负数并保持截断减法；公式AST相同也不能跨域沿用审核或证明。知识语法指纹包含域。

沿用omega/simp/decide/显式反例、原30秒检查与独立重放预算及公理政策，无自由Lean、策略或未验证引理输入。一般非线性整数问题可能未知；猜想模型生成仍限定Nat，真实模型待配置。此处通过算术回归不代表已具备数学四大期刊级原创求解能力。

## 实测

- 全量Python **319项通过，0失败/跳过，1616.296秒**，启用真实Lean；含新增整数协议6/真实集成6，以及原有证书、回放、证据、猜想、审查、知识、任务、HTTP、评测等。协议模拟与真实Lean按各测试标注，真实模型调用0。
- 前端公式11项通过，55个Python Ruff/格式、6个JS语法、source gate通过。全量运行时源码前后不变；结束后仅修papers.js新增数学域SELECT的change处理，避免误当策略选择访问空容器。最终Node11项/JS语法及实际域切换浏览器检查覆盖该变更，后端与测试未改。
- 实际Edge **10项通过、7张截图**，桌面1440与手机390已目视，JS异常和console/CSP/资源错误0。六个Nat/Int作者示例、取负/负数AST无损、域切换清确认、真实整数完整/错误原证明/负数反例、私有归档、旧源停用及九页面手机宽度均覆盖。
- 浏览器新增7项真实Lean义务/14个候选与独立重放进程。完整证明PROVED/PROVED，真命题错误证明PROVED/GAP_FOUND且不引用失败步骤，错误的“所有整数非负”由-1核验为REFUTED/GAP_FOUND。每项E4与文件摘要独立核对；样例是项目作者编写。
- 显式重封存快照 `9e82b5e620df9ceb5e0c9199f0e4ec95b0e92828c94288821f297819ee9d8760`。Lean4.35.0、Mathlib及33152项安装树不变，官方归档17925/源码10267/154缓存包2310文件重新核对，无新依赖/下载/训练。
- 旧5条有效知识因源协议实现SHA变化停用，旧记录/原证明档案及一致数据库备份保留；不会把旧确认变成新认证。需用户重新准备、具名确认、真实运行及入库。三条新的浏览器Int条目可用并保持私有。测试另会生成明确标注的内部记录，不将条目数量称为不同定理数量。
- 最终18静态路由200且逐字节匹配最终源码，保留CSP/no-store；四账本integrity ok且无RUNNING/QUEUED，模型未配置，worker无错误。服务恢复8765，无开机自启；代码未提交/推送/发布，远端CI未运行。

## 证据

工件前缀 `artifacts/integer-*`：stage-backup、seal/log、validation/tests.log、execution-evidence、quality/final-quality、notation-validation/log、browser-qa/截图、browser-execution-evidence、legacy-status、service-health/start、final-runtime及delivery-check。最终manifest单列回归后JS变更，保留原始回归摘要。

实际示例：<http://127.0.0.1:8765/papers#paper-fdbb9753598d4a90b9ea32607abe095c>；知识条目：<http://127.0.0.1:8765/library#knowledge-105c3864015bddc83a1fe2c1e4d2f7c4>。
