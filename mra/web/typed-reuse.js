"use strict";
(() => {
  const $=id=>document.getElementById(id), esc=v=>window.MRA.escape(String(v)), json=v=>JSON.stringify(v,null,2);
  const structured=()=>s.context?.mode==='structure';
  const api=(path,body)=>{
    const gen=s.gen;let waited=false;
    return window.MRATypedTransport.request('/v1/'+(structured()?'structure-reuse':'typed-reuse')+path,body,{isCurrent:()=>gen===s.gen,onBusy:()=>{if(gen===s.gen){waited=true;note('证据正在核对，稍后将自动重新读取。');}}}).then(value=>{if(waited&&gen===s.gen)note('证据已重新核对。');return value;});
  };
  const s={gen:0,version:0,runVersion:0,context:null,catalog:null,sources:[],plan:null,pending:null,run:null,busy:false,reading:0,timer:null,draft:'',seconds:30,suggestions:null};
  function expression(v) {
    if(structured())return window.MRAStructureNotation.expression(v);
    if(!v||typeof v!=='object')return String(v);
    const [op,x]=Object.entries(v)[0]||[];
    if(['var','nat','int'].includes(op))return String(x);
    if(op==='rat')return x[0]+'/'+x[1];
    if(op==='call')return x.name+'('+x.args.map(expression).join(', ')+')';
    if(op==='cast')return '('+expression(x.value)+' : '+x.to+')';
    if(['forall','exists'].includes(op))return (op==='forall'?'∀ ':'∃ ')+x.binders.map(b=>b.name+' : '+b.type).join(', ')+', '+expression(x.body);
    if(['not','neg'].includes(op))return (op==='not'?'¬':'−')+expression(x);
    const symbol={eq:'=',add:'+',sub:'−',mul:'×',div:'/',le:'≤',lt:'<',and:'∧',or:'∨',imp:'→',iff:'↔'}[op];
    return symbol?'('+x.map(expression).join(' '+symbol+' ')+')':json(v);
  }
  function note(text,error=false){if($('tru-message')){$('tru-message').textContent=text;$('tru-message').dataset.error=String(error);}}
  function clearReview(){s.version++;s.suggestions=null;if($('tru-suggestions'))$('tru-suggestions').innerHTML='';s.plan=null;s.pending=null;if($('tru-review')){$('tru-review').hidden=true;$('tru-review').innerHTML='';}controls();}
  function controls(){
    const waiting=s.busy||s.reading>0;
    for(const e of document.querySelectorAll('#tru-panel [data-tru-edit]'))e.disabled=waiting||!!s.pending;
    for(const e of document.querySelectorAll('#tru-run button,#tru-history button'))e.disabled=waiting;
    if($('tru-reviewer'))$('tru-reviewer').disabled=waiting||!!s.pending;
    if($('tru-confirm'))$('tru-confirm').disabled=waiting||!!s.pending;
    if($('tru-preview'))$('tru-preview').disabled=waiting||!s.catalog?.context.depends_on.length||s.sources.some(x=>!x.detail);
    if($('tru-submit')){$('tru-submit').disabled=waiting||!s.plan||(!$('tru-confirm').checked&&!s.pending)||!$('tru-reviewer').value.trim();$('tru-submit').textContent=s.pending?'按原请求 ID 手动重发':'确认并执行一次组合';}
    if($('tru-reload'))$('tru-reload').disabled=s.busy||!s.context;
    if($('tru-suggest'))$('tru-suggest').disabled=waiting||!!s.pending||!s.catalog?.context.depends_on.length;
    document.querySelectorAll('[data-tru-candidate]').forEach(b=>b.disabled=waiting||!!s.pending);
  }
  function ensure(){
    if($('tru-panel'))return true;
    const parent=document.querySelector('#page .tr');if(!parent)return false;
    const panel=document.createElement('section');panel.id='tru-panel';panel.className='tr-box tru';
    panel.innerHTML='<header class="tru-heading"><div><span class="tr-kicker">PROOF REUSE / 03</span><h2>让已证引理，成为下一步。</h2><p>显式调用前置证明，核验完整目标。</p></div><button id="tru-reload" class="button secondary" disabled>重新核对来源与记录</button></header><p id="tru-message" role="status" aria-live="polite"></p><div id="tru-body"></div>';
    parent.querySelector('.tr-bottom').before(panel);
    $('tru-reload').addEventListener('click',()=>void load());return true;
  }
  function environment(r){
    const definitions=`<div class="tru-definitions">${r.definitions.map(d=>`<article><code>${esc(d.name)}(${esc(d.parameters.map(p=>p.name+' : '+p.type).join(', '))}) : ${esc(d.result_type)}</code><p>${esc(d.original)}</p><p class="tru-goal">${esc(expression(d.body))}</p></article>`).join('')||'<p>无共享定义</p>'}</div>`;
    if(!structured())return `<details><summary>共享定义 · ${r.definitions.length} 项</summary>${definitions}</details>`;
    return `<div class="tru-environment"><div class="tr-heading"><h3>完整结构环境</h3><span class="tr-badge">逐字绑定来源与目标</span></div><div class="tru-carriers">${r.carriers.map(c=>`<article><code>${esc(c.name)} : Type</code><p>${esc(c.original)}</p></article>`).join('')}</div><div class="tru-operations">${r.operations.map(o=>`<p><code>${esc(o.name)} : ${esc([...o.parameters,o.result_type].join(' → '))}</code><span>${esc(o.original)}</span></p>`).join('')}</div><h4>闭合定义 · ${r.definitions.length} 项</h4>${definitions}</div>`;
  }
  function target(r){return `<h3>${esc(r.title)}</h3><p class="tr-original">${esc(r.original)}</p><p class="tr-variable">${esc(r.variables.map(v=>v.name+' : '+v.type).join(' · ')||'无自由变量')}</p><div class="tru-conditions">${r.assumptions.map(a=>`<p><code>${esc(a.name)}</code> ${esc(expression(a.formula))}</p>`).join('')||'<p>无原前提</p>'}</div><p class="tru-goal">⊢ ${esc(expression(r.goal))}</p>`;}
  function drawSources(){
    if(!$('tru-sources'))return;
    $('tru-sources').innerHTML=s.sources.map((entry,i)=>{
      const options=s.catalog.source_candidates.filter(c=>c.node_id===entry.node);
      const d=entry.detail;
      return `<article class="tru-source"><div class="tru-source-heading"><span class="tru-index">${String(i+1).padStart(2,'0')}</span><h3>前置 ${esc(entry.node)}</h3><span class="tr-badge">${d?'已核对 · E4':'待核对'}</span></div><label>证明来源<select id="tru-source-${i}" data-tru-edit><option value="">选择当前来源</option>${options.map(c=>`<option value="${esc(c.item_id)}" ${c.item_id===entry.id?'selected':''}>${c.origin.endsWith('-graph-node')?'原图检查':'组合记录'} · ${esc(c.item_id)}${c.stored_invalidated?' · 已停用':''}</option>`).join('')}</select></label><label>调用别名<input id="tru-alias-${i}" data-tru-edit value="${esc(entry.alias)}" maxlength="24"></label>${d?`<p class="tr-variable">${esc(d.request.variables.map(v=>v.name+' : '+v.type).join(' · ')||'无自由变量')}</p><div class="tru-conditions">${d.request.assumptions.map(a=>`<p><code>${esc(a.name)}</code> ${esc(expression(a.formula))}</p>`).join('')||'<p>无原前提</p>'}</div><p class="tru-goal">⊢ ${esc(expression(d.request.goal))}</p><p class="tr-subtle">需显式提供 ${d.request.variables.length} 项实参与 ${d.request.assumptions.length} 项条件证明。</p><details><summary>完整来源类型、语义确认与祖先</summary><pre class="tr-formula">${esc(d.formal_statement)}</pre>${environment(d.request)}<pre class="tr-json">${esc(json(d))}</pre></details>`:`<p class="tr-note">${esc(entry.error||'选择来源后重新核对完整证据；目录记录本身不是可用证明。')}</p>`}</article>`;
    }).join('');
    s.sources.forEach((entry,i)=>{
      $('tru-source-'+i).addEventListener('change',()=>{entry.id=$('tru-source-'+i).value;entry.detail=null;entry.error=null;clearReview();void loadSource(i,s.gen);});
      $('tru-alias-'+i).addEventListener('input',()=>{entry.alias=$('tru-alias-'+i).value;clearReview();});
    });controls();
  }
  async function loadSource(i,gen){
    const entry=s.sources[i],id=entry.id;if(!id){drawSources();return;}
    s.reading++;controls();
    try{const d=await api('/sources/'+encodeURIComponent(id));if(gen===s.gen&&s.sources[i]===entry&&entry.id===id)entry.detail=d;}
    catch(error){if(gen===s.gen&&s.sources[i]===entry){entry.detail=null;entry.error=error.message;}}
    finally{if(gen===s.gen){s.reading--;drawSources();}}
  }
  function history(){
    $('tru-history').innerHTML=`<h3>本节点组合记录</h3><p class="tr-note">共 ${s.catalog.matching_runs} 条，展示最近 ${s.catalog.history.length} 条。点击记录重新核对完整证据。</p><div class="tru-history">${s.catalog.history.map(r=>`<button class="tru-history-item" data-run="${esc(r.run_id)}"><span>${esc(r.state)}${r.invalidated?' · 已停用':''}</span><strong>${esc(r.math_verdict)} / ${esc(r.evidence_level)}</strong><small>${esc(r.run_id)}</small></button>`).join('')||'<p class="tr-note">尚无组合检查。预览与来源选择不会执行 Lean。</p>'}</div>`;
    $('tru-history').querySelectorAll('[data-run]').forEach(b=>b.addEventListener('click',()=>{if(!s.busy)void loadRun(b.dataset.run,s.gen);}));
    controls();
  }
  function draw(){
    const c=s.catalog.context,r=c.target;
    $('tru-body').innerHTML=`<div class="tru-identity"><span>节点 ${esc(c.node_id)}</span><code>${esc(c.graph_id)}</code><span class="tru-separate">组合证据单独记录 · 原图判断不自动晋升</span></div><div class="tru-map"><span>核对前置证明</span><b>→</b><span>填写完整调用</span><b>→</b><span>内核导入 + 独立重放</span></div>${environment(r)}<div class="tru-target">${target(r)}<details><summary>完整原目标数据</summary><pre class="tr-json">${esc(json(r))}</pre></details></div>${c.depends_on.length?'<div id="tru-sources" class="tru-sources"></div>':'<p class="tr-note">此节点没有前置，请使用上方原图检查建立根引理证据。</p>'}${structured()?'<div class="tru-suggestion-heading"><div><span class="tr-kicker">LEMMA MATCHING</span><h3>从已证引理，寻找完整调用。</h3><p>自动匹配实参与全部条件；选择后仍需预览、具名核验。</p></div><button id="tru-suggest" class="button secondary">匹配已证引理</button></div><div id="tru-suggestions"></div>':''}<div class="tru-compose"><div><label for="tru-proof">结构化证明调用</label><textarea id="tru-proof" data-tru-edit class="tru-editor" spellcheck="false"></textarea><p class="tr-note">使用来源别名、完整 terms 与 proofs。支持 hyp、pair、fst/snd、mp/mpr、apply、refl、symm、trans${structured()?'、congr（显式函数及等式）':''}；不接受任意 Lean。</p></div><div class="tru-budget"><label>候选与重放共享预算<input id="tru-seconds" data-tru-edit type="number" min="1" max="30" step="1" value="${s.seconds}">秒</label><p>1 个候选进程<br>1 个独立重放进程<br>完整性核对与等待另计</p><p>最多 200 条组合记录；额度与原图 16 次／480 秒分别记账。</p><button id="tru-preview" class="button primary">预览完整组合与所有条件</button></div></div><div id="tru-review" class="tr-review" hidden></div><div id="tru-history"></div><div id="tru-run"></div>`;
    $('tru-proof').value=s.draft;
    if($('tru-suggest'))$('tru-suggest').addEventListener('click',()=>void act(async gen=>{
      clearReview();const version=s.version;
      note('正在重新核对已证来源并匹配完整调用…');
      const value=await api('/graphs/'+c.graph_id+'/nodes/'+c.node_id+'/suggestions',{budget:{max_depth:4,max_rewrite_steps:3,max_terms:32,max_expansions:1600,max_candidates:4},timeout_seconds:s.seconds});
      if(gen!==s.gen||version!==s.version)return;s.suggestions=value;drawSuggestions(value);
      note(value.candidates.length?'已生成未验证的调用建议。选择后重新预览全部条件。':'本次有界匹配未找到完整调用，请查看条件推演记录。');
    }));
    $('tru-proof').addEventListener('input',()=>{s.draft=$('tru-proof').value;clearReview();});
    $('tru-seconds').addEventListener('input',()=>{s.seconds=Number($('tru-seconds').value);clearReview();});
    $('tru-preview').addEventListener('click',()=>void act(async gen=>{
      clearReview();const version=s.version;
      const raw={schema:structured()?'structure-proof-composition-v1':'typed-proof-composition-v1',graph_id:c.graph_id,node_id:c.node_id,sources:s.sources.map(x=>({alias:x.alias,item_id:x.id})),proof:JSON.parse(s.draft),timeout_seconds:s.seconds};
      const plan=await api('/preview',raw);if(gen!==s.gen||version!==s.version)return;s.plan=plan;review(plan);note('预览完成，尚未执行。请核对每个实参与全部条件证明。');
    }));drawSources();history();controls();
  }
  function drawSuggestions(value){
    const reasons={no_direct_prerequisites:'此节点没有直接前置',current_e4_source_unavailable:'缺少当前可用的前置证据',no_complete_program_with_all_direct_sources_within_bounds:'预算内未找到使用全部直接前置的完整程序',premise_program_not_found_within_bounds:'预算内未找到以下条件的完整证明',no_typed_argument_in_bounded_pool:'有界实参池内没有匹配类型',cyclic_obligation:'遇到循环条件',proof_depth_limit:'达到条件推演深度上限'};
    $('tru-suggestions').innerHTML='<p class="tr-note">建议与条件推演均为 UNKNOWN / E0；原图判断保持独立。</p><div class="tru-suggestion-grid">'+value.candidates.map((c,i)=>'<article class="tru-suggestion"><span class="tr-kicker">方案 '+(i+1)+' · '+c.applications.length+' 次引理调用</span><h4>UNKNOWN / E0</h4>'+c.applications.map(a=>'<p><code>'+esc(a.alias)+'</code> · '+esc(Object.entries(a.substitution).map(([n,t])=>n+' ↦ '+expression(t)).join('，')||'无自由参数')+'</p><p class="tru-goal">'+esc(expression(a.instantiated_goal))+'</p>').join('')+'<details><summary>完整实参、全部条件证明与来源指纹</summary><pre class="tr-json">'+esc(json(c))+'</pre></details><button class="button secondary" data-tru-candidate="'+i+'">带入调用草稿</button></article>').join('')+'</div><details class="tru-match-diagnostics"><summary>条件推演记录 · '+value.diagnostics.length+' 项（未执行 Lean）</summary>'+value.diagnostics.map(d=>'<article><p>'+esc(reasons[d.reason]||d.reason)+'</p>'+(d.item_id?'<code>'+esc(d.item_id)+'</code>':'')+'<p class="tru-goal">⊢ '+esc(expression(d.goal))+'</p>'+(d.obligations||[]).map(o=>'<p>'+esc(expression(o.formula))+' · '+(o.matched_original_hypothesis?'已在原前提直接匹配':'尚未在原前提直接匹配')+'</p>').join('')+'</article>').join('')+'<pre class="tr-json">'+esc(json({source_audits:value.source_audits,budget:value.budget,stats:value.stats,limitations:value.limitations}))+'</pre></details><details><summary>原图实际检查反馈 · '+value.feedback.length+' 条</summary><pre class="tr-json">'+esc(json(value.feedback))+'</pre></details>';
    $('tru-suggestions').querySelectorAll('[data-tru-candidate]').forEach(button=>button.addEventListener('click',()=>{
      const candidate=value.candidates[Number(button.dataset.truCandidate)];
      void act(async gen=>{
        clearReview();const version=s.version;
        if(value.context.context_fingerprint!==s.catalog.context.context_fingerprint)throw new Error('目标语境已变化，请重新匹配。');
        for(const entry of s.sources){const binding=candidate.request.sources.find(b=>b.item_id.split('.')[2]===entry.node)||candidate.request.sources.find(b=>value.source_audits.some(a=>a.item_id===b.item_id&&a.node_id===entry.node));if(!binding)throw new Error('建议缺少当前直接前置。');entry.alias=binding.alias;entry.id=binding.item_id;entry.detail=null;entry.error=null;}
        drawSources();for(let i=0;i<s.sources.length&&gen===s.gen;i++)await loadSource(i,gen);
        if(gen!==s.gen||version!==s.version)return;
        if(s.sources.some(e=>!e.detail||e.detail.source_fingerprint!==candidate.source_fingerprints[e.id]))throw new Error('来源证据已变化或不可用，请重新匹配。');
        s.draft=json(candidate.request.proof);$('tru-proof').value=s.draft;note('完整调用已带入草稿，旧确认已清除。请预览并核对全部条件。');
      });
    }));controls();
  }
  function review(p){
    $('tru-review').hidden=false;
    $('tru-review').innerHTML=`<div class="tr-heading"><h3>完整组合预览</h3><span class="tr-badge">UNKNOWN / E0</span></div><pre class="tr-formula">${esc(p.formal_statement)}</pre><div class="tru-applications">${p.applications.map((a,i)=>`<article><span class="tr-kicker">CALL ${i+1} · ${esc(a.alias)}</span>${a.environment_arguments?`<div class="tru-environment-args"><strong>显式载体与运算实参 · ${a.environment_arguments.length} 项</strong><p>${a.environment_arguments.map(v=>`<code>${esc(v)}</code>`).join(' → ')}</p><p class="tr-note">按上方载体、运算的声明顺序传入；定义在完整类型内绑定。</p></div>`:''}<p>${Object.entries(a.substitution).map(([k,v])=>`<code>${esc(k)}</code> ↦ ${esc(expression(v))}`).join('<br>')||'无量化实参'}</p>${a.instantiated_assumptions.map((h,j)=>`<div class="tru-premise"><strong>条件 ${j+1} · ${esc(h.name)}</strong><p>${esc(expression(h.formula))}</p><pre class="tr-json">${esc(json(a.premise_proofs[j]))}</pre></div>`).join('')}<p class="tru-goal">⊢ ${esc(expression(a.instantiated_goal))}</p></article>`).join('')}</div><details><summary>完整预览、来源指纹与有界证明树</summary><pre class="tr-json">${esc(json(p))}</pre></details><label>语义核对人<input id="tru-reviewer" maxlength="200" autocomplete="name"></label><label class="tr-confirm"><input id="tru-confirm" type="checkbox"><span>我已核对原文、${structured()?'全部载体与运算、':''}全部定义、变量类型、每条原前提证明、完整调用与目标，确认语义一致，并同意本次预算。姓名只是本地声明。</span></label><button id="tru-submit" class="button primary" disabled>确认并执行一次组合</button><p id="tru-request-id" class="tr-note"></p>`;
    $('tru-reviewer').addEventListener('input',()=>{$('tru-confirm').checked=false;controls();});
    $('tru-confirm').addEventListener('change',controls);
    $('tru-submit').addEventListener('click',()=>void act(async gen=>{
      if(!s.pending)s.pending={request_id:(structured()?'structure-reuse-ui-':'typed-reuse-ui-')+crypto.randomUUID().replaceAll('-',''),request:s.plan.request,plan_fingerprint:s.plan.plan_fingerprint,reviewer:$('tru-reviewer').value,confirmed:true};
      $('tru-request-id').textContent='请求 ID：'+s.pending.request_id+'；失败时仅手动按原正文重发。';
      $('tru-reviewer').disabled=true;const value=await api('/runs',s.pending);if(gen!==s.gen)return;
      s.pending=null;s.plan=null;$('tru-review').hidden=true;note('已登记一次组合。运行状态与数学判断分别显示。');await loadRun(value.run_id,gen);
    },true));controls();
  }
  async function act(work,write=false){
    if(s.busy)return;const gen=s.gen;s.busy=true;controls();
    if(write)document.dispatchEvent(new CustomEvent('mra:typed-reuse-writing',{detail:true}));
    try{await work(gen);}catch(error){if(gen===s.gen)note(error.message+(s.pending?'；可按原请求 ID 手动重发。':''),true);}
    finally{if(gen===s.gen){s.busy=false;controls();}if(write)document.dispatchEvent(new CustomEvent('mra:typed-reuse-writing',{detail:false}));}
  }
  function renderRun(r){
    const result=r.result||{},candidate=result.candidate,replay=result.replay;
    $('tru-run').innerHTML=`<div class="tru-result"><div class="tru-result-top"><div><span class="tr-kicker">COMPOSITION EVIDENCE</span><h3>组合数学判断</h3><strong id="tru-verdict">${esc(r.math_verdict)} / ${esc(r.evidence_level)}</strong></div><div><small>执行状态</small><strong id="tru-state">${esc(r.state)}</strong></div><button id="tru-export" class="button secondary">导出完整证据</button></div><p class="tr-subtle">${esc(r.run_id)}${r.invalidated?' · 证据已持续停用':''}</p><div class="tru-processes"><div><small>候选与来源内核导入</small><strong>PID ${esc(candidate?.pid??'—')}</strong><p>${candidate?.imports?'来源类型匹配 '+esc(candidate.imports.filter(i=>i.type_matches).length)+' / '+candidate.imports.length:'尚无成功导入审计'}</p></div><div><small>独立证明数据重放</small><strong>PID ${esc(replay?.pid??'—')}</strong><p>完整类型匹配：${esc(replay?.audit?.type_matches??'尚未取得')}</p></div></div>${result.error?`<p class="tr-danger">${esc(result.error)}</p>`:''}<details><summary>完整目标、语义确认、公理与工件审计</summary><pre class="tr-json">${esc(json(r))}</pre></details>${['QUEUED','RUNNING'].includes(r.state)?'<button id="tru-cancel" class="button secondary">取消此组合检查</button>':''}<details><summary class="tr-danger">具名撤销此组合证据</summary><form id="tru-revoke" class="tr-withdraw"><label>撤销人<input id="tru-revoke-reviewer" required maxlength="200"></label><label>原因<input id="tru-revoke-reason" required maxlength="500"></label><label class="tr-confirm"><input id="tru-revoke-confirm" type="checkbox" required><span>确认停用本组合；依赖它的后继将在审计时持续停用。原图检查保持自己的判断。</span></label><button class="button secondary tr-danger">确认撤销组合</button></form></details></div>`;
    $('tru-export').addEventListener('click',()=>void act(async gen=>{const fresh=await api('/runs/'+r.run_id+'/export');if(gen!==s.gen)return;const url=URL.createObjectURL(new Blob([json(fresh)+'\n'],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=r.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
    if($('tru-cancel'))$('tru-cancel').addEventListener('click',()=>void act(async gen=>{await api('/runs/'+r.run_id+'/cancel',{});await loadRun(r.run_id,gen);},true));
    $('tru-revoke').addEventListener('submit',event=>{event.preventDefault();const body={reviewer:$('tru-revoke-reviewer').value,reason:$('tru-revoke-reason').value,confirmed:$('tru-revoke-confirm').checked};void act(async gen=>{await api('/runs/'+r.run_id+'/revoke',body);clearReview();await loadRun(r.run_id,gen);note('组合已撤销。恢复旧工件或重证来源不会复活旧后继。');},true);});
    controls();
  }
  async function loadRun(id,gen){
    clearTimeout(s.timer);if(gen!==s.gen)return;const version=++s.runVersion;
    $('tru-run').innerHTML='<p class="tr-note">正在重新核对组合证据…</p>';
    try{const r=await api('/runs/'+id);if(gen!==s.gen||version!==s.runVersion)return;s.run=r;renderRun(r);
      if(['QUEUED','RUNNING'].includes(r.state)&&!r.invalidated)s.timer=setTimeout(()=>{if(!s.busy)void loadRun(id,gen);else s.timer=setTimeout(()=>void loadRun(id,gen),1500);},1500);
      else{const c=await api('/graphs/'+s.context.graph_id+'/nodes/'+s.context.node_id);if(gen!==s.gen||version!==s.runVersion)return;s.catalog=c;history();}
    }catch(error){if(gen===s.gen&&version===s.runVersion){s.run=null;$('tru-run').innerHTML='<p class="tr-danger">当前组合证据不可读取。旧判断已隐藏，请手动重新核对。</p>';note(error.message,true);}}
  }
  async function load(){
    if(!s.context)return;const gen=++s.gen;clearTimeout(s.timer);s.reading=0;s.busy=true;s.run=null;clearReview();note('正在读取完整目标与前置来源…');$('tru-body').innerHTML='<p class="tr-note">重新核对中；旧判断已隐藏。</p>';controls();
    try{
      const c=await api('/graphs/'+s.context.graph_id+'/nodes/'+s.context.node_id);if(gen!==s.gen)return;s.catalog=c;
      const previous=s.sources;
      s.sources=c.context.depends_on.map((node,i)=>{const old=previous.find(x=>x.node===node),choices=c.source_candidates.filter(x=>x.node_id===node);const choice=choices.find(x=>x.item_id===old?.id)||choices.find(x=>x.origin.endsWith('-graph-node')||x.stored_state==='COMPLETED'&&!x.stored_invalidated);return {node,alias:old?.alias||'lemma'+i,id:choice?.item_id||'',detail:null};});
      draw();for(let i=0;gen===s.gen&&i<s.sources.length;i++)await loadSource(i,gen);if(gen!==s.gen)return;
      note('目标与来源已读取。每份来源仍会在提交和执行门禁重新核对。');if(c.history.length)await loadRun(c.history[0].run_id,gen);
    }catch(error){if(gen===s.gen){s.catalog=null;$('tru-body').innerHTML='<p class="tr-danger">当前目标与证据不可读取，请手动重新核对。</p>';note(error.message,true);}}
    finally{if(gen===s.gen){s.busy=false;controls();}}
  }
  document.addEventListener('mra:research-context',event=>{
    if(!ensure())return;const context=event.detail;
    if(!context){s.gen++;s.suggestions=null;s.context=null;s.plan=null;s.pending=null;s.sources=[];s.busy=false;s.reading=0;clearTimeout(s.timer);$('tru-body').innerHTML='<p class="tr-note">请打开已保存任务并选择节点。编辑中的任务不能复用旧确认。</p>';note('');controls();return;}
    if(s.context?.mode===context.mode&&s.context?.graph_id===context.graph_id&&s.context?.node_id===context.node_id&&s.context?.revision===context.revision)return;
    const same=s.context?.mode===context.mode&&s.context?.graph_id===context.graph_id&&s.context?.node_id===context.node_id;s.context=context;
    $('tru-panel').dataset.mode=context.mode;
    $('tru-panel').querySelector('.tru-heading p').textContent=structured()?'显式绑定载体、运算及全部原条件，复用真实结构证明。':'显式调用前置证明，核验完整目标。';
    if(!same){s.sources=[];s.draft=json({lemma:'lemma0',terms:[],proofs:[]});s.seconds=30;}void load();
  });
})();
