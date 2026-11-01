# Mathlib 源码检索与研究引用

入口：`http://127.0.0.1:8765/studio`。保存问题、建立研究步骤后，在“查找可用的数学依据”输入 Lean 名称、英文或预设中文概念。展开命中查看源文件、行号、片段、固定commit及公开来源，核对外围声明语境；选择“加入当前步骤”。可以撤回，历史记录及JSON导出保留。

有效的当前步骤引用会进入后续模型研究上下文；模型尚未配置时仍可检索和人工研究。源码或索引变化时引用标为不可用并从可用上下文排除；切换计划不自动复用旧引用。检索或引用不会运行 Lean，也不会把研究文字标为证明。

## API 与预算

新增三个同源、CSRF保护的 POST 路由：

- `/v1/studio/{id}/premise-search`：`node_id, query, limit`。查询最多240字符、1至12个不同词、最多12条结果，不修改研究记录。
- `/v1/studio/{id}/attach-premise`：`request_id, expected_revision, node_id, item_id`。只接受服务器索引中当前有效的条目身份，重新检查实际源码；不能上传自造引用对象。
- `/v1/studio/{id}/withdraw-premise`：`request_id, expected_revision, citation_id`。保留历史，撤回后不传入模型。

每研究问题最多12条历史引用，沿用原子记录、请求幂等、旧版本拒绝和2MiB记录上限。模型运行期间不接受引用修改。结果和引用为UNKNOWN/E0，`new_lean_checks=0`。

## 本地索引维护

在项目根执行：

```bash
PYTHONPATH=. python3 -m mra.premise_search build
PYTHONPATH=. python3 -m mra.premise_search status
PYTHONPATH=. python3 -m mra.premise_search search --query norm_inner_le_norm
```

索引位置 `artifacts/mathlib-premise-index/catalog.sqlite3`。只根据现有固定源文件构建，不下载、不自动重封存。源码、工具链快照或索引器版本变化后须显式重建；临时库构建失败保留旧完整库，旧库若版本不符仍不能使用。运行网页和只读检索不自动重建索引。

名称按源码原样返回，不保证命名空间补全；片段不是已展开类型，可能缺少前文局部变量和类型类。当前为词法检索，常见中文别名见源码ALIASES。库中已有某定理、检索命中、适用于用户原题、用户原题已证明，是四个不同判断。
