# pde-energy-v1

2026-10-29。固定作者已知的一维积分恒等式检查和证明数据复用。验收见 [结果](../evaluation/pde-energy-results.md)，决策见 [ADR0077](../adr/0077-poisson-energy-proof-data.md)。

## 操作

在项目根执行，目录均位于项目内：

- PYTHONPATH=. python3 ops/scripts/pde_energy.py catalog
- PYTHONPATH=. python3 ops/scripts/pde_energy.py seal
- PYTHONPATH=. python3 ops/scripts/pde_energy.py run --probe integration-by-parts --reviewer NAME
- PYTHONPATH=. python3 ops/scripts/pde_energy.py run --probe zero-boundary-green --source-run-id pde-32hex --reviewer NAME
- PYTHONPATH=. python3 ops/scripts/pde_energy.py report --run-id pde-32hex
- PYTHONPATH=. python3 ops/scripts/pde_energy.py recover --run-id pde-32hex

seal 拒绝覆盖已有清单或锁；不是升级或迁移工具。run 只接受不变 catalog ID，不能提交任意 Lean、额外假设、测度或证明源。后继所需源类型由 catalog 固定，不能用同名、较弱条件或另一个定理代替。

## 数学目标

五个已知正目标按顺序复用，每个后继仍完整验证自己的目标。定义使用显式函数 u/du/ddu、f、测试函数 φ/dφ，端点 a/b，HasDerivAt 关系、volume 下 IntervalIntegrable、闭区间方程以及零 Dirichlet 条件。能量目标是 ∫du·du = ∫f·u；非负性另需 a≤b。

五个数学负对照：缺零边界、Poisson 符号错误、任意测度冒用 volume 规则、缺导数条件、缺端点顺序。证明拒绝只说明该候选未完成检查，不发布 REFUTED。sorry 与未授权公理是另外两项政策控制。

## 执行与证据

共享 TaskStore worker 锁；命名空间、无网络、只读依赖和有限资源沿用受信启动器。每次候选与独立重放共享 40 秒，扫描和源复核另计。一个阶段成功不等于完整检查成功。程序回调取消在 worker 中和最终发布前核验；命令行中断保持 UNKNOWN。

新运行 ID 为 pde-32hex，目录 artifacts/pde-energy-v1/runs/。request 绑定原文、完整目标、reviewer、政策、环境及确切父记录。候选目录保留 source-0.ndjson；候选内完整类型桥接和直接常量使用分别有 MRA_IMPORT、MRA_SOURCE_USE 审计；结果证明重新导出，再以不同 PID 重放，不导入候选模块。

report 重新检查来源闭包、工件、原目标、审计、不同进程和有限预算。损坏持久停用。RUNNING 读取只返回 UNKNOWN；recover 获得全局锁后仅置 INTERRUPTED/UNKNOWN，不能依据旧文件恢复认证，不自动重试。

## 范围

当前为运维 CLI，没有加入 /analysis 的任意结构化输入或研究图。既有图的前置仍只调度。无模型、盲测、原创或一般 PDE 能力声明。完全相同的 Lean 内核实现、基础库、本地 Python/文件用户仍受信；本地姓名不等于身份认证。
