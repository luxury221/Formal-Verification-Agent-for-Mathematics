"use strict";
(() => {
  if (location.pathname !== '/mathlib') return;
  const {escape:e, icon, session, modal, notify} = window.MRA;
  const $ = id => document.getElementById(id);
  let catalog, plan=null, epoch=0, requestId=crypto.randomUUID(), selected=null, timer, submitting=false;
  async function api(path, body) {
    const info=await session;
    const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MRA-CSRF':info.csrf_token},body:JSON.stringify(body)})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'本地实验暂不可用。');
    return result;
  }
  function error(err){$('ml-error').textContent=err.message;$('ml-error').hidden=false;}
  async function run(action){$('ml-error').hidden=true;try{await action();}catch(err){error(err);}}
  function invalidate(){++epoch;plan=null;requestId=crypto.randomUUID();$('ml-preview').hidden=true;$('ml-confirm').checked=false;$('ml-submit').disabled=true;}
  function loadExample(value){
    invalidate();$('ml-title').value=value.title;$('ml-original').value=value.original;$('ml-variables').value=value.variables.join(', ');
    for(const name of ['assumptions','goal','proof'])$('ml-'+name).value=JSON.stringify(value[name],null,2);
    $('ml-timeout').value=value.timeout_seconds;
  }
  function read(){return {schema:catalog.mode,title:$('ml-title').value,original:$('ml-original').value,variables:$('ml-variables').value.trim()?$ ('ml-variables').value.split(',').map(v=>v.trim()):[],assumptions:JSON.parse($('ml-assumptions').value),goal:JSON.parse($('ml-goal').value),proof:JSON.parse($('ml-proof').value),timeout_seconds:Number($('ml-timeout').value)};}
  function enable(){ $('ml-submit').disabled=!plan||!$('ml-confirm').checked||!$('ml-reviewer').value.trim()||submitting; }
  function displayPlan(value){
    plan=value;$('ml-preview').hidden=false;
    $('ml-full-statement').textContent=value.formal_statement;$('ml-source-quote').textContent=value.request.original;
    $('ml-proof-source').textContent=value.proof_source;$('ml-mapping').textContent=Object.entries(value.variable_mapping).map(([a,b])=>`${a} → ${b}`).join(' · ')||'闭合自然数命题';
    $('ml-citations').innerHTML=value.references.map(name=>`<span class="ml-citation">${e(name)}</span>`).join('')||'<span>未引用库引理</span>';
    $('ml-budget').textContent=`1 次候选检查 + 1 次独立重放，共享 ${value.budget.shared_worker_seconds} 秒 worker 时间。环境完整性核对另外计时。`;
    $('ml-fingerprint').textContent=value.plan_fingerprint;enable();
  }
  async function history(){
    const result=await api('/v1/mathlib/runs');
    $('ml-history').innerHTML=result.runs.length?result.runs.map(row=>`<button class="ml-history-row" data-ml-run="${e(row.run_id)}"><span><strong>${e(row.title)}</strong><small>${e(row.created_at.slice(0,19).replace('T',' '))}</small></span><span class="ml-result-pill ${row.math_verdict==='PROVED'?'is-proved':''}">${e(row.math_verdict)}<small>${e(row.state)}</small></span></button>`).join(''):'<p class="muted">还没有实验记录。先核对一个完整命题，再提交检查。</p>';
    document.querySelectorAll('[data-ml-run]').forEach(button=>button.addEventListener('click',()=>run(()=>inspect(button.dataset.mlRun))));
  }
  function renderResult(value){
    selected=value;const passed=value.math_verdict==='PROVED';
    $('ml-result').hidden=false;
    $('ml-result').innerHTML=`<div class="panel-heading"><div><span class="eyebrow">EXPERIMENT RECORD</span><h2>${e(value.plan.request.title)}</h2></div><span class="ml-result-pill ${passed?'is-proved':''}">${e(value.math_verdict)} / ${e(value.evidence_level)}</span></div><div class="ml-result-meta"><span>运行 <strong>${e(value.state)}</strong></span><span>阶段 <strong>${e(value.stage)}</strong></span><span>worker <strong>${Number(value.result?.worker_seconds||0).toFixed(2)} 秒</strong></span></div><pre class="ml-formula">${e(value.plan.formal_statement)}</pre><p class="hint">${value.invalidated?'确认或证据已停用，历史结论不再可用。':passed?'真实 Lean 检查与独立内核重放通过；具名对齐记录有效。当前显示的是已保存证据的核对结果。':value.state==='REJECTED'?'提交的证明被 Lean 拒绝。命题保持未知，失败不能作为反例。':['QUEUED','RUNNING'].includes(value.state)?'正在按已确认的完整目标执行。候选检查通过后仍需独立重放。':'本次运行没有形成可用证明；数学判断保持未知。'}</p><p class="task-id">${e(value.run_id)}</p><div class="ml-result-actions"><button class="button secondary" id="ml-reuse">带回编辑并重新核对</button><button class="button secondary" id="ml-export">${icon('down')} 导出记录</button>${['QUEUED','RUNNING'].includes(value.state)?'<button class="button secondary" id="ml-cancel">取消运行</button>':''}${!value.invalidated?'<button class="text-button" id="ml-revoke">撤销语义确认</button>':''}</div>${value.result?.error?`<details open><summary>真实检查反馈</summary><pre class="ml-log">${e(value.result.error)}</pre></details>`:''}<details><summary>证明工件与内核审计 · ${Object.keys(value.files).length} 个文件</summary><pre class="ml-log">${e(JSON.stringify({alignment:value.alignment,artifact_status:value.artifact_status,candidate:value.result?.candidate,replay:value.result?.replay,files:value.files},null,2))}</pre></details><details><summary>运行事件 · ${value.events.length}</summary><ol class="ml-events">${value.events.map(event=>`<li><strong>${e(event.kind)}</strong><small>${e(event.at)}</small>${event.reason?`<p>${e(event.reason)}</p>`:''}</li>`).join('')}</ol></details>`;
    $('ml-reuse').addEventListener('click',()=>{loadExample(value.plan.request);$('ml-editor').scrollIntoView({behavior:'smooth'});notify('已带回原始请求，请重新核对并具名确认。');});
    $('ml-export').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=value.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    if($('ml-cancel'))$('ml-cancel').addEventListener('click',()=>run(async()=>{await api('/v1/mathlib/runs/'+value.run_id+'/cancel',{});await inspect(value.run_id);await history();}));
    if($('ml-revoke'))$('ml-revoke').addEventListener('click',()=>{
      const dialog=modal('撤销本次语义确认',`<p>撤销后，本次实验的数学结论停用。原始证明工件与运行记录保留；需要新核对和新检查才能产生新结果。</p><form id="ml-revoke-form"><label>本地操作者<input id="ml-revoke-reviewer" required maxlength="200"></label><label>原因<textarea id="ml-revoke-reason" required maxlength="500"></textarea></label><label class="check-row"><input id="ml-revoke-confirm" type="checkbox" required><span>确认撤销本次语义对齐。</span></label><p class="error" id="ml-revoke-error" hidden></p><button class="button primary" type="submit">确认撤销</button></form>`);
      dialog.querySelector('form').addEventListener('submit',async event=>{event.preventDefault();const button=dialog.querySelector('[type=submit]');button.disabled=true;try{await api('/v1/mathlib/runs/'+value.run_id+'/revoke',{reviewer:$('ml-revoke-reviewer').value,reason:$('ml-revoke-reason').value,confirmed:$('ml-revoke-confirm').checked});dialog.close();await inspect(value.run_id);await history();}catch(err){$('ml-revoke-error').hidden=false;$('ml-revoke-error').textContent=err.message;}finally{button.disabled=false;}});
    });
  }
  async function inspect(id){
    clearTimeout(timer);const generation=++inspect.generation;
    const value=await api('/v1/mathlib/runs/'+encodeURIComponent(id));if(generation!==inspect.generation)return;
    renderResult(value);window.dispatchEvent(new CustomEvent('mra-mathlib-result',{detail:value}));window.history.replaceState(null,'','/mathlib#'+id);
    if(['QUEUED','RUNNING'].includes(value.state))timer=setTimeout(()=>run(async()=>{await inspect(id);await history();}),1200);
  }
  inspect.generation=0;
  async function main(){
    catalog=await api('/v1/mathlib/catalog');
    $('page').innerHTML=`<section class="page-heading"><div><div class="eyebrow">CONTROLLED LIBRARY · REAL KERNEL</div><h1>让库中的引理，参与下一步推演。</h1><p>从自然数整除出发，显式组合引理，再让 Lean 检查完整证明。</p></div><a class="button secondary" href="/library">${icon('graph')} 私有知识库</a></section><section class="ml-hero"><div><span class="ml-kicker">MATHLIB / NATURAL DIVISIBILITY</span><h2>定义清楚，引用准确。</h2><p>每个实参、每条前提，都留在完整命题里。</p><div class="ml-hero-tags"><span>Nat · 自然数</span><span>9 条受控引用</span><span>独立内核重放</span></div></div><div class="ml-hero-math" aria-hidden="true"><span>a ∣ b</span><span>b ∣ c</span><strong>⇒ &nbsp; a ∣ b+c</strong></div></section><div id="ml-error" class="error" role="alert" hidden></div><div class="ml-layout"><div class="ml-main"><section class="panel ml-editor" id="ml-editor"><div class="panel-heading"><div><span class="eyebrow">01 / STATEMENT & TERMS</span><h2>构造一个可检查的命题</h2></div><span class="draft-badge">人工结构化实验</span></div><label for="ml-example">从示例开始<select id="ml-example">${catalog.examples.map((item,i)=>`<option value="${i}">${e(item.title)}</option>`).join('')}</select></label><div class="ml-fields"><label>标题<input id="ml-title" maxlength="160"></label><label>全称变量 · 逗号分隔<input id="ml-variables" placeholder="a, b, c" maxlength="150"></label></div><label>原始陈述<textarea id="ml-original" rows="3" maxlength="12000"></textarea></label><details class="ml-help"><summary>结构化输入说明</summary><p>表达式用 var / nat / add / mul / sub；公式用 dvd / eq / le / and / or / iff。证明用 ref、terms、proofs，假设用 hyp；pair 构造合取，mp / mpr 应用等价式。引用必须填写全部自然数参数和证明前提。</p><p>参数上限：6 个变量、6 条假设、240 个节点、深度 14。只支持自然数；减法在零处截断。</p></details><label>完整假设列表 · JSON<textarea class="ml-code" id="ml-assumptions" rows="7" spellcheck="false"></textarea></label><label>结论 · JSON<textarea class="ml-code" id="ml-goal" rows="6" spellcheck="false"></textarea></label><label>显式证明项 · JSON<textarea class="ml-code" id="ml-proof" rows="12" spellcheck="false"></textarea></label><div class="ml-editor-actions"><label>共享 worker 预算（秒）<input id="ml-timeout" type="number" min="1" max="30" value="30"></label><button class="button primary" id="ml-prepare">核对完整公式 ${icon('arrow')}</button></div></section><section class="panel ml-preview" id="ml-preview" hidden><span class="eyebrow">02 / REVIEW THE COMPLETE CLAIM</span><h2>确认系统检查的是你的原意。</h2><blockquote id="ml-source-quote"></blockquote><p class="hint" id="ml-mapping"></p><pre class="ml-formula" id="ml-full-statement"></pre><div class="ml-citations" id="ml-citations"></div><details><summary>确切 Lean 证明项</summary><pre class="ml-log" id="ml-proof-source"></pre></details><p class="hint" id="ml-budget"></p><label>语义核对者 · 本地具名声明<input id="ml-reviewer" maxlength="200" autocomplete="name"></label><label class="check-row"><input id="ml-confirm" type="checkbox"><span>我已核对原文、自然数域、所有变量和前提、结论、引理实参及预算。</span></label><button class="button primary" id="ml-submit" disabled>${icon('verify')} 提交真实 Lean 检查</button><p class="task-id" id="ml-fingerprint"></p></section><section class="panel ml-result" id="ml-result" aria-live="polite" hidden></section></div><aside class="ml-side"><section class="panel ml-definition"><span class="eyebrow">THE DEFINITION</span><h2>整除，确切意味着什么？</h2><div class="ml-definition-formula">a ∣ b ⇔ ∃ k ∈ ℕ, b = a·k</div><p>零整除 b 仅当 b=0；每个自然数都整除零。减法使用自然数截断规则。</p><span class="draft-badge">Lean ${e(catalog.lean_version)} · Apache-2.0</span><details><summary>固定版本与定义来源</summary><code>${e(catalog.definition.source)}</code><p class="task-id">Mathlib ${e(catalog.mathlib_commit)}</p></details></section><section class="panel ml-reference-panel"><div class="panel-heading"><h2>可引用的引理</h2><span class="draft-badge">9</span></div><p class="hint">展开查看完整类型和实参。条目来自已封存的库，实验结果仍需真实检查。</p>${catalog.references.map(ref=>`<details class="ml-reference"><summary><strong>${e(ref.title)}</strong><small>${e(ref.origin==='mathlib'?'Mathlib':'Lean core')}</small></summary><code>${e(ref.name)}</code><pre>${e(ref.type)}</pre><p>${ref.nat_arguments} 个 Nat 实参 · ${ref.proof_arguments} 个证明实参</p><small>${e(ref.source)}</small></details>`).join('')}</section><section class="panel ml-history-panel"><div class="panel-heading"><h2>最近实验</h2><button class="text-button" id="ml-refresh">刷新</button></div><div id="ml-history"></div></section><div class="ml-scope-note">当前支持自然数整除的人工证明项实验。真实结果可具名纳入研究项目与研究线；也可保存到私有知识库，检索完整命题并带回重新检查。</div></aside></div>`;
    for(const id of ['title','original','variables','assumptions','goal','proof','timeout'])$('ml-'+id).addEventListener('input',invalidate);
    $('ml-example').addEventListener('change',()=>loadExample(catalog.examples[Number($('ml-example').value)]));
    $('ml-reviewer').addEventListener('input',()=>{$('ml-confirm').checked=false;enable();});$('ml-confirm').addEventListener('change',enable);
    $('ml-prepare').addEventListener('click',()=>run(async()=>{invalidate();const version=epoch,body=read();$('ml-prepare').disabled=true;try{const value=await api('/v1/mathlib/preview',body);if(version===epoch){displayPlan(value);$('ml-preview').scrollIntoView({behavior:'smooth',block:'start'});}}finally{$('ml-prepare').disabled=false;}}));
    $('ml-submit').addEventListener('click',()=>run(async()=>{if(!plan||submitting||!$('ml-confirm').checked)return;submitting=true;enable();try{const result=await api('/v1/mathlib/runs',{request_id:requestId,request:plan.request,plan_fingerprint:plan.plan_fingerprint,reviewer:$('ml-reviewer').value.trim(),confirmed:true});invalidate();await inspect(result.run_id);await history();$('ml-result').scrollIntoView({behavior:'smooth',block:'start'});}finally{submitting=false;enable();}}));
    $('ml-refresh').addEventListener('click',()=>run(history));loadExample(catalog.examples[0]);await history();
    window.addEventListener('hashchange',()=>run(async()=>{const id=location.hash.slice(1);if(id.startsWith('mathlib-'))await inspect(id);else{clearTimeout(timer);++inspect.generation;selected=null;$('ml-result').hidden=true;}}));
    const id=location.hash.slice(1);if(id.startsWith('mathlib-'))await inspect(id);
  }
  main().catch(err=>{const box=document.createElement('p');box.className='error';box.textContent=err.message;$('page').replaceChildren(box);});
})();
