"use strict";
(() => {
  if(!['/library','/mathlib'].includes(location.pathname))return;
  const {escape:e,icon,modal,notify,session,copy}=window.MRA;
  const $=id=>document.getElementById(id),base='/v1/mathlib-knowledge',handoffKey='mra-mathlib-knowledge-handoff';
  const labels={ACTIVE:'可用归档',WITHDRAWN:'已撤销',INVALIDATED:'证据已失效',DELETED:'已移除'};
  let epoch=0,readEpoch=0,selected=null,rows=[],limit=30;
  async function api(path,body){const info=await session;const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MRA-CSRF':info.csrf_token},body:JSON.stringify(body)})});const value=await response.json();if(!response.ok)throw new Error(value.error||'暂时无法核对 Mathlib 归档。');return value;}
  async function act(fn){try{await fn();}catch(error){notify(error.message);}}
  function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function proofDetails(content){const r=content.source_record;return `<details class="mk-evidence"><summary>完整请求、证明项、版本与内核审计</summary><pre>${e(JSON.stringify({request:content.source.request,proof_source:content.source.proof_source,alignment:r.alignment,source_manifest:r.plan.source_manifest,candidate:r.candidate,replay:r.replay,files:content.file_hashes,adapter:content.adapter},null,2))}</pre></details>`;}
  async function admitDialog(runId=''){
    let plan=null,revision=0,pending=false,requestId=crypto.randomUUID();
    const d=modal('保存一份有据可查的证明',`<p class="hint">将真实 E4 实验保存到本机私有知识库。核对来源与证明副本，本次不运行新 Lean。</p><label>Mathlib 来源运行 ID<input id="mk-source" maxlength="100" value="${e(runId)}" placeholder="mathlib-…" spellcheck="false"></label><button class="button secondary" id="mk-prepare">预览完整入库内容</button><div id="mk-plan" hidden></div><p class="error" id="mk-dialog-error" role="alert" hidden></p>`);
    const fail=error=>{if(d.open){$('mk-dialog-error').textContent=error.message;$('mk-dialog-error').hidden=false;}};
    function invalidate(){++revision;plan=null;requestId=crypto.randomUUID();$('mk-plan').hidden=true;$('mk-dialog-error').hidden=true;}
    $('mk-source').oninput=invalidate;
    async function prepare(){
      if(pending)return;invalidate();pending=true;$('mk-prepare').disabled=true;const generation=revision;
      try{const value=await api(base+'/prepare',{run_id:$('mk-source').value.trim()});if(generation!==revision||!d.open)return;plan=value;const c=value.content,r=c.source.request;
        $('mk-plan').hidden=false;$('mk-plan').innerHTML=`<div class="mk-preview"><span class="eyebrow">EXACT STATEMENT / HISTORICAL E4</span><h3>${e(r.title)}</h3><blockquote>${e(r.original)}</blockquote><pre class="mk-formula">${e(c.source.formal_statement)}</pre><div class="mk-facts"><span>Nat · 全部前提已包含</span><span>${Object.keys(c.file_hashes).length} 个归档文件</span><span>原 worker 预算 ${e(r.timeout_seconds)} 秒</span></div><p class="hint">语义核对：${e(c.source.source_alignment.reviewer)} · 本地具名声明</p>${value.same_statement_ids.length?`<p class="mk-duplicate">已有 ${value.same_statement_ids.length} 条相同结构的完整命题；本次保留独立来源。${value.same_statement_ids.map(id=>`<a href="/library#${e(id)}">查看 ${e(id.slice(-8))}</a>`).join(' ')}</p>`:''}${proofDetails(c)}<p class="task-id">${e(value.plan_fingerprint)}</p></div><form id="mk-admit-form"><label>入库审核者 · 本地具名声明<input id="mk-reviewer" required maxlength="200" autocomplete="name"></label><label class="check-row"><input id="mk-confirm" type="checkbox" required><span>已核对原文、完整公式与前提、证明项、来源、版本、预算及归档内容。</span></label><label class="check-row"><input id="mk-rights" type="checkbox" required><span>我有权将这些内容保存到本机私有知识库。</span></label><button class="button primary" id="mk-admit" type="submit" disabled>${icon('graph')} 确认私有入库</button><p class="hint">仅本机保存，不分享、不用于训练。姓名为本地声明。</p></form>`;
        const enable=()=>{$('mk-admit').disabled=pending||!plan||!$('mk-reviewer').value.trim()||!$('mk-confirm').checked||!$('mk-rights').checked;};
        $('mk-reviewer').oninput=()=>{$('mk-confirm').checked=false;$('mk-rights').checked=false;requestId=crypto.randomUUID();enable();};
        for(const id of ['mk-confirm','mk-rights'])$(id).onchange=enable;
        $('mk-admit-form').onsubmit=async event=>{event.preventDefault();if(pending||!plan)return;pending=true;enable();try{const row=await api(base,{request_id:requestId,run_id:c.source.run_id,plan_fingerprint:value.plan_fingerprint,reviewer:$('mk-reviewer').value.trim(),confirmed:$('mk-confirm').checked,rights_confirmed:$('mk-rights').checked});if(!d.open)return;d.close();notify('证明已私有归档，来源与副本已核对。');if(location.pathname==='/library'){await refresh();await inspect(row.item_id);}else location.href='/library#'+row.item_id;}catch(error){fail(error);}finally{pending=false;if(d.open&&$('mk-admit'))enable();}};
      }catch(error){fail(error);}finally{pending=false;if(d.open){$('mk-prepare').disabled=false;}}
    }
    $('mk-prepare').onclick=prepare;if(runId)await prepare();
  }
  function list(){
    const box=$('mk-list');if(!box)return;
    $('mk-count').textContent=`${rows.length} 条匹配记录 · 当前显示 ${Math.min(limit,rows.length)} 条`;
    box.innerHTML=rows.length?rows.slice(0,limit).map((row,i)=>`<button class="mk-row ${row.item_id===selected?.item_id?'is-selected':''}" data-mk-item="${e(row.item_id)}"><span class="mk-row-top"><span class="mk-number">${String(i+1).padStart(2,'0')}</span><span class="mk-status ${row.state==='ACTIVE'?'is-active':''}">${e(labels[row.state])}</span><span class="mk-row-level">${e(row.evidence_level)}</span></span><strong>${e(row.title)}</strong><code>${e(row.formal_statement||'仅保留操作审计')}</code><span class="mk-row-bottom">${e(row.created_at.slice(0,10))}<span>查看来源 ${icon('arrow')}</span></span></button>`).join(''):`<div class="mk-empty"><span class="mk-empty-symbol">∴</span><h3>给下一次推演，留下一份依据。</h3><p>完成 Mathlib 真实检查后，在结果中选择“存入私有知识库”。也可输入已有 E4 运行 ID。</p><a class="button secondary" href="/mathlib">开始一个 Mathlib 实验 ${icon('arrow')}</a></div>`;
    box.querySelectorAll('[data-mk-item]').forEach(b=>b.onclick=()=>act(()=>inspect(b.dataset.mkItem)));
    $('mk-more').hidden=limit>=rows.length;
  }
  async function refresh(){
    if(!$('mk-query'))return;const generation=++epoch;
    $('mk-error').hidden=true;$('mk-count').textContent='正在核对来源与证明副本…';
    try{const value=await api(base+'/search?q='+encodeURIComponent($('mk-query').value)+'&include_inactive='+$('mk-inactive').checked);if(generation!==epoch)return;rows=value.items;limit=30;list();}
    catch(error){if(generation===epoch){$('mk-error').textContent=error.message;$('mk-error').hidden=false;$('mk-count').textContent='暂不可用 · 可以重试';}}
  }
  async function inspect(id){
    const generation=++readEpoch;const row=await api(base+'/'+encodeURIComponent(id));if(generation!==readEpoch)return;
    selected=row;window.history.replaceState(null,'','/library#'+row.item_id);details();list();
  }
  function details(){
    const row=selected,c=row.content,r=c?.source.request,box=$('mk-detail');
    box.innerHTML=`<div class="mk-detail-heading"><span class="eyebrow">PROOF / SOURCE / CONTEXT</span><span class="mk-status ${row.state==='ACTIVE'?'is-active':''}">${e(labels[row.state])}</span></div><h3>${e(r?.title||'已移除的 Mathlib 条目')}</h3><p class="task-id">${e(row.item_id)}</p>${c?`<blockquote>${e(r.original)}</blockquote><pre class="mk-formula">${e(c.source.formal_statement)}</pre><dl class="mk-metadata"><div><dt>来源检查</dt><dd><a href="/mathlib#${e(c.source.run_id)}">${e(c.source.run_id)} ${icon('arrow')}</a></dd></div><div><dt>语义确认</dt><dd>${e(c.source.source_alignment.reviewer)}</dd></div><div><dt>私有入库</dt><dd>${e(row.admission.reviewer)} · 版本 ${row.version}</dd></div><div><dt>证明副本</dt><dd>${Object.keys(c.file_hashes).length} 个文件 · ${Math.ceil(c.source_bytes/1024)} KiB</dd></div><div><dt>存储范围</dt><dd>本机私有 · 不分享、不用于训练</dd></div></dl>${proofDetails(c)}<div class="mk-detail-actions"><button class="button primary" id="mk-recheck" ${row.state==='ACTIVE'?'':'disabled'}>带回 Mathlib 重新检查 ${icon('arrow')}</button><button class="button secondary" id="mk-copy">复制完整公式</button></div>`:''}<p class="mk-history-note">${row.state==='ACTIVE'?'来源与归档副本当前一致。历史 PROVED / E4；本次没有重新运行 Lean。':'归档已停用，当前数学判断 UNKNOWN / E0。'} 带回检查需要新的预览、具名确认和真实验证。</p><div class="mk-detail-actions"><button class="text-button" id="mk-withdraw" ${row.state==='ACTIVE'?'':'disabled'}>撤销条目</button><button class="text-button" id="mk-remove" ${row.state==='DELETED'?'disabled':''}>移除私有副本</button><button class="text-button" id="mk-export">${icon('down')} 导出记录</button></div><details><summary>入库与停用记录 · ${row.events.length}</summary><ol class="mk-events">${row.events.map(event=>`<li><strong>${e(event.kind)}</strong><small>${e(event.at)} · ${e(event.actor)}</small><p>${e(event.reason)}</p></li>`).join('')}</ol></details>`;
    $('mk-withdraw').onclick=()=>retire(false);$('mk-remove').onclick=()=>retire(true);
    $('mk-export').onclick=()=>act(async()=>download(await api(base+'/'+row.item_id+'/export'),row.item_id));
    if(c){$('mk-copy').onclick=()=>copy(c.source.formal_statement);$('mk-recheck').onclick=()=>act(async()=>{const value=await api(base+'/'+row.item_id+'/request');sessionStorage.removeItem('mra-mathlib-research-handoff');sessionStorage.setItem(handoffKey,JSON.stringify(value));location.href='/mathlib';});}
  }
  function retire(remove){
    const row=selected;let requestId=crypto.randomUUID();
    const d=modal(remove?'移除这份私有归档':'撤销知识条目',`<p>${remove?'清除此条目的内容与证明副本，保留标识和操作审计。原 Mathlib 实验独立保留。':'停用此归档，保留证明副本与审计。原 Mathlib 实验独立保留；此来源不会自动重新入库。'}</p><form id="mk-retire-form"><label>本地操作者<input id="mk-actor" required maxlength="200"></label><label>原因<textarea id="mk-reason" required rows="3" maxlength="500"></textarea></label><label class="check-row"><input id="mk-retire-confirm" required type="checkbox"><span>已核对所选条目，确认${remove?'移除私有副本':'撤销归档'}。</span></label><p class="error" id="mk-retire-error" hidden></p><button class="button primary" type="submit">确认${remove?'移除':'撤销'}</button></form>`);
    for(const id of ['mk-actor','mk-reason'])$(id).oninput=()=>{requestId=crypto.randomUUID();$('mk-retire-confirm').checked=false;};
    $('mk-retire-form').onsubmit=async event=>{event.preventDefault();const b=d.querySelector('[type=submit]');b.disabled=true;try{await api(base+'/'+row.item_id+(remove?'/remove':'/withdraw'),{request_id:requestId,reviewer:$('mk-actor').value.trim(),reason:$('mk-reason').value.trim(),confirmed:$('mk-retire-confirm').checked});d.close();await refresh();await inspect(row.item_id);notify(remove?'私有副本已移除，原实验保留。':'归档已撤销。');}catch(error){if(d.open){$('mk-retire-error').textContent=error.message;$('mk-retire-error').hidden=false;}}finally{if(d.open)b.disabled=false;}};
  }
  function mount(){
    const anchor=document.querySelector('.knowledge-summary');if(!anchor)return false;if($('mathlib-knowledge'))return true;
    const section=document.createElement('section');section.id='mathlib-knowledge';section.className='mk-section';
    section.innerHTML=`<div class="mk-heading"><div><span class="eyebrow">PRIVATE MATHLIB ARCHIVE</span><h2>已验证的推演，成为下一次的起点。</h2><p>把完整命题、原始语境和证明副本，一起留在本机。</p></div><button class="button primary" id="mk-new">${icon('plus')} 从真实实验入库</button></div><div class="mk-band"><div class="mk-band-symbol" aria-hidden="true">∣<span>⇒</span>∴</div><div><strong>每份归档，都保留证据的来处。</strong><p>真实 Lean + 独立重放 · 完整前提 · 可撤销、可追溯</p></div><span class="mk-private">${icon('verify')} LOCAL / PRIVATE</span></div><div class="mk-toolbar"><label class="mk-search">${icon('search')}<input id="mk-query" maxlength="200" placeholder="检索 Mathlib 原文、完整公式、引用或运行 ID" aria-label="检索 Mathlib 归档"></label><label class="check-row"><input id="mk-inactive" type="checkbox"><span>包含已停用</span></label><button class="icon-button" id="mk-refresh" aria-label="刷新 Mathlib 归档">${icon('refresh')}</button></div><p class="error" id="mk-error" role="alert" hidden></p><div class="mk-count"><span id="mk-count">正在核对证据…</span><span>历史检查记录 · 非新认证</span></div><div class="mk-layout"><div><div id="mk-list" class="mk-list" aria-live="polite"></div><button class="button secondary" id="mk-more" hidden>再显示 30 条</button></div><aside id="mk-detail" class="panel mk-detail"><span class="eyebrow">AN ARCHIVE WITH CONTEXT</span><div class="mk-placeholder">∀ → ∴</div><h3>结论之外，也保存为什么。</h3><p class="muted">选择一份归档，查看确切原文、完整公式、证明文件和两阶段内核审计。</p><p class="mk-history-note">研究节点的前置用于安排顺序。归档本身保存独立实验的完整证明。</p></aside></div>`;
    anchor.after(section);$('mk-new').onclick=()=>act(()=>admitDialog());$('mk-refresh').onclick=()=>act(refresh);$('mk-inactive').onchange=refresh;
    let timer;$('mk-query').oninput=()=>{++epoch;clearTimeout(timer);timer=setTimeout(refresh,240);};$('mk-more').onclick=()=>{limit+=30;list();};
    act(async()=>{await refresh();if(location.hash.startsWith('#ml-knowledge-'))await inspect(location.hash.slice(1));});return true;
  }
  function handoff(){
    if(!$('ml-title'))return false;let value;try{value=JSON.parse(sessionStorage.getItem(handoffKey)||'null');}catch{sessionStorage.removeItem(handoffKey);return true;}if(!value?.request)return true;
    sessionStorage.removeItem(handoffKey);const r=value.request,values={title:r.title,original:r.original,variables:r.variables.join(', '),assumptions:JSON.stringify(r.assumptions,null,2),goal:JSON.stringify(r.goal,null,2),proof:JSON.stringify(r.proof,null,2),timeout:r.timeout_seconds};
    for(const [key,val] of Object.entries(values)){$('ml-'+key).value=val;$('ml-'+key).dispatchEvent(new Event('input',{bubbles:true}));}
    $('ml-reviewer').value='';$('ml-confirm').checked=false;$('ml-submit').disabled=true;
    const banner=document.createElement('div');banner.className='mk-handoff';banner.innerHTML=`<div><strong>从私有归档带回的完整命题</strong><p>原文、全部前提、证明输入与预算已恢复。请重新预览、具名确认，再运行真实检查。</p></div><a class="button secondary" href="/library#${e(value.item_id)}">返回原归档 ${icon('arrow')}</a>`;$('ml-editor').before(banner);return true;
  }
  if(location.pathname==='/library'){const observer=new MutationObserver(()=>{if(mount())observer.disconnect();});observer.observe($('page'),{childList:true,subtree:true});if(mount())observer.disconnect();window.addEventListener('hashchange',()=>{if(location.hash.startsWith('#ml-knowledge-'))act(()=>inspect(location.hash.slice(1)));});}
  else{window.addEventListener('mra-mathlib-result',event=>{const row=event.detail;if(row.state==='COMPLETED'&&row.math_verdict==='PROVED'&&row.evidence_level==='E4'&&!row.invalidated){const b=document.createElement('button');b.className='button secondary';b.id='mk-save-result';b.textContent='存入私有知识库';b.onclick=()=>act(()=>admitDialog(row.run_id));document.querySelector('#ml-result .ml-result-actions')?.append(b);}});const observer=new MutationObserver(()=>{if(handoff())observer.disconnect();});observer.observe($('page'),{childList:true,subtree:true});if(handoff())observer.disconnect();}
})();
