"use strict";
(() => {
  if(!['/research','/mathlib'].includes(location.pathname))return;
  const {escape:e,icon,modal,notify,session}=window.MRA;
  const $=id=>document.getElementById(id),fields=['id','kind','request','depends_on','notes'];
  const labels={problem:'主问题',subproblem:'子问题',lemma:'引理'};
  let generation=0,scope=null,selection='',sourceRun=null;
  const ctx=()=>window.MRAResearch?.context();
  const base=p=>'/v1/research/'+encodeURIComponent(p)+'/mathlib/';
  async function api(path,body){const info=await session;const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MRA-CSRF':info.csrf_token},body:JSON.stringify(body)})});const value=await response.json();if(!response.ok)throw new Error(value.error||'暂时无法核对研究节点。');return value;}
  async function act(fn){try{await fn();}catch(error){notify(error.message);}}
  const contextData=row=>({branch_id:row.branch_id,expected_revision:row.revision,parent_context_fingerprint:row.parent_context_fingerprint});
  function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function clean(){if(ctx()?.dirty)throw new Error('请先保存当前研究图，再操作 Mathlib 节点。');}
  function mount(){
    ++generation;scope=null;
    const c=ctx();if(!c?.project)return;
    let target=null;try{target=JSON.parse(sessionStorage.getItem('mra-mathlib-research-return')||'null');}catch{ /* Ignore obsolete navigation state. */ }
    if(target?.project_id===c.project.project_id){sessionStorage.removeItem('mra-mathlib-research-return');const select=$('research-branch');if(select&&[...select.options].some(o=>o.value===target.branch_id)&&c.branchId!==target.branch_id){setTimeout(()=>{if(select.isConnected&&ctx()?.project?.project_id===target.project_id&&!ctx().dirty){select.value=target.branch_id;select.dispatchEvent(new Event('change',{bubbles:true}));}},0);return;}}
    selection=c.project.project_id+':'+c.branchId;
    $('research-mathlib')?.remove();
    const section=document.createElement('section');section.id='research-mathlib';section.className='panel rm-panel';
    section.innerHTML=`<div class="rm-heading"><div><span class="eyebrow">MATHLIB / RESEARCH THREAD</span><h2>把一条证明，放回研究脉络。</h2><p>完整命题、显式前置与可追溯来源，保存在当前研究线。</p></div><a class="button secondary" href="/mathlib">打开 Mathlib 实验 ${icon('arrow')}</a></div><div class="rm-toolbar"><span class="rm-scope">Nat · 整除与显式证明项</span><span id="rm-count" class="draft-badge"></span><div class="rm-actions"><button class="text-button" id="rm-edit">编辑节点图</button><button class="text-button" id="rm-history">版本与事件</button><button class="text-button" id="rm-export">导出</button><button class="text-button" id="rm-refresh">刷新</button></div></div><div id="rm-nodes" aria-live="polite"><p class="hint">正在核对本研究线的完整命题与来源…</p></div><p class="rm-note">依赖用于安排研究顺序。每个节点独立证明完整命题；更改、撤销或证据损坏会停用相关绑定。</p>`;
    (document.querySelector('#research-execution')||document.querySelector('.research-workspace')).insertAdjacentElement('afterend',section);
    $('rm-edit').disabled=c.dirty;$('rm-refresh').onclick=()=>act(refresh);
    $('rm-edit').onclick=()=>act(editGraph);$('rm-history').onclick=()=>act(history);
    $('rm-export').onclick=()=>act(async()=>{clean();download(await api(base(c.project.project_id)+c.branchId+'/export'),'mathlib-'+c.project.project_id+'-'+c.branchId);});
    act(refresh);
  }
  async function refresh(){
    const c=ctx();if(!c?.project||!$('rm-nodes'))return;
    const key=c.project.project_id+':'+c.branchId,epoch=++generation;
    try{
      const row=await api(base(c.project.project_id)+c.branchId);
      if(epoch!==generation||key!==selection)return;scope=row;render();
    }catch(error){if(epoch===generation&&$('rm-nodes')){$('rm-nodes').innerHTML='<p class="error">'+e(error.message)+'</p>';$('rm-count').textContent='暂不可用';}throw error;}
  }
  function render(){
    const row=scope;if(!row)return;
    $('rm-count').textContent=`${row.nodes.filter(n=>n.math_verdict==='PROVED').length} / ${row.nodes.length} 已证明 · 版本 ${row.graph_revision}`;
    $('rm-nodes').innerHTML=row.nodes.length?`<div class="rm-grid">${row.nodes.map((n,i)=>{
      const b=row.bindings[n.id],proved=n.math_verdict==='PROVED';
      return `<article class="rm-node ${proved?'is-proved':''}"><div class="rm-node-top"><span class="rm-index">${String(i+1).padStart(2,'0')}</span><span class="rm-kind">${e(labels[n.kind])} · ${e(n.id)}</span><span class="status-badge ${proved?'success':''}">${proved?'PROVED · E4':'UNKNOWN'}</span></div><h3>${e(n.request.title)}</h3><p class="rm-original">${e(n.request.original)}</p><pre class="rm-formula">${e(n.formal_statement)}</pre><div class="rm-deps">前置：${n.depends_on.length?n.depends_on.map(key=>`<span>${e(key)}</span>`).join(''):'无'}${n.blocked_by.length?`<strong>待证明 ${e(n.blocked_by.join('、'))}</strong>`:''}</div>${b?`<div class="rm-source"><a href="/mathlib#${e(b.source.run_id)}">${icon('verify')} 查看真实检查来源 ${icon('arrow')}</a><small>${e(b.state==='ACTIVE'?'具名绑定：'+b.reviewer:b.state==='WITHDRAWN'?'绑定已撤销':'绑定已停用')} ${e(b.reason||'')}</small></div>`:'<p class="hint">尚未绑定检查来源。主问题需要自身完整证明。</p>'}${n.notes?`<details><summary>研究笔记</summary><p>${e(n.notes)}</p></details>`:''}<div class="rm-node-actions"><button class="button secondary" data-rm-check="${e(n.id)}">带回检查</button><button class="text-button" data-rm-bind="${e(n.id)}">绑定已有来源</button>${proved?`<button class="text-button" data-rm-withdraw="${e(n.id)}">撤销绑定</button>`:''}</div></article>`;
    }).join('')}</div>`:`<div class="rm-empty"><span class="rm-empty-mark">∣</span><div><h3>从一个可检查的引理开始。</h3><p>在 Mathlib 实验中完成真实检查后，点击“纳入研究项目”；也可先创建节点，保留待证问题。</p><button class="button secondary" id="rm-new">添加第一个节点</button></div></div>`;
    $('rm-new')?.addEventListener('click',()=>act(editGraph));
    for(const button of document.querySelectorAll('[data-rm-check]'))button.onclick=()=>act(()=>bringBack(row.nodes.find(n=>n.id===button.dataset.rmCheck)));
    for(const button of document.querySelectorAll('[data-rm-bind]'))button.onclick=()=>act(()=>bindExisting(row.nodes.find(n=>n.id===button.dataset.rmBind)));
    for(const button of document.querySelectorAll('[data-rm-withdraw]'))button.onclick=()=>act(()=>withdraw(button.dataset.rmWithdraw));
  }
  function bringBack(node){
    clean();sessionStorage.setItem('mra-mathlib-research-handoff',JSON.stringify({project_id:scope.project_id,branch_id:scope.branch_id,node_id:node.id,scope_revision:scope.revision,node:Object.fromEntries(fields.map(key=>[key,node[key]])),request:node.request}));location.href='/mathlib';
  }
  async function editGraph(){
    clean();if(!scope)throw new Error('先刷新节点。');const row=scope;
    let nodes=row.nodes.map(n=>Object.fromEntries(fields.map(key=>[key,n[key]])));
    if(!nodes.length){const catalog=await api('/v1/mathlib/catalog');const plan=await api('/v1/mathlib/preview',catalog.examples[0]);nodes=[{id:'divisibility',kind:'lemma',request:plan.request,depends_on:[],notes:''}];}
    const d=modal('编辑 Mathlib 节点图',`<p class="hint">最多 16 个节点，每项保留完整 request、原文与全部前提。depends_on 仅引用本研究线的 Mathlib 节点。保存变更会停用变更节点及其下游绑定。</p><label>完整节点列表 · JSON<textarea id="rm-graph-json" class="rm-code" rows="22" spellcheck="false" maxlength="160000">${e(JSON.stringify(nodes,null,2))}</textarea></label><p class="error" id="rm-dialog-error" hidden></p><button class="button primary" id="rm-save">保存新版本</button>`);
    let id=crypto.randomUUID(),submitted=null;
    $('rm-save').onclick=async()=>{const button=$('rm-save');button.disabled=true;try{const raw=$('rm-graph-json').value,nodes=JSON.parse(raw);if(submitted!==raw){id=crypto.randomUUID();submitted=raw;}await api(base(row.project_id)+'edit',{...contextData(row),request_id:id,nodes});d.close();await refresh();}catch(error){$('rm-dialog-error').hidden=false;$('rm-dialog-error').textContent=error.message;}finally{button.disabled=false;}};
  }
  async function history(){clean();if(!scope)return;const value=await api(base(scope.project_id)+scope.branch_id+'/history');modal('Mathlib 图版本与事件',`<p class="hint">图版本 ${value.versions.length} · 事件 ${value.events.length} · 当前修订 ${value.revision}</p><div class="rm-timeline">${value.events.slice().reverse().map(item=>`<div><strong>${e(item.kind)}</strong><small>${e(item.at)}</small><p>${e(item.node_id||'')}${item.reason?' · '+e(item.reason):''}</p></div>`).join('')||'<p>尚无保存记录。</p>'}</div><details><summary>完整历史 JSON</summary><pre class="rm-code">${e(JSON.stringify(value,null,2))}</pre></details>`);}
  async function withdraw(nodeId){
    clean();const row=scope,d=modal('撤销研究节点绑定',`<p>原始 Mathlib 检查保留。本研究线的该绑定撤销，下游绑定会持久停用。</p><form id="rm-withdraw-form"><label>本地操作者<input id="rm-reviewer" required maxlength="200"></label><label>原因<textarea id="rm-reason" required maxlength="500"></textarea></label><label class="check-row"><input id="rm-confirm" type="checkbox" required><span>确认撤销并停用依赖此绑定的后续结果。</span></label><p class="error" id="rm-dialog-error" hidden></p><button class="button primary" type="submit">确认撤销</button></form>`);
    const id=crypto.randomUUID();d.querySelector('form').onsubmit=async event=>{event.preventDefault();const b=d.querySelector('[type=submit]');b.disabled=true;try{await api(base(row.project_id)+'withdraw',{...contextData(row),request_id:id,node_id:nodeId,reviewer:$('rm-reviewer').value,reason:$('rm-reason').value,confirmed:$('rm-confirm').checked});d.close();await refresh();}catch(error){$('rm-dialog-error').hidden=false;$('rm-dialog-error').textContent=error.message;}finally{b.disabled=false;}};
  }
  async function bindExisting(node){
    clean();const runs=await api('/v1/mathlib/runs'),valid=runs.runs.filter(r=>r.state==='COMPLETED'&&r.math_verdict==='PROVED'&&!r.invalidated);
    if(!valid.length)throw new Error('尚无可用的真实 E4 来源，请先带回检查。');
    const row=scope;modal('选择已有的真实检查',`<label>Mathlib 来源<select id="rm-run">${valid.map(r=>`<option value="${e(r.run_id)}">${e(r.title)} · ${e(r.run_id.slice(-8))}</option>`).join('')}</select></label><p class="hint">完整请求、原文、证明项和预算必须与该节点完全相同。</p><button class="button primary" id="rm-choose">核对来源与节点</button>`);
    $('rm-choose').onclick=()=>act(async()=>{const value=await api('/v1/mathlib/runs/'+$('rm-run').value);await attachDialog(value,{project_id:row.project_id,branch_id:row.branch_id,node});});
  }
  async function attachDialog(run,fixed=null){
    if(run.state!=='COMPLETED'||run.math_verdict!=='PROVED'||run.invalidated)throw new Error('来源尚未形成可用的真实 E4 证明。');
    const projects=(await api('/v1/research')).projects;if(!projects.length)throw new Error('请先在研究空间创建项目，再纳入证明。');
    let handoff=null;try{handoff=JSON.parse(sessionStorage.getItem('mra-mathlib-research-handoff')||'null');}catch{ /* Ignore an obsolete local handoff. */ }
    const preferred=fixed?.project_id||handoff?.project_id||projects[0].project_id;
    const node=fixed?.node||handoff?.node;let preview=null,rev=0,row=null,pending=false,requestId=crypto.randomUUID();
    const d=modal('将证明纳入研究项目',`<p class="hint">这一步复核已有工件并具名绑定，不运行新 Lean。前置必须已有有效绑定。</p><div class="rm-form-grid"><label>研究项目<select id="rm-project" ${fixed?'disabled':''}>${projects.map(p=>`<option value="${e(p.project_id)}" ${p.project_id===preferred?'selected':''}>${e(p.title)}</option>`).join('')}</select></label><label>研究线<select id="rm-branch" ${fixed?'disabled':''}></select></label><label>节点 ID<input id="rm-node-id" maxlength="32" value="${e(node?.id||handoff?.node_id||'divisibility')}" ${fixed?'readonly':''}></label><label>节点类型<select id="rm-kind" ${fixed?'disabled':''}>${Object.entries(labels).map(([k,v])=>`<option value="${k}" ${k===(node?.kind||'lemma')?'selected':''}>${v}</option>`).join('')}</select></label></div><label>前置节点 ID · 逗号分隔<input id="rm-deps" value="${e(node?.depends_on.join(', ')||'')}" ${fixed?'readonly':''}></label><label>研究笔记<textarea id="rm-notes" maxlength="4000" ${fixed?'readonly':''}>${e(node?.notes||'')}</textarea></label><p class="hint" id="rm-existing"></p><button class="button secondary" id="rm-preview">核对完整绑定</button><div id="rm-attachment" hidden></div><p class="error" id="rm-dialog-error" hidden></p>`);
    function invalidate(){++rev;preview=null;requestId=crypto.randomUUID();$('rm-attachment').hidden=true;$('rm-attachment').replaceChildren();}
    async function branches(){invalidate();$('rm-preview').disabled=true;$('rm-existing').textContent='正在读取研究线…';const epoch=rev,p=$('rm-project').value;row=null;const value=await api('/v1/research/'+p+'/mathlib-branches');if(epoch!==rev||!d.open)return;$('rm-branch').innerHTML=value.branches.map(b=>`<option value="${e(b.branch_id)}" ${b.branch_id===(fixed?.branch_id||handoff?.branch_id||'main')?'selected':''}>${e(b.label)}</option>`).join('');await branch();}
    function useExisting(){const existing=row?.nodes.find(n=>n.id===$('rm-node-id').value.trim());if(existing&&!fixed){$('rm-kind').value=existing.kind;$('rm-deps').value=existing.depends_on.join(', ');$('rm-notes').value=existing.notes;}invalidate();}
    async function branch(){invalidate();$('rm-preview').disabled=true;$('rm-existing').textContent='正在核对节点…';const epoch=rev,p=$('rm-project').value,b=$('rm-branch').value;row=null;if(!b)return;const value=await api(base(p)+b);if(epoch!==rev||!d.open)return;row=value;$('rm-existing').textContent='现有 Mathlib 节点：'+(row.nodes.map(n=>n.id).join('、')||'无');useExisting();$('rm-preview').disabled=false;}
    $('rm-project').onchange=()=>act(branches);$('rm-branch').onchange=()=>act(branch);
    for(const id of ['rm-node-id','rm-kind','rm-deps','rm-notes'])$(id).addEventListener('input',invalidate);
    $('rm-node-id').addEventListener('change',useExisting);
    $('rm-preview').onclick=async()=>{if(pending)return;pending=true;const b=$('rm-preview');b.disabled=true;try{if(!row)throw new Error('请等待研究线加载。');invalidate();const epoch=rev,body={...contextData(row),node_id:$('rm-node-id').value.trim(),kind:$('rm-kind').value,depends_on:$('rm-deps').value.split(',').map(s=>s.trim()).filter(Boolean),notes:$('rm-notes').value,run_id:run.run_id};const value=await api(base(row.project_id)+'preview-attachment',body);if(epoch!==rev||!d.open)return;preview=value;$('rm-attachment').hidden=false;$('rm-attachment').innerHTML=`<div class="rm-confirm-source"><strong>${e(value.parent.title)} / ${e(value.parent.branch_label)} / ${e(value.node.id)}</strong><blockquote>${e(value.node.request.original)}</blockquote><pre class="rm-formula">${e(value.source.formal_statement)}</pre><details><summary>完整请求、证明项与前置指纹</summary><pre class="rm-code">${e(JSON.stringify({request:value.node.request,proof_source:value.source.proof_source,prerequisites:value.prerequisites,source:value.source.run_id,source_reviewer:value.source.source_alignment.reviewer},null,2))}</pre></details><p class="hint">已有真实 Lean + 独立重放 · PROVED / E4 · 本次新增检查 0</p></div><label>绑定审核者 · 本地具名声明<input id="rm-attach-reviewer" maxlength="200"></label><label class="check-row"><input id="rm-attach-confirm" type="checkbox"><span>已核对原文、完整命题与证明项、预算、项目语境、节点及所有前置；确认绑定此真实来源。</span></label><button class="button primary" id="rm-attach-submit" disabled>确认纳入研究线</button><p class="task-id">${e(value.attachment_fingerprint)}</p>`;
      const enable=()=>{$('rm-attach-submit').disabled=!$('rm-attach-confirm').checked||!$('rm-attach-reviewer').value.trim();};$('rm-attach-reviewer').oninput=()=>{$('rm-attach-confirm').checked=false;enable();};$('rm-attach-confirm').onchange=enable;
      $('rm-attach-submit').onclick=async()=>{if(!preview)return;const button=$('rm-attach-submit');button.disabled=true;try{const result=await api(base(value.project_id)+'attach',{...body,request_id:requestId,reviewer:$('rm-attach-reviewer').value,confirmed:$('rm-attach-confirm').checked,attachment_fingerprint:value.attachment_fingerprint});d.close();sessionStorage.removeItem('mra-mathlib-research-handoff');sessionStorage.removeItem('mra-mathlib-research-handoff-loaded');if(ctx()?.project?.project_id===result.project_id&&ctx().branchId===result.branch_id)await refresh();else{sessionStorage.setItem('mra-mathlib-research-return',JSON.stringify({project_id:result.project_id,branch_id:result.branch_id}));location.href='/research#'+result.project_id;}notify('已纳入研究线。绑定可追溯到原始完整证明。');}catch(error){$('rm-dialog-error').hidden=false;$('rm-dialog-error').textContent=error.message;button.disabled=false;}};
    }catch(error){$('rm-dialog-error').hidden=false;$('rm-dialog-error').textContent=error.message;}finally{pending=false;b.disabled=false;}};
    await branches();
  }
  function result(event){sourceRun=event.detail;$('rm-attach-from-result')?.remove();if(sourceRun.state==='COMPLETED'&&sourceRun.math_verdict==='PROVED'&&!sourceRun.invalidated){const button=document.createElement('button');button.id='rm-attach-from-result';button.className='button primary';button.innerHTML=icon('graph')+' 纳入研究项目';button.onclick=()=>act(()=>attachDialog(sourceRun));document.querySelector('#ml-result .ml-result-actions')?.prepend(button);}}
  function handoff(){
    if(!$('ml-title'))return false;let value=null;try{value=JSON.parse(sessionStorage.getItem('mra-mathlib-research-handoff')||'null');}catch{return true;}if(!value?.request)return true;
    const r=value.request,values={title:r.title,original:r.original,variables:r.variables.join(', '),assumptions:JSON.stringify(r.assumptions,null,2),goal:JSON.stringify(r.goal,null,2),proof:JSON.stringify(r.proof,null,2),timeout:r.timeout_seconds};
    for(const [key,val] of Object.entries(values)){$('ml-'+key).value=val;$('ml-'+key).dispatchEvent(new Event('input',{bubbles:true}));}
    const banner=document.createElement('div');banner.className='rm-handoff';banner.innerHTML=`<div><strong>来自研究节点 ${e(value.node_id)}</strong><p>完整请求已带入。重新核对并完成真实检查后，再明确绑定。</p></div><a class="button secondary" href="/research#${e(value.project_id)}">返回研究项目 ${icon('arrow')}</a>`;banner.querySelector('a').onclick=()=>sessionStorage.setItem('mra-mathlib-research-return',JSON.stringify({project_id:value.project_id,branch_id:value.branch_id}));$('ml-editor').before(banner);return true;
  }
  if(location.pathname==='/research'){window.addEventListener('mra-research-render',mount);if(ctx()?.project)mount();}
  else{window.addEventListener('mra-mathlib-result',result);const observer=new MutationObserver(()=>{if(handoff())observer.disconnect();});observer.observe($('page'),{childList:true,subtree:true});if(handoff())observer.disconnect();window.addEventListener('hashchange',()=>{if(!location.hash)$('rm-attach-from-result')?.remove();});}
})();
