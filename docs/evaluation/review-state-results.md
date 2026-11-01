# MRA-019 审核有效状态与撤销验收

日期：2026-10-19。活动项目：WSL Ubuntu-22.04；Python 3.10.12、SQLite 3.37.2；真实 Lean 使用既有封存环境。本阶段扩展 MRA-019，设计见 ADR-006，命令见 `docs/protocols/formalizer-v1.md`。

本页保留审核状态阶段验收；后续自然数评测数据、扩展封存和 116 项完整回归见 `nat-arithmetic-results.md`。

`MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v`：92 项全部通过，0 失败、0 跳过，130.891 秒。

| 测试分组 | 数量 | 实际验证范围 |
| --- | ---: | --- |
| 审核 SQLite / 文件系统 | 23 | 有效状态、撤销无回退、历史撤销、精确绑定、幂等冲突、全旧版本失效、多候选隔离、并发顺序、修订竞争、事务回滚、真实子进程 SIGKILL、导出恢复、旧工件迁移、篡改/符号链接/未知 schema 拒绝 |
| 无 Lean 项目的真实 CLI | 11 | 实际子进程执行候选创建、审核、撤销、幂等、修订及旧版本拒绝、注册、损坏数据库和退出码 |
| 模型协议 mock | 20 | 既有解析、预算与失败边界；不代表真实模型能力 |
| 本地 fixture / 文件工件 | 7 | 确切样例、歧义/不支持、假命题忠实保留与工件完整性 |
| 真实 Lean 集成 | 15 | 既有内核证据、语义对齐、环境与策略漂移、伪造回执拒绝和隔离；黄金案例方法内含 10 个子案例，全部通过 |
| 环境完整性 | 12 | 完整快照、拓扑、内容和运行前验证 |
| 基础合同 | 4 | 不可变命题、版本指纹及状态/数学判断分离 |

日志：`artifacts/review-state-tests.log`；机器记录、分组、源码摘要与日志 SHA-256：`artifacts/review-state-validation.json`。未 mock Lean checker，真实模型调用数为 0。审核测试使用本地精确 fixture；一个测试注入导出 OSError，一个使用 SQLite 触发器中止事务，另一个确实杀死持有未提交事务的子进程。未验证整机断电、磁盘损坏或恶意数据库管理员。

活动项目另实际执行 10 步 CLI 生命周期，完整参数、退出码和响应保存在 `artifacts/review-state-demo.json`：

| 操作 | 观察结果 |
| --- | --- |
| 创建版本 1 | UNREVIEWED；具名接受后为 ACCEPTED |
| 以同一 event-id 重试接受 | 返回相同 sequence，无重复事件 |
| 后续拒绝 | 当前为 REJECTED，早先接受保留为历史 |
| 撤销这条拒绝 | 当前为 REVOKED，不恢复早先接受 |
| 新的明确接受 | 当前为 ACCEPTED |
| 修订为同源版本 2 | 持久化父目录，新版本为 UNREVIEWED |
| 查看旧版本 / 提交新的旧版审核 | 旧版 SUPERSEDED；新审核退出码 1 |
| 重试旧版已提交接受 | 只返回历史声明，当前状态仍 SUPERSEDED |

演示中的新版本使用另一个已收录的假命题样例，候选照录原文，数学判断保持 UNKNOWN。演示未调用模型或 Lean；它验证状态流程，不宣称证明或反驳输入。

源码门禁通过 39 个 Python/JSON 文件；Ruff 0.12.0 格式及 lint 通过，29 个 Python 文件格式一致。前轮独立 `artifacts/p0-benchmark.json` 保留，本轮黄金案例由完整真实 Lean 集成测试覆盖，没有重复独立基准。远端 CI 未运行。

候选接受/拒绝/撤销仍保持 UNKNOWN/E0，不产生 Alignment，不执行候选 Lean。审核身份仍为 local-assertion-only，没有登录、权限、持久签名或独立复验后的 E4 映射，因此不把整个 MRA-019 标为完成。SQLite 仅覆盖本地审核事务与事件幂等，证明任务调度、取消、重试与恢复仍属于 MRA-021。
