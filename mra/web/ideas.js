"use strict";
const $ = id => document.getElementById(id);
let token = "", current = null, selected = null, polling = false, epoch = 0;
const stateLabels = {WAITING_FOR_MODEL:"等待模型配置",WAITING_FOR_REVIEW:"待语义确认",QUEUED:"排队中",RUNNING:"正在研究",COMPLETED:"本轮已结束",FAILED:"执行失败",CANCELLED:"已取消",REVOKED:"确认已撤销",NEEDS_CLARIFICATION:"待澄清",UNSUPPORTED:"超出支持范围"};
const active = state => ["QUEUED", "RUNNING"].includes(state);
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-MRA-CSRF":token}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function context() { return $("context").value.split("\n").map(s => s.trim()).filter(Boolean); }
function clearEvidence() { $("evidence").textContent = ""; $("evidence-panel").hidden = true; }
function notice(status) {
  $("model-notice").textContent = status.configured ? `模型已配置：${status.model}。输入将发送到 ${status.endpoint_host}；候选仍须人工确认与 Lean 验证。` : "尚未配置模型：可以保存新猜想，配置模型后再生成候选。当前不会调用模型，也不会自动得出数学结论。";
}
function render(data) {
  const changed = !current || current.idea_id !== data.idea_id || current.source.version !== data.source.version;
  const attemptsChanged = !current || JSON.stringify(current.attempts) !== JSON.stringify(data.attempts) || current.artifact_status !== data.artifact_status || current.state !== data.state;
  if (changed) { selected = null; $("confirmed").checked = false; $("reviewer").value = ""; }
  if (changed || attemptsChanged) clearEvidence();
  current = data;
  $("detail").hidden = false; $("empty").hidden = true; $("state").textContent = stateLabels[data.state] || data.state;
  window.history.replaceState(null, "", "/ideas#" + data.idea_id);
  const step = data.review ? 3 : data.state === "WAITING_FOR_REVIEW" ? 2 : data.state === "WAITING_FOR_MODEL" ? 0 : 1;
  for (let i = 0; i < 4; i++) $("flow-" + i)?.classList.toggle("is-current", i === step);
  $("idea-id").textContent = `${data.idea_id} · 原文版本 ${data.source.version}`;
  $("original").textContent = data.source.statement; $("source-context").textContent = data.source.context.join("；");
  $("questions").hidden = !data.questions.length; $("questions").textContent = data.questions.join("\n");
  const choices = $("candidates");
  const signature = JSON.stringify(data.candidates);
  if (changed || choices.dataset.signature !== signature) {
    choices.replaceChildren(); choices.dataset.signature = signature;
    data.candidates.forEach((candidate, i) => {
      const box = document.createElement("div"); box.className = "statement";
      const label = document.createElement("label"); label.className = "check-row";
      const radio = document.createElement("input"); radio.type = "radio"; radio.name = "candidate"; radio.value = candidate.target_fingerprint;
      radio.checked = i === 0; radio.disabled = data.state !== "WAITING_FOR_REVIEW";
      if (i === 0) selected = radio.value;
      radio.addEventListener("change", () => { selected = radio.value; $("confirmed").checked = false; clearEvidence(); });
      const text = document.createElement("span"); text.textContent = `候选 ${i + 1} · ${candidate.target.interpretation}`;
      label.append(radio, text); const formal = document.createElement("pre"); formal.textContent = candidate.formal_statement;
      box.append(label, formal); choices.append(box);
    });
  }
  choices.querySelectorAll("input").forEach(input => { input.disabled = data.state !== "WAITING_FOR_REVIEW"; });
  $("confirm-form").hidden = data.state !== "WAITING_FOR_REVIEW";
  $("verdict").textContent = data.math_verdict; $("level").textContent = data.evidence_level.split("_")[0];
  $("reason").textContent = data.error ? `运行状态：${data.error}。未据此判断命题真伪。` : data.result ? data.result.reason : data.artifact_status === "CHANGED_OR_MISSING" ? "检查工件发生变化，历史数学结论已停用。" : data.state === "COMPLETED" ? "有限搜索已结束，尚未取得可发布的验证证据。" : "候选和确认本身不构成数学证明。";
  $("metrics").textContent = `模型响应 ${data.model_calls} 次${data.simulation_calls ? ` · 协议模拟 ${data.simulation_calls} 次` : ""} · Lean 候选 ${data.attempts.length} 次 · 费用未估算`;
  $("cancel").hidden = !active(data.state); $("revoke").hidden = !data.review;
  $("revise").disabled = active(data.state);
  $("generate").hidden = active(data.state) || !!data.review || !data.model_status.configured;
  $("evidence-button").hidden = data.artifact_status !== "MATCHES_RECORDED_HASHES";
  $("events").replaceChildren(...data.events.map(event => { const item = document.createElement("li"); item.textContent = `${event.at} · ${event.kind}`; return item; }));
  $("attempts").textContent = data.attempts.map(attempt => `${attempt.number}. ${attempt.plan.direction} / ${attempt.plan.strategy}：${attempt.result.run_status} · ${attempt.result.reason}`).join("\n");
  notice(data.model_status);
}
async function refreshHistory() {
  const data = await api("/v1/ideas");
  $("history").replaceChildren(...data.ideas.map(idea => {
    const button = document.createElement("button"); button.textContent = idea.source.statement.slice(0, 42);
    const small = document.createElement("small"); small.textContent = `${stateLabels[idea.state] || idea.state} · v${idea.source.version}`; button.append(small);
    button.addEventListener("click", () => run(() => loadIdea(idea.idea_id))); return button;
  }));
  if (!data.ideas.length) { const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "尚无猜想记录。保存第一个想法，从这里继续研究。"; $("history").append(empty); }
}
function updateCount() { if ($("statement-count")) $("statement-count").textContent = `${$("statement").value.length} / 8000`; }
async function loadIdea(id) {
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(id)) throw new Error("猜想记录 ID 不正确。");
  const generation = ++epoch, report = await api(`/v1/ideas/${id}`);
  if (generation !== epoch) return;
  render(report); $("statement").value = report.source.statement; $("context").value = report.source.context.join("\n"); updateCount();
}
async function run(action) { try { error(""); await action(); } catch (e) { error(e.message); } }
async function action(name, body = {}) {
  if (!current) return;
  const id = current.idea_id, generation = epoch;
  const data = await api(`/v1/ideas/${id}/${name}`, body);
  if (generation === epoch && current.idea_id === id) render(data);
  await refreshHistory();
}
$("idea-form").addEventListener("submit", e => { e.preventDefault(); run(async () => {
  $("submit").disabled = true;
  const generation = ++epoch;
  try { const data = await api("/v1/ideas", {request_id:crypto.randomUUID(), statement:$("statement").value, context:context()}); if (generation === epoch) render(data); await refreshHistory(); }
  finally { $("submit").disabled = false; }
}); });
$("confirm-form").addEventListener("submit", e => { e.preventDefault(); run(() => action("confirm", {target_fingerprint:selected, source_version:current.source.version, reviewer:$("reviewer").value, confirmed:$("confirmed").checked})); });
$("generate").addEventListener("click", () => run(() => action("generate")));
$("cancel").addEventListener("click", () => run(() => action("cancel")));
$("revoke").addEventListener("click", () => run(() => action("revoke")));
$("revise").addEventListener("click", () => run(async () => {
  await action("revise", {expected_version:current.source.version, statement:$("statement").value, context:context()});
}));
$("refresh").addEventListener("click", () => run(refreshHistory));
$("evidence-button").addEventListener("click", () => run(async () => {
  const id = current.idea_id, generation = epoch, signature = JSON.stringify(current.attempts);
  clearEvidence(); const data = await api(`/v1/ideas/${id}/evidence`);
  if (generation !== epoch || current.idea_id !== id || JSON.stringify(current.attempts) !== signature || current.artifact_status !== "MATCHES_RECORDED_HASHES") return;
  $("evidence").textContent = JSON.stringify(data, null, 2); $("evidence-panel").hidden = false; $("evidence-panel").open = true;
}));
$("statement").addEventListener("input", updateCount);
document.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => { $("statement").value = button.dataset.example; updateCount(); $("statement").focus(); }));
$("idea-form").addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !$("submit").disabled) { event.preventDefault(); $("idea-form").requestSubmit(); } });
run(async () => {
  const session = await api("/v1/session"); token = session.csrf_token; notice(session.idea_model); await refreshHistory();
  if (location.hash.length > 1) await loadIdea(location.hash.slice(1));
  else { try { const prefill = sessionStorage.getItem("mra.idea.prefill"); if (prefill) { $("statement").value = prefill.slice(0,8000); sessionStorage.removeItem("mra.idea.prefill"); updateCount(); } } catch {} }
});
window.addEventListener("hashchange", () => { if (location.hash.length > 1) run(() => loadIdea(location.hash.slice(1))); });
setInterval(() => { if (polling || !current || !active(current.state)) return; polling = true;
  const id = current.idea_id, generation = epoch;
  run(async () => { const data = await api(`/v1/ideas/${id}`); if (generation === epoch && current.idea_id === id) render(data); await refreshHistory(); }).finally(() => { polling = false; });
}, 1000);
