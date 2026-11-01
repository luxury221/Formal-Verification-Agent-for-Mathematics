# 人工标注单定理审查验收

日期：2026-10-25。依据：[ADR-016](../adr/0016-annotated-single-theorem-review.md)、[审查协议](../protocols/paper-review-v1.md)。对应蓝图 3.1、3.3 与 MRA-024。

## 交付与边界

论文页新增人工标注的自然数单定理真实审查：先核对带行号的原文、全部前提、每步确切公式及依赖，再具名确认并入队。命题与作者步骤分别接受真实 Lean 检查及独立进程重放。可查看工件、取消、撤销、导出和恢复历史；近期审查接入共享搜索及研究记录。保留暖白/墨绿视觉与桌面/手机布局。

| 自有示例 | 命题判断 | 作者步骤判断 | 真实证据 |
| --- | --- | --- | --- |
| 自然数递增：完整步骤 | PROVED | PROVED | 命题及两步共三项真实检查 |
| 命题成立，原证明有缺口 | PROVED | GAP_FOUND | 第2行断言 n=n+1 的否定核验，见证0；依赖此步的后续步骤跳过 |
| 乘法消去：缺少必要条件 | REFUTED | GAP_FOUND | a=0、b=1、c=0 满足 a*b=a*c 且 b≠c，命题与作者推理分别核验 |

每个义务至多一次候选检查及独立进程重放，共用30秒；至多四步、五项检查，环境内容扫描另计。准备不调用 Lean 或模型。没有新增依赖、Lean模板或证据政策；运行快照仍为 `901dbf78492acf4cb255e744b2affac16ad2789a0a986981da55edb18530d107`。

本阶段需要人工结构化标注与本地具名语义确认，只覆盖受限 Nat 单定理及所标注步骤。自动抽取、PDF解析、一般数学、完整论文审稿、身份认证和知识依赖撤销传播仍待后续。真实模型未配置、调用为0；三个作者示例和协议模拟不代表模型能力或通用审稿效果。

## 验证

- **76项受影响回归全部通过，0失败、0跳过，344.064秒**。其中新增审查15项（10项协议、5项真实Lean），另含HTTP19、工作区6、猜想25、形式化CLI11。这是受影响范围回归，不是全部历史测试重跑。
- 覆盖完整步骤、双通道分离、真实缺条件反例、失败不当反例、未验证依赖、预算/标注/版本边界、幂等、取消/撤销、崩溃恢复及工件变化停用结论。回归运行期间源码摘要不变。
- **实际Edge13项流程检查与补充5项错误恢复检查通过，共18项、4张截图**。实际提交产生 `PROVED / GAP_FOUND`，核对原文第2行、独立重放工件、跳过s2、导出、深链接、搜索、记录、取消及九页面手机宽度；桌面1440和手机390布局已目视检查。
- 回归和首轮浏览器检查后，仅 `papers.js` 将标注JSON解析移入既有try/finally，避免非法JSON使准备按钮永久禁用。补充5项实际浏览器检查覆盖修复、服务端无效行号拒绝、同页纠正、确认失效及重启后真实历史恢复。后端、测试、Lean与配置与76项回归源码完全一致，没有把早期回归冒充修复后JS的执行验收。
- 主流程JS异常和console/CSP/资源错误为0。补充负例有1项预期HTTP400，完整保留；未预期错误和JS异常均为0。初次验收脚本选择器转义失败和补充脚本误将预期400计作失败的原始记录另存，均为辅助脚本修正。
- 最终Ruff52个Python的lint/格式及5个JS语法检查、source gate70文件通过。三个账本完整性ok，RUNNING与QUEUED均为0。最终17条页面/静态资源均HTTP200且逐字节匹配源码，保留CSP/no-store；模型未配置、worker无错误。QA8780已停止，8765新版服务保留。

## 证据

| 工件 | 内容 |
| --- | --- |
| `artifacts/paper-review-validation.json`、`paper-review-tests.log` | 76项回归、范围、源码与日志摘要 |
| `artifacts/paper-review-execution-evidence.json` | 实际Lean进程、独立重放与工件；含故意破坏工件的负例 |
| `artifacts/paper-review-quality.json`、`paper-review-final-quality.json` | 回归前及最终静态检查 |
| `artifacts/paper-review-browser-qa.json` | 实际13项流程、4截图及真实记录ID |
| `artifacts/paper-review-browser-recovery-qa.json` | 补充5项错误恢复，保留1项预期400 |
| `artifacts/paper-review-browser-result.json`、`paper-review-service-health.json` | 真实双通道报告、单项回执复核、最终服务与资产 |
| `artifacts/paper-review-final-runtime.json`、`paper-review-delivery-check.json` | 账本、最终源码/文档/截图及证据摘要 |

取消、撤销、崩溃或工件/协议变化均停用两项对外结论。历史摘要一致不等于当前环境新认证；本地姓名不是账户身份。代码未提交、推送或发布，远端CI未运行。
