# 完整前端设计验收

日期：2026-10-25。范围：统一产品前端、真实已有流程的衔接及只读评测界面。依据：[ADR-015](../adr/0015-unified-research-workspace.md)，使用方式见 [工作区协议](../protocols/workspace-ui-v1.md)。

## 交付

九个入口统一为暖白/墨绿视觉，包含共享侧栏、全局记录搜索、对话框、移动导航和数学 SVG。总览为 `/`，原固定目标验证迁到 `/verify`；整体构造可在 `/workflow` 点击六个步骤查看。原猜想/验证表单、证据、确认、取消和任务逻辑继续使用真实后端。

论文页提供三栏阅读、章节、本地会话草稿及笔记。知识页提供可点图谱与条目列表。两者明确标为设计预览，尚无自动审稿、PDF 解析或知识入库。评测页读取真实计划/历史，并展示 CLI 命令；设置页只在浏览器生成配置，未接收密钥或写入服务器。真实模型继续按用户选择暂不配置。

## 验证

- **65 项受影响回归通过，0 失败、0 跳过，353.195 秒**：新增工作区 HTTP 6 项、原 HTTP 19 项、猜想 25 项、模型评测 15 项。包括真实 Lean 的证明、完整否定、失败、撤销、取消及工件变更；模型提案使用明确标记的协议模拟，真实模型调用为零。这是受影响范围回归，不是全部历史测试重跑。
- Python 回归期间源码摘要前后一致，两个账本 integrity_check=ok、RUNNING=0。后续浏览器检查发现猜想页 `history` 函数与 `window.history` 冲突，已仅在 `ideas.js` 将该函数改名为 `refreshHistory`；后端、测试、Lean 与配置摘要均未变化。该修复重新通过 JS 语法及最终真实浏览器检查，没有冒充 Python 回归已覆盖修改后的 JS。
- **实际 Edge 29 项检查全部通过，13 张截图**：1440×1000 与 390×844，九个页面无横向溢出；图谱切换、流程选择、论文草稿/笔记保留、猜想示例、配置空白拒绝与内容生成、实际历史评测、搜索、真实猜想/任务深链接以及手机抽屉正常。JS 异常与 console/CSP/资源错误均为零。
- Ruff 对 50 个 Python 文件的 lint/格式检查、四个 JS 的语法检查通过；源码门禁 65 个 Python/JSON 文件通过。最终线上 16 个页面/静态资源均 HTTP 200，响应逐字节匹配当前源码，保留 CSP 与 no-store。模型未配置，worker 无错误。

## 证据

| 工件 | 内容 |
| --- | --- |
| `artifacts/frontend-design-validation.json` | 回归范围、计数、源码摘要、日志及运行封存绑定 |
| `artifacts/frontend-design-tests.log` | 65 项实际回归日志 |
| `artifacts/frontend-design-execution-evidence.json` | 实际 Lean 回执、进程与工件摘要；包含有意破坏工件的负例 |
| `artifacts/frontend-design-quality.json` | Ruff 与 JS 语法结果 |
| `artifacts/frontend-design-browser-qa.json` | 最终 29 项实际 Edge 检查及截图列表 |
| `artifacts/frontend-design-service-health.json` | 最终服务、模型状态与逐资源源码匹配 |
| `artifacts/frontend-design-delivery-check.json` | 最终源码、文档、截图与验收工件摘要 |

首次最终浏览器检查因后台启动未就绪而超时，保留 `frontend-design-browser-startup-failure.json`；后续深链接检查发现上述真实命名冲突，保留 `frontend-design-browser-before-history-fix.json`。最终通过记录来自修复、重启后对 8765 实际服务的完整浏览器检查。最终采用带就绪检查的隐藏后台 WSL 启动，未安装开机自启。

没有新增运行依赖、外部图标/字体/图片、模型下载、训练或用户资料上传。Lean 与证据政策没有修改，无需重新封存；运行快照仍为 `901dbf78492acf4cb255e744b2affac16ad2789a0a986981da55edb18530d107`。姓名仍为本地声明，历史完整性不等于当前环境新认证。论文审查、知识积累与真实模型效果仍待各自功能验收。
