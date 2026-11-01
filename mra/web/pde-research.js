"use strict";
(() => {
  const $ = id => document.getElementById(id), esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const api = '/v1/pde-research', transport = window.MRATypedTransport;
  let epoch=0, plan=null, report=null, graphId=null, nodePreview=null, examples=[], pending=false, timer=null, reading=null, withdrawNode=null, lastWrite=null;
  const uid = () => 'pde-web-' + crypto.randomUUID().replaceAll('-','');
  const context = () => ({project_id:$('ar-project').value,branch_id:$('ar-branch').value});
  const unbound = p => (p.request.sources||[]).some(s => /^(pdelang|pdewatched)-0{32}$/.test(s.run_id||''));
  const active = row => row && row.attempts.some(a => ['QUEUED','RUNNING'].includes(a.state) && !a.invalidated);
  function message(text, kind='info') {const box=$('ar-message');box.textContent=text;box.dataset.kind=kind;box.hidden=!text;}
  function clearCheck() {nodePreview=null;$('ar-check').hidden=true;$('ar-confirm').checked=false;$('ar-run').disabled=true;}
  function clearEvidence() {report=null;clearCheck();$('ar-result').textContent='尚未取得本次核对结果';$('ar-nodes').replaceChildren();$('ar-history').replaceChildren();$('ar-export').disabled=true;}
  function clearPlan() {plan=null;$('ar-save').disabled=true;$('ar-plan').replaceChildren();clearCheck();}
  function reset() {epoch++;clearTimeout(timer);withdrawNode=null;if($('ar-withdraw-dialog').open)$('ar-withdraw-dialog').close();graphId=null;clearEvidence();clearPlan();$('ar-work').hidden=true;$('ar-editor').hidden=false;}
  function syncRun() {$('ar-run').disabled=!nodePreview||!$('ar-confirm').checked||!$('ar-reviewer').value.trim();}
  function request(route, body, token=epoch) {
    if(body?.request_id)lastWrite={route,request_id:body.request_id};
    return transport.request(route,body,{isCurrent:()=>epoch===token,onBusy:()=>{if(token===epoch){clearEvidence();clearPlan();message('证据正在核对，暂时隐藏旧判断；只重读结果，不重新执行数学。');}}});
  }
  async function action(fn) {
    if(pending)return;
    pending=true;lastWrite=null;document.body.dataset.analysisPending='true';
    try{message('');await fn();}catch(error){if(error.name!=='AbortError')message(error.message+(lastWrite?' 原写请求编号：'+lastWrite.request_id+'。':'')+' 若写请求未收到响应，请刷新原项目任务记录核对；系统不会自动重发。','error');}
    finally{pending=false;delete document.body.dataset.analysisPending;}
  }
  function nodePlan(p,id) {const node=p.request.nodes.find(n=>n.id===id);return {request:node.target,...p.graph_compilation.node_plans[id]};}
  function statement(p) {
    return '<p>'+esc(p.request.original)+'</p>'+sourceRows(p)+p.conditions.map(c=>'<div class="ar-condition">条件 '+esc(c.name)+'：'+esc(c.formal_statement)+'</div>').join('')+'<details><summary>完整 Lean 命题 · '+esc(p.request.timeout_seconds)+' 秒共享检查预算</summary><pre>'+esc(p.formal_statement)+'</pre></details>';
  }
  function sourceRows(p) {return (p.request.sources||[]).map(s=>'<p class="pr-source">证明引用 · '+esc(s.name)+' ← '+esc(s.node_id||(/^(pdelang|pdewatched)-0{32}$/.test(s.run_id||'')?'尚未绑定实际来源':s.run_id))+(s.type_fingerprint?' · 类型 '+esc(s.type_fingerprint):' · 待前置节点完成后绑定实际证据')+'</p>').join('');}
  function draft() {
    try {const value=JSON.parse($('ar-json').value);if(Object.keys(value).length!==1||!Array.isArray(value.nodes))throw new Error('完整请求需包含 nodes 数组。');
      $('ar-draft-nodes').innerHTML=value.nodes.map(n=>'<article class="ar-node"><div class="ar-node-title"><h3>'+esc(n.target?.title||n.id)+'</h3><span class="ar-pill">'+(n.kind==='problem'?'原问题':'前置节点')+'</span></div><p>'+esc(n.target?.original)+'</p><p>原条件 '+esc(n.target?.assumptions?.length??0)+' 项 · 前置 '+esc((n.depends_on||[]).join('、')||'无')+'</p></article>').join('');
    } catch(error){$('ar-draft-nodes').textContent='请求尚未形成有效的节点结构，请检查展开的编辑内容。';}
  }
  function chooseExample() {const example=examples.find(e=>e.id===$('ar-example').value);if(!example)return;$('ar-json').value=JSON.stringify({nodes:example.nodes},null,2);clearPlan();draft();}
  async function projects(selected) {
    const token=epoch, value=await request('/v1/research',undefined,token);if(token!==epoch)return;
    $('ar-project').innerHTML='<option value="">请选择或新建研究项目</option>'+value.projects.map(p=>'<option value="'+esc(p.project_id)+'">'+esc(p.title)+'</option>').join('');
    if(selected)$('ar-project').value=selected;
  }
  async function loadContext() {
    reset();const token=epoch, key=$('ar-project').value;$('ar-tasks').textContent='请选择研究项目。';
    if(!key)return;
    const p=await request('/v1/research/'+encodeURIComponent(key),undefined,token);if(token!==epoch)return;
    $('ar-branch').innerHTML=Object.values(p.branches).map(b=>'<option value="'+esc(b.branch_id)+'">'+esc(b.label||b.branch_id)+'</option>').join('');
    await listTasks(token);
  }
  async function listTasks(token=epoch) {
    const ctx=context();if(!ctx.project_id)return;
    const data=await request(api+'/projects/'+encodeURIComponent(ctx.project_id)+'/'+encodeURIComponent(ctx.branch_id),undefined,token);if(token!==epoch)return;
    $('ar-tasks').innerHTML=data.graphs.length?data.graphs.map(g=>'<button type="button" class="ar-task '+(g.graph_id===graphId?'is-active':'')+'" data-graph="'+esc(g.graph_id)+'">'+esc(g.title)+'<small>'+esc(g.nodes)+' 个完整目标 · '+(g.invalidated?'已失效':'已保存')+'</small></button>').join(''):'<p class="ar-muted">本分支还没有分析任务。先预览并保存一个完整目标图。</p>';
  }
  function render() {
    if(!report)return;
    $('ar-work').hidden=false;$('ar-editor').hidden=true;$('ar-export').disabled=false;
    $('ar-work-title').textContent=nodePlan(report.plan,report.plan.problem_id).request.title;
    $('ar-result').innerHTML='<strong>'+ (report.math_verdict==='PROVED'?'原问题已证明':'原问题尚未知') +'</strong><span>已预留 '+esc(report.checks_reserved)+' 次检查 / '+esc(report.worker_seconds_reserved)+' worker 秒</span>';
    const nodeById=Object.fromEntries(report.plan.request.nodes.map(n=>[n.id,n]));
    $('ar-nodes').innerHTML=report.plan.order.map(id=>{
      const n=nodeById[id],s=report.node_states[id],p=nodePlan(report.plan,id);
      const latest=report.attempts.filter(a=>a.node_id===id).slice(-1)[0];
      const child=latest&&report.checks[latest.attempt_id],diag=child?.result?.diagnostic;
      return '<article class="ar-node"><div class="ar-node-title"><h3>'+esc(n.kind==='problem'?'原问题 · ':'引理 · ')+esc(p.request.title)+'</h3><span class="ar-state" data-verdict="'+esc(s.math_verdict)+'">'+esc(unbound(p)?'SOURCE_REQUIRED':s.status)+' / '+esc(s.math_verdict)+'</span></div>'+statement(p)+(s.blocked_by.length?'<p>等待当前已证前置：'+esc(s.blocked_by.join('、'))+'</p>':'')+(diag?'<details><summary>上次候选检查诊断（不构成反证）</summary><pre>'+esc(diag.text)+'</pre></details>':'')+'<div class="ar-actions">'+(!unbound(p)&&!report.invalidated&&s.math_verdict!=='PROVED'&&!s.blocked_by.length&&!active(report)?'<button type="button" class="ar-btn ar-primary" data-preview-node="'+esc(id)+'">核对并检查此节点</button>':'')+(latest&&['QUEUED','RUNNING'].includes(latest.state)&&!latest.invalidated?'<button type="button" class="ar-btn" data-cancel="'+esc(latest.attempt_id)+'">取消本次检查</button>':'')+(s.math_verdict==='PROVED'?'<button type="button" class="ar-link" data-withdraw="'+esc(id)+'">撤销此节点证据</button>':'')+'</div></article>';
    }).join('');
    $('ar-history').innerHTML=derivation(report.plan)+'<details class="ar-advanced"><summary>全部 '+esc(report.attempts.length)+' 次执行记录与来源</summary><pre>'+esc(JSON.stringify(report.attempts,null,2))+'</pre></details>';
    clearTimeout(timer);
    if(active(report)){showProgress(report);scheduleProgress();}
  }
  function scheduleProgress() {
    clearTimeout(timer);
    if(graphId)timer=setTimeout(()=>{
      if(pending||document.hidden||reading){scheduleProgress();return;}
      void pollProgress().catch(e=>message(e.message,'error'));
    },3000);
  }
  function showProgress(value) {
    clearCheck();$('ar-export').disabled=true;
    const attempts=value.attempts.filter(a=>['QUEUED','RUNNING'].includes(a.state)&&!a.invalidated);
    $('ar-result').innerHTML='<strong>正在检查，数学结论等待完整证据复核</strong>'+attempts.map(a=>'<span>'+esc(a.node_id)+' · '+esc(a.state)+(a.cancel_requested?' · 正在取消':' <button type="button" class="ar-btn" data-progress-cancel="'+esc(a.attempt_id)+'">取消本次检查</button>')+'</span>').join('');
    $('ar-nodes').querySelectorAll('.ar-state').forEach(el=>{el.textContent='证据待复核';el.dataset.verdict='UNKNOWN';});
    $('ar-nodes').querySelectorAll('button').forEach(el=>{el.disabled=true;});
    $('ar-history').textContent='本次执行状态：'+attempts.map(a=>a.node_id+' / '+a.state).join('；');
  }
  async function pollProgress() {
    if(!graphId)return;
    const key=graphId,token=epoch;
    if(reading){scheduleProgress();return;}
    const marker={key,token};reading=marker;
    try {
      const value=await request(api+'/'+encodeURIComponent(key)+'/progress',undefined,token);
      if(token!==epoch||key!==graphId)return;
      if(value.evidence_checked!==false)throw new Error('执行进度响应格式错误。');
      if(active(value)){showProgress(value);scheduleProgress();}
      else {if(reading===marker)reading=null;await refresh();}
    } catch(error) {
      if(token===epoch&&key===graphId){clearEvidence();message('进度读取未完成，数学判断已隐藏。'+error.message,'error');scheduleProgress();}
      throw error;
    } finally {if(reading===marker)reading=null;}
  }
  async function refresh() {
    if(!graphId)return;const key=graphId,token=epoch;if(reading&&reading.key===key&&reading.token===token)return;const marker={key,token};reading=marker;clearCheck();
    try {const value=await request(api+'/'+encodeURIComponent(key),undefined,token);if(token!==epoch||key!==graphId)return;report=value;render();message('已读取任务账本；逐节点执行真实检查，原问题的判断单独列出。');}
    catch(error){if(token===epoch){clearEvidence();message('本次证据读取未完成，旧判断已隐藏。'+error.message,'error');}throw error;}
    finally {if(reading===marker)reading=null;}
  }
  async function openGraph(key) {reset();graphId=key;await refresh();}
  $('ar-project').addEventListener('change',()=>void loadContext().catch(e=>message(e.message,'error')));
  $('ar-branch').addEventListener('change',()=>{reset();void listTasks().catch(e=>message(e.message,'error'));});
  $('ar-new-project').addEventListener('click',()=>{$('ar-project-form').hidden=!$('ar-project-form').hidden;});
  $('ar-project-form').addEventListener('submit',event=>{event.preventDefault();void action(async()=>{const token=epoch;const value=await request('/v1/research',{request_id:uid(),title:$('ar-project-title').value,question:$('ar-project-question').value},token);if(token!==epoch)return;await projects(value.project_id);$('ar-project-form').hidden=true;await loadContext();});});
  $('pr-seed-bind').addEventListener('click',()=>void action(async()=>{clearPlan();const token=epoch,raw=JSON.parse($('ar-json').value),id=$('pr-seed-id').value.trim(),node=raw.nodes.find(n=>n.id==='ibp');if(!node)throw new Error('当前草稿没有分部积分节点。');const value=await request(api+'/source-preview',{run_id:id},token);if(token!==epoch)return;node.target.sources=[{name:'ibp',run_id:value.run_id,type_fingerprint:value.type_fingerprint}];epoch++;$('ar-json').value=JSON.stringify(raw,null,2);draft();$('pr-seed-details').textContent=value.formal_statement;message('已将确切来源写入草稿，请重新预览全部目标。');}));
  $('ar-example').addEventListener('change',()=>{epoch++;chooseExample();});
  $('ar-json').addEventListener('input',()=>{epoch++;clearPlan();draft();});
  $('ar-new-task').addEventListener('click',()=>{reset();chooseExample();});
  $('ar-refresh-list').addEventListener('click',()=>void action(()=>listTasks()));
  async function previewTask(expanded=false) {
    clearPlan();const token=epoch,ctx=context();if(!ctx.project_id)throw new Error('请先选择研究项目。');
    const input=JSON.parse($('ar-json').value);if(Object.keys(input).length!==1||!input.nodes)throw new Error('编辑区只填写完整 nodes。');
    const value=await request(api+(expanded?'/expanded-preview':'/preview'),{...ctx,nodes:input.nodes},token);if(token!==epoch)return;
    plan=value;$('ar-save').disabled=false;
    $('ar-plan').innerHTML='<h3>'+ (expanded?'核对合并后的完整原问题':'逐项核对完整命题') +'</h3>'+plan.order.map(id=>'<article class="ar-node"><h3>'+esc(id)+'</h3>'+statement(nodePlan(plan,id))+'</article>').join('')+derivation(plan);
    if(expanded)message('已将推导展开为一次完整检查计划。保存后仍需具名确认；中间目标尚未单独验证。');
  }
  function derivation(p) {
    return p.expansion?'<details class="ar-advanced"><summary>保留的 '+esc(p.expansion.steps.length)+' 步原始推导 · 中间目标未单独验证</summary>'+p.expansion.steps.map(step=>'<p>'+esc(step.id)+' · UNKNOWN / E0</p><pre>'+esc(step.formal_statement)+'</pre>').join('')+'</details>':'';
  }
  $('ar-preview').addEventListener('click',()=>void action(()=>previewTask()));
  $('pr-expand').addEventListener('click',()=>void action(()=>previewTask(true)));
  $('ar-save').addEventListener('click',()=>void action(async()=>{if(!plan)throw new Error('请先重新预览全部目标。');const token=epoch,expanded=!!plan.expansion,raw={request_id:uid(),request:expanded?{...plan.request,nodes:plan.expansion.source_graph.nodes}:plan.request,plan_fingerprint:plan.plan_fingerprint};$('ar-save').disabled=true;const value=await request(api+(expanded?'/expanded':''),raw,token);if(token!==epoch)return;graphId=value.graph_id;report=value;plan=null;clearCheck();render();await listTasks(token);}));
  $('ar-tasks').addEventListener('click',event=>{const button=event.target.closest('[data-graph]');if(button)void action(()=>openGraph(button.dataset.graph));});
  $('ar-refresh').addEventListener('click',()=>void action(refresh));
  $('ar-result').addEventListener('click',event=>{const button=event.target.closest('[data-progress-cancel]');if(button)void action(async()=>{const key=graphId,token=epoch;const value=await request(api+'/'+key+'/cancel',{attempt_id:button.dataset.progressCancel},token);if(token!==epoch||key!==graphId)return;report=value;render();});});
  $('ar-nodes').addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;
    if(button.dataset.previewNode)void action(async()=>{clearCheck();const key=graphId,token=epoch;const value=await request(api+'/'+key+'/node-preview',{node_id:button.dataset.previewNode},token);if(token!==epoch||key!==graphId)return;nodePreview=value;syncRun();$('ar-check-details').innerHTML=statement(value.child_plan)+'<p class="ar-muted">已绑定 '+esc(Object.keys(value.prerequisites).length)+' 项确切前置证据；此处尚未执行 Lean。</p>';$('ar-check').hidden=false;$('ar-check').scrollIntoView({behavior:'smooth',block:'start'});});
    if(button.dataset.cancel)void action(async()=>{const key=graphId,token=epoch;clearCheck();const value=await request(api+'/'+key+'/cancel',{attempt_id:button.dataset.cancel},token);if(token!==epoch||key!==graphId)return;report=value;render();});
    if(button.dataset.withdraw){withdrawNode=button.dataset.withdraw;$('ar-withdraw-confirm').checked=false;$('ar-withdraw-dialog').showModal();}
  });
  $('ar-reviewer').addEventListener('input',()=>{$('ar-confirm').checked=false;syncRun();});
  $('ar-confirm').addEventListener('change',syncRun);
  $('ar-run-form').addEventListener('submit',event=>{event.preventDefault();void action(async()=>{if(!nodePreview||!$('ar-confirm').checked)throw new Error('请重新核对并确认本次完整目标。');const value=nodePreview,key=graphId,token=epoch;const payload={request_id:uid(),node_id:value.node_id,execution_fingerprint:value.execution_fingerprint,reviewer:$('ar-reviewer').value,confirmed:true};clearCheck();await request(api+'/'+key+'/submit',payload,token);if(token===epoch&&key===graphId){showProgress({attempts:[]});await pollProgress();}});});
  $('ar-withdraw-close').addEventListener('click',()=>{$('ar-withdraw-dialog').close();});
  $('ar-withdraw-form').addEventListener('submit',event=>{event.preventDefault();void action(async()=>{if(!withdrawNode||!$('ar-withdraw-confirm').checked)throw new Error('请明确确认撤销。');const key=graphId,token=epoch;const payload={node_id:withdrawNode,reviewer:$('ar-withdraw-reviewer').value,reason:$('ar-withdraw-reason').value,confirmed:true};$('ar-withdraw-dialog').close();clearCheck();const value=await request(api+'/'+key+'/withdraw',payload,token);if(token!==epoch||key!==graphId)return;report=value;render();});});
  $('ar-export').addEventListener('click',()=>{if(!report)return;const blob=new Blob([JSON.stringify(report,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=graphId+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  window.addEventListener('pagehide',()=>clearTimeout(timer));
  void action(async()=>{await window.MRA.session;const value=await request(api+'/catalog');examples=value.examples;$('ar-example').innerHTML=examples.map(e=>'<option value="'+esc(e.id)+'">'+esc(e.title)+'</option>').join('');chooseExample();const link=location.hash.slice(1).split('/');await projects(link[0]||undefined);if(link[0]&&$('ar-project').value===link[0]){await loadContext();if(link[1])$('ar-branch').value=link[1];if(link[2])await openGraph(link[2]);}else $('ar-tasks').textContent='请选择研究项目。';});
})();
