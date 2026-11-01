# 有限 Nat 反例协议 v1

候选协议 `mra-nat-counterexample-v1`，实验协议 `mra-nat-counterexample-search-v1`。仅支持原 `mathlib-nat-gcd-v1` 中的 Nat、加法、乘法、截断减法、gcd、整除、互素、相等、≤、合取、析取和等价式。原变量、所有前提和原文完整保留。

## 使用

在 `/number-theory` 填写完整目标，点击“探索反例候选”。实验无需证明项，不执行 Lean 或模型；搜索结果始终 UNKNOWN/E0。导出候选 JSON 后放入 WSL 项目目录：

```sh
python3 -m mra counterexample-prepare --input counterexample-candidate.json
```

返回 `cex-<32hex>`、PREPARED 和完整 plan，不创建子检查目录。审查原文、Nat 域、量词、全部前提、完整反例赋值、封闭实例、**原猜想的否定**及 worker 预算后，使用实际 ID 和指纹：

```sh
python3 -m mra counterexample-run cex-<id> --plan-fingerprint <sha256> --reviewer '核对者姓名' --confirmed
python3 -m mra counterexample-report cex-<id>
```

实验也可通过 `counterexample-search --input exploration.json` 调用，输入为：

```json
{
  "schema": "mra-nat-counterexample-search-v1",
  "target": {
    "schema": "mathlib-nat-gcd-v1",
    "title": "完整猜想标题",
    "original": "原始完整陈述",
    "variables": ["a"],
    "assumptions": [],
    "goal": {"eq": [{"var": "a"}, {"nat": 0}]},
    "timeout_seconds": 30
  },
  "budget": {"max_value": 4, "max_assignments": 1000}
}
```

返回的 `candidate` 保留 target 字段，将 schema 改为候选协议并加入完整 `witness`；它可直接交给 prepare。不能夹带 `proof`、源代码或额外字段。每个 witness 必须为 0–32 的整数，bool 不视作整数；所有变量必须恰好出现一次。缺少或违反任何原前提、没有违反结论的赋值拒绝准备。

## 数学与运行语义

数值解释中 `0 ∣ b` 当且仅当 `b=0`；任意自然数整除 0；`Nat.Coprime 0 0` 为假，`Nat.Coprime 0 1` 为真；自然数减法在 0 处截断。实验最多 32 的每变量上界、5000 组赋值、100000 次 AST 运算、256 位中间值和 0.5 秒协作式时间，输入验证与来源摘要另计。溢出的赋值跳过并计数，不作为满足前提或没有反例的证据。枚举按变量声明顺序的字典序进行，无变量时检查唯一空赋值。

prepare 仅验证数值候选并保存完整计划，UNKNOWN/E0。检查器证明的是 `¬ (原全称命题)`；数值前提使用内核可检查的 decide，原命题实例化后与结论否定矛盾。一次真实候选检查和一次不导入候选模块的证明数据重放共享原目标的 1–30 秒 worker。不得从模型、自报、编译退出码或单次数值计算直接赋予 REFUTED。

报告同时保留 `plan.original_formal_statement`、`plan.formal_statement`（否定）、`plan.request.witness` 和实际 `check`。子检查的 PROVED 对象为否定命题；父报告只有 COMPLETED 且子 PROVED/E4、对齐及工件完整时才为 REFUTED/E4。其他状态均为 UNKNOWN/E0，不能将父运行状态与数学判断混用。没有原命题 PROVED 通道。

确认必须为布尔 true、非空具名核对者和精确 plan_fingerprint。姓名是本地声明，不提供认证。相同确认命令幂等，不重试、不刷新预算；不同核对者拒绝接管。中断或监督进程退出后读为 INTERRUPTED，即使子否定证明已完成，父结论也保持 UNKNOWN。新的检查需另行准备和审查。

## 存储与接口

计划及执行报告以原子 `{payload,sha256}` envelope 发布，每文件最多 2MiB，本地最多 50 次记录。每次子账本隔离保存于 `artifacts/nat-counterexamples/cex-<id>/checks/`；13 个主业务库不变。读取重新编译原目标和反例，核对版本、对齐、子证据及实际文件摘要；检测变化后写持久 invalidated 标记，恢复旧文件不复活记录。宿主文件系统和本地 SHA 本身仍非签名信任根。

HTTP：POST `/v1/number-theory/counterexamples/search` 只做有限实验，受现有 Origin/CSRF/请求大小限制；GET `/v1/counterexamples`、GET `/v1/counterexamples/<id>` 读取记录。其余执行写接口不开放。浏览器入口 `/number-theory#cex-<id>`，支持完整证据及候选导出；打开历史不执行 Lean。

模型未配置、真实调用 0。该协议不修改 MRA039 的旧分数、不自动入库或绑定研究图，不承担原论文证明忠实性、新颖性判断或一般数学求解评测。
