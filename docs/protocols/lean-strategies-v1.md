# 固定 Lean 策略实验 v1

入口：[Lean 策略实验](http://127.0.0.1:8765/strategies)，也可从数论实验顶部进入。模式 `mathlib-nat-strategy-v1`。输入是完整 Nat 目标，沿用原 Nat/gcd/互素语言：最多六个变量、六个假设、240 节点、14 层深度，worker 预算 1–30 秒。表达式与公式见数论编辑器，不接收任意证明源码、额外导入或战术文本。

1. 选择示例或编辑原文、全部变量、假设及结论，选择 `omega` 或 `simp-gcd`。
2. 预览完整形式化陈述、变量映射、确切固定证明来源、共享预算与指纹。预览没有启动 Lean。
3. 填写本地核对者，确认数学域、全部条件、结论、策略和预算，再提交真实检查。
4. 查看运行状态与数学判断。候选、类型/公理审计和独立证明数据重放通过后才 PROVED/E4。失败、超时、中断与取消都保持 UNKNOWN/E0；错误诊断不构成反例。
5. 可取消队列或运行、具名撤销语义确认、导出完整记录或带回重新核对。带回和任何编辑都会清除旧确认。检测到来源/工件漂移会持久停用，恢复文件不会复活结论。

`omega` 处理自然数加法、大小关系和部分截断减法；一般乘法、gcd、整除不在其保证范围。`simp-gcd` 为 `by intros; simp_all only [...]` 的固定模板，六条规则是 `Nat.gcd_self`、`Nat.gcd_zero_left`、`Nat.gcd_zero_right`、`Nat.gcd_comm`、`Nat.coprime_iff_gcd_eq_one`、`Nat.dvd_refl`；同时保留 Lean 的基础化简机制。每次只尝试所选一条策略，一个候选及一次重放，无自动重试。

CLI（项目根目录，先设置 `PYTHONPATH=.`）：

```bash
python3 -m mra lean-strategy-catalog
python3 -m mra lean-strategy-preview --input target.json
python3 -m mra lean-strategy-submit --input reviewed-submission.json
python3 -m mra lean-strategy-work --run-id mathlib-<32hex>
python3 -m mra lean-strategy-report mathlib-<32hex>
python3 -m mra lean-strategy-list
python3 -m mra lean-strategy-cancel mathlib-<32hex>
python3 -m mra lean-strategy-revoke mathlib-<32hex> --input revocation.json
python3 -m mra lean-strategy-recover
```

`target.json` 是 catalog.examples 中任一完整对象。提交对象严格包含 `request_id`、`request`、`plan_fingerprint`、`reviewer`、`confirmed: true`；指纹必须来自完全相同的预览。相同请求身份重复提交幂等，改核对者不能接管旧请求。撤销对象严格包含 `reviewer`、`reason`、`confirmed: true`。服务 worker 会处理明确提交的队列，CLI work 与其共享同一个全局锁；recover 不自动重试。

HTTP：GET `/v1/lean-strategies/catalog`、GET/POST `/v1/lean-strategies/runs`、POST `/v1/lean-strategies/preview`、GET `/v1/lean-strategies/runs/<id>`、POST 后缀 `/cancel`（空对象）及 `/revoke`。写接口沿用 Origin/CSRF 门禁，查询串不能触发执行。

这是受限目标的单次战术尝试，未提供交互式证明状态、分支搜索、归纳或模型规划。保留前提不认证其相容性；相矛盾前提可能导致空真。姓名仅为本地对齐声明，导出的 SHA 也不是数字签名。没有自动研究图/知识入库、旧评测回填或一般数学求解保证。记录保存于新增第十四主库及 `artifacts/lean-strategies/runs/`，备份应包含两者和原十三库。见[ADR032](../adr/0032-fixed-lean-strategy-experiments.md)及[实测](../evaluation/lean-strategies-results.md)。
