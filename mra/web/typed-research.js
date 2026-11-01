"use strict";
(() => {
  if(location.pathname==='/typed-research/lemmas'){window.MRALemmaResearch.mount();return;}
  const {escape: esc, notify} = window.MRA;
  const $ = id => document.getElementById(id), json = v => JSON.stringify(v, null, 2);
  const uid = () => 'typed-ui-' + crypto.randomUUID().replaceAll('-', '');
  const s = {mode:"typed",epoch:0, busy:false, project:null, branch:'main', catalog:null, graph:null, node:null, plan:null, execution:null, pending:null, save:null, dirty:false, proposals:null, timer:null};
  const api = () => s.mode==='structure' ? '/v1/structure-research' : '/v1/typed-research';
  const structured = () => s.mode==='structure';
  const expression = value => window.MRAStructureNotation.expression(value);
  const editable = request => Object.fromEntries((structured() ? ['schema','carriers','operations','definitions','laws','nodes'] : ['definitions','nodes']).map(k=>[k,request[k]]));
  let reuseWriting=false;
  document.addEventListener('mra:typed-reuse-writing',event=>{reuseWriting=event.detail;controls();});
  const call = (path,body,options) => window.MRATypedTransport.request(path,body,options);
  const get = path => {
    const epoch=s.epoch;let waited=false;
    return call(path,undefined,{isCurrent:()=>epoch===s.epoch,onBusy:()=>{
      if(epoch!==s.epoch)return;
      waited=true;message('证据正在核对，稍后将自动重新读取。');
      if(s.graph&&path===api()+'/'+s.graph.graph_id){
        s.execution=null;clearTimeout(s.timer);
        $('tr-work').innerHTML='<div class="tr-empty"><h3>正在等待证据核对</h3><p>旧判断已隐藏，完成后自动显示当前结果。</p></div>';controls();
      }
    }}).then(value=>{if(waited&&epoch===s.epoch)message('证据已重新核对。');return value;});
  };
  function publishContext() {
    const context=s.graph&&!s.dirty?{mode:s.mode,graph_id:s.graph.graph_id,node_id:s.node,revision:s.graph.revision}:null;
    document.dispatchEvent(new CustomEvent('mra:typed-context',{detail:!structured()?context:null}));
    document.dispatchEvent(new CustomEvent('mra:research-context',{detail:context}));
  }
  function message(text, error=false) {$('tr-message').textContent = text;$('tr-message').dataset.error = String(error);}
  function forget() {s.proposals=null;if($('tr-proposals'))$('tr-proposals').innerHTML='';s.epoch++;s.execution=null;s.pending=null;clearTimeout(s.timer);if ($('tr-review')) $('tr-review').hidden=true;}
  function updateHash() {const parts=s.project?[s.project.project_id,s.branch,s.graph?.graph_id||'']:['','',''];if(structured())parts.push('structure');history.replaceState(null,'','#'+parts.map(encodeURIComponent).join('/'));}
  function controls() {
    const busy=s.busy||reuseWriting;
    for(const button of document.querySelectorAll('#tr-work [data-node]'))button.disabled=busy;
    for (const id of ['tr-preview','tr-example']) $(id).disabled=busy||!s.project||!s.catalog;
    for (const id of ['tr-project','tr-branch','tr-open','tr-graph','tr-create-project','tr-editor','tr-reload','tr-mode-typed','tr-mode-structure']) $(id).disabled=busy;
    for(const e of document.querySelectorAll('#tr-work [data-cancel],#tr-withdraw input,#tr-withdraw button'))e.disabled=busy;
    if($('tr-reviewer'))$('tr-reviewer').disabled=busy||!!s.pending;
    if($('tr-confirm'))$('tr-confirm').disabled=busy||!!s.pending;
    $('tr-save').disabled=busy||!s.plan;
    $('tr-refresh').disabled=busy||!s.graph;
    $('tr-export').disabled=busy||!s.graph;
    if($('tr-propose'))$('tr-propose').disabled=busy||s.dirty||!structured()||!!s.graph?.invalidated;
    for(const button of document.querySelectorAll('[data-proposal]'))button.disabled=busy||s.dirty;
    const ready=s.graph?.node_states[s.node];
    if ($('tr-node-preview')) $('tr-node-preview').disabled=busy||s.dirty||!ready||ready.status!=='READY'||s.graph.attempts.some(a=>['QUEUED','RUNNING'].includes(a.state)&&!a.invalidated);
    if ($('tr-submit')) { $('tr-submit').disabled=busy||!s.execution||!$('tr-confirm').checked||!$('tr-reviewer').value.trim();$('tr-submit').textContent=s.pending?'按原请求 ID 手动恢复':'确认并执行一次'; }
  }
  async function action(fn) {
    if(s.busy||reuseWriting)return;
    s.busy=true;controls();
    try {await fn();} catch(error) {message(error.message + (s.pending ? '；请用原请求 ID 手动重发：' + s.pending.request_id : ''),true);}
    finally{s.busy=false;controls();}
  }
  function target(plan) {
    const r=plan.request;
    const method=structured()?window.MRAStructureNotation.proof(r.proof):r.strategy;
    return `<div class="tr-original">${esc(r.original)}</div><p class="tr-variable">${r.variables.map(v=>esc(v.name)+' : '+esc(v.type)).join(' · ') || '无自由变量'}</p><p class="tr-note">原前提 ${r.assumptions.length} 条 · 证明程序 ${esc(method)} · 候选与重放共用 ${r.timeout_seconds} 秒</p>${structured()?`<div class="tr-conditions"><h4>完整前提 · ${r.assumptions.length} 条</h4>${r.assumptions.map(a=>`<p><code>${esc(a.name)}</code><span>${esc(expression(a.formula))}</span></p>`).join('')||'<p>无原前提</p>'}</div><div class="tr-math-goal"><small>目标</small><p>⊢ ${esc(expression(r.goal))}</p></div>`:''}<pre class="tr-formula">${esc(plan.formal_statement)}</pre>${structured()?`<details><summary>受控证明程序</summary><pre class="tr-json">${esc(plan.proof_source)}</pre></details>`:''}`;
  }
  function definitions(plan) {
    const r=plan.request,values=r.definitions;
    const base=`<div class="tr-definitions"><h3>共享定义 · ${values.length} 项，逐项进入完整目标</h3>${values.map(d=>`<article class="tr-definition"><code>${esc(d.name)}(${d.parameters.map(p=>esc(p.name)+' : '+esc(p.type)).join(', ')}) : ${esc(d.result_type)}</code><p>${esc(d.original)}</p>${structured()?`<p class="tr-definition-body">${esc(expression(d.body))}</p>`:''}</article>`).join('')||'<p class="tr-note">未声明共享定义。</p>'}</div>`;
    if(!structured())return base+`<details><summary>完整定义数据与表达式</summary><pre class="tr-json">${esc(json(values))}</pre></details>`;
    return `<div class="tr-environment"><div class="tr-environment-title"><span class="tr-kicker">SHARED CONTEXT</span><h3>先明确结构，再检查结论。</h3><p>${r.carriers.length} 个载体 · ${r.operations.length} 项运算 · ${values.length} 个定义 · ${r.laws.length} 条共享定律</p></div><div class="tr-carriers">${r.carriers.map(c=>`<article><code>${esc(c.name)} : Type</code><p>${esc(c.original)}</p></article>`).join('')}</div><div class="tr-operations">${r.operations.map(o=>`<p><code>${esc(o.name)} : ${esc([...o.parameters,o.result_type].join(' → '))}</code><span>${esc(o.original)}</span></p>`).join('')}</div>${base}<h3>共享结构定律 · 全部作为原前提</h3><ol class="tr-law-grid">${r.laws.map(l=>`<li><code>${esc(l.name)}</code><p>${esc(expression(l.formula))}</p></li>`).join('')||'<li>未声明共享定律</li>'}</ol><details><summary>完整共享环境数据</summary><pre class="tr-json">${esc(json({carriers:r.carriers,operations:r.operations,definitions:values,laws:r.laws}))}</pre></details></div>`;
  }
  function modeUI() {
    document.querySelector('#page .tr').dataset.mode=s.mode;
    $('tr-mode-typed').setAttribute('aria-pressed',String(!structured()));$('tr-mode-structure').setAttribute('aria-pressed',String(structured()));
    $('tr-kicker').textContent=structured()?'STRUCTURES & INDUCTION / 01':'TYPED RESEARCH / 01';
    $('tr-headline').textContent=structured()?'从结构出发，让归纳落地。':'让每个定义，进入证明。';
    $('tr-intro').textContent=structured()?'明确载体、运算与每条结构定律，在同一项目中检查引理，再独立核验原问题。':'在同一个研究项目中，明确对象的类型与共同定义，逐项检查引理，最后核验原问题。';
    $('tr-sigil').innerHTML=structured()?'∀ n ∈ ℕ<small>STRUCTURE · INDUCT · VERIFY</small>':'ℕ → ℤ → ℚ<small>DEFINE · REVIEW · VERIFY</small>';
    $('tr-banner').textContent=structured()?'支持 Type 载体、显式运算、闭合结构定律和自然数归纳。每个节点包含完整前提；原问题必须取得自己的完整证明，前置不会自动成为新假设。当前没有模型自动规划。':'支持显式 Nat / Int / Rat、精确分数与非递归定义。原图按固定策略独立核验；下方可以显式复用前置证明，重新检查完整目标。当前没有模型自动规划。';
    $('tr-editor-label').textContent=structured()?'结构化任务 · 共享环境、定律与节点':'结构化任务 · definitions 与 nodes';
    $('tr-editor-help').textContent=structured()?'完整声明载体、运算、定义、共享定律及每个节点的原条件、目标和受控程序。编辑后需保存新任务并重新确认。':'在 JSON 中完整填写每个节点的原文、变量、原前提、目标、策略和预算。此入口不解析任意自然语言或 Lean 代码。';
    $('tr-example').textContent=structured()?'载入同态保持幂示例':'载入平方展开示例';
    $('tr-chips').innerHTML=structured()?'<span class="tr-chip">Type · 显式定律</span><span class="tr-chip">Nat 归纳</span><span class="tr-chip">≤ 8 nodes</span>':'<span class="tr-chip">Nat · Int · Rat</span><span class="tr-chip">≤ 6 definitions</span><span class="tr-chip">≤ 8 nodes</span>';
    $('tr-bottom').textContent=structured()?'原图检查：前置只控制调度和绑定证据。下方的显式组合独立导入证明项，拥有自己的数学判断。系统不证明结构存在或前提一致；失败保持 UNKNOWN。作者示例用于功能验证，不代表原创研究能力。':'语义提示：Nat 减法在零处截断；Rat 除法为总函数，a / 0 = 0。系统不会自动增加非零条件，也不证明原前提一致。固定策略失败保持 UNKNOWN。作者示例用于验证功能，不代表原创研究能力。';
  }
  function drawPlan(plan) {
    $('tr-plan').hidden=false;
    $('tr-plan').innerHTML=`<h3>完整图预览 · UNKNOWN / E0</h3><p class="tr-note">保存只登记任务。每个节点执行前仍需具名确认。</p>${definitions(plan)}${plan.order.map(id=>`<section class="tr-target"><h3>${esc(id)} · ${esc(plan.node_plans[id].request.title)}</h3>${target(plan.node_plans[id])}</section>`).join('')}<details><summary>完整计划与指纹</summary><pre class="tr-json">${esc(json(plan))}</pre></details>`;
  }
  function drawCatalog() {
    $('tr-graph').innerHTML='<option value="">选择已保存任务</option>'+s.catalog.graphs.map(g=>`<option value="${esc(g.graph_id)}">${esc(g.graph_id.slice(0,20))}… · ${g.nodes} 节点${g.invalidated?' · 已停用':''}</option>`).join('');
    if(s.graph)$('tr-graph').value=s.graph.graph_id;
    $('tr-project-link').href='/research#'+s.project.project_id;
    $('tr-project-link').hidden=false;
    $('tr-question').textContent=s.project.question;
  }
  function drawGraph() {
    const container=$('tr-work');
    if(!s.graph){container.innerHTML='<div class="tr-empty"><div class="tr-empty-symbol">⊢</div><h3>从定义开始，逐项求证</h3><p>载入结构化示例或已有任务，查看定义、引理和原问题。</p></div>';controls();publishContext();return;}
    const g=s.graph, plan=g.plan;
    if(!plan.order.includes(s.node))s.node=plan.order[0];
    const state=g.node_states[s.node], node=plan.request.nodes.find(n=>n.id===s.node);
    container.innerHTML=`<div class="tr-heading"><h2>研究路线</h2><span class="tr-step">02 / CHECK</span></div><div class="tr-stats"><div><small>原问题判断</small><strong id="tr-verdict">${esc(g.math_verdict)} / ${esc(g.evidence_level)}</strong></div><div><small>已登记检查</small><strong>${g.checks_reserved} / 16</strong></div><div><small>已预留 worker</small><strong>${g.worker_seconds_reserved} / 480s</strong></div></div>${g.invalidated?`<p class="tr-danger">任务已持续停用：${esc(g.reason)}</p>`:''}<div class="tr-nodes">${plan.order.map(id=>{const n=plan.request.nodes.find(n=>n.id===id),st=g.node_states[id];return `<button type="button" class="tr-node" data-node="${esc(id)}" aria-pressed="${id===s.node}"><span class="tr-node-line"><span>${n.kind==='problem'?'原问题':n.kind==='lemma'?'引理':'子问题'} · ${esc(id)}</span><span class="tr-badge" data-proved="${st.math_verdict==='PROVED'}">${esc(st.status)}</span></span><strong>${esc(n.target.title)}</strong><small>前置：${esc(n.depends_on.join(' → ')||'无')} · 数学判断 ${esc(st.math_verdict)} / ${esc(st.evidence_level)}</small></button>`;}).join('')}</div><section id="tr-node-panel"><div class="tr-heading"><h3>${esc(s.node)} · 完整目标</h3><span id="tr-node-verdict" class="tr-badge" data-proved="${state.math_verdict==='PROVED'}">${esc(state.math_verdict)} / ${esc(state.evidence_level)}</span></div><p id="tr-gate" class="tr-note">${s.dirty?'编辑器有未保存修改，请先打开已保存任务或保存新任务。':state.blocked_by.length?'等待前置证据：'+esc(state.blocked_by.join('、')):'前置仅控制调度；不作为附加假设，不导入其证明项。'}</p>${target(plan.node_plans[s.node])}<details><summary>全部共享定义与显式原前提</summary><pre class="tr-json">${esc(json({...editable(plan.request),local_assumptions:node.target.assumptions}))}</pre></details>${structured()?'<section class="tr-strategies"><div class="tr-heading"><div><span class="tr-kicker">NEXT PROGRAM</span><h3>为下一次尝试，提出候选。</h3></div><button class="button secondary" id="tr-propose" type="button">提出候选程序</button></div><p class="tr-note">只使用当前完整命题、显式定律和递推定义。最多 4 步改写、800 次展开；此处不运行 Lean。候选需另存任务并重新核对。</p><div id="tr-proposals"></div></section>':''}<button class="button secondary" type="button" id="tr-node-preview">核对本节点并准备检查</button><div id="tr-review" class="tr-review" hidden></div><div id="tr-attempts">${g.attempts.filter(a=>a.node_id===s.node).map(a=>{const c=g.checks[a.attempt_id],r=c?.result;return `<article class="tr-attempt"><header><strong>执行状态 ${esc(a.state)}</strong><span>${a.invalidated?'证据已停用':'当前记录'}</span></header><p>${esc(a.attempt_id)}<br>具名确认：${esc(a.reviewer)}${a.reason?'<br>'+esc(a.reason):''}</p>${r?`<div class="tr-audit">候选 PID ${esc(r.candidate?.pid??'—')} · 独立重放 PID ${esc(r.replay?.pid??'—')}<br>重放类型匹配：${esc(r.replay?.audit?.type_matches??'未取得')} · 允许公理审计：${esc(r.replay?.audit ? json(r.replay.audit.axioms) : '未取得')}</div>`:''}${['QUEUED','RUNNING'].includes(a.state)?`<button class="button secondary" data-cancel="${esc(a.attempt_id)}">取消本次检查</button>`:''}<details><summary>检查、工件与语义对齐记录</summary><pre class="tr-json">${esc(json({attempt:a,check:c||null}))}</pre></details></article>`;}).join('')||'<p class="tr-note">尚未执行。本节点当前没有真实检查证据。</p>'}</div><details><summary class="tr-danger">具名撤销此节点证据</summary><form id="tr-withdraw" class="tr-withdraw"><label>撤销人<input id="tr-withdraw-reviewer" required maxlength="200" autocomplete="name"></label><label>原因<input id="tr-withdraw-reason" required maxlength="500"></label><label class="tr-confirm"><input type="checkbox" id="tr-withdraw-confirm" required><span>确认持续停用此节点及依赖它的后继证据。恢复旧工件不会复活确认。</span></label><button class="button secondary tr-danger">确认撤销</button></form></details></section>`;
    container.querySelectorAll('[data-node]').forEach(button=>button.addEventListener('click',()=>{if(s.busy||reuseWriting)return;forget();s.node=button.dataset.node;drawGraph();schedule();}));
    $('tr-node-preview').addEventListener('click',previewNode);
    if($('tr-propose'))$('tr-propose').addEventListener('click',()=>void proposeStrategies());
    drawProposals();
    container.querySelectorAll('[data-cancel]').forEach(button=>button.addEventListener('click',()=>action(async()=>{const key=g.graph_id;await call(api()+'/'+key+'/cancel',{attempt_id:button.dataset.cancel});forget();await refresh(key);message('已登记取消；最终状态以当前报告为准。');})));
    $('tr-withdraw').addEventListener('submit',event=>{event.preventDefault();const data={node_id:s.node,reviewer:$('tr-withdraw-reviewer').value,reason:$('tr-withdraw-reason').value,confirmed:$('tr-withdraw-confirm').checked};action(async()=>{forget();s.graph=await call(api()+'/'+g.graph_id+'/withdraw',data);drawGraph();message('已撤销，并重新核对后继依赖。');});});
    controls();
    publishContext();
  }
  function proposalCurrent(value) {
    return value&&structured()&&!s.dirty&&value.context.graph_id===s.graph?.graph_id&&value.context.node_id===s.node&&value.context.revision===s.graph.revision;
  }
  function drawProposals() {
    const box=$('tr-proposals');if(!box)return;
    if(!proposalCurrent(s.proposals)){box.innerHTML='';return;}
    const v=s.proposals;
    const ranking=v.induction_ranking?.length?'<p class="tr-note">递推索引排序：'+v.induction_ranking.map(r=>esc(r.variable)+'（后继 '+r.successor_exposures+' · 零 '+r.zero_exposures+'）').join(' → ')+'。并列按目标首次出现顺序；排序只是启发式。</p>':'';
    const generalization=v.generalization_analysis?.some(a=>a.eligible.length)?'<p class="tr-note">泛化候选只使用原目标参数；原前提依赖的参数和绑定内部参数保守排除。每项仍需完整核对并真实检查。</p>':'';
    box.innerHTML=ranking+generalization+'<p class="tr-note">候选 '+v.candidates.length+' 项 · 全部 UNKNOWN / E0 · 历史检查 '+v.feedback.length+' 次</p>'+v.candidates.map(c=>'<article class="tr-proposal"><div><strong>'+esc(c.label)+'</strong><span class="tr-badge">'+(c.previously_rejected?'同程序曾被拒绝':'待真实检查')+'</span></div><p>'+esc(window.MRAStructureNotation.proof(c.request.proof))+'</p><details><summary>核对候选程序与完整目标</summary><pre class="tr-json">'+esc(json({proof:c.request.proof,formal_statement:v.formal_statement,generalization_analysis:v.generalization_analysis}))+'</pre></details><button class="button secondary" type="button" data-proposal="'+esc(c.candidate_id)+'">仅带入本节点草稿</button></article>').join('')+(v.feedback.length?'<details class="tr-strategy-feedback"><summary>已有真实检查反馈 · '+v.feedback.length+' 次</summary>'+v.feedback.map(f=>'<article class="tr-proposal"><strong>'+esc(f.state)+' · '+esc(f.math_verdict)+' / '+esc(f.evidence_level)+'</strong><p class="tr-subtle">'+esc(f.run_id)+(f.invalidated?' · 证据已停用':'')+'</p><p>候选返回码 '+esc(f.candidate_returncode??'—')+' · 重放 '+esc(f.replay_state??'未运行')+' · worker '+esc(f.worker_seconds.toFixed(3))+' 秒</p>'+(f.error?'<pre class="tr-json">'+esc(f.error)+(f.error_truncated?'\n…完整日志见原检查记录':'')+'</pre>':'')+'</article>').join('')+'</details>':'<p class="tr-note">此节点还没有真实检查反馈。候选生成不会改变已有判断。</p>');
    box.querySelectorAll('[data-proposal]').forEach(button=>button.addEventListener('click',()=>{
      if(s.busy||reuseWriting||!proposalCurrent(s.proposals))return;
      const candidate=s.proposals.candidates.find(c=>c.candidate_id===button.dataset.proposal);
      if(!candidate)return;
      const expected={...s.graph.plan.node_plans[s.node].request};delete expected.proof;
      if(json(expected)!==json(s.proposals.target)){message('目标已变化，请重新读取并生成候选。',true);return;}
      const draft=structuredClone(editable(s.graph.plan.request)),node=draft.nodes.find(n=>n.id===s.node);
      node.target.proof=structuredClone(candidate.request.proof);
      $('tr-editor').value=json(draft);s.dirty=true;s.plan=null;s.save=null;forget();$('tr-plan').hidden=true;drawGraph();
      message('已仅把候选程序带入本节点草稿，完整目标和全部条件保留。请预览、另存新任务，再具名确认执行。');
    }));
  }
  async function proposeStrategies() {
    await action(async()=>{
      if(!structured()||!s.graph||s.dirty)return;
      const epoch=s.epoch,key=s.graph.graph_id,node=s.node;
      s.proposals=null;if($('tr-proposals'))$('tr-proposals').innerHTML='';
      const value=await call('/v1/structure-research/'+key+'/strategy-proposals-v3',{node_id:node,budget:{max_rewrite_steps:4,max_expansions:800,max_candidates:8}});
      if(epoch!==s.epoch||!proposalCurrent(value)){if(epoch===s.epoch)message('节点证据版本已变化，请刷新后重新生成。',true);return;}
      s.proposals=value;drawProposals();message('已提出候选程序，尚未执行 Lean。原目标与全部条件保持完整。');
    });
  }
  function drawReview(value) {
    const box=$('tr-review');box.hidden=false;
    box.innerHTML=`<h3>具名确认 · ${esc(value.node_id)}</h3>${definitions(structured()?s.graph.plan:{request:value.child_plan.request})}${target(value.child_plan)}<p class="tr-note">1 个候选进程 + 1 个独立重放进程；共享 ${value.child_plan.request.timeout_seconds} 秒 worker 预算，完整性核对另计。失败、超时和中断保持 UNKNOWN，不自动重试。</p><details><summary>本次完整执行预览与前置证据指纹</summary><pre class="tr-json">${esc(json(value))}</pre></details><label>语义核对人<input id="tr-reviewer" maxlength="200" autocomplete="name" placeholder="填写你的姓名或本地标识"></label><label class="tr-confirm"><input id="tr-confirm" type="checkbox"><span>我已核对原文、所有变量类型、共享定义、全部结构定律与原前提，确认完整目标忠实表达本节点，并同意本次资源预算。姓名仅为本地声明。</span></label><button class="button primary" type="button" id="tr-submit" disabled>确认并执行一次</button><p id="tr-submit-id" class="tr-subtle"></p>`;
    $('tr-reviewer').addEventListener('input',()=>{$('tr-confirm').checked=false;controls();});
    $('tr-confirm').addEventListener('change',controls);
    $('tr-submit').addEventListener('click',()=>action(async()=>{
      if(!s.execution||!$('tr-confirm').checked)return;
      if(!s.pending)s.pending={request_id:uid(),node_id:s.node,execution_fingerprint:s.execution.execution_fingerprint,reviewer:$('tr-reviewer').value,confirmed:true};
      $('tr-submit-id').textContent='请求 ID：'+s.pending.request_id+'。发送失败时可手动原样重发。';
      // A transport failure retains this exact body. A repeated click never creates a new request ID.
      $('tr-reviewer').disabled=true;
      const key=s.graph.graph_id;
      await call(api()+'/'+key+'/submit',s.pending);
      forget();await refresh(key);message('已提交一次检查。运行状态与数学判断分别显示。');
    }));controls();
  }
  async function previewNode() {
    if(s.busy)return;
    forget();const epoch=s.epoch,key=s.graph.graph_id,node=s.node;
    $('tr-node-preview').disabled=true;
    try{const value=await call(api()+'/'+key+'/node-preview',{node_id:node});if(epoch!==s.epoch)return;s.execution=value;drawReview(value);}
    catch(error){if(epoch===s.epoch)message(error.message,true);}
    finally{if(epoch===s.epoch){controls();schedule();}}
  }
  function schedule() {
    clearTimeout(s.timer);
    if(s.graph?.attempts.some(a=>['QUEUED','RUNNING'].includes(a.state)&&!a.invalidated))s.timer=setTimeout(()=>{if(s.busy){schedule();return;}refresh(s.graph.graph_id).catch(error=>message(error.message,true));},1400);
  }
  async function refresh(key) {
    const epoch=s.epoch;s.execution=null;clearTimeout(s.timer);
    $('tr-work').innerHTML='<div class="tr-empty"><h3>正在重新核对当前证据</h3><p>旧判断已隐藏，完成后显示当前结果。</p></div>';controls();
    document.dispatchEvent(new CustomEvent('mra:typed-context',{detail:null}));
    document.dispatchEvent(new CustomEvent('mra:research-context',{detail:null}));
    try{
      const value=await get(api()+'/'+key);
      if(epoch!==s.epoch)return;
      if(s.pending && (value.revision!==s.graph.revision||value.node_states[s.node]?.status!=='READY'))s.pending=null;
      s.graph=value;drawGraph();updateHash();schedule();
    }catch(error){
      if(epoch===s.epoch){s.execution=null;s.pending=null;clearTimeout(s.timer);$('tr-work').innerHTML='<div class="tr-empty"><h3>当前证据暂不可读取</h3><p>旧判断已隐藏。请手动刷新后重新核对。</p></div>';controls();document.dispatchEvent(new CustomEvent('mra:typed-context',{detail:null}));document.dispatchEvent(new CustomEvent('mra:research-context',{detail:null}));}
      throw error;
    }
  }
  async function openGraph(key) {
    if(!key)throw Error('先选择已保存任务。');
    forget();s.plan=null;s.save=null;s.graph=null;$('tr-plan').hidden=true;drawGraph();
    const epoch=s.epoch,value=await get(api()+'/'+key);
    if(epoch!==s.epoch)return;
    if(value.plan.parent.project_id!==s.project.project_id||value.plan.parent.branch_id!==s.branch)throw Error('任务不属于当前项目研究线。');
    s.graph=value;s.dirty=false;s.node=value.plan.order[0];$('tr-editor').value=json(editable(value.plan.request));drawGraph();updateHash();schedule();
  }
  async function loadProject(id, branch='main', key=null) {
    forget();s.graph=null;s.plan=null;s.save=null;s.catalog=null;s.dirty=false;$('tr-plan').hidden=true;drawGraph();
    const epoch=s.epoch, project=await get('/v1/research/'+encodeURIComponent(id));if(epoch!==s.epoch)return;
    s.project=project;s.branch=project.branches[branch]?branch:'main';
    $('tr-branch').innerHTML=Object.values(project.branches).map(b=>`<option value="${esc(b.branch_id)}">${esc(b.label||b.branch_id)}</option>`).join('');$('tr-branch').value=s.branch;
    const catalog=await get(api()+'/projects/'+encodeURIComponent(id)+'/'+encodeURIComponent(s.branch));if(epoch!==s.epoch)return;
    s.catalog=catalog;drawCatalog();$('tr-editor').value=json(catalog.example);updateHash();controls();if(key)await openGraph(key);
  }
  $('page').innerHTML=`<div class="tr"><header class="tr-hero"><div><div id="tr-kicker" class="tr-kicker">TYPED RESEARCH / 01</div><h1 id="tr-headline">让每个定义，进入证明。</h1><p id="tr-intro">在同一个研究项目中，明确对象的类型与共同定义，逐项检查引理，最后核验原问题。</p></div><div id="tr-sigil" class="tr-sigil">ℕ → ℤ → ℚ<small>DEFINE · REVIEW · VERIFY</small></div></header><div class="tr-mode-switch" role="group" aria-label="研究模式"><button id="tr-mode-typed" type="button" aria-pressed="true"><span>01</span>数域与定义<small>Nat / Int / Rat</small></button><button id="tr-mode-structure" type="button" aria-pressed="false"><span>02</span>结构与归纳<small>Type / 运算 / 定律</small></button><a id="tr-mode-lemmas" class="button secondary" href="/typed-research/lemmas"><span>03</span>递归与引理<small>Plan / Verify / Reuse</small></a></div><div class="tr-context"><label>研究项目<select id="tr-project" aria-label="研究项目"></select></label><label>研究线<select id="tr-branch" aria-label="研究线"><option>main</option></select></label><div class="tr-actions"><a class="tr-toplink" id="tr-project-link" hidden>回到项目</a><button class="button secondary" id="tr-reload" type="button">重读项目列表</button></div></div><details class="tr-box tr-new"><summary>新建研究项目</summary><form id="tr-new-project"><label>项目名称<input id="tr-title" required maxlength="200" placeholder="例如：有理数恒等式研究"></label><label>原始研究问题<textarea id="tr-question-input" required maxlength="12000" rows="2" placeholder="保留完整问题与研究目标"></textarea></label><button id="tr-create-project" class="button secondary">保存项目</button></form></details><p id="tr-question" class="tr-note"></p><div id="tr-banner" class="tr-banner">支持显式 Nat / Int / Rat、精确分数与非递归定义。原图按固定策略独立核验；下方可以显式复用前置证明，重新检查完整目标。当前没有模型自动规划。</div><div id="tr-message" class="tr-message" role="status" aria-live="polite"></div><div class="tr-layout"><section class="tr-box"><div class="tr-heading"><h2>定义与任务</h2><span class="tr-step">01 / DEFINE</span></div><div class="tr-load"><label>已保存任务<select id="tr-graph"><option value="">选择已保存任务</option></select></label><button class="button secondary" id="tr-open">打开</button></div><div id="tr-chips" class="tr-chips"><span class="tr-chip">Nat · Int · Rat</span><span class="tr-chip">≤ 6 definitions</span><span class="tr-chip">≤ 8 nodes</span></div><label id="tr-editor-label" for="tr-editor">结构化任务 · definitions 与 nodes</label><textarea id="tr-editor" class="tr-editor" spellcheck="false" aria-describedby="tr-editor-help"></textarea><p class="tr-note" id="tr-editor-help">在 JSON 中完整填写每个节点的原文、变量、原前提、目标、策略和预算。此入口不解析任意自然语言或 Lean 代码。</p><div class="tr-actions"><button class="button secondary" id="tr-example">载入平方展开示例</button><button class="button primary" id="tr-preview">预览全部完整目标</button><button class="button secondary" id="tr-save" disabled>保存这份任务</button></div><div id="tr-plan" class="tr-plan" hidden></div></section><section class="tr-box"><div class="tr-actions tr-heading"><span class="tr-step">EVIDENCE / CURRENT</span><button class="button secondary" id="tr-refresh" disabled>刷新证据</button><button class="button secondary" id="tr-export" disabled>导出完整 JSON</button></div><div id="tr-work"></div></section></div><p id="tr-bottom" class="tr-bottom">语义提示：Nat 减法在零处截断；Rat 除法为总函数，a / 0 = 0。系统不会自动增加非零条件，也不证明原前提一致。固定策略失败保持 UNKNOWN。作者示例用于验证功能，不代表原创研究能力。</p></div>`;
  for(const mode of ['typed','structure'])$('tr-mode-'+mode).addEventListener('click',()=>{if(s.busy||reuseWriting||s.mode===mode)return;action(async()=>{forget();s.mode=mode;modeUI();if(s.project)await loadProject(s.project.project_id,s.branch);else{drawGraph();updateHash();}message('已切换研究模式，旧确认已清除。');});});
  $('tr-mode-lemmas').addEventListener('click',event=>{if(s.busy||reuseWriting)event.preventDefault();});
  $('tr-editor').addEventListener('input',()=>{forget();s.dirty=true;s.plan=null;s.save=null;$('tr-plan').hidden=true;drawGraph();controls();});
  $('tr-example').addEventListener('click',()=>{$('tr-editor').value=json(s.catalog.example);$('tr-editor').dispatchEvent(new Event('input'));message('已载入作者示例；请逐项核对，再预览。');});
  $('tr-preview').addEventListener('click',()=>action(async()=>{forget();s.plan=null;s.save=null;$('tr-plan').hidden=true;const raw=JSON.parse($('tr-editor').value);const fields=structured()?'carriers,definitions,laws,nodes,operations,schema':'definitions,nodes';if(Object.keys(raw).sort().join(',')!==fields)throw Error('编辑器字段与当前研究模式不匹配，请重新载入示例。');const request={project_id:s.project.project_id,branch_id:s.branch,...raw};s.plan=await call(api()+'/preview',request);drawPlan(s.plan);message('预览完成，尚未执行 Lean。');}));
  $('tr-save').addEventListener('click',()=>action(async()=>{if(!s.save)s.save={request_id:uid(),request:s.plan.request,plan_fingerprint:s.plan.plan_fingerprint};message('保存请求 ID：'+s.save.request_id);const value=await call(api(),s.save);s.catalog=await get(api()+'/projects/'+s.project.project_id+'/'+s.branch);drawCatalog();await openGraph(value.graph_id);$('tr-graph').value=value.graph_id;message('任务已保存，仍为 UNKNOWN / E0。');}));
  $('tr-open').addEventListener('click',()=>action(()=>openGraph($('tr-graph').value)));
  $('tr-refresh').addEventListener('click',()=>action(async()=>{forget();await refresh(s.graph.graph_id);message('已重读当前证据；这次读取没有重新执行 Lean。');}));
  $('tr-export').addEventListener('click',()=>action(async()=>{forget();const key=s.graph.graph_id;await refresh(key);const blob=new Blob([json(s.graph)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=key+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('完整目标、检查与对齐记录已导出');}));
  $('tr-project').addEventListener('change',()=>action(()=>loadProject($('tr-project').value)));
  $('tr-branch').addEventListener('change',()=>action(()=>loadProject(s.project.project_id,$('tr-branch').value)));
  let newProject=null;
  $('tr-new-project').addEventListener('input',()=>{newProject=null;});
  $('tr-new-project').addEventListener('submit',event=>{event.preventDefault();action(async()=>{if(!newProject)newProject={request_id:uid(),title:$('tr-title').value,question:$('tr-question-input').value};message('项目请求 ID：'+newProject.request_id);const p=await call('/v1/research',newProject);await loadProjects(p.project_id);newProject=null;message('项目已保存，可以开始定义任务。');});});
  async function loadProjects(id,branch='main',key=null) {
    const list=await get('/v1/research');$('tr-project').innerHTML=list.projects.map(p=>`<option value="${esc(p.project_id)}">${esc(p.title)}</option>`).join('');
    if(!list.projects.length){message('先新建一个研究项目。');controls();return;}
    id=list.projects.some(p=>p.project_id===id)?id:list.projects[0].project_id;$('tr-project').value=id;await loadProject(id,branch,key);
  }
  $('tr-reload').addEventListener('click',()=>action(()=>loadProjects(s.project?.project_id,s.branch,s.graph?.graph_id)));
  drawGraph();
  const parts=location.hash.slice(1).split('/').map(v=>{try{return decodeURIComponent(v);}catch{return '';}});
  s.mode=parts[3]==='structure'||parts[2]?.startsWith('structureflow-')?'structure':'typed';modeUI();
  action(()=>loadProjects(parts[0],parts[1]||'main',parts[2]||null));
})();

