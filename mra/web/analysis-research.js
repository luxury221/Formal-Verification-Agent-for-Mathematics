"use strict";
(() => {
  const $ = id => document.getElementById(id), esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const api = location.pathname === '/proof' ? '/v1/proof-research' : '/v1/calculus-research', transport = window.MRATypedTransport;
  let epoch=0, plan=null, report=null, graphId=null, nodePreview=null, examples=[], pending=false, timer=null, reading=null, withdrawNode=null, lastWrite=null;
  const uid = () => 'analysis-' + crypto.randomUUID().replaceAll('-','');
  const context = () => ({project_id:$('ar-project').value,branch_id:$('ar-branch').value});
  const active = row => row && row.attempts.some(a => ['QUEUED','RUNNING'].includes(a.state) && !a.invalidated);
  function message(text, kind='info') {const box=$('ar-message');box.textContent=text;box.dataset.kind=kind;box.hidden=!text;}
  function clearCheck() {nodePreview=null;$('ar-check').hidden=true;$('ar-confirm').checked=false;$('ar-run').disabled=true;}
  function clearEvidence() {report=null;clearCheck();$('ar-result').textContent='尚未取得本次核对结果';$('ar-nodes').replaceChildren();$('ar-history').replaceChildren();$('ar-export').disabled=true;}
  function clearPlan() {plan=null;$('ar-save').disabled=true;$('ar-plan').replaceChildren();clearCheck();}
  function reset() {epoch++;clearTimeout(timer);withdrawNode=null;if($('ar-withdraw-dialog').open)$('ar-withdraw-dialog').close();graphId=null;clearEvidence();clearPlan();message('');$('ar-work').hidden=true;$('ar-editor').hidden=false;return epoch;}
  function syncRun() {$('ar-run').disabled=!nodePreview||!$('ar-confirm').checked||!$('ar-reviewer').value.trim();}
  function request(route, body, token=epoch) {
    if(body?.request_id)lastWrite={route,request_id:body.request_id};
    return transport.request(route,body,{isCurrent:()=>token===null||epoch===token,onBusy:()=>{if(token===epoch){clearEvidence();clearPlan();message('证据正在核对，暂时隐藏旧判断；只重读结果，不重新执行数学。');}}});
  }
  async function action(fn) {
    if(pending)return;
    const token=epoch;pending=true;lastWrite=null;document.body.dataset.analysisPending='true';
    try{message('');await fn();}catch(error){if(token===epoch&&error.name!=='AbortError')message(error.message+(lastWrite?' 原写请求编号：'+lastWrite.request_id+'。若未收到响应，请刷新原项目任务记录核对；系统不会自动重发。':''),'error');}
    finally{pending=false;delete document.body.dataset.analysisPending;}
  }
  function statement(p) {
    const version=p.mode==='proof-language-v4'?'证明语言 v4':p.mode==='proof-language-v3'?'证明语言 v3':p.mode==='proof-language-v2'?'证明语言 v2':p.mode==='proof-language-v1'?'证明语言 v1':'';
    return '<p class="mra-math">'+esc(p.request.original)+'</p>'+(version?'<p class="ar-muted">'+version+' · 保留本次确认的版本</p>':'')+(p.mode==='proof-language-v4'?'<p class="ar-muted">有限维ℓp表达式显式指定指数与坐标；范数语义及实幂前提须逐项核对，反例特例与原命题结论分别记录。</p>':'')+(p.mode==='proof-language-v3'?'<p class="ar-muted">最小值同时检查实际取得与全局下界；驻点本身不代表极小值。</p>':'')+p.conditions.map(c=>'<div class="ar-condition">条件 '+esc(c.name)+'：'+esc(c.formal_statement)+'</div>').join('')+'<details><summary>完整 Lean 命题 · '+esc(p.request.timeout_seconds)+' 秒共享检查预算</summary><pre>'+esc(p.formal_statement)+'</pre></details>'+(p.proof_steps?'<details><summary>证明步骤 · '+esc(p.proof_steps.length)+' 项</summary><ol>'+p.proof_steps.map(s=>'<li><strong>'+esc({have:'证明局部引理',rewrite:'显式等式改写',normalize:'按显式等式化简',rule:'应用受控定理',arithmetic:'代数推导',exists_intro:'给出存在证人',exists_elim:'展开存在证明',or_cases:'分别证明两种情况',by_cases:'按命题分情况',finite_compute:'有限坐标精确计算',finite_simplify:'按显式等式化简有限表达式',coordinate_eq:'取坐标等式',rational_power:'核查有理实幂根证书'}[s.kind]||s.kind)+'</strong> '+esc(s.name||s.method||'')+(s.formal_statement?'<pre>'+esc(s.formal_statement)+'</pre>':'')+(s.equalities?'<pre>'+esc(JSON.stringify(s.equalities,null,2))+'</pre>':'')+'</li>').join('')+'</ol><details><summary>完整证明程序</summary><pre>'+esc(p.proof_source)+'</pre></details></details>':'');
  }
  function draft() {
    try {const value=JSON.parse($('ar-json').value);if(Object.keys(value).length!==1||!Array.isArray(value.nodes))throw new Error('完整请求需包含 nodes 数组。');
      $('ar-draft-nodes').innerHTML=value.nodes.map(n=>'<article class="ar-node"><div class="ar-node-title"><h3>'+esc(n.target?.title||n.id)+'</h3><span class="ar-pill">'+(n.kind==='problem'?'原问题':'前置节点')+'</span></div><p class="mra-math">'+esc(n.target?.original)+'</p><p>原条件 '+esc(n.target?.assumptions?.length??0)+' 项 · 前置 '+esc((n.depends_on||[]).join('、')||'无')+'</p></article>').join('');
    } catch(error){$('ar-draft-nodes').textContent='请求尚未形成有效的节点结构，请检查展开的编辑内容。';}
  }
  function chooseExample() {const example=examples.find(e=>e.id===$('ar-example').value);if(!example)return;$('ar-json').value=JSON.stringify({nodes:example.nodes},null,2);clearPlan();draft();}
  async function projects(selected,token=epoch) {
    const value=await request('/v1/research',undefined,token);if(token!==null&&token!==epoch)return;
    $('ar-project').innerHTML='<option value="">请选择或新建研究项目</option>'+value.projects.map(p=>'<option value="'+esc(p.project_id)+'">'+esc(p.title)+'</option>').join('');
    if(selected)$('ar-project').value=selected;
  }
  function hash(graph=graphId) {
    const ctx=context(),parts=ctx.project_id?[ctx.project_id,ctx.branch_id]:[];
    if(graph)parts.push(graph);
    history.replaceState(null,'',location.pathname+location.search+(parts.length?'#'+parts.map(encodeURIComponent).join('/'):''));
  }
  function navigationError(error,token) {if(token===epoch&&error.name!=='AbortError')message(error.message,'error');}
  async function loadContext(branch='',graph='',token=reset()) {
    const key=$('ar-project').value;$('ar-tasks').textContent=key?'正在读取本分支任务…':'请选择研究项目。';
    $('ar-branch').replaceChildren();
    if(!key){hash();return;}
    const p=await request('/v1/research/'+encodeURIComponent(key),undefined,token);if(token!==epoch)return;
    $('ar-branch').innerHTML=Object.values(p.branches).map(b=>'<option value="'+esc(b.branch_id)+'">'+esc(b.label||b.branch_id)+'</option>').join('');
    if(branch){$('ar-branch').value=branch;if($('ar-branch').value!==branch){$('ar-tasks').textContent='指定分支不存在，请重新选择。';throw new Error('链接中的研究分支不存在，未读取其他分支的证据。');}}
    else if(p.branches.main)$('ar-branch').value='main';
    const data=await listTasks(token);if(token!==epoch||!data)return;
    if(graph){
      if(!data.graphs.some(g=>g.graph_id===graph))throw new Error('链接中的任务不属于所选项目与分支，未显示其证据。');
      graphId=graph;markTask();hash();await refresh();
    }else hash();
  }
  async function navigateHash() {
    const route=location.hash==='#page'?'':location.hash.slice(1),token=reset();$('ar-tasks').textContent='正在读取研究语境…';
    try {
      await initialized;if(token!==epoch)return;if(initializationError)throw initializationError;
      const parts=route?route.split('/').map(decodeURIComponent):[];
      if(parts.length>3||parts.some(part=>!part))throw new Error('研究链接格式不完整，请选择项目、分支与任务。');
      $('ar-project').value=parts[0]||'';
      if(parts[0]&&$('ar-project').value!==parts[0]){$('ar-branch').replaceChildren();$('ar-tasks').textContent='指定项目不存在，请重新选择。';throw new Error('链接中的研究项目不存在。');}
      await loadContext(parts[1]||'',parts[2]||'',token);
    }catch(error){navigationError(error,token);}
  }
  async function listTasks(token=epoch) {
    const ctx=context();if(!ctx.project_id||!ctx.branch_id)return;
    const data=await request(api+'/projects/'+encodeURIComponent(ctx.project_id)+'/'+encodeURIComponent(ctx.branch_id),undefined,token);if(token!==epoch)return;
    $('ar-tasks').innerHTML=data.graphs.length?data.graphs.map(g=>'<button type="button" class="ar-task '+(g.graph_id===graphId?'is-active':'')+'" data-graph="'+esc(g.graph_id)+'">'+esc(g.title)+'<small>'+esc(g.nodes)+' 个完整目标 · '+(g.invalidated?'已失效':'已保存')+'</small></button>').join(''):'<p class="ar-muted">本分支还没有分析任务。先预览并保存一个完整目标图。</p>';
    return data;
  }
  function markTask() {$('ar-tasks').querySelectorAll('[data-graph]').forEach(button=>button.classList.toggle('is-active',button.dataset.graph===graphId));}
  function render() {
    if(!report)return;
    $('ar-work').hidden=false;$('ar-editor').hidden=true;$('ar-export').disabled=false;
    $('ar-work-title').textContent=report.plan.node_plans[report.plan.problem_id].request.title;
    $('ar-result').innerHTML='<strong>'+ (report.math_verdict==='PROVED'?'原问题已证明':'原问题尚未知') +'</strong><span>已预留 '+esc(report.checks_reserved)+' 次检查 / '+esc(report.worker_seconds_reserved)+' worker 秒</span>';
    const nodeById=Object.fromEntries(report.plan.request.nodes.map(n=>[n.id,n]));
    $('ar-nodes').innerHTML=report.plan.order.map(id=>{
      const n=nodeById[id],s=report.node_states[id],p=report.plan.node_plans[id];
      const latest=report.attempts.filter(a=>a.node_id===id).slice(-1)[0];
      const child=latest&&report.checks[latest.attempt_id],diag=child?.result?.diagnostic;
      return '<article class="ar-node"><div class="ar-node-title"><h3>'+esc(n.kind==='problem'?'原问题 · ':'引理 · ')+esc(p.request.title)+'</h3><span class="ar-state" data-verdict="'+esc(s.math_verdict)+'">'+esc(s.status)+' / '+esc(s.math_verdict)+'</span></div>'+statement(p)+(s.blocked_by.length?'<p>等待当前已证前置：'+esc(s.blocked_by.join('、'))+'</p>':'')+(diag?'<details><summary>上次候选检查诊断（不构成反证）</summary><pre>'+esc(diag.text)+'</pre></details>':'')+'<div class="ar-actions">'+(!report.invalidated&&s.math_verdict!=='PROVED'&&!s.blocked_by.length&&!active(report)?'<button type="button" class="ar-btn ar-primary" data-preview-node="'+esc(id)+'">核对并检查此节点</button>':'')+(latest&&['QUEUED','RUNNING'].includes(latest.state)&&!latest.invalidated?'<button type="button" class="ar-btn" data-cancel="'+esc(latest.attempt_id)+'">取消本次检查</button>':'')+(s.math_verdict==='PROVED'?'<button type="button" class="ar-link" data-withdraw="'+esc(id)+'">撤销此节点证据</button>':'')+'</div></article>';
    }).join('');
    $('ar-history').innerHTML='<details class="ar-advanced"><summary>全部 '+esc(report.attempts.length)+' 次执行记录与来源</summary><pre>'+esc(JSON.stringify(report.attempts,null,2))+'</pre></details>';
    clearTimeout(timer);
    if(active(report)){
      const key=graphId,token=epoch;
      const poll=()=>{if(token!==epoch||key!==graphId)return;if(pending||document.hidden){timer=setTimeout(poll,6000);return;}void refresh().catch(()=>{});};
      timer=setTimeout(poll,6000);
    }
  }
  async function refresh() {
    if(!graphId)return;const key=graphId,token=epoch,ctx=context();if(reading&&reading.key===key&&reading.token===token)return;const marker={key,token};reading=marker;clearCheck();
    try {
      const value=await request(api+'/'+encodeURIComponent(key),undefined,token);if(token!==epoch||key!==graphId)return;
      const saved=value.plan?.request;
      if(!saved||saved.project_id!==ctx.project_id||saved.branch_id!==ctx.branch_id)throw new Error('返回任务与当前项目或分支不对应，未显示其证据。');
      report=value;render();message('已重新核对记录与证据。');
    }
    catch(error){if(token===epoch&&key===graphId&&error.name!=='AbortError'){clearEvidence();message('本次证据读取未完成，旧判断已隐藏。'+error.message,'error');}throw error;}
    finally {if(reading===marker)reading=null;}
  }
  async function openGraph(key) {const token=reset();graphId=key;markTask();hash();try{await refresh();}catch(error){navigationError(error,token);}}
  $('ar-project').addEventListener('change',()=>{const token=reset();void loadContext('','',token).catch(error=>navigationError(error,token));});
  $('ar-branch').addEventListener('change',()=>{const token=reset();hash();void listTasks(token).catch(error=>navigationError(error,token));});
  $('ar-new-project').addEventListener('click',()=>{$('ar-project-form').hidden=!$('ar-project-form').hidden;});
  $('ar-project-form').addEventListener('submit',event=>{event.preventDefault();void action(async()=>{const token=epoch;const value=await request('/v1/research',{request_id:uid(),title:$('ar-project-title').value,question:$('ar-project-question').value},token);if(token!==epoch)return;await projects(value.project_id,token);if(token!==epoch)return;$('ar-project-form').hidden=true;const next=reset();await loadContext('','',next).catch(error=>navigationError(error,next));});});
  $('ar-example').addEventListener('change',()=>{epoch++;chooseExample();});
  $('ar-json').addEventListener('input',()=>{epoch++;clearPlan();draft();});
  $('ar-new-task').addEventListener('click',()=>{reset();hash();markTask();chooseExample();});
  $('ar-refresh-list').addEventListener('click',()=>void action(()=>listTasks()));
  $('ar-preview').addEventListener('click',()=>void action(async()=>{clearPlan();const token=epoch,ctx=context();if(!ctx.project_id)throw new Error('请先选择研究项目。');const input=JSON.parse($('ar-json').value);if(Object.keys(input).length!==1||!input.nodes)throw new Error('编辑区只填写完整 nodes。');const value=await request(api+'/preview',{...ctx,nodes:input.nodes},token);if(token!==epoch)return;plan=value;$('ar-save').disabled=false;$('ar-plan').innerHTML='<h3>逐项核对完整命题</h3>'+plan.order.map(id=>'<article class="ar-node"><h3>'+esc(id)+'</h3>'+statement(plan.node_plans[id])+'</article>').join('');}));
  $('ar-save').addEventListener('click',()=>void action(async()=>{if(!plan)throw new Error('请先重新预览全部目标。');const token=epoch,raw={request_id:uid(),request:plan.request,plan_fingerprint:plan.plan_fingerprint};$('ar-save').disabled=true;const value=await request(api,raw,token);if(token!==epoch)return;graphId=value.graph_id;report=value;plan=null;clearCheck();hash();render();await listTasks(token);}));
  $('ar-tasks').addEventListener('click',event=>{const button=event.target.closest('[data-graph]');if(button)void openGraph(button.dataset.graph);});
  $('ar-refresh').addEventListener('click',()=>void action(refresh));
  $('ar-nodes').addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;
    if(button.dataset.previewNode)void action(async()=>{clearCheck();const key=graphId,token=epoch;const value=await request(api+'/'+key+'/node-preview',{node_id:button.dataset.previewNode},token);if(token!==epoch||key!==graphId)return;nodePreview=value;syncRun();$('ar-check-details').innerHTML=statement(value.child_plan)+'<p class="ar-muted">已绑定 '+esc(Object.keys(value.prerequisites).length)+' 项确切前置证据；此处尚未执行 Lean。</p>';$('ar-check').hidden=false;$('ar-check').scrollIntoView({behavior:'smooth',block:'start'});});
    if(button.dataset.cancel)void action(async()=>{const key=graphId,token=epoch;clearCheck();const value=await request(api+'/'+key+'/cancel',{attempt_id:button.dataset.cancel},token);if(token!==epoch||key!==graphId)return;report=value;render();});
    if(button.dataset.withdraw){withdrawNode=button.dataset.withdraw;$('ar-withdraw-confirm').checked=false;$('ar-withdraw-dialog').showModal();}
  });
  $('ar-reviewer').addEventListener('input',()=>{$('ar-confirm').checked=false;syncRun();});
  $('ar-confirm').addEventListener('change',syncRun);
  $('ar-run-form').addEventListener('submit',event=>{event.preventDefault();void action(async()=>{if(!nodePreview||!$('ar-confirm').checked)throw new Error('请重新核对并确认本次完整目标。');const value=nodePreview,key=graphId,token=epoch;const payload={request_id:uid(),node_id:value.node_id,execution_fingerprint:value.execution_fingerprint,reviewer:$('ar-reviewer').value,confirmed:true};clearCheck();await request(api+'/'+key+'/submit',payload,token);if(token===epoch&&key===graphId)await refresh();});});
  $('ar-withdraw-close').addEventListener('click',()=>{$('ar-withdraw-dialog').close();});
  $('ar-withdraw-form').addEventListener('submit',event=>{event.preventDefault();void action(async()=>{if(!withdrawNode||!$('ar-withdraw-confirm').checked)throw new Error('请明确确认撤销。');const key=graphId,token=epoch;const payload={node_id:withdrawNode,reviewer:$('ar-withdraw-reviewer').value,reason:$('ar-withdraw-reason').value,confirmed:true};$('ar-withdraw-dialog').close();clearCheck();const value=await request(api+'/'+key+'/withdraw',payload,token);if(token!==epoch||key!==graphId)return;report=value;render();});});
  $('ar-export').addEventListener('click',()=>{if(!report)return;const blob=new Blob([JSON.stringify(report,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=graphId+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  window.addEventListener('pagehide',()=>{epoch++;clearTimeout(timer);clearCheck();});
  window.addEventListener('pageshow',event=>{if(event.persisted&&graphId)void refresh().catch(()=>{});});
  window.addEventListener('hashchange',()=>{if(location.hash!=='#page')void navigateHash();});
  let initializationError=null;
  const initialized=(async()=>{
    await window.MRA.session;const value=await request(api+'/catalog',undefined,null);examples=value.examples;
    $('ar-example').innerHTML=examples.map(e=>'<option value="'+esc(e.id)+'">'+esc(e.title)+'</option>').join('');
    if(!$('ar-json').value)chooseExample();await projects(undefined,null);
  })().catch(error=>{initializationError=error;});
  void navigateHash();
})();
