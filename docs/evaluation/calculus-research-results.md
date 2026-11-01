# 微积分研究工作台阶段验收

MRA084，2026-10-29。257项相关回归208通过/49跳过；12质量命令通过；单独真实图/HTTP专项通过（857.806秒），14条实际CLI通过；实际Edge桌面/390px浏览器14项通过。

完整目标现在可以在同一研究项目和分支内组织为独立微积分图，通过/analysis、HTTP和CLI逐节点具名确认，由原单worker真实检查。每个节点保留原变量、全部条件、测度、端点和原目标；前置仅调度并绑定确切当前证据，不导入证明项，原问题需要自己的完整E4。新协议不会静默更改旧题组成绩。

## 实际证据

- 实际图/HTTP专项：artifacts/calculus-research-20261029-01/validation-real-8cab58cbb2654ce8a65eef192964f5c1，真实单项用时857.806秒、exit0、源码不变；运行在6GiB/512MiB swap范围。
- 五图七个已记录终态检查：两条两节点正链各有独立原问题E4，错误方向图的前置真而原问题REJECTED/UNKNOWN，另一次真实重放成功后取消仍CANCELLED/UNKNOWN。
- 再以真实子进程成功重放后os._exit(71)，保留RUNNING/UNKNOWN，显式恢复为INTERRUPTED/UNKNOWN，没有自动重试。成功重放marker有进程/耗时，监督阶段候选完整计时未写回，不推算成完整总worker成本。
- 七终态检查有13份候选/重放结果，累计53.975877518 worker秒；监督成功重放另计见review.json，不把857秒专项墙钟当worker用时。
- 证明NDJSON实际损坏、恢复原字节及旧父记录后仍失效；另一图通过HTTP撤销前置并停用后继。5历史节点E4，控制后1当前节点E4、0当前原问题E4；这是主动安全控制后的状态。
- 浏览器：artifacts/calculus-research-20261029-01/browser-1793268919769，实际Edge桌面1440×1080与390×844，14项通过，8截图；JS异常0、页面外网请求0。两次真实数学检查及一次排队取消；证据长时间暂忙导致有界读取耗尽，实际通过一次明确手动刷新取得已完成E4（manual-read-recovery.json），未重跑数学。下载、具名确认、编辑/语境切换丢弃旧响应、503隐藏旧证据、成功写入丢响应不重发及撤销传播均覆盖。
- CLI：artifacts/calculus-research-20261029-01/cli-03e0ec193af745059493d3440f65ab07，14条实际命令通过，涵盖完整预览/保存/幂等/节点预览/具名排队/取消/撤销/报告/列表/空闲work/恢复；额外数学检查0。
- 相关协议回归：artifacts/calculus-research-20261029-01/validation-contracts-987c10f6a0d94a98b317dd2fb30421a0，257项中208通过、49跳过；真实Lean关闭，真实集成由上面的独立专项提供，不能把跳过项算通过。
- 质量：artifacts/calculus-research-20261029-01/quality-e10ffe4608c54792990cf3c26043d516，12命令通过。review.json复核真实源码、完整目标、条件、工件哈希、实际审计、NDJSON、不同PID及共享预算。manual-code-review.json是Codex自审，不是独立人类审查。

浏览器实际生成的两份证明另经browser-evidence-review.json逐项复核：4个候选/重放阶段、12.577538081 worker秒，完整计划、工件哈希、审计、证明导出与不同PID均匹配；排队取消没有数学执行。

## 开发失败与限制

浏览器首轮11项后未取得全局锁辅助进程就绪回执而exit1，原browser-1793268099282/summary.json保持all_passed=false。随后使用有明确stderr/退出回执及有界锁竞争重试的文件辅助进程，在既有保存任务上仅续验排队取消与窄屏，累计14项通过，数学重跑0；8张截图包含保留的首轮失败截图。没有把首轮改写成全通过。

浏览器工件复审收集器首次漏写runs子目录，原FileNotFoundError及原脚本保留browser-review-attempt-1.*；核对实际目录后仅修收集器，逐项复核通过，未改产品或重跑数学。

首次14项契约开发测试中一项测试hook错误，将CalculusStore类型整体mock导致object.__new__失败；改为观察构造函数后通过。预真实自审修正了HTTPConnection名称遮蔽及CLI node-preview分派；其后的格式检查有一次拒绝，保留quality-4738cc68ba8040b1b666abb1a2473cbe，Ruff仅格式化新CLI后通过。所有真实数学执行均在这些修正之后；没有为变绿重写已产生E4的来源。

网页仍有交互限制：长时间证据暂忙后需要手动刷新，响应速度并未在本轮优化。

MRA083原quantified-derivative超时、composed-derivative中断，以及旧同字节改写测试的波动记录保持；本轮不扩大预算重跑或删除分母。HTTP/网页仅新图集成，不新增微积分证明规则；示例已知、作者可见，不能算盲测、模型求解率或原创研究能力。模型未配置，调用0。图前置尚无分析引理证明项导入；PDE、一般弱形式和边界空间未完成。

## 保全与后续

旧来源备份585项、旧数据库444个、旧状态940项在baseline.json；最终逐项结果以preservation-final.json为准。canonical研究父项目仅允许本轮新项目行追加，旧行逐项保持；没有执行整项目restore、升级或回滚。新核心provider 7f2dbd11d8e24ff1ba21b475cafe5d54831c285221335df8da427a3250a243a9，原微积分provider e4eb5afdadf246ddeea80882ef3ccd73498f7425fce65da2e384b90751a75d7a及环境85ea4b09d3c1112afea78ee3c8d35a2df4446ad5307da054db1df7efc5a123ed保持。

服务和全部静态资源以runtime-*/summary.json、Windows检查及delivery.json为准；不是开机自启。持久记忆仅D盘memory。下一步按[pde-energy-preflight](../product/pde-energy-preflight.md)准备独立有限分部积分环境，保留正则性、Lebesgue测度与边界项，并逐步建立能量链。现有指定分部积分和Sobolev缓存缺失，源码预查E0不算证明。
