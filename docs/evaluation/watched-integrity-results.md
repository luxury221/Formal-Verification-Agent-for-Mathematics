# 事件监视式完整性扫描与独立 PDE profile 验收

MRA086，2026-10-29。本轮改善扫描期间可观察变化的检测；不是新增一般PDE或自主研究能力。

## 实现与范围

新增mra/streaming_integrity_v2.py、mra/pde_energy_watched.py、独立CLI与三测试模块。
原streaming、PDE、微积分及其已封存环境保持不变。新身份pde-energy-watched-v2，
复用原不可变Mathlib树；新run前缀pdewatched-，不混用旧来源。
候选与重放的24种Lean文本逐字与旧profile一致，六个关键函数AST一致；见
[源码自审](../../artifacts/watched-integrity-20261029-01/manual-code-review.json)。
这是Codex自审，不是独立人类审查。

每次全字节校验、两端seal、完整结构终检和inotify观察均必需。
实际文件inode监视覆盖通过硬链接别名的普通写入，父目录规则合并且过滤无关名字。
不可用、资源不足、溢出、畸形或排空耗尽拒绝，不回退旧scanner。
它不是原子快照、恶意宿主防护、返回后监视或所有mmap/远程写入的完备保证。

## 实际验证

- [46专项契约](../../artifacts/watched-integrity-20261029-01/validation-quick-f1f58274b29442cd8f891d677fccc5c3/summary.json)通过。
  包含实际写入、同戳控制、改后恢复、瞬时新增后删除、硬链接/跨进程、真实内核队列溢出、
  缺失/配额不足、FD释放、解析和排空上限。相同戳控制只隔离元数据路径，文件事件仍是真实产生。
- [相关回归](../../artifacts/watched-integrity-20261029-01/validation-contracts-13f6feafad6e4e81b68346115aee1199/summary.json)317项：266通过、51跳过、0失败。
  未启用的真实专项不算通过；旧竞态在本次运行通过不代表旧实现修复。
  MRA085的218通过/50跳过/2失败及100次诊断原样保留。
- [12质量命令](../../artifacts/watched-integrity-20261029-01/quality-c0f993627f3b410da09c18db8034d6a2/summary.json)通过，来源前后不变。
- [真实全链专项](../../artifacts/watched-integrity-20261029-01/validation-real-12b10831d1b546c1803f6846fc49569b/summary.json)通过，2655.049秒，6GiB/512MiB，
  12案例各一次：5正目标历史E4，5数学负对照REJECTED/UNKNOWN，两政策控制UNKNOWN。
  实际错误重放目标、畸形数据和错误源类型拒绝，取消/监督中断/损坏恢复及传递失效均通过。
  最终这条新profile链1当前正目标E4，不能说仍有5当前有效。
- [成功重放后监视丢失](../../artifacts/watched-integrity-20261029-01/publication-monitor-loss-3bb50cb5101b4a5da2b24bb4b5fcb295/summary.json)真实执行两阶段Lean后关闭inotify FD，
  发布前校验拒绝，结果POLICY_BLOCKED/UNKNOWN/E0；恢复正常观测后也不晋升。
- [工件语义复审](../../artifacts/watched-integrity-20261029-01/review.json)核完整原目标/条件、
  来源数据相等、完整类型桥接、直接checked常量使用、不同PID、审计/NDJSON和共享预算。
  全链与额外监视故障合计26正式worker阶段、103.351290201 worker秒；
  完整性、来源闭包及准备另计，不把专项总耗时当Lean时间。
- [14条实际CLI](../../artifacts/watched-integrity-20261029-01/cli-e297b67899574940b96d51dc3ce9f16f/summary.json)通过，额外数学检查0，包含非法来源/输入和重复封存拒绝。

## 环境和保留事项

[封存](../../artifacts/watched-integrity-20261029-01/seal.json) exit0，
96.321746秒，环境指纹7b1b84f54a944a6dbd020052dba65e413ba7abf3bf2bfb65dcdc65ecd9d15509。
最后一次扫描读取70033文件次、6046301358字节，73315 watched inode，完整终检两个树，
事件0；只是本机单次观察，不作稳定性能结论。

[依赖复审](../../artifacts/watched-integrity-20261029-01/dependency-review.json)与独立adoption/acquisition
固定既有CPython、ctypes/_ctypes、libffi、glibc及原环境；新下载/安装0。
初采集器错误假定_struct有独立__file__，修为固定解释器加实际模块文件；
原源码和记录保留，发生于封存和数学执行前。

baseline为610旧来源、452旧数据库、1002旧状态和上轮3417摘要。
最终保全、服务与交付摘要分别见本阶段preservation-final.json、runtime-*/summary.json、
delivery.json和postseal-windows-recheck.json；未实际执行全项目恢复、升级或回滚。

等待真实专项期间另有[下一阶段语言E0预查](../../artifacts/watched-integrity-20261029-01/next-language-preflight/summary.json)：20个Python输入案例，补闭区间uIcc和显式来源应用草案。源码只在项目.tmp与工件，不是已发布接口。另有一次声明后的实际候选与独立重放预查：2进程、6.511729571 worker秒，实际检查当前既有来源、完整类型桥接、NDJSON导入与直接使用。新编译器尚未封存并接入正式账本，保持UNKNOWN/E0，不晋升E4。首次缺少辅助函数导入的Python失败保留；这两阶段另计，不并入正式profile成绩。

数学仍是已知的一维经典条件链，不是一般弱解、存在唯一性、高维、原创或模型成绩。
模型调用0，网页/后台队列不自动切换新profile。下一步按
[受控PDE输入预查](../product/pde-language-preflight.md)实现新目标与确切来源应用，
再接研究图/网页，随后推进边界空间及弱形式。
