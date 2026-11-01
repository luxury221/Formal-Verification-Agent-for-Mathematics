# P0 实测记录

日期：2026-10-19。开发路径：`/home/blacklions/workspace/Linux/数学形式化验证Agent`。

下列 17 项测试为首版 P0 记录。后续 MRA-016 加固后的 31 项测试及重新运行的黄金基准，见 `runtime-integrity-results.md`；当前 `artifacts/p0-benchmark.json` 已更新为加固后的环境指纹。

## 已执行

- 官方 Lean 4.35.0 release SHA-256 为 `7289597a44543246c9227b8cae501767e095ce17f6d448b95fc4a8dc97268f6d`，已校验并运行；其内部 commit 为 `eb6cafb7290e8bd5f2937522d8d804f58f02bafc`。
- Mathlib commit 为 `56a005855ea17e0b0958f73ec801ae140b0a1679`；固定八个依赖，取得并解包实际需要的 154 个官方缓存文件。
- `MRA_REAL_LEAN=1 python3 -m unittest discover -s tests -v`：17 项测试全部通过，0 跳过，运行 25.159 秒；黄金案例作为其中一个测试方法的 10 个子案例运行。没有 mock checker。
- `python3 ops/scripts/check_source.py`：23 个 Python/JSON 文件通过语法、JSON 和基本空白检查。
- 既有 Ruff 0.12.0 执行格式化及 lint，通过；开发工具未引入产品运行依赖。
- Danus 固定 commit `6d92e8d415933ca2ef52fd1a4da73fdfcd418f1c` 的核心 add/read/dedup/search smoke 通过，约 0.163 秒。此项只是数据 API 检查，未调用其 LLM/Lean 系统；观察到 `FactGraph.add` 本身接受未核验文本，未来适配器必须先执行本项目证据门禁。

机器可读黄金基准见 `artifacts/p0-benchmark.json`，每个案例引用本机实际 Lean 工件。黄金基准内的具名映射是样例作者的声明，没有将其描述成人类用户或独立专家审核。

## 实际修复与边界

隔离启动时发现 `/dev/urandom` 不能继承普通只读文件的 nodev 标志；启动器仅对三个明确的设备挂载例外，并在启动 Lean 前测试设备可读性。另实测 Lean4.35 在 4 GiB 地址空间限制下无法创建线程，16 GiB 虚拟地址空间和双工作线程可正常运行。这里限制的是虚拟地址空间，生产部署仍需 cgroup 实际内存配额。

WSL Git smart-HTTP 出现 TLS 断连，采用固定 commit 的 codeload 源归档；没有伪造 Git commit。缓存构建使用暂时的本地路径 manifest，再恢复原始上游字节，保证缓存哈希匹配。WSL 缓存连接慢时，按官方 Cache 生成的 URL 计划从宿主下载后在 WSL 解包，未跳过 TLS 或哈希检查。

## 尚待后续验证

自动形式化、LLM 证明搜索、持久异步任务、HTTP/UI、真实论文审稿和知识撤销均属于 P1/P2+。PyPantograph/LeanDojo-v2/ProofFlow 尚未完成安装和实际推理，已记录版本/许可/运行前提。尚未进行干净 CI runner 上的从零重建，也未发布、推送或部署服务。

P0 的证据可用于审查本轮有限测试结果，不能据此声称已验证通用数学能力或任意用户 Lean 输入的安全性。
