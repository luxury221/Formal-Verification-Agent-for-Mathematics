"use strict";
(() => {
  const {escape:esc}=window.MRA, $=id=>document.getElementById(id), json=v=>JSON.stringify(v,null,2);
  const expression=v=>window.MRAStructureNotation.expression(v);
  const names={programs:'完整递归证明',exports:'独立引理',applications:'跨任务应用'};
  const s={kind:'programs',epoch:0,busy:false,plan:null,pending:null,record:null,source:null,proposals:null,timer:null,history:[]};
  const root='/v1/lemma-research';
  function message(text,error=false){$('lw-message').textContent=text;$('lw-message').dataset.error=String(error);}
  function invalidate(){s.epoch++;s.plan=null;s.pending=null;s.proposals=null;clearTimeout(s.timer);$('lw-review').innerHTML='';$('lw-proposals').innerHTML='';if($('lw-matches'))$('lw-matches').innerHTML='';}
  function clearRecord(){s.record=null;$('lw-record').innerHTML='<div class="lw-empty"><span>⊢</span>打开一条记录，核对它自己的数学证据。</div>';}
  function controls(){
    document.querySelectorAll('#lw-input input,#lw-input select,#lw-input textarea,#lw-input button,[data-kind],[data-open],[data-next],#lw-history-refresh').forEach(e=>{e.disabled=s.busy||!!s.pending;});
    for(const id of ['lw-reviewer','lw-confirm'])if($(id))$(id).disabled=s.busy||!!s.pending;
    if($('lw-submit')){$('lw-submit').disabled=s.busy||!s.plan||(!s.pending&&(!$('lw-reviewer').value.trim()||!$('lw-confirm').checked));$('lw-submit').textContent=s.pending?'按原请求 ID 手动重发':'确认并检查一次';}
    for(const e of document.querySelectorAll('#lw-record button,#lw-record input'))e.disabled=s.busy||!!s.pending;
  }
  async function call(path,body,epoch=s.epoch){
    const value=await window.MRATypedTransport.request(root+path,body,{isCurrent:()=>epoch===s.epoch,onBusy:()=>{
      if(epoch===s.epoch){message('来源或证据正在核对，等待后自动重新读取。');if(path.includes('/'+s.record?.run_id)){clearRecord();}}
    }});
    if(epoch!==s.epoch){const error=new Error('任务已变化，旧响应已丢弃。');error.name='AbortError';throw error;}
    return value;
  }
  async function action(fn){
    if(s.busy)return;s.busy=true;controls();
    try{await fn();}catch(error){if(error.name!=='AbortError')message(error.message+(s.pending?'；保留原请求 ID：'+s.pending.request_id:''),true);}
    finally{s.busy=false;controls();}
  }
  function target(r,formal){
    return `<div class="lw-summary"><strong>${esc(r.title)}</strong><p>${esc(r.original)}</p><p>${r.variables.map(v=>esc(v.name)+' : '+esc(v.type)).join(' · ')||'无原变量'}</p><div class="lw-env"><div><b>载体</b>${r.carriers.map(v=>esc(v.name)+' : Type').join('<br>')||'无'}</div><div><b>运算</b>${r.operations.map(v=>esc(v.name)+' : '+esc([...v.parameters,v.result_type].join(' → '))).join('<br>')||'无'}</div></div><h3>全部原条件 · ${r.assumptions.length}</h3>${r.assumptions.map(a=>`<p><b>${esc(a.name)}</b><br>${esc(expression(a.formula))}</p>`).join('')||'<p>无原条件</p>'}<hr class="lw-rule"><div class="lw-formula">⊢ ${esc(expression(r.goal))}</div></div><details><summary>共享定义 · ${r.definitions.length} 项</summary>${r.definitions.map(d=>`<div class="lw-summary"><strong>${esc(d.name)}</strong>${esc(d.original)}<div class="lw-formula">${esc(expression(d.body))}</div></div>`).join('')||'<p class="lw-note">未声明共享定义。</p>'}</details>${formal?`<details open><summary>完整被检查类型 · 包含全部量词与条件</summary><pre class="lw-full-type">${esc(formal)}</pre></details>`:''}`;
  }
  function form(){
    s.source=null;
    const headings={programs:['从完整目标，规划必要义务。','提出候选只生成数据。确认后，全部局部引理与原目标共享一次检查预算。'],exports:['让已证引理，成为可复用成果。','选择完整父证明，重新描述独立定理；导出仍需新的真实检查与具名核对。'],applications:['带着全部条件，进入新目标。','选择当前可用的导出引理，显式填写实参与原条件证明，再检查完整新目标。']};
    $('lw-input-title').textContent=headings[s.kind][0];$('lw-input-note').textContent=headings[s.kind][1];
    $('lw-input').innerHTML=s.kind==='programs'?`<div class="lw-actions"><button id="lw-example" class="button secondary">载入交换迭代示例</button></div><p class="lw-note">作者示例用于体验流程，不是模型输出或独立研究测验。</p><div id="lw-target-summary"></div><details open><summary>完整目标数据 · 可编辑</summary><label>结构化目标<textarea id="lw-target" spellcheck="false" aria-label="完整结构化目标"></textarea></label></details><div class="lw-actions"><button id="lw-propose" class="button primary">规划局部引理</button></div><p class="lw-note">最多 2 个候选、800 次展开、4 步改写；候选均为 UNKNOWN / E0。</p><div id="lw-proposals"></div><details><summary>已有完整程序 · 手动预览</summary><label>结构化程序<textarea id="lw-program" spellcheck="false"></textarea></label><button id="lw-program-preview" class="button secondary">预览这份程序</button></details>`:`<label>${s.kind==='exports'?'完整父证明':'导出引理'}<select id="lw-source-select"><option value="">先选择当前 E4 来源</option></select></label><label>或填写来源记录 ID<input id="lw-source-id" placeholder="mathlib-…" autocomplete="off"></label><button id="lw-source-load" class="button secondary">核对当前来源</button><div id="lw-source-detail"></div><div id="lw-source-form"></div><div id="lw-proposals"></div>`;
    if(s.kind==='applications'){
      $('lw-input').insertAdjacentHTML('afterbegin',`<details open class="lw-discovery"><summary>从完整新目标，查找已证引理</summary><p class="lw-note">只扫描当前独立导出库，不增加原条件；所有建议仍为 UNKNOWN / E0。选择后须重新预览和具名执行。</p><button id="lw-match-example" class="button secondary">从已证来源载入双向示例</button><label>完整新目标 · 无 proof<textarea id="lw-match-target" spellcheck="false"></textarea></label><button id="lw-match" class="button primary">匹配已导出引理</button><div id="lw-matches"></div></details><hr class="lw-rule">`);
      $('lw-match-target').oninput=()=>{invalidate();clearRecord();controls();};
      $('lw-match-example').onclick=()=>action(async()=>{invalidate();clearRecord();const p=await call('/matching-example');$('lw-match-target').value=json(p.target);message('已从当前已证来源构造双向目标；保留原环境和条件，这是机制示例。');});
      $('lw-match').onclick=()=>action(async()=>{
        invalidate();clearRecord();s.source=null;$('lw-source-detail').innerHTML='';$('lw-source-form').innerHTML='';$('lw-source-id').value='';
        const p=await call('/match',{schema:'export-lemma-matching-v1',target:JSON.parse($('lw-match-target').value),budget:{max_depth:4,max_rewrite_steps:3,max_terms:32,max_expansions:1600,max_candidates:2}});
        $('lw-matches').innerHTML=target(p.target,p.formal_statement)+`<p class="lw-note">扫描 ${p.stats.headers_scanned} 条目录，复核 ${p.stats.live_source_audits} 个来源，生成 ${p.candidates.length} 个待检方案${p.catalog_truncated?'；目录或来源复核已达上限':''}。</p>`+p.candidates.map((c,i)=>`<article class="lw-choice"><header><h3>复用方案 ${i+1}</h3><span class="lw-tag">UNKNOWN / E0</span></header><p>${c.request.sources.length} 个已证来源 · ${c.applications.length} 次显式调用</p><details><summary>实参、全部原条件证明与来源</summary><pre>${esc(json({sources:c.request.sources,applications:c.applications}))}</pre></details><button class="button secondary" data-match="${i}">带入并核对完整应用</button></article>`).join('')+(p.candidates.length?'':'<p class="lw-warning">在本次范围内没有找到完整应用；不能据此判断命题为假。查看下方未闭合条件。</p>')+`<details><summary>来源复核与未闭合条件</summary><pre>${esc(json({sources:p.source_audits,diagnostics:p.diagnostics,budget:p.budget,stats:p.stats}))}</pre></details>`;
        document.querySelectorAll('[data-match]').forEach(e=>{e.onclick=()=>action(()=>{const c=p.candidates[Number(e.dataset.match)];return preview(c.request,c.source_fingerprints);});});message('匹配只生成待检程序，没有执行 Lean。');
      });
    }
    document.querySelectorAll('[data-kind]').forEach(e=>e.setAttribute('aria-selected',String(e.dataset.kind===s.kind)));
    if(s.kind==='programs'){
      $('lw-target').addEventListener('input',()=>{invalidate();$('lw-target-summary').innerHTML='';clearRecord();controls();});
      $('lw-program').addEventListener('input',()=>{invalidate();clearRecord();controls();});
      $('lw-example').onclick=()=>action(async()=>{invalidate();clearRecord();const v=await call('/example');$('lw-target').value=json(v.target);$('lw-target-summary').innerHTML=target(v.target);message('示例已载入，请核对完整条件和目标。');});
      $('lw-propose').onclick=()=>action(async()=>{
        invalidate();clearRecord();const epoch=s.epoch, raw={schema:'structure-recursion-proposals-v2',target:JSON.parse($('lw-target').value),budget:{max_candidates:2,max_expansions:800,max_rewrite_steps:4}};
        const p=await call('/propose',raw,epoch);s.proposals=p;$('lw-target-summary').innerHTML=target(p.target,p.formal_statement);
        $('lw-proposals').innerHTML=p.candidates.map((c,i)=>`<article class="lw-choice"><header><h3>路线 ${i+1}</h3><span class="lw-tag">${c.request.program.lemmas.length} 个局部义务</span></header>${c.request.program.lemmas.map(l=>`<p class="lw-formula">${esc(l.name)} : ${esc(expression(l.goal))}</p>`).join('')||'<p class="lw-note">此路线直接检查完整目标。</p>'}<p class="lw-note">候选 UNKNOWN / E0 · 全部义务与主目标共享 ${c.request.timeout_seconds} 秒 worker</p><button class="button secondary" data-candidate="${i}">核对这条完整路线</button></article>`).join('');
        document.querySelectorAll('[data-candidate]').forEach(e=>{e.onclick=()=>action(()=>preview(p.candidates[Number(e.dataset.candidate)].request));});message('候选已生成，没有执行 Lean。');
      });
      $('lw-program-preview').onclick=()=>action(()=>preview(JSON.parse($('lw-program').value)));
    }else{
      $('lw-source-select').onchange=()=>{if(s.pending)return;invalidate();s.source=null;clearRecord();$('lw-source-detail').innerHTML='';$('lw-source-form').innerHTML='';$('lw-source-id').value=$('lw-source-select').value;controls();};
      $('lw-source-id').oninput=()=>{invalidate();s.source=null;clearRecord();$('lw-source-detail').innerHTML='';$('lw-source-form').innerHTML='';controls();};
      $('lw-source-load').onclick=()=>action(()=>loadSource($('lw-source-id').value.trim()));
    }
  }
  async function loadSource(key){
    invalidate();clearRecord();s.source=null;$('lw-source-detail').innerHTML='';$('lw-source-form').innerHTML='';
    const sourceKind=s.kind==='exports'?'programs':'exports';
    const row=await call('/'+sourceKind+'/'+encodeURIComponent(key));
    if(row.math_verdict!=='PROVED'||row.evidence_level!=='E4'||row.invalidated||!row.source_fingerprint)throw Error('该来源没有当前完整 E4，不能继续。');
    if(s.kind==='applications')s.source=await call('/exports/'+key+'/source');else s.source=row;
    $('lw-source-detail').innerHTML=`<div class="lw-chain"><span>当前来源 · ${esc(key)}</span><span>PROVED / E4</span></div>${target(row.plan.target,row.plan.formal_statement)}`;
    if(s.kind==='exports'){
      $('lw-source-form').innerHTML=`<hr class="lw-rule"><label>待导出的局部引理<select id="lw-lemma">${row.plan.local_lemmas.map(l=>`<option value="${esc(l.name)}">${esc(l.name)}</option>`).join('')}</select></label><div id="lw-helper"></div><label>独立定理标题<input id="lw-export-title" maxlength="200" placeholder="为这个引理命名"></label><label>独立语义描述<textarea id="lw-export-original" class="lw-description" maxlength="12000" placeholder="描述此引理的对象、量词、全部条件和结论"></textarea></label><label>候选与重放共享 worker 秒<input id="lw-export-seconds" type="number" min="1" max="30" value="20"></label><button id="lw-source-preview" class="button primary">预览独立完整定理</button>`;
      const draw=()=>{const l=row.plan.local_lemmas.find(v=>v.name===$('lw-lemma').value);$('lw-helper').innerHTML=l?`<div class="lw-summary"><strong>选中的局部义务</strong><div class="lw-formula">${esc(expression(l.compiled.request.goal))}</div></div>`:'';};draw();
      $('lw-lemma').onchange=()=>{invalidate();clearRecord();draw();controls();};
      $('lw-source-preview').onclick=()=>action(()=>preview({schema:'structure-lemma-export-v1',source:{run_id:key,fingerprint:row.source_fingerprint},lemma:$('lw-lemma').value,title:$('lw-export-title').value,original:$('lw-export-original').value,timeout_seconds:Number($('lw-export-seconds').value)}));
    }else{
      const source=s.source,r=structuredClone(source.request);delete r.proof;r.title='应用引理 · '+r.title;r.original='在以下完整环境和全部原条件下，核对本次显式应用的完整目标。';
      const raw={schema:'structure-export-application-v1',target:r,sources:[{alias:'core',item_id:source.item_id}],proof:{lemma:'core',terms:r.variables.map(v=>({var:v.name})),proofs:r.assumptions.map(a=>({hyp:a.name}))},timeout_seconds:r.timeout_seconds};
      $('lw-source-form').innerHTML=`<hr class="lw-rule"><p class="lw-note">已带入原变量与全部条件。可编辑新目标与 terms/proofs；同一载体、运算和定义环境须保持。</p><label>完整应用数据<textarea id="lw-application" spellcheck="false"></textarea></label><button id="lw-source-preview" class="button primary">预览新目标与全部调用</button>`;
      $('lw-application').value=json(raw);$('lw-match-target').value=json(raw.target);$('lw-source-preview').onclick=()=>action(()=>preview(JSON.parse($('lw-application').value)));
    }
    $('lw-source-form').querySelectorAll('input,textarea').forEach(e=>e.addEventListener('input',()=>{invalidate();clearRecord();controls();}));
    message('来源已核对。提交和检查前仍会重新核对来源是否有效。');
  }
  async function preview(raw,expectedSources=null){
    invalidate();clearRecord();const p=await call('/'+s.kind+'/preview',raw);
    if(expectedSources){const current=Object.fromEntries(p.source_items.map(v=>[v.item_id,v.archive_fingerprint]));if(Object.keys(current).length!==Object.keys(expectedSources).length||Object.entries(expectedSources).some(([k,v])=>current[k]!==v))throw Error('匹配来源已经变化，请重新匹配并确认。');}
    s.plan=p;drawReview(p);message('已预览完整陈述，尚未执行 Lean。');
  }
  function chain(p){
    if(p.parent_source)return `<div class="lw-chain"><span>完整父证明</span><span>→ ${esc(p.parent_source.lemma)}</span><span>→ 独立引理</span></div><p class="lw-note">父来源 ${esc(p.parent_source.run_id)}<br>指纹 ${esc(p.parent_source.fingerprint)}</p>`;
    if(p.source_items?.length)return `<div class="lw-chain"><span>完整父证明</span><span>→ 导出引理 ${p.source_items.length} 项</span><span>→ 本次完整目标</span></div>${p.source_items.map(v=>`<details><summary>来源 ${esc(v.alias)} · ${esc(v.item_id)}</summary><p class="lw-note">${esc(v.archive_fingerprint)}</p><pre>${esc(json(v.ancestors))}</pre></details>`).join('')}`;
    return '<p class="lw-note">局部引理是本次待检查义务，不是额外假设。</p>';
  }
  function drawReview(p){
    $('lw-review').innerHTML=`<div class="lw-card-head"><h2>核对完整陈述</h2><span class="lw-pill">UNKNOWN / E0</span></div><div class="lw-body">${chain(p)}${target(p.target,p.formal_statement)}${p.local_lemmas?.length?`<h3>按顺序检查的局部义务 · ${p.local_lemmas.length}</h3>${p.local_lemmas.map(l=>`<details><summary>${esc(l.name)}</summary><pre>${esc(l.compiled.formal_statement)}</pre></details>`).join('')}`:''}${p.applications?.length?`<h3>显式调用与全部条件证明</h3>${p.applications.map(a=>`<div class="lw-summary"><strong>${esc(a.alias)}</strong>${Object.entries(a.substitution).map(([k,v])=>`<p>${esc(k)} ↦ ${esc(expression(v))}</p>`).join('')}${a.instantiated_assumptions.map((h,i)=>`<p>${esc(h.name)} : ${esc(expression(h.formula))}</p><pre>${esc(json(a.premise_proofs[i]))}</pre>`).join('')}<p class="lw-formula">⊢ ${esc(expression(a.instantiated_goal))}</p></div>`).join('')}`:''}<p class="lw-note">1 个候选进程 + 1 个独立重放进程，共享 ${p.request.timeout_seconds} 秒 worker。完整性核对和等待另计。</p><details><summary>完整计划、证明程序与来源指纹</summary><pre>${esc(json(p))}</pre></details><label>语义核对人<input id="lw-reviewer" maxlength="200" autocomplete="name"></label><label class="lw-check"><input id="lw-confirm" type="checkbox"><span>我已核对原文、完整量词、环境、全部条件、局部义务和调用，确认本次完整陈述符合原意，并同意本次预算。姓名仅为本地声明。</span></label><button class="button primary" id="lw-submit" disabled>确认并检查一次</button><p id="lw-request" class="lw-pending"></p></div>`;
    $('lw-reviewer').oninput=controls;$('lw-confirm').onchange=controls;
    $('lw-submit').onclick=()=>action(async()=>{
      if(!s.plan)return;
      if(!s.pending)s.pending={request_id:'lemma-ui-'+crypto.randomUUID().replaceAll('-',''),request:s.plan.request,plan_fingerprint:s.plan.plan_fingerprint,reviewer:$('lw-reviewer').value,confirmed:true};
      controls();$('lw-request').textContent='请求 ID：'+s.pending.request_id;
      const row=await call('/'+s.kind+'/runs',s.pending);s.pending=null;s.plan=null;$('lw-review').innerHTML='';await openRecord(s.kind,row.run_id);message('任务已登记；数学判断以它自己的完整检查结果为准。');
    });controls();
  }
  async function openRecord(kind,key){
    invalidate();clearRecord();s.kind=kind;
    const row=await call('/'+kind+'/'+encodeURIComponent(key));s.record=row;drawRecord(row);schedule();
  }
  function drawRecord(row){
    $('lw-record').innerHTML=`<div class="lw-card-head"><h2>${esc(names[s.kind])}证据</h2><span class="lw-pill" data-proved="${row.math_verdict==='PROVED'}">${esc(row.evidence_level)}</span></div><div class="lw-body"><div class="lw-status"><div><small>执行状态</small><strong id="lw-state">${esc(row.state)}</strong></div><div><small>数学判断 · 与运行状态分开</small><strong id="lw-verdict">${esc(row.math_verdict)} / ${esc(row.evidence_level)}</strong></div></div><p class="lw-pending">${esc(row.run_id)}</p>${row.invalidated?'<div class="lw-warning">此证据已持续停用。恢复旧文件不会恢复确认。</div>':''}${chain(row.plan)}${target(row.plan.target,row.plan.formal_statement)}<p class="lw-note">候选 PID ${esc(row.result?.candidate?.pid??'—')} · 重放 PID ${esc(row.result?.replay?.pid??'—')}<br>重放完整类型匹配：${esc(row.result?.replay?.audit?.type_matches??'尚未取得')}<br>公理审计：${esc(row.result?.replay?.audit?json(row.result.replay.audit.axioms):'尚未取得')}<br>工件：${esc(row.artifact_status)} · 具名核对：${esc(row.alignment.reviewer)}</p>${row.result?.error?`<details><summary>未完成的检查反馈</summary><pre>${esc(row.result.error)}</pre></details>`:''}<div class="lw-actions"><button class="button secondary" id="lw-record-refresh">重新核对证据</button><button class="button secondary" id="lw-download">导出当前完整记录</button>${['QUEUED','RUNNING'].includes(row.state)?'<button class="button secondary" id="lw-cancel">取消本次检查</button>':''}</div>${row.math_verdict==='PROVED'&&s.kind!=='applications'?`<hr class="lw-rule"><button class="button primary" data-next>${s.kind==='programs'?'选择局部引理，独立导出':'带入新目标，显式复用'}</button>`:''}<details><summary>完整检查、工件摘要和语义对齐记录</summary><pre>${esc(json(row))}</pre></details><details><summary class="lw-danger">具名撤销此证据</summary><form id="lw-revoke"><label>撤销人<input id="lw-revoke-reviewer" required maxlength="200"></label><label>原因<input id="lw-revoke-reason" required maxlength="500"></label><label class="lw-check"><input id="lw-revoke-confirm" type="checkbox" required><span>确认持续撤销本记录，依赖它的后代在复核时也会失效。</span></label><button class="button secondary lw-danger">确认撤销</button></form></details></div>`;
    const kind=s.kind,key=row.run_id;
    $('lw-record-refresh').onclick=()=>action(()=>openRecord(kind,key));
    $('lw-download').onclick=()=>action(async()=>{await openRecord(kind,key);const blob=new Blob([json(s.record)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=key+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    if($('lw-cancel'))$('lw-cancel').onclick=()=>action(async()=>{await call('/'+kind+'/'+key+'/cancel',{});await openRecord(kind,key);});
    if($('lw-record').querySelector('[data-next]'))$('lw-record').querySelector('[data-next]').onclick=()=>action(async()=>{await switchKind(kind==='programs'?'exports':'applications');$('lw-source-id').value=key;await loadSource(key);});
    $('lw-revoke').onsubmit=event=>{event.preventDefault();const data={reviewer:$('lw-revoke-reviewer').value,reason:$('lw-revoke-reason').value,confirmed:$('lw-revoke-confirm').checked};action(async()=>{invalidate();clearRecord();await call('/'+kind+'/'+key+'/revoke',data);await openRecord(kind,key);message('已撤销本记录。后代下次复核时会重新检查来源。');});};controls();
  }
  function schedule(){
    clearTimeout(s.timer);if(!s.record||!['QUEUED','RUNNING'].includes(s.record.state))return;
    const epoch=s.epoch,key=s.record.run_id,kind=s.kind;
    s.timer=setTimeout(()=>action(async()=>{if(epoch!==s.epoch)return;clearRecord();const row=await call('/'+kind+'/'+key,undefined,epoch);s.record=row;drawRecord(row);schedule();}),1800);
  }
  async function history(){
    const rows=[];for(const kind of Object.keys(names)){const v=await call('/'+kind);rows.push(...v.runs.map(r=>({...r,kind})));}s.history=rows;
    const sourceKind=s.kind==='exports'?'programs':s.kind==='applications'?'exports':null;
    if($('lw-source-select')){$('lw-source-select').innerHTML='<option value="">选择当前完整 E4 来源</option>'+rows.filter(r=>r.kind===sourceKind&&r.math_verdict==='PROVED'&&!r.invalidated).map(r=>`<option value="${esc(r.run_id)}">${esc(r.title)} · ${esc(r.run_id.slice(-8))}</option>`).join('');}
    $('lw-history-list').innerHTML=rows.sort((a,b)=>b.created_at.localeCompare(a.created_at)).slice(0,30).map(r=>`<button data-open="${esc(r.run_id)}" data-record-kind="${r.kind}"><span class="lw-pill" data-proved="${r.math_verdict==='PROVED'}">${esc(names[r.kind])} · ${esc(r.math_verdict)} / ${esc(r.evidence_level)}</span><strong>${esc(r.title)}</strong><small>${esc(r.state)} · ${esc(r.run_id)}</small></button>`).join('')||'<p class="lw-note">尚无记录。先载入示例，规划一条完整证明路线。</p>';
    document.querySelectorAll('[data-open]').forEach(e=>{e.onclick=()=>action(async()=>{await switchKind(e.dataset.recordKind,false);await openRecord(e.dataset.recordKind,e.dataset.open);});});controls();
  }
  async function switchKind(kind,reload=true){
    invalidate();clearRecord();s.kind=kind;form();if(reload)await history();
    historyState();message('已切换流程，旧确认已清除。');
  }
  function historyState(){window.history.replaceState(null,'','#'+s.kind);}
  function mount(){
    $('page').innerHTML=`<div class="lw"><header class="lw-hero"><div><div class="lw-eyebrow">MATHEMATICAL RESEARCH / LEMMA STUDIO</div><h1>把一次证明，变成下一步的起点。</h1><p>从完整目标规划局部义务，把经过核验的引理独立积累，再带着全部条件进入新的证明。</p></div><div class="lw-mark" aria-hidden="true">λ ⊢</div></header><nav class="lw-mode" aria-label="数学研究模式"><a href="/typed-research">01 · 数域与定义<small>NAT / INT / RAT</small></a><a href="/typed-research#///structure">02 · 结构与归纳<small>TYPE / LAWS / INDUCTION</small></a><a href="/typed-research/lemmas" aria-current="page">03 · 递归与引理<small>PLAN / VERIFY / REUSE</small></a></nav><div class="lw-flow" role="tablist" aria-label="引理研究流程"><button data-kind="programs" role="tab"><b>01</b><span>规划与检查<small>完整目标 · 局部义务</small></span></button><button data-kind="exports" role="tab"><b>02</b><span>独立导出<small>新陈述 · 新验证</small></span></button><button data-kind="applications" role="tab"><b>03</b><span>跨任务复用<small>显式条件 · 完整证明</small></span></button></div><div id="lw-message" class="lw-message" role="status" aria-live="polite"></div><div class="lw-grid"><section class="lw-card"><div class="lw-card-head"><h2 id="lw-input-title"></h2><span class="lw-tag">RESEARCH</span></div><div class="lw-body"><p id="lw-input-note" class="lw-note"></p><div id="lw-input"></div></div></section><section><div id="lw-review" class="lw-card"></div><div id="lw-record" class="lw-card lw-evidence"></div></section></div><section class="lw-history"><div class="lw-actions"><h2>证明与引理记录</h2><button id="lw-history-refresh" class="button secondary">重新核对记录与来源</button></div><div id="lw-history-list" class="lw-history-list"></div></section><p class="lw-footer">当前支持显式结构、闭合定律与有界递归规划。模型尚未配置。每次检查包含候选与独立重放，使用同一固定 Lean 内核实现。语义核对人是本地声明；条件一致性和数学新颖性不由检查器保证。来源撤销在访问时级联，失败或超时保持 UNKNOWN。</p></div>`;
    const initial=location.hash.slice(1);if(Object.hasOwn(names,initial))s.kind=initial;
    form();clearRecord();document.querySelectorAll('[data-kind]').forEach(e=>{e.onclick=()=>action(()=>switchKind(e.dataset.kind));});
    $('lw-history-refresh').onclick=()=>action(async()=>{invalidate();clearRecord();s.source=null;form();await history();message('已重新核对记录与来源，没有重新执行数学检查。');});
    document.querySelectorAll('.lw-mode a').forEach(e=>e.addEventListener('click',event=>{if(s.busy||s.pending)event.preventDefault();}));
    action(()=>history());
  }
  window.MRALemmaResearch={mount};
})();
