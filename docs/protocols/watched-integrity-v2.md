# 事件监视式完整性扫描与 Poisson 执行 profile

版本：watched-streaming-content-scan-v2 / pde-energy-watched-v2。MRA086，2026-10-29。

## 范围和身份

扫描器为 mra/streaming_integrity_v2.py 的 WatchedSnapshot。沿用 ContentSnapshot 数据格式与每次实际全字节 SHA256 校验，不缓存成功结果；两个读取线程、最多八个待处理项，64KiB 以上并行，256KiB 缓冲。新增监视不是放宽任何原范围。数学 verdict 与 E0 扫描观察分开。

PDE profile 使用 mra/pde_energy_watched.py 和 ops/scripts/pde_energy_watched.py，独立 .runtime/pde-energy-watched-v2 元数据和 artifacts/pde-energy-watched-v2/runs/pdewatched-* 收据。数学树仍为 .runtime/pde-energy-v1/mathlib，只读复用，不改变原环境。旧 pde-* ID 不能混入新来源。来源需要本 profile 中确切、当前有效的 E4 ID。

## 每次核验

1. 创建 NONBLOCK/CLOEXEC inotify FD，记录目录子项规则及普通文件 inode；显式文件与宿主依赖还保护其路径解析链上的相关名字。目录自身失效始终拒绝；同 wd 的路径和过滤规则合并。
2. 树内每层精确排除 .git，与旧范围相同。显式父目录仅匹配相关名字，避免无关日志触发失败；不监视自身读取产生的 OPEN/ACCESS/CLOSE_NOWRITE。
3. 完成订阅后开始整体监视窗口，检查安装期间已积累事件。完整读取全部声明字节、两端重核 seal，保留模式、解析身份和内部链接约束。
4. 全量再次枚举树结构，核对完整名字集合、种类、模式及链接目标。最后排空事件至 EAGAIN，再关闭 FD。读取池在成功和失败路径均关闭。
5. MODIFY、ATTRIB、CLOSE_WRITE、CREATE、DELETE、MOVE、自身删除/移动和 UNMOUNT 等相关事件拒绝；OVERFLOW、IGNORED、畸形/未知记录同样拒绝。排空有八MiB/128次读取上限；不提高宿主限额，无自动回退。

last_scan 仅为当前一次 E0 观察，包括读取字节数、原树/终检规模、watch inode 数、事件数和监视窗口；不是 mathematical verdict 或跨调用成功缓存。

## 可信边界

只覆盖全部订阅装好后至最终事件排空的窗口；安装期间观察到的相关事件也拒绝。不是原子快照、返回后持续监视、恶意宿主防护或所有 mmap/远程写入的事件保证。同字节/戳控制测试只证明新事件路径不依赖时间戳；不能将其表述成所有文件系统条件下的完备并发防护。

额外标准库/二进制依赖在采用记录中固定版本、许可、用途与实际宿主文件；kernel release 不符拒绝。不得因监视缺失而继续发布 E4。运行失败、取消、中断保持 UNKNOWN，历史失效恢复原字节不复活。

## 数学执行与 CLI

PYTHONPATH=. python3 ops/scripts/pde_energy_watched.py catalog
PYTHONPATH=. python3 ops/scripts/pde_energy_watched.py seal
PYTHONPATH=. python3 ops/scripts/pde_energy_watched.py run --probe ID --reviewer NAME [--source-run-id pdewatched-ID]
PYTHONPATH=. python3 ops/scripts/pde_energy_watched.py report --run-id pdewatched-ID
PYTHONPATH=. python3 ops/scripts/pde_energy_watched.py recover --run-id pdewatched-ID

seal 不覆盖已有元数据；依赖环境已经封存以后，不就地改核心。数学来源仍是固定 [pde-energy-v1](pde-energy-v1.md) 的一维经典条件链，实际 NDJSON 导入、完整类型桥接、直接 checked 常量使用审计和不同 PID 重放均保留。候选/重放共享40 worker秒，完整性与来源闭包检查另计。不接受任意 Lean 或通用 PDE 输入；网页不自动切换执行身份。

参见 [ADR0078](../adr/0078-watched-integrity-profile.md)、[阶段验收](../evaluation/watched-integrity-results.md)及[后续 PDE 输入](../product/pde-language-preflight.md)。
