# gcd 与互素实验 v1

协议 `mathlib-nat-gcd-v1`，决策见[ADR028](../adr/0028-controlled-gcd-and-coprimality.md)。人工结构化输入，复用原真实Lean检查及独立重放；模型调用0。

## 输入与类型

请求严格包含schema、title、original、variables、assumptions、goal、proof、timeout_seconds。最多6个Nat变量、6条命名假设、240个节点、深度14、自然数字面量0至1000000。表达式支持var/nat/add/mul/sub/gcd，公式支持dvd/eq/le/coprime/and/or/iff。gcd接收两个Nat并返回Nat；coprime接收两个Nat并返回Prop，不能互换。Nat减法在零处截断；没有整数、任意新定义或Lean源码入口。

`Nat.gcd a b`使用已固定Lean库定义；gcd(a,0)=a，gcd(0,0)=0。`Nat.Coprime a b`定义为gcd(a,b)=1，因此零只与一互素。目录列出22条允许引用的完整类型、显式Nat参数/证明参数数量、版本及来源，包含原9条整除引用和13条gcd/互素引用。

证明仅支持hyp、带全部terms/proofs的允许ref，以及pair/mp/mpr。每个本来隐式的Nat实参和证明前提都必须显式填写。编译器生成宿主名称，拒绝任意源、未知字段/构造器、未声明变量、错误数量、过深或超限输入。完整目标包含所有全称变量和前提，不因失败改写问题。

## 计划与执行

先预览完整原文、目标、证明项、变量映射、引用和共享预算；修改任何输入会清除旧确认。具名确认绑定确切计划指纹；姓名为本地对齐声明，不是身份认证。候选与独立重放各最多一个进程，共享1至30秒worker，环境完整性扫描另计。独立重放只导入受信基线与新增声明的证明数据，不导入候选模块。

新账本 `artifacts/number-theory/lab.sqlite3` 和本入口的runs目录独立保存记录，复用冻结MathlibLabStore执行生命周期及全局worker锁。ID沿用mathlib-加32位小写十六进制；必须连同账本/页面/协议识别，不能将本入口ID当作原Mathlib实验来源。最多200轮；计划同时绑定新编译器/适配器及原执行器/策略/封存摘要。

取消、中断、失败、超时、来源变化与撤销均保持UNKNOWN/E0；只有真实两阶段且精确目标/公理/语义对齐门禁全部通过才有PROVED/E4。本协议不提供反驳通道；证明拒绝不等于数学命题为假。中断不自动重试，来源或工件失效持久停用，恢复文件不复活。历史读取fresh_attestation=false，不重新执行Lean。

运行源码、公理策略、Lean/Mathlib版本和原封存保持不变。旧整除实验、归档/实例化与功能评测仍使用原协议；旧轮次的gcd阻塞不因新增语言改成已证明。新结果暂在独立入口查看/导出/带回新检查，尚未接入旧域研究节点、私有归档和模型形式化。

## HTTP 与 CLI

| 方法 / 路由 | 行为 |
| --- | --- |
| GET `/v1/number-theory/catalog` | 完整定义、引用与6份作者可编辑示例 |
| POST `/v1/number-theory/preview` | 完整计划，无Lean调用 |
| GET / POST `/v1/number-theory/runs` | 历史列表 / 具名提交 |
| GET `/v1/number-theory/runs/{id}` | 工件复核后的完整记录 |
| POST `/v1/number-theory/runs/{id}/cancel` | 空对象取消 |
| POST `/v1/number-theory/runs/{id}/revoke` | reviewer/reason/confirmed撤销 |

沿用loopback、Host/Origin、CSRF、CSP、no-store和192KiB请求体边界。浏览器访问 `/number-theory`；原 `/mathlib` 顶部可进入，暖白墨绿样式和原导航保留。

CLI提供number-theory-catalog/preview/submit/list/report/work/cancel/revoke/recover；preview/submit/revoke使用--input，report/cancel/revoke接收run_id，work可用--run-id。提交正文包含request_id、request、plan_fingerprint、reviewer、confirmed，request_id复用时必须对应相同计划和确认者。先准备再执行，例如：

```bash
python3 -m mra number-theory-catalog
python3 -m mra number-theory-preview --input request.json
python3 -m mra number-theory-submit --input confirmed-submission.json
python3 -m mra number-theory-work --run-id <mathlib-…>
python3 -m mra number-theory-report <mathlib-…>
```

六份示例为项目作者编写且已查看的功能输入，不是独立持出集、模型输出或原创数学成果；不用于训练。未开放的定义仍拒绝；一般数论自动求解和四大期刊级研究能力没有由这些样例得到验证。
