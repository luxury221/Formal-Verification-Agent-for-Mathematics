# 完整目标驱动的独立引理匹配验收（MRA077）

日期：2026-10-29。目标是在MRA076统一工作台中，免去预先知道来源ID并手写全部terms/proofs的步骤。结果限定为已证库上的有界应用发现，不是从零发现数学定理。

入口：[引理工作台](http://127.0.0.1:8765/typed-research/lemmas#applications)；CLI：structure-export-match --input FILE。设计见[ADR0069](../adr/0069-target-first-export-lemma-matching.md)，请求与限制见[协议](../protocols/export-lemma-matching-v1.md)。权威证据目录为 artifacts/export-lemma-matching-20261029-01，最终以该目录delivery.json及Windows独立复核为准。

## 实际实现

用户提供explicit-structures-v1完整目标（无proof），包括精确载体、运算、定义、变量、全部原条件和目标。最多扫描最近200条独立导出目录，按运算符重合排序后最多对8个来源执行原load_source完整当前核验。目录E4标签只是提示；暂忙抛出，不将部分结果当完整复核。固定相同载体/运算/定义，含original描述，不跨环境改名或扩展运算。

沿用原LemmaSearch与composition编译器，合成类型实参、全部原条件证明、合取、等式反向/传递及绑定体外合同。每个方案最多使用4个来源，未用来源不导入；保留完整原目标，不增加假设。搜索、预览无Lean/模型调用，建议均UNKNOWN/E0。选择方案后重新预览并逐项比较当前来源archive_fingerprint，再由用户具名确认原执行器的真实证明数据导入与独立重放。

网页新增可编辑完整目标、已知来源双向示例、匹配方案及全部调用/条件详情。编辑清候选和确认，epoch丢弃迟到响应；包括match在内的全部POST不自动重发。示例从实际当前可用的导出等式派生并披露来源，没有重新制造或检查来源来填库。原完整程序与手工导出/应用入口保留。旧结构图仍使用v3；新流程不自动关联或晋升旧图。

## 冻结比较与实际结果

执行前将全部六个目标、两组候选与来源固定在 real-560dd371b5f4479e999162f5dbfaf4b4/declared-plan.json。来源为现有两份独立导出：mathlib-b7e0cda505ee413c9b577cb7c6c00e45、mathlib-ba3c9fa40e644b109dfef292e721ce38；本轮只复核既有完整证据，没有重跑来源准备。

| 固定目标 | 旧obligation-v2无导入 | 新独立导出匹配 |
| --- | --- | --- |
| two-lemmas：k/y处两个结论的合取 | UNKNOWN | PROVED/E4 |
| shifted-reversed：k+1、f(y)实参与反向 | UNKNOWN | PROVED/E4 |
| two-layer-chain：双层迭代改写与交换结论 | UNKNOWN | PROVED/E4 |
| missing-condition：删除交换条件 | UNKNOWN | 无方案、UNKNOWN |
| wrong-condition：同名条件仅为y=y | UNKNOWN | 无方案、UNKNOWN |
| different-environment：增加运算 | UNKNOWN | 无方案、UNKNOWN |

完整分母均为6，结果0/6与3/6。新组三个无方案目标没有提交Lean，仍保留分母。实际比较15次完整检查：旧组12次、新组3次。每目标每组最多两次，每次完整检查共享20 worker秒；完整性审计与准备另计。旧搜索800展开，新搜索4000展开，不能称同搜索预算。已有来源证明及审查成本也不能记作零成本；所有目标均作者可见、彼此相关并由已知来源构造，不是盲测或统计推断。

旧组12次检查中5次REJECTED/UNKNOWN，7次EXECUTION_ERROR/UNKNOWN。后者失败grind诊断写满1048576 bytes，stderr为file too large (error code: 27)；沙箱原RLIMIT_FSIZE保持1MiB（ops/scripts/sandbox_worker.py）。未改限额、题目、候选或重试数学来改善本次结果。此类错误是受限运行失败，既不是数学反证，也不能支持无条件的算法优势结论。完整日志和失败都封存。

浏览器另提交一次完整双引理合取目标，当前记录 mathlib-3f726033695e4468a4a963e82e29462f，真实导入两份来源并在不同PID独立重放至E4。合计16完整检查、20实际worker阶段：4当前PROVED/E4、5REJECTED/UNKNOWN、7EXECUTION_ERROR/UNKNOWN。不是16个新定理。

## 验证与审查

最终相关回归496项：418通过、78项旧真实opt-in跳过，100.027秒。单独真实比较1项通过，224.020秒；最终quality十条命令通过，含Python静态/格式、源码约束、JS语法与旧传输/数学记号契约。新协议契约10项覆盖严格字段/预算、多个来源、复合实参、条件/环境拒绝、目录/审计上限、暂忙和无执行HTTP。测试前后产品/测试字节一致；回归、真实专项、主浏览器顺序执行。

只读CLI九次通过，包含新匹配、应用预览、缺条件、交叉schema拒绝和旧报告读取；新Lean调用0。主Edge浏览器10检查/7截图覆盖完整交互、迟到响应丢弃、重新具名确认、实际下载、缺条件不出提交动作及390px无横向溢出。来源指纹变化控制是浏览器模拟preview响应，不能描述为实际撤销来源；真正的来源撤销门禁沿用已验收执行器。另做不执行数学的卡片视觉补验2检查/2截图，仅一次match POST。全部9张图直接目视，零页面JS/console/HTTP错误及外网请求。

只读review重编完整目标，核所有16条实际工件hash、完整类型/公理、不同PID、真实imports、当前原来源与旧E4；86个旧核心/数据文件不变。旧完整图/组合报告及MRA071两组5/8保持。辅助review首次因error:null字符串检查出错，修复后又发现浏览器summary.target已指向末尾缺条件控制，改为以实际唯一/runs请求及下载记录核对成功目标。两个辅助脚本错误及修前代码保留；没有产品/测试变化或新Lean执行。

保全以520来源文件、429旧数据库、784旧状态文件为基线：允许4个旧产品文件及5份状态文档改变；全部旧数据库旧行及旧状态保持，canonical应用队列只按浏览器实际request_id追加一条。两份新私有比较库、当前新状态与原证据链备份另存，未执行实际restore。无新依赖、外部模型调用、提交或发布。最终服务须重新核cmd/cwd、4GiB内存/512MiB swap、79资产实际字节与Windows HTTP200；PID以封存runtime记录为准，不沿用历史值。

## 下一步边界

先独立处理失败诊断输出限额及其准确分类，再在新版本和预声明目标上比较。随后扩展来源/曝光明确的数学目标、跨环境显式映射和旧图关联，并分别衡量检索、搜索及已有证明成本。当前没有任意自然语言到数学的通用解析，没有模型驱动的自主研究，也没有四大期刊级原创研究证据。
