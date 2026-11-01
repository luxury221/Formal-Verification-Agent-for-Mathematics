# Structure recursion program v1

输入字段严格为 schema、target、program、timeout_seconds。schema=structure-recursion-program-v1；target为完整explicit-structures-v1目标且不含proof，保留其timeout_seconds；外层timeout_seconds必须同值，表示整个程序的候选与独立重放共享1–30 worker秒。program包含lemmas数组及proof。lemmas最多两个，每项只有name、goal、proof。

局部引理的载体／运算／定义／全部原条件均继承主目标；变量从原目标中按自由使用推导。名称必须为合法标识符且不重复；不存在新增假设字段。原条件自由依赖目标变量时，当前拒绝带helper的程序。helper的完整目标与证明源码出现在预览的local_lemmas，顶层formal_statement仍严格等于完整原目标。

proof支持rfl、grind、命名rewrite及单层nat_induction。rewrite规则仅限原条件、当前后继分支的@ih、四条固定Nat规则，或已证明的@lemma.NAME。每分支最多24条显式改写，无任意Lean／自定义tactic／新import。固定算术化简和局部recursor等式真实处于内核检查范围。

CLI：structure-recursion-propose --input proposal.json 只生成建议；proposal输入为schema=structure-recursion-proposals-v1、完整target和明确budget（max_candidates/max_expansions/max_rewrite_steps）。候选request可直接交给structure-recursion-preview --input program.json。预览、具名submit、work --run-id ID、report ID、cancel ID、revoke ID --input、recover及list沿用原账本控制；工作台后台目前不扫描新模式，须CLI明确work。catalog会打开该模式的独立账本。

submit输入保持request_id、request、plan_fingerprint、reviewer、confirmed五字段。确认前不执行，已取消／中断ID不会自动续跑；已绑定ID不更换请求或确认。全部候选与预览UNKNOWN/E0，只有完整候选与独立数据重放均成功且完整类型、公理、当前源身份和具名对齐记录通过才E4。任何helper失败使整个程序不晋升；即使主目标自反也不能忽略错误helper。

自动模板、共享合成预算和词法边界见ADR0065。实际Lean预算包含helper与主证明，共享一次候选及一次重放，不把helper计为免费已证来源。最终类型不增加helper条件；保存的helper描述是证明义务，不是用户的新假设。不存在自动跨图／永久引理入库。

测试协议夹具明确NONEXECUTABLE，不作为数学证据。真实对照须MRA_REAL_LEAN=1，保留每槽失败／UNKNOWN与成功后跳过；删条件未通过不代表反证。已知障碍和作者可见新题不外推独立研究或四大期刊能力。
