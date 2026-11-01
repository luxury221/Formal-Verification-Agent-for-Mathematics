# PDE 能量链的下一阶段预查

2026-10-29，E0 源码预查；没有执行新的 Lean 检查。Mathlib commit 仍为 56a005855ea17e0b0958f73ec801ae140b0a1679。来源摘要与指定 olean 存在性见 artifacts/calculus-research-20261029-01/pde-energy-source-preflight.json。

优先从有完整边界项的一维分部积分开始，再处理零边界能量恒等式和线性问题的条件估计。一维边值模型不能代表一般多维 PDE 能力；抽象内积不等式也不能自动代表 PDE 弱形式已经建立。

IntervalIntegral/IntegrationByParts.lean:141 的 integral_mul_deriv_eq_deriv_mul 要求 u、v 在无向闭区间上的 HasDerivAt，以及 u′、v′ 对 volume 的 IntervalIntegrable。它保留端点项 u(b)v(b)−u(a)v(a)，积分有方向。新语言不能把该定理直接用于任意 μ；省略边界项必须明确核验端点条件。能量非负估计还要明确 a≤b。

对光滑一维 Poisson 模型 −u″=f，先完整验证导数关系、区间可积性及零 Dirichlet 边界，才能连接 ∫(u′)²=∫fu。存在性、唯一性、一般弱导数和高维推广分别建立义务，不能从该恒等式推断全部已完成。

Analysis/Distribution/Sobolev.lean 当前描述基于 Fourier/Bessel potential 的全空间 Sobolev 分布；不能仅凭文件名就视为有界区域 H₀¹ 和边界迹条件已经具备。先建立具体类型和经检查的桥接，再考虑一般弱形式。

实施前准备独立环境身份和有限 imports，核依赖缓存闭包并真实检查。旧环境、微积分 provider 和既有 E4 来源保持；新理论须完成候选导出、独立重放、完整类型/公理门禁，以及缺边界、错符号、错测度和缺正则性的负对照。新引理进入后继时必须实际导入证明数据；MRA084 的前置目前只作调度。
