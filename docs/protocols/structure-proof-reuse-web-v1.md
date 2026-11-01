# 结构证明复用网页传输 v1

页面 /typed-research 的“结构与归纳”模式与数域模式共用复用面板；新旧图判断不互相晋升。数学协议仍为 structure-proof-composition-v1，详见 [核心协议](structure-proof-composition-v1.md)。

| 方法 | 路由（前缀 /v1/structure-reuse） | 内容 |
| --- | --- | --- |
| GET | /graphs/GRAPH/nodes/NODE | 完整目标语境、同图直接前置候选、最近20条组合历史；目录 UNKNOWN/E0 |
| GET | /sources/ITEM | 当前来源完整类型、原请求、具名对齐及祖先；不传证明数据 |
| POST | /preview | 数学原请求；不执行 Lean |
| POST | /runs | request_id、request、plan_fingerprint、reviewer、confirmed=true；202，唤醒单 worker |
| GET | /runs/RUN | 重新审计完整报告 |
| GET | /runs/RUN/export | 同一完整报告，网页下载 JSON |
| POST | /runs/RUN/cancel | 恰为空对象；取消不退额度、不自动重排 |
| POST | /runs/RUN/revoke | reviewer、reason、confirmed=true；持久停用与后继审计传播 |

禁止 query；写操作遵守原同源与 CSRF 检查，正文严格 JSON、拒绝重复键及超出192KiB。来源 origin 为 structure-graph-node 或 structure-composition-run。来源标识与核心协议一致：node.<structureflow-id>.<node-id> 或 run.<mathlib-id>，每个直接前置恰一份当前可用E4。

完整载体与运算按声明次序显式应用；闭合定义进入完整类型，再提供每个变量实参及全部原条件证明。预览逐条展示，与语义核对人确认绑定。共享环境字节变化、来源撤销／替换／损坏均使旧确认不可复用。

运行状态与数学判断分开；原图原问题保持自己的证据。新的组合必须通过实际源数据导入、完整类型／公理审计及不同进程独立重放，才能有自己的 PROVED/E4。错误条件拒绝或超时仍 UNKNOWN，不能标 REFUTED。

POST不自动重发。服务端接受但响应丢失时，网页保留原请求ID和正文、锁定姓名／确认／编辑器，允许人工原样恢复；切换语境或刷新会丢失页面内草稿，应先检查已有历史。GET沿用共享FIFO与有界503重读，最终失败隐藏旧当前判断。完整扫描和锁等待无硬HTTP超时，可能需要人工重新核对。
