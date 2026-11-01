# 项目归档与容量管理 v1

入口：`http://127.0.0.1:8765/research`，展开“整理项目与恢复归档”。

## 使用

搜索标题或原始问题，选择活跃、已归档或全部项目。归档/恢复均需填写操作人、原因并确认。归档释放一个活跃名额，历史内容和证据链接保留，已有任务仍可通过直链继续。归档不表示撤销证据或数学结论。

活跃项目最多100；需要新建论文/Studio任务时，先归档已核对的旧项目。恢复需要空闲名额。包含归档的项目最多保留10000个；达到该上限时，归档也不能继续释放保留额度，需要导出并另建工作区。

界面请求失败不会自动重发。应刷新容量并核对项目状态；同一确认框内再次确认复用原编号和完整请求，避免网络结果不明时重复操作。若修订已改变，关闭确认框、重新读取后再提交。未保存的研究图不因容量事件被覆盖，草稿提示会阻止研究列表自动切换。

## HTTP

沿用当前 `/v1/session` 会话、同源要求及 `X-MRA-CSRF` 请求头。

- `GET /v1/research-capacity`：完整组织目录、counts、limits。
- `POST /v1/research-capacity/{project_id}/archive`：归档。
- `POST /v1/research-capacity/{project_id}/restore`：恢复。
- `GET /v1/research`：默认仅活跃项目，同时返回 capacity。
- `GET /v1/research/{project_id}`：归档项目仍可直读，附 lifecycle。

归档和恢复请求精确为：

```json
{
  "request_id": "unique-capacity-request",
  "expected_revision": 0,
  "reviewer": "操作人",
  "reason": "整理原因",
  "confirmed": true
}
```

接口不接受 query 参数、额外字段、布尔修订或 truthy 确认。expected_revision 是 lifecycle.revision，不是研究正文 version。编号同内容重发返回当前生命周期及 `idempotent_replay: true`；不会把后续恢复重新变成归档。

## 本地 CLI

在 WSL 项目根执行，无需网络或模型：

```bash
PYTHONPATH=. python3 ops/scripts/research_capacity.py list
PYTHONPATH=. python3 ops/scripts/research_capacity.py show research-...
PYTHONPATH=. python3 ops/scripts/research_capacity.py archive research-... --request-file .tmp/archive.json
PYTHONPATH=. python3 ops/scripts/research_capacity.py restore research-... --request-file .tmp/restore.json
PYTHONPATH=. python3 ops/scripts/research_capacity.py create --request-file .tmp/create.json
```

CLI 可用 `--root` 显式选择工作区。请求文件采用严格 UTF-8 JSON，最大128KiB；create 使用原 `request_id`、`title`、`question` 三字段格式。失败返回非零退出码和 JSON error，不执行数学检查。

## 升级、恢复与限制

旧数据首次启动自动在同库补充三张容量表；原三表格式和原正文摘要保持。部分容量表丢失或身份损坏时停止并诊断，不要自行删表重建。详见 ADR0093。

回滚代码须保留当前库和新表；旧版不理解归档，也无法突破其原100物理项目限制。新版本写入后直接恢复升级前整库备份会丢失新记录，不能把它当普通回滚操作。

归档不压缩数据库和证明工件，不是阻止旧任务执行的资源锁。目录现在逐项校验正文，10000规模性能尚未验证。当前生命周期只保留最新操作原因，旧请求保留摘要和修订，不提供每次历史原因的完整恢复。原文数学仍以 TeX 文本显示；新增任务必须另行形式化和严格验证。

## 本次正式验收

原100项目全部保留，其中空白界面测试项目 `research-9d658c8b922c447e946996f074a2e58d` 已归档。新建论文命题2研究：

- Studio：`study-391dd3dc997846a3b86f45096f286b28`
- 项目：`research-56449b8a38544c60880dbe5ecad3a36e`
- 论文 workspace：`articlework-d36d97ceada94fac8e6ca04f99b98b55`
- 原始完整命题、p/q/r条件及传递前置说明均保留，状态 UNKNOWN。

验收工件在 `artifacts/project-capacity-20261030-01/`。正式服务最终100活跃、1归档、101保留。新增研究任务本身不增加论文已验证义务。
