# 自然数猜想闭环 v1

设计依据：ADR-013。入口为 `http://127.0.0.1:8765/ideas`，根路径 `/` 为研究总览，原固定目标工作台使用 `/verify`。这是受控自然数算术的本机功能，不是通用数学或论文审稿。

## 使用

1. 输入命题及可选背景。未配置模型时仅保存为 WAITING_FOR_MODEL，模型调用为零。
2. 配置模型后点“生成形式化候选”。模型可返回至多三个候选、待澄清问题或不支持；候选不具有 Lean 证据。
3. 核对原文、定义域、量词、所有前提及确切 Lean 陈述，选择候选并具名确认。
4. 后台最多提出三个证明计划；每次模型请求 30 秒、2048 输出 token、64 KiB 输出，每次 Lean 候选及独立重放合用 30 秒。环境内容核对另计。
5. 模型选择证明原式或完整否定，及 omega、simp、decide、显式自然数见证反例策略。程序生成全部 Lean 源；模型不能提交自由战术、命令或导入。失败反馈只用于换计划，不允许改变确认过的命题。
6. 查看原式/否定、数学判断、证据和真实尝试。失败、超时、取消保持 UNKNOWN；不将搜索失败当反例。

修订必须增加源版本并改变原文或背景，旧候选及确认只保留为历史。活动任务须先取消再修订。撤销确认后立即停用对外数学结论；正在检查的进程将在取消检查点终止。历史工件变动也会抑制结论。

## 配置

把 `config/model.example.json` 复制为 `config/model.local.json`，修改 endpoint（完整 Chat Completions URL）、model、revision（供应商版本或部署标识）、api_key_env 和 token_field。不接受 latest/main 等 revision。此版本标识为操作者声明，不能声称供应商内部权重已经由本程序认证。

在启动服务的 Linux 环境设置 api_key_env 对应密钥；默认名称为 MRA_MODEL_API_KEY。密钥不写入配置、请求工件或命令行。更改环境变量后重启服务。无认证的本地兼容服务可不设密钥；HTTP 仅允许回环地址，远程端点要求 HTTPS。配置仅允许本机文件管理，不提供 HTTP 配置/密钥接口。

接口要求 messages、非流式响应、response_format=json_object；token_field 可选 max_completion_tokens 或 max_tokens。响应须包含单个完成的 choices、实际 model、usage.prompt_tokens 与 usage.completion_tokens。禁止重定向，不隐式重试。模型返回文本不可信，必须再经过严格 JSON、作用域与预算检查。

供应商用量来自响应信封；费用未估算时为 null，不能显示为零费用。配置启用后，原文、背景和检查反馈将发送到所配置服务；Lean 子进程仍禁止出网。真实模型稍后由用户配置，本轮协议模拟不代表模型效果。

官方接口参考：<https://platform.openai.com/docs/api-reference/chat/create>。2026-10-24 本机访问官方页面返回 HTTP 403，未据此宣称核对了最新模型兼容性；当前字段由本地协议往返测试验证，具体供应商能力需配置后实际测试。

## 数据范围

有界一阶 Nat 公式：forall/exists/not/and/or/imp、eq/le/lt、num/var/add/mul/sub。减法采用 Nat 截断语义。最多 256 节点、24 层、8 个同时绑定变量、自然数字面量及反例见证最多 1000000。所有前提必须显示在公式内，背景不会作为隐含前提自动加入检查。

omega 适合线性自然数算术，simp 仅调用受信默认化简规则，decide 限于可计算判定；乘法虽可表达，但一般非线性问题可能 UNKNOWN。反例策略仅实例化最外层全称变量再用 omega 证明矛盾。此范围不包含自定义定义、任意函数、实数/集合/分析或完整论文。

## HTTP

沿用工作台精确 Host、同源 Origin、CSRF 与 192 KiB 正文约束，响应不缓存。

| 方法 / 路由 | 行为 |
| --- | --- |
| GET /ideas、/ideas.js | 工作台资源 |
| GET /v1/session | 新增 idea_model 状态；不返回密钥 |
| POST /v1/ideas | request_id、statement、context；幂等保存或入队 |
| GET /v1/ideas | 最近 30 个猜想的执行元数据 |
| GET /v1/ideas/{id} | 版本、候选、确认、尝试、事件、判断和工件状态 |
| POST /v1/ideas/{id}/generate | 空对象；显式生成新候选 |
| POST /v1/ideas/{id}/confirm | target_fingerprint、source_version、reviewer、confirmed=true |
| POST /v1/ideas/{id}/revise | expected_version、statement、context |
| POST /v1/ideas/{id}/cancel | 空对象；取消当前操作 |
| POST /v1/ideas/{id}/revoke | 空对象；撤销确认及对外结论 |
| GET /v1/ideas/{id}/evidence | 摘要完整时读取最后验证报告及回执 |

账本位于 `artifacts/ideas/ideas.sqlite3`，与原任务共用 worker 进程锁。崩溃只标记 INTERRUPTED，保留历史而不从遗留报告恢复数学判断。SQLite、操作者及宿主仍受信，姓名不是账户身份；历史报告 fresh_attestation=false，不代表当前运行环境重新认证。
