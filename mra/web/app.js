"use strict";
const $ = id => document.getElementById(id);
let session, selected = null, task = null, requestId = crypto.randomUUID(), pollBusy = false;
const stages = {PREPARING:"准备检查",STARTING:"领取任务",CHECKING:"核对环境",LEAN_RUNNING:"检查候选证明",REPLAY_PREPARING:"准备独立重放",REPLAY_RUNNING:"独立进程重放",ASSESSING:"核对目标与证据",FINISHED:"流程结束",INTERRUPTED:"执行中断",QUEUED:"等待执行"};
const states = {QUEUED:"排队中",RUNNING:"运行中",COMPLETED:"已完成",FAILED:"执行失败",CANCELLED:"已取消"};
const target = () => session.targets.find(item => item.case_id === $("target").value);
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, body) {
  const options = body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-MRA-CSRF":session.csrf_token},body:JSON.stringify(body)};
  const response = await fetch(path, {...options, cache:"no-store"});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}
function resetReview() { $("confirmed").checked = false; $("reviewer").value = ""; requestId = crypto.randomUUID(); }
function showTarget() {
  const current = target();
  $("original").textContent = current.claim.original_statement;
  $("formal").textContent = current.formalization.checked_statement;
  $("direction").textContent = current.formalization.direction === "refute" ? "本次检查原命题的完整否定。通过并确认映射后，原命题可判为 REFUTED。" : "本次检查上述原命题。";
  $("assumptions").textContent = "附加假设：" + (current.claim.assumptions.join("；") || "无");
  $("sample").replaceChildren(...current.samples.map((sample,index) => new Option(sample.name,index)));
  $("certificate").value = "";
  resetReview();
}
function showMode() { $("certificate-panel").hidden = $("mode").value !== "kernel-expression-certificate-v1"; requestId = crypto.randomUUID(); }
function elapsed(attempt) {
  if (!attempt) return "—";
  return Math.max(0, ((attempt.finished_at ? Date.parse(attempt.finished_at) : Date.now()) - Date.parse(attempt.started_at)) / 1000).toFixed(1) + " 秒";
}
function showTask(value) {
  const key = item => JSON.stringify([item?.task_id,item?.latest_attempt_id,item?.artifact_status]);
  if (key(task) !== key(value)) {
    $("evidence").textContent = "点击“查看检查工件”读取记录。";
    $("evidence-details").open = false;
  }
  task = value;
  $("empty").hidden = true; $("result").hidden = false;
  $("task-id").textContent = value.task_id;
  $("state").textContent = states[value.state] || value.state;
  $("verdict").textContent = value.math_verdict;
  $("level").textContent = value.evidence_level.split("_")[0];
  $("stage").textContent = stages[value.stage] || value.stage;
  $("run-status").textContent = value.run_status || "尚无终态";
  $("attempts").textContent = `${value.attempts.length} / 3`;
  const latest = value.attempts.at(-1);
  $("elapsed").textContent = elapsed(latest);
  let explanation = "任务尚未产生可发布的数学结论。";
  if (value.math_verdict === "PROVED") explanation = "确切原式通过了两阶段内核检查，并有此版本的具名语义对齐声明。";
  else if (value.math_verdict === "REFUTED") explanation = "确切原式的完整否定通过了两阶段内核检查，并有此版本的具名语义对齐声明。";
  else if (value.evidence_level.startsWith("E3")) explanation = "两阶段内核检查已通过。原文与形式化的语义对齐尚未确认，因此数学判断仍为 UNKNOWN。";
  else if (value.evidence_level.startsWith("E2")) explanation = "形式化目标已记录；此证明尚未获得可发布的内核证据。检查失败不构成反例。";
  if (value.artifact_status === "CHANGED_OR_MISSING") explanation = "历史工件已变化或缺失，已抑制数学结果。需要重新运行检查。";
  else if (value.state === "CANCELLED") explanation = "任务已取消，没有发布任务证据。";
  else if (value.run_status === "TIMEOUT") explanation = "检查超时，数学判断保持 UNKNOWN。可按原证书和预算显式重试。";
  else if (latest?.error) explanation += " " + latest.error;
  $("explanation").textContent = explanation;
  $("cancel").disabled = !["QUEUED","RUNNING"].includes(value.state) || value.cancel_requested;
  $("cancel").textContent = value.cancel_requested ? "已请求取消" : "取消任务";
  $("retry").disabled = !value.retryable;
  $("review-again").hidden = !value.evidence_level.startsWith("E3") || !session.targets.some(item => item.case_id === value.request.case_id);
  $("artifact-status").textContent = ({NONE:"尚无已提交的检查工件。",MATCHES_RECORDED_HASHES:"工件摘要与任务记录一致 · 历史记录",CHANGED_OR_MISSING:"工件变化或缺失 · 结果已抑制"})[value.artifact_status];
  $("checked-statement").textContent = (value.request.direction === "refute" ? `¬ (${value.request.formal_statement})` : value.request.formal_statement);
  $("alignment-status").textContent = value.request.reviewer ? `此任务的对齐声明：${value.request.reviewer}（本地姓名）` : "此任务未附带语义对齐声明。";
  $("events").replaceChildren(...value.events.map(event => {const item = document.createElement("li"); item.textContent = `${new Date(event.at).toLocaleTimeString()} · ${stages[event.payload.stage] || event.kind}`; return item;}));
}
async function loadTask(id, populate = false) {
  selected = id; location.hash = id;
  const result = await api("/v1/tasks/" + encodeURIComponent(id));
  if (selected !== id) return;
  showTask(result);
  $("evidence").textContent = "点击“查看检查工件”读取记录。";
  if (populate && session.targets.some(item => item.case_id === result.request.case_id)) {
    $("target").value = result.request.case_id; showTarget();
    $("mode").value = result.request.input_mode || "repository-lean-reference-v1"; showMode();
    $("timeout").value = result.request.timeout_seconds;
    $("certificate").value = result.request.certificate || "";
  }
}
async function refreshHistory() {
  const result = await api("/v1/tasks");
  if (!result.tasks.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "还没有验证任务"; $("history").replaceChildren(p); return; }
  $("history").replaceChildren(...result.tasks.map(item => {
    const button = document.createElement("button"); button.type = "button";
    button.classList.toggle("selected", item.task_id === selected);
    button.append(document.createTextNode(item.case_id));
    const small = document.createElement("small"); small.textContent = `${states[item.state]} · ${item.task_id.slice(-8)}`; button.append(small);
    button.addEventListener("click", () => run(() => loadTask(item.task_id, true))); return button;
  }));
}
async function run(action) { try { error(""); await action(); } catch (failure) { error(failure.message); } }
$("submit-form").addEventListener("submit", event => {event.preventDefault(); run(async () => {
  const current = target(), confirmed = $("confirmed").checked, reviewer = $("reviewer").value.trim();
  if (confirmed && !reviewer) throw new Error("请填写对齐确认者姓名。");
  if (reviewer && !confirmed) throw new Error("填写姓名后，请明确勾选语义对齐确认；也可清空姓名，仅运行内核检查。");
  const body = {request_id:requestId,case_id:current.case_id,input_mode:$("mode").value,timeout_seconds:Number($("timeout").value),claim_fingerprint:current.certificate_header.claim_fingerprint,formalization_fingerprint:current.certificate_header.formalization_fingerprint,certificate:$("mode").value === "kernel-expression-certificate-v1" ? $("certificate").value : null,alignment:confirmed ? {reviewer,confirmed:true} : null};
  $("submit").disabled = true;
  try {const result = await api("/v1/tasks",body); selected = result.task_id; location.hash = selected; showTask(result); requestId = crypto.randomUUID(); await refreshHistory();}
  finally {$("submit").disabled = false;}
});});
$("cancel").addEventListener("click", () => run(async () => {showTask(await api(`/v1/tasks/${selected}/cancel`,{})); await refreshHistory();}));
$("retry").addEventListener("click", () => run(async () => {showTask(await api(`/v1/tasks/${selected}/retry`,{attempt_id:task.latest_attempt_id,request_id:crypto.randomUUID()})); await refreshHistory();}));
$("review-again").addEventListener("click", () => run(async () => {await loadTask(selected,true); $("alignment-panel").open = true; $("reviewer").focus(); $("submit").textContent = "确认后提交新的验证任务 →";}));
$("evidence-button").addEventListener("click", () => run(async () => {const id = selected, attemptId = task.latest_attempt_id; const result = await api(`/v1/tasks/${id}/evidence`); if (id !== selected || attemptId !== task.latest_attempt_id) return; if (result.available && task.artifact_status !== "MATCHES_RECORDED_HASHES") return; $("evidence").textContent = JSON.stringify(result,null,2); $("evidence-details").open = true;}));
$("lookup-form").addEventListener("submit", event => {event.preventDefault(); run(() => loadTask($("lookup").value.trim(),true));});
$("target").addEventListener("change",showTarget); $("mode").addEventListener("change",showMode);
$("use-sample").addEventListener("click", () => {$("certificate").value = JSON.stringify(target().samples[Number($("sample").value)].certificate,null,2); requestId = crypto.randomUUID();});
$("submit-form").addEventListener("input", () => {requestId = crypto.randomUUID();});
$("refresh").addEventListener("click", () => run(refreshHistory));
async function start() {
  session = await api("/v1/session");
  $("target").replaceChildren(...session.targets.map(item => new Option(item.claim.original_statement,item.case_id)));
  $("worker-state").textContent = session.worker.state === "EXTERNAL" ? "外部 Worker" : "队列已连接";
  showTarget(); showMode(); await refreshHistory();
  if (location.hash.startsWith("#task-")) await loadTask(location.hash.slice(1),true);
  setInterval(async () => {
    if (pollBusy || document.hidden) return; pollBusy = true;
    try {if (selected) {const id = selected, result = await api("/v1/tasks/" + id); if (selected === id) showTask(result);} await refreshHistory();}
    catch (failure) {error(failure.message);} finally {pollBusy = false;}
  },1500);
}
run(start);
