# ADR-002：固定原生 Lean CLI，使用 WSL namespace 隔离

日期：2026-10-19。状态：P0 采用。

Lean 4.35.0 官方 Linux release 与 Mathlib v4.35.0 匹配。前者固定 release SHA-256，后者固定 commit 及源归档哈希。工具链保存在项目 `.runtime/`，不修改系统 Lean 默认版本。

`unshare` 建立 user、mount、network 和 PID namespace；可信 Python 启动器将输入、Lean 和 Mathlib 挂载为只读，chroot 到独立 tmpfs，清理传入环境，不提供宿主 home 或 D 盘。Lean 进程使用两个工作线程，CPU/时间/地址空间/文件资源受限；宿主超时杀死整个进程组。

比较：原生 CLI 无需引入 Python 大型 ML 依赖；PyPantograph 当前元数据要求 Python 3.11+，本机 WSL 为 3.10.12；LeanDojo-v2 亦要求 3.11+，且有 Torch、DeepSpeed、Ray 等重依赖，许可元数据与正文不一致；ProofFlow README 要求 3.12+，pyproject 声明 3.8+，还需模型与 Lean Server 配置。后三者本轮仅审计接口与运行前提，不声称已完成安装或推理测试。

检查包含确切 theorem 声明、定义等价的目标类型、目标及本模块的传递公理依赖。Lean 自身查询用于发现 `sorryAx` 和额外公理，不以字符串匹配替代语义检查。

限制：P0 输入是可信仓库案例。任意 Lean 元编程可能影响同进程审计器，因此公开源代码接口需另设限制语法、独立内核复验、Worker 身份认证和攻击测试。隔离措施已经具备不代表任意输入可信。Windows 原生和 Docker 路径本轮不采用；Docker daemon 当时未运行。

回滚：环境变更产生新 pin 与证据，旧报告保留；接口允许以后替换成 Pantograph/LeanDojo 等，但须重新通过相同门禁基准。
