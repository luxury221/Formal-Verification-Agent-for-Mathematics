# 结构候选程序与真实反馈 v1

schema：structure-strategy-proposals-v1。CLI：structure-strategy-plan --input <JSON>；structure-strategy-node <graph_id> --input <JSON>。前者接收schema、完整target（原explicit-structures-v1字段减proof）、budget；后者接收node_id、budget，从当前保存图获取目标和真实反馈。

HTTP：POST /v1/structure-research/<graph_id>/strategy-proposals，body仅node_id及budget。严格同源／CSRF、无query、严格JSON、192KiB输入边界；生成不排队、不唤醒worker、不运行Lean或模型。反馈来自原图当前重新审计的检查记录，客户端不能提供成功／失败标签。

## 候选与预算

budget必填整数：max_rewrite_steps为1–6、max_expansions为1–2000、max_candidates为1–8。网页固定4／800／8。合成检查点0.75秒，规范化累计32768节点访问，替换前中间数据估计4096节点；函数调用准备／工件审计和后续Lean预算另计，无端到端硬实时保证。

只分析原等式定律、透明闭合定义和目标AST，不读取输入参考proof、题名对应策略或示例证明表。有限广度搜索尝试正反向第一个数据位置匹配；不在词法绑定内部改写。定义展开与Nat计算是启发式近似，fold／量词绑定按词法作用域区分并避免参数捕获。

存在Nat递推时按Nat变量声明逆序提出单层归纳，不合成generalize。基与步分别尝试有界等式改写，步允许显式@ih；没有找到路径的分支可提出固定grind。另有固定rfl与grind尝试。来源字段区分fixed、symbolic-rewrite、symbolic-induction和heuristic-induction；这些字段不代表证明成功。超限保留已经生成的候选，不保证所有有用程序都出现。

每个candidate携带完整request、原编译器产生的proof_source、candidate_id和UNKNOWN/E0；完整形式化目标必须逐字一致。计划绑定完整target、source_manifest、预算、候选与指纹，stats中的非确定耗时不参与指纹。lean_calls/model_calls均为0；requires_preview_and_named_confirmation=true。

## 真实反馈及草稿

保存图模式返回graph_id／node_id／revision／parent、完整目标及全部已有尝试的反馈：实际状态、当前数学判断／证据级别、失效标记、程序摘要、候选返回码、重放状态、worker秒和最多1600字符错误（截断须标记）。撤销或损坏后反馈降为UNKNOWN/E0；失败不标REFUTED。有效REJECTED的相同proof排序靠后但不删除。语义目标严格比较，其他节点或不同条件不能借用反馈。

网页应用只修改目标节点草稿的proof，并清除旧预览与确认。原保存图与原错误记录不变；新草稿必须完整预览、另存新图，再走原具名节点检查与独立重放。没有自动执行、失败修复循环或预算挪用。迟到响应、编辑、节点／模式切换和revision变化清候选；读取失败不显示旧候选为当前结果。

本地姓名是声明，原检查仍同一固定Lean内核的两个独立进程。候选不能证明条件一致性、结构存在性、新颖性或原创研究能力。本轮题目全部作者可见，没有模型解题率、训练、自我修改或一般性能结论。
