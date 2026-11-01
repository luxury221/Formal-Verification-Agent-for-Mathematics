"use strict";
(() => {
  if(!['/compositions','/library-compositions'].includes(location.pathname))return;
  const libraryMode=location.pathname==='/library-compositions';
  const {escape:e,modal,notify}=window.MRA,{parse,format}=window.MRACompositionNotation;
  const $=id=>document.getElementById(id),base=libraryMode?'/v1/library-compositions':'/v1/lemma-compositions';
  const active=r=>['QUEUED','RUNNING'].includes(r.state)&&!r.invalidated;
  const clone=v=>JSON.parse(JSON.stringify(v));
  let catalog,selected=[],tree={hyp:''},plan=null,epoch=0,viewEpoch=0,requestId=crypto.randomUUID();
  let submitting=false,postBody=null,timer,lastId=null,tail=Promise.resolve();
  // Serialize page requests. Only GETs receive two bounded retries on temporary unavailability.
  function api(route,body){
    const work=async()=>{
      const opts={cache:'no-store'};
      if(body!==undefined){const session=await window.MRA.session;Object.assign(opts,{method:'POST',headers:{'Content-Type':'application/json','X-MRA-CSRF':session.csrf_token},body:JSON.stringify(body)});}
      for(let attempt=0;;attempt++){
        const response=await fetch(route.startsWith('/v1/')?route:base+route,opts),value=await response.json();
        if(response.ok)return value;
        if(body===undefined&&response.status===503&&attempt<2){await new Promise(r=>setTimeout(r,350*(attempt+1)));continue;}
        const error=Error(value.error||'本地组合服务暂不可用。');error.status=response.status;throw error;
      }
    };
    const pending=tail.then(work,work);tail=pending.catch(()=>{});return pending;
  }
  window.MRACompositionTransport={json:api};
  function error(value){$('lc-error').textContent=value.message;$('lc-error').hidden=false;}
  async function action(work){$('lc-error').hidden=true;try{await work();}catch(value){error(value);}}
  function enable(){$('lc-submit').disabled=!plan||!$('lc-reviewer').value.trim()||!$('lc-confirm').checked||submitting;}
  function invalidate(){epoch++;plan=null;postBody=null;requestId=crypto.randomUUID();$('lc-review').hidden=true;$('lc-confirm').checked=false;$('lc-submit-status').textContent='';enable();}
  function namedEdit(){epoch++;postBody=null;requestId=crypto.randomUUID();$('lc-confirm').checked=false;enable();}
  function assumptions(){
    const text=$('lc-assumptions').value.trim();if(!text)return [];
    return text.split('\n').map(line=>{const m=/^\s*([a-z][a-z0-9_]{0,19})\s*:\s*(.+)$/.exec(line);if(!m)throw Error('每条前提请写成 h1: a | b，每行一条。');return {name:m[1],formula:parse(m[2])};});
  }
  function readProof(node,depth=0){
    if(depth>14)throw Error('证明树超过 14 层。');
    if(Object.hasOwn(node,'hyp'))return {hyp:node.hyp};
    if(node.lemma!==undefined||node.ref!==undefined){const key=node.lemma!==undefined?'lemma':'ref';return {[key]:node[key],terms:node.terms.map(t=>typeof t==='string'?parse(t,'term'):clone(t)),proofs:node.proofs.map(p=>readProof(p,depth+1))};}
    const key=Object.keys(node)[0];return {[key]:node[key].map(p=>readProof(p,depth+1))};
  }
  function read(){return {schema:catalog.mode,title:$('lc-title').value,original:$('lc-original').value,variables:$('lc-variables').value.trim()?$('lc-variables').value.split(',').map(s=>s.trim()):[],assumptions:assumptions(),goal:parse($('lc-goal').value),sources:selected.map(s=>({alias:s.alias,item_id:s.item_id})),proof:readProof(tree),timeout_seconds:Number($('lc-timeout').value)};}
  function options(){
    return [['hyp','使用原前提'],...selected.map(s=>['lemma:'+s.alias,'私有引理 · '+s.alias]),['pair','合取：分别证明两项'],['mp','等价式：从左推右'],['mpr','等价式：从右推左'],...Object.entries(catalog.references).map(([k,v])=>['ref:'+k,v.title+' · '+k])];
  }
  function makeNode(kind){
    if(kind==='hyp')return {hyp:''};
    if(['pair','mp','mpr'].includes(kind))return {[kind]:[{hyp:''},{hyp:''}]};
    const [type,name]=kind.split(':'),source=selected.find(s=>s.alias===name),ref=catalog.references[name];
    const n=type==='lemma'?source.signature.variables.length:ref.terms,p=type==='lemma'?source.signature.assumptions.length:ref.premises;
    return {[type]:name,terms:Array(n).fill(''),proofs:Array.from({length:p},()=>({hyp:''}))};
  }
  function renderTree(){
    const host=$('lc-tree');host.replaceChildren();let count=0;
    function draw(node,parent,replace,depth=0){
      if(depth>14||++count>240)throw Error('证明树超过编辑范围。');
      const box=document.createElement('div');box.className='lc-node';parent.append(box);
      const kind=node.hyp!==undefined?'hyp':node.lemma!==undefined?'lemma:'+node.lemma:node.ref!==undefined?'ref:'+node.ref:Object.keys(node)[0];
      const opts=options();if(!opts.some(([k])=>k===kind))opts.unshift([kind,'来源已移除 · '+kind]);
      box.innerHTML=`<label>证明步骤 · 第 ${depth+1} 层<select aria-label="证明步骤类型">${opts.map(([k,v])=>`<option value="${e(k)}" ${k===kind?'selected':''}>${e(v)}</option>`).join('')}</select></label>`;
      box.querySelector('select').onchange=event=>{invalidate();replace(makeNode(event.target.value));renderTree();};
      if(kind==='hyp'){
        const label=document.createElement('label');label.textContent='原前提名称';const input=document.createElement('input');input.value=node.hyp;input.placeholder='例如 h1';input.maxLength=20;input.oninput=()=>{node.hyp=input.value;invalidate();};label.append(input);box.append(label);return;
      }
      if(node.terms){
        const source=selected.find(s=>s.alias===node.lemma),ref=catalog.references[node.ref];
        const hint=document.createElement('p');hint.className='lc-tree-caption';hint.textContent=source?`按来源顺序填写全部变量：${source.signature.variables.join(', ')||'无'}；全部原前提：${source.signature.assumptions.map(h=>h.name).join(', ')||'无'}`:ref?.formula||'请重新选择有效来源。';box.append(hint);
        const terms=document.createElement('div');terms.className='lc-node-terms';box.append(terms);
        node.terms.forEach((v,i)=>{const label=document.createElement('label');label.textContent=`实参 ${i+1}${source?' · '+source.signature.variables[i]:''}`;const input=document.createElement('input');input.className='lc-formula';input.value=typeof v==='string'?v:format(v);input.maxLength=8192;input.oninput=()=>{node.terms[i]=input.value;invalidate();};label.append(input);terms.append(label);});
      }
      const children=node.proofs||node[Object.keys(node)[0]];
      if(children.length){const holder=document.createElement('div');holder.className='lc-node-children';box.append(holder);children.forEach((child,i)=>{const label=document.createElement('p');label.className='lc-tree-caption';label.textContent=node.proofs?`原前提证明 ${i+1}`:kind==='pair'?`合取第 ${i+1} 项`:i===0?'完整等价式的证明':'所用方向起点的证明';holder.append(label);draw(child,holder,v=>children[i]=v,depth+1);});}
    }
    draw(tree,host,v=>tree=v);
  }
  function renderSources(){
    $('lc-sources').innerHTML=selected.length?selected.map((s,i)=>`<article class="lc-source"><header><strong>${e(s.signature.title)}</strong><button class="text-button" data-remove="${i}" aria-label="移除来源 ${i+1}">移除</button></header><label>本次引用别名<input data-alias="${i}" value="${e(s.alias)}" maxlength="20"></label><p>${e(s.signature.original)}</p><pre class="lc-code">${e(s.formal_statement)}</pre><details><summary>完整签名与出处 · 版本 ${e(s.version)}</summary><p>全称变量：${e(s.signature.variables.join(', ')||'无')} ∈ ℕ</p><p>全部原前提：${e(s.signature.assumptions.map(h=>h.name+': '+format(h.formula)).join('；')||'无')}</p><p>结论：${e(format(s.signature.goal))}</p><code>${e(s.item_id)}<br>${e(s.archive_fingerprint)}</code><p><a href="${e(s.href)}">查看私有归档</a></p></details></article>`).join(''):'<p class="lc-empty">选择一条有效私有引理开始。<br>每个来源都必须在证明中实际使用。</p>';
    document.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{invalidate();selected.splice(Number(b.dataset.remove),1);renderSources();renderTree();});
    document.querySelectorAll('[data-alias]').forEach(input=>{input.oninput=()=>{selected[Number(input.dataset.alias)].alias=input.value;invalidate();};input.onchange=renderTree;});
    $('lc-source-count').textContent=selected.length+' / 4';$('lc-add-source').disabled=selected.length>=4||!catalog.sources.length;
    $('lc-from-source').disabled=selected.length!==1;
  }
  async function source(item_id){return api('/sources/'+encodeURIComponent(item_id));}
  async function load(request){
    invalidate();const version=epoch,values=[];
    for(const b of request.sources){const info=await source(b.item_id);if(version!==epoch)return;values.push({...info,alias:b.alias});}
    // Validate conversion completely before replacing any editor field.
    const goal=format(request.goal),hyps=request.assumptions.map(h=>h.name+': '+format(h.formula)).join('\n');
    selected=values;tree=clone(request.proof);
    for(const k of ['title','original'])$('lc-'+k).value=request[k];
    $('lc-variables').value=request.variables.join(', ');$('lc-timeout').value=request.timeout_seconds;
    $('lc-goal').value=goal;$('lc-assumptions').value=hyps;renderSources();renderTree();
  }
  function applications(value){
    return value.applications.map((a,i)=>`<article class="lc-application"><h3>${i+1}. 调用 ${e(a.alias)}</h3><p class="lc-formula">${e(Object.entries(a.substitution).map(([k,v])=>k+' ↦ '+format(v)).join('；')||'无量化实参')}</p><p>需要逐项证明：${e(a.instantiated_assumptions.map(h=>format(h.formula)).join('；')||'无原前提')}</p><p class="lc-formula">得到 ${e(format(a.instantiated_goal))}</p><details><summary>完整实例化条件、目标与前提证明输入</summary><pre class="lc-code">${e(a.formal_statement)}</pre><pre class="lc-code">${e(JSON.stringify(a.premise_proofs,null,2))}</pre></details></article>`).join('');
  }
  function showPlan(value){
    plan=value;$('lc-review').hidden=false;$('lc-quote').textContent=value.request.original;$('lc-full').textContent=value.formal_statement;
    $('lc-mapping').textContent='变量对应：'+(Object.entries(value.variable_mapping).map(([k,v])=>k+' → '+v).join('；')||'无自由变量');
    $('lc-applications').innerHTML=value.source_items.map(s=>`<details><summary>本次来源快照 · ${e(s.alias)} · 版本 ${e(s.version)}</summary><pre class="lc-code">${e(s.formal_statement)}</pre><p class="lc-id">${e(s.item_id)}<br>${e(s.archive_fingerprint)}</p></details>`).join('')+applications(value);$('lc-fingerprint').textContent=value.plan_fingerprint;
    $('lc-budget').textContent=`1 次候选检查 + 1 次独立证明数据重放，共享 ${value.budget.shared_worker_seconds} 秒 worker。完整性检查另计，不自动重试；预览为 UNKNOWN / E0。`;
    $('lc-plan-data').textContent=JSON.stringify(value,null,2);enable();
  }
  async function history(){
    try{const value=await api('/runs');$('lc-history').innerHTML=value.runs.length?value.runs.map(r=>`<button class="lc-history" data-run="${e(r.run_id)}"><span><strong>${e(r.title)}</strong><small>${e(r.created_at.slice(0,19).replace('T',' '))}</small></span><span class="lc-pill ${r.math_verdict==='PROVED'?'proved':''}">${e(r.math_verdict)}<small>${e(r.state)}</small></span></button>`).join(''):'<p class="lc-empty">还没有组合记录。</p>';document.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>action(async()=>{invalidate();await inspect(b.dataset.run);}));}
    catch(value){$('lc-history').innerHTML='<p class="lc-empty">历史暂不可读，请点击上方刷新。</p>';throw value;}
  }
  function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function showResult(r){
    const result=r.result,proved=r.math_verdict==='PROVED',candidate=result?.candidate,replay=result?.replay;
    $('lc-result').hidden=false;$('lc-result').innerHTML=`<div class="lc-subhead"><div><span class="eyebrow">CHECKED COMPOSITION / HISTORY</span><h2>${e(r.plan.request.title)}</h2></div><span class="lc-pill ${proved?'proved':''}" id="lc-verdict">${e(r.math_verdict)} / ${e(r.evidence_level)}</span></div><p class="lc-status">运行 ${e(r.state)} · 阶段 ${e(r.stage)} · worker ${Number(result?.worker_seconds||0).toFixed(2)} 秒</p><pre class="lc-code">${e(r.plan.formal_statement)}</pre><p class="hint">${r.invalidated?'本次确认或证据已持久停用。重新检查需要新计划与新确认。':proved?'完整目标已经真实 Lean 检查及独立进程重放。此处复核的是保存的历史证据。':active(r)?'后台执行已确认的组合。只有两阶段检查通过，才会得到已证结论。':'本次未形成有效证明。失败、取消、超时或中断均保持未知。'}</p><div class="lc-progress"><div><strong>01 · 具名核对</strong>${e(r.alignment.reviewer)}<br>本地声明</div><div><strong>02 · 候选内核检查</strong>${e(candidate?.run_status||'等待')}<br>${candidate?.pid?'PID '+e(candidate.pid):''}</div><div><strong>03 · 独立进程重放</strong>${e(replay?.run_status||'等待')}<br>${replay?.pid?'PID '+e(replay.pid):''}</div></div><p class="lc-id">${e(r.run_id)}<br>工件 ${e(r.artifact_status)} · 新认证 false</p><div class="lc-actions"><button class="button secondary" id="lc-reuse">带回重新核对</button><button class="button secondary" id="lc-export">导出完整记录</button>${active(r)?'<button class="button secondary" id="lc-cancel">取消运行</button>':''}${!r.invalidated?'<button class="text-button" id="lc-revoke">撤销语义确认</button>':''}</div>${result?.error?`<details open><summary>检查反馈 · 结论保持未知</summary><pre class="lc-code">${e(result.error)}</pre></details>`:''}<details><summary>来源与每次完整条件调用</summary>${applications(r.plan)}</details><details><summary>完整计划、审计、事件及 ${Object.keys(r.files).length} 项工件摘要</summary><pre class="lc-code">${e(JSON.stringify(r,null,2))}</pre></details>`;
    $('lc-reuse').onclick=()=>action(async()=>{await load(r.plan.request);$('lc-editor').scrollIntoView({block:'start',behavior:'smooth'});});
    $('lc-export').onclick=()=>action(async()=>{try{const value=await api('/runs/'+r.run_id+'/export');if(lastId!==r.run_id)return;showResult(value);download(value,r.run_id+'.json');}catch(value){if(lastId===r.run_id){$('lc-result').hidden=true;$('lc-result-read').textContent='导出前无法核对当前证据，请手动刷新。';}throw value;}});
    if($('lc-cancel'))$('lc-cancel').onclick=event=>action(async()=>{event.target.disabled=true;await api('/runs/'+r.run_id+'/cancel',{});await inspect(r.run_id);await history();});
    if($('lc-revoke'))$('lc-revoke').onclick=()=>{
      const dialog=modal('撤销组合的语义确认',`<p>本次结论将停用，原工件和历史保留。</p><form id="lc-revoke-form"><label>本地核对者<input id="lc-revoke-name" required maxlength="200"></label><label>撤销原因<textarea id="lc-revoke-reason" required maxlength="500"></textarea></label><label class="check-row"><input id="lc-revoke-confirm" type="checkbox" required>确认撤销本次语义对齐</label><p class="error" id="lc-revoke-error" hidden></p><button class="button primary" type="submit">确认撤销</button></form>`);
      dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=dialog.querySelector('[type=submit]');button.disabled=true;try{await api('/runs/'+r.run_id+'/revoke',{reviewer:$('lc-revoke-name').value,reason:$('lc-revoke-reason').value,confirmed:$('lc-revoke-confirm').checked});dialog.close();await inspect(r.run_id);await history();}catch(value){$('lc-revoke-error').textContent=value.message;$('lc-revoke-error').hidden=false;}finally{button.disabled=false;}};
    };
    window.dispatchEvent(new CustomEvent('mra-composition-result',{detail:{origin:libraryMode?'library-composition':'composition',record:clone(r)}}));
  }
  async function loadArchive(id){
    invalidate();const version=epoch,info=await source(decodeURIComponent(id.slice(8)));if(version!==epoch)return;
    await load({...info.signature,schema:catalog.mode,sources:[{alias:'previous',item_id:info.item_id}],proof:{lemma:'previous',terms:info.signature.variables.map(v=>({var:v})),proofs:info.signature.assumptions.map(h=>({hyp:h.name}))}});
    $('lc-editor').scrollIntoView({block:'start',behavior:'smooth'});
  }
  async function inspect(id){
    clearTimeout(timer);const version=++viewEpoch;window.dispatchEvent(new CustomEvent('mra-composition-result',{detail:null}));if(lastId!==id)$('lc-result').hidden=true;lastId=id;$('lc-result-refresh').hidden=false;$('lc-result-read').textContent='正在核对历史证据…';
    try{const r=await api('/runs/'+encodeURIComponent(id));if(version!==viewEpoch)return;showResult(r);$('lc-result-read').textContent='';window.history.replaceState(null,'',location.pathname+'#'+id);if(active(r))timer=setTimeout(()=>action(()=>inspect(id)),4500);}
    catch(value){if(version!==viewEpoch)return;$('lc-result').hidden=true;$('lc-result-read').textContent='当前记录暂不可读；旧结论已收起，请手动刷新。';throw value;}
  }
  function mount(){
    $('page').innerHTML=`<section class="page-heading"><div><div class="eyebrow">PRIVATE LEMMAS / NEW PROOFS</div><h1>让已证引理，成为下一步的起点。</h1><p>显式组合私有成果，为完整新命题重新建立证据。</p></div><a class="button secondary" href="/library">浏览私有知识库</a></section><section class="lc-hero"><div><span class="eyebrow">COMPOSE · REVIEW · VERIFY</span><h2>每一次引用，都带着它的条件。</h2><p>选择已归档引理，填写全部实参与前提证明。先核对完整目标，再交给真实 Lean 检查与独立进程重放。</p></div><div class="lc-diagram" aria-label="引理与条件组合得到新目标"><div><span>引理 A · 条件</span><span>引理 B · 条件</span></div><b>→</b><span>新目标<br>⊢ P</span></div></section><p class="error" id="lc-error" role="alert" hidden></p><div class="lc-layout"><div class="lc-main"><section class="panel lc-panel" id="lc-source-panel"><div class="lc-subhead"><div><span class="eyebrow">01 / SELECT THE SOURCES</span><h2>选择有效的私有引理</h2></div><span class="lc-pill" id="lc-source-count">0 / 4</span></div><label for="lc-source-select">已归档、有效的 Mathlib E4 成果</label><select id="lc-source-select"></select><div class="lc-actions"><button class="button secondary" id="lc-add-source">＋ 加入本次组合</button><button class="text-button" id="lc-refresh-sources">刷新来源</button></div><div id="lc-sources"></div><button class="text-button" id="lc-from-source" disabled>以所选引理完整签名为起点 →</button></section><section class="panel lc-panel" id="lc-editor"><span class="eyebrow">02 / THE COMPLETE NEW CLAIM</span><h2>写下新目标与全部前提</h2><div class="lc-fields"><label>标题<input id="lc-title" maxlength="160" placeholder="例如：将两个条件引理组合"></label><label>全称变量 · 全部属于 ℕ<input id="lc-variables" maxlength="125" placeholder="a, b, c"></label></div><label>你的原始陈述<textarea id="lc-original" rows="3" maxlength="12000" placeholder="写明自然数范围、必要条件与想要证明的结论。"></textarea></label><label>全部原前提 · 每行 名称: 公式<textarea id="lc-assumptions" rows="3" maxlength="49152" class="lc-formula" placeholder="h1: a | b&#10;h2: b | c"></textarea></label><label>完整结论<textarea id="lc-goal" rows="3" maxlength="8192" class="lc-formula" placeholder="a | (b + c)"></textarea></label><details><summary>自然数公式怎么填写？</summary><p>支持 <code>a | b</code>、<code>a = b</code>、<code>a &lt;= b</code>、<code>gcd(a, b)</code>、<code>coprime(a, b)</code>，用 ∧、∨、↔（或 &amp;&amp;、||、&lt;-&gt;）连接命题。算术使用 +、*、-，乘法显式填写。ℕ 的减法是截断减法。最多 6 变量、6 前提；所有量化实参和前提证明都必须提供。</p></details><label>共享 worker 预算 · 1–30 秒<input id="lc-timeout" type="number" min="1" max="30" value="30"></label></section><section class="panel lc-panel"><span class="eyebrow">03 / BUILD THE PROOF</span><h2>把证明步骤连接起来</h2><p class="hint">选择私有引理、受控库引用或原前提。合取与等价推理可继续展开；此处不运行 Lean。</p><div id="lc-tree"></div><div class="lc-actions"><button class="button primary" id="lc-preview">预览完整组合</button><button class="text-button" id="lc-draft-export">导出当前输入</button></div></section><section class="panel lc-panel lc-plan" id="lc-review" hidden><span class="eyebrow">04 / REVIEW BEFORE EXECUTION</span><h2>确认每一个条件与结论</h2><blockquote id="lc-quote"></blockquote><p class="hint" id="lc-mapping"></p><pre class="lc-code" id="lc-full"></pre><div id="lc-applications"></div><p class="hint" id="lc-budget"></p><label>语义核对者 · 本地具名声明<input id="lc-reviewer" maxlength="200" autocomplete="name"></label><label class="lc-check"><input type="checkbox" id="lc-confirm"><span>我已核对原文、Nat 域、全部来源与版本、变量/前提、新结论、每次调用及预算，确认形式化表达本次原意。</span></label><button class="button primary" id="lc-submit" disabled>确认并执行真实检查</button><p id="lc-submit-status" class="lc-status" role="status"></p><p id="lc-fingerprint" class="lc-id"></p><details><summary>完整核验计划</summary><pre class="lc-code" id="lc-plan-data"></pre></details></section><p id="lc-result-read" class="lc-status" role="status"></p><button class="text-button" id="lc-result-refresh" hidden>重新读取当前记录</button><section class="panel lc-panel" id="lc-result" aria-live="polite" hidden></section></div><aside class="lc-side"><section class="panel lc-panel"><div class="lc-subhead"><h2>组合记录</h2><button class="text-button" id="lc-history-refresh">刷新</button></div><div id="lc-history"></div></section><section class="panel lc-panel"><span class="eyebrow">EVIDENCE, STEP BY STEP</span><h2>从引用到新的证据</h2><p class="lc-tip">预览 · UNKNOWN / E0<br>完整条件与原意先由你核对。</p><p class="lc-tip">执行 · 两个真实进程<br>候选内核检查后，独立重放证明数据。</p><p class="lc-tip">历史 · 持续核对来源<br>来源撤销或工件变化会停用旧结论。</p><p class="lc-tip">当前支持受控 Nat 证明项组合。前提相容性仍需审查；组合结果可在研究页具名关联。尚无自主定理搜索或跨领域数学定义。无需配置模型。</p></section></aside></div>`;
    document.title='MRA · 引理组合';document.querySelector('.breadcrumb strong').textContent='引理组合';
    ['title','original','variables','assumptions','goal','timeout'].forEach(k=>$('lc-'+k).oninput=invalidate);
    $('lc-source-select').onchange=invalidate;$('lc-reviewer').oninput=namedEdit;$('lc-confirm').onchange=()=>{if(!$('lc-confirm').checked)postBody=null;enable();};
    $('lc-add-source').onclick=()=>action(async()=>{if(selected.length>=4)return;invalidate();const version=epoch,id=$('lc-source-select').value;if(!id)throw Error('暂无可用来源，请先在私有知识库归档成果。');const info=await source(id);if(version!==epoch)return;let n=1;while(selected.some(s=>s.alias==='lemma'+n))n++;selected.push({...info,alias:'lemma'+n});renderSources();renderTree();});
    $('lc-from-source').onclick=()=>action(async()=>{if(selected.length!==1)return;const s=selected[0],v=s.signature;await load({schema:catalog.mode,...v,sources:[{alias:s.alias,item_id:s.item_id}],proof:{lemma:s.alias,terms:v.variables.map(n=>({var:n})),proofs:v.assumptions.map(h=>({hyp:h.name}))},timeout_seconds:30});});
    $('lc-refresh-sources').onclick=()=>action(refreshSources);$('lc-history-refresh').onclick=()=>action(history);
    $('lc-preview').onclick=()=>action(async()=>{invalidate();const version=epoch,input=read(),button=$('lc-preview');button.disabled=true;try{const value=await api('/preview',input);if(version===epoch){showPlan(value);$('lc-review').scrollIntoView({block:'start',behavior:'smooth'});}}catch(value){if(version===epoch)throw value;}finally{button.disabled=false;}});
    $('lc-submit').onclick=()=>action(async()=>{
      if(!plan||submitting||!$('lc-confirm').checked)return;
      const version=epoch;postBody=postBody||{request_id:requestId,request:clone(plan.request),plan_fingerprint:plan.plan_fingerprint,reviewer:$('lc-reviewer').value.trim(),confirmed:true};
      const body=clone(postBody);submitting=true;enable();$('lc-submit-status').textContent='正在提交 '+body.request_id+'；不会自动重发。';
      try{const value=await api('/runs',body);if(version===epoch){invalidate();await inspect(value.run_id);$('lc-result-refresh').hidden=false;}else notify('请求已接受，可在组合记录中查看。');await history();}
      catch(value){if(version===epoch){$('lc-submit-status').textContent='提交结果尚未确认。再次点击会使用原请求 ID 和原正文：'+body.request_id;throw value;}}
      finally{submitting=false;enable();}
    });
    $('lc-result-refresh').onclick=()=>action(()=>inspect(lastId));$('lc-draft-export').onclick=()=>action(async()=>download(read(),'composition-input.json'));
    renderTree();
  }
  async function refreshSources(){
    invalidate();const version=epoch,value=await api('/catalog');if(version!==epoch)return;catalog=value;
    $('lc-source-select').innerHTML=catalog.sources.length?catalog.sources.map(s=>`<option value="${e(s.item_id)}">${e(s.title)}</option>`).join(''):'<option value="">暂无有效私有 Mathlib E4 成果</option>';
    // Cached signatures are display data only; a fresh server preview revalidates every selected source.
    renderSources();renderTree();
  }
  async function main(){
    catalog={mode:libraryMode?'library-lemma-composition-v1':'private-lemma-composition-v1',sources:[],references:{}};mount();
    const links=document.createElement('div');links.className='lc-actions';links.innerHTML='<a class="button secondary" href="/proof-library">组合成果库</a><a class="button secondary" href="'+(libraryMode?'/compositions':'/library-compositions')+'">'+(libraryMode?'原版引理组合':'使用已归档组合')+'</a>';$('page').querySelector('.page-heading').after(links);
    if(libraryMode){$('page').querySelector('h1').textContent='让证明积累，继续生长。';$('lc-source-panel').querySelector('label').textContent='有效的私有Mathlib引理与已归档组合';const note=document.createElement('p');note.className='hint';note.textContent='本页可引用组合归档及其传递来源。完整条件逐项保留；新结果仍需内核检查与独立重放，研究图关联暂沿用原版组合协议。';links.after(note);$('page').querySelector('.lc-tip:last-child').textContent='当前支持受控 Nat 证明项及传递来源复用。本页新组合尚未接入研究图；原版组合支持具名关联。尚无自主定理搜索或跨领域数学定义。无需配置模型。';}
    await refreshSources();await history();
    const id=location.hash.slice(1);if(/^mathlib-[a-f0-9]{32}$/.test(id)){$('lc-result-refresh').hidden=false;await inspect(id);}
    else if(libraryMode&&id.startsWith('archive/'))await loadArchive(id);
    else if(id.startsWith('target/')){
      const parts=id.split('/');if(parts.length!==3)throw Error('研究目标链接格式不正确。');
      const r=await fetch('/v1/research-compositions/'+encodeURIComponent(decodeURIComponent(parts[1]))+'/nodes/'+encodeURIComponent(decodeURIComponent(parts[2])),{cache:'no-store'}),value=await r.json();
      if(!r.ok)throw Error(value.error||'研究目标暂不可读，请返回研究页重试。');
      await load({...value.target,sources:[],proof:{hyp:''}});notify('已带入完整研究目标。选择来源并构建证明后，重新预览确认。');
    }
  }
  window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(/^mathlib-[a-f0-9]{32}$/.test(id)&&id!==lastId)action(async()=>{invalidate();await inspect(id);});else if(libraryMode&&id.startsWith('archive/'))action(()=>loadArchive(id));});
  main().catch(value=>{if($('lc-error')){error(value);$('lc-refresh-sources').disabled=false;}else $('page').textContent=value.message;});
})();
