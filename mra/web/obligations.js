"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, text, cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const roles={statement:'原始陈述',definition:'定义 / 记号',assumption:'前提条件',proof:'证明片段',context:'上下文'};
  const checks={sources:'所用原文片段对应完整',objects:'对象、类型与空间一致',definitions:'定义与记号已完整展开',quantifiers:'量词顺序与范围一致',assumptions:'全部前提保留，未添加条件',conclusion:'全部子结论与边界情形保留',dependencies:'依赖与引用完整，未冒充已证假设'};
  let session, catalog, report=null, current='', selected='', selectedSources=[], fragment='', dirty=false, epoch=0, preview=null, evidence=null, transfers=[], busy=false;
  const pending = new Map();
  const recheckButton=node('button','复核这条义务的当前证据','button secondary');recheckButton.id='ob-recheck';recheckButton.type='button';const recheckActions=node('div',undefined,'ob-actions');recheckActions.append(recheckButton);$('ob-links').before(recheckActions);
  const lines = id => $(id).value.split('\n').map(x=>x.trim()).filter(Boolean);
  const endpoint = (action='', id=current) => '/v1/obligations/'+encodeURIComponent(id)+(action?'/'+action:'');
  function message(text,error=false){$('ob-message').textContent=text;$('ob-message').classList.toggle('error',error);}
  async function api(path,data){const options={cache:'no-store'};if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-MRA-CSRF':session.csrf_token};options.body=JSON.stringify(data);}const response=await fetch(path,options),value=await response.json();if(!response.ok)throw Error(value.error||('请求未完成：HTTP '+response.status));return value;}
  async function mutation(path,body){const key=JSON.stringify([path,body]);if(!pending.has(key))pending.set(key,'ob-'+crypto.randomUUID());const result=await api(path,{...body,request_id:pending.get(key)});pending.delete(key);return result;}
  const guarded=fn=>async event=>{if(busy)return;busy=true;$('page').inert=true;$('page').setAttribute('aria-busy','true');try{await fn(event);}catch(error){message(error.message,true);}finally{busy=false;$('page').inert=false;$('page').removeAttribute('aria-busy');}};
  function options(select,values,placeholder){select.replaceChildren();if(placeholder!==undefined)select.append(new Option(placeholder,''));for(const [key,label] of values)select.append(new Option(label,key));}
  function keepEdits(){return !dirty||window.confirm('有未保存的义务修改。确定离开这份草稿吗？');}
  function saved(){if(!selected||dirty)throw Error('请先保存完整数学义务，再进行审核或转交。');if(report.source_state!=='CURRENT')throw Error('原文已修订或不可用。请从最新版本建立新的整理记录，旧记录仅供回看。');}
  function invalidate(){preview=evidence=null;$('ob-semantic-preview').hidden=true;$('ob-evidence').hidden=true;$('ob-evidence-confirm').checked=false;}
  function markDirty(){dirty=true;invalidate();$('ob-dirty').textContent='有未保存修改；保存后需要重新审核。';}
  async function listing(preferred){const value=await api('/v1/obligations');options($('ob-dossiers'),value.dossiers.map(x=>[x.dossier_id,x.title+' · 原文 r'+x.document_revision+' · '+x.obligations+' 条义务']),'选择一个固定版本');$('ob-dossiers').value=preferred||current;return value.dossiers;}
  async function documents(){const docs=(await api('/v1/documents')).documents.filter(x=>x.state==='READY');options($('ob-documents'),docs.map(x=>[x.document_id,x.title+' · r'+x.revision]),'选择已导入的论文');return docs;}
  async function openHash(){
    const hash=location.hash.slice(1),dossiers=await listing(),docs=await documents();
    if(hash.startsWith('dossier-')){await choose(hash);return;}
    if(hash.startsWith('document-')){
      const doc=docs.find(x=>x.document_id===hash);
      if(!doc){await choose('');message('这份论文尚未完成提取或已不在当前导入记录中。',true);return;}
      const old=dossiers.find(x=>x.document_id===hash&&x.document_revision===doc.revision);
      if(old){history.replaceState(null,'','#'+old.dossier_id);await choose(old.dossier_id);}
      else{await choose('');$('ob-documents').value=hash;$('ob-create-panel').open=true;}
      return;
    }
    if(dossiers.length)await choose(dossiers[dossiers.length-1].dossier_id);
    else{await choose('');$('ob-create-panel').open=true;}
  }
  async function choose(key){const token=++epoch;current=key;report=null;selected='';dirty=false;invalidate();$('ob-workspace').hidden=true;$('ob-empty').hidden=Boolean(key);$('ob-export').disabled=true;message('');if(!key)return;const value=await api(endpoint('',key));if(token!==epoch)return;report=value;render();$('ob-create-panel').open=false;$('ob-dossiers').value=key;}
  function render(){
    if(!report)return;
    $('ob-workspace').hidden=false;$('ob-empty').hidden=true;$('ob-export').disabled=false;
    $('ob-title').textContent=report.source.title;
    $('ob-source-state').textContent='原文修订 r'+report.source.document_revision+' · 整理修订 r'+report.revision+' · '+({CURRENT:'正在整理固定版本',SUPERSEDED:'原文已有新修订；此记录仅供历史回看',SOURCE_CHANGED:'原文件身份发生变化；当前证据不可用',SOURCE_UNAVAILABLE:'原文件不可用；当前证据不可用'}[report.source_state]||report.source_state);
    $('ob-document-link').href='/documents#'+report.source.document_id;
    const states=Object.values(report.obligation_states),proved=states.filter(x=>x.math_verdict==='PROVED'&&x.evidence_level==='E4').length;
    const summary=[['原文片段',report.source.blocks.length],['数学义务',states.length],['当前已证义务',proved],['全文状态','未验证']];
    $('ob-summary').replaceChildren(...summary.map(([label,value])=>{const n=node('div');n.append(node('small',label),node('strong',String(value)));return n;}));
    options($('ob-selection'),Object.values(report.obligations).map(x=>[x.id,x.id+' · '+x.content.title]),'新建数学义务');
    if(!report.obligations[selected])selected=Object.keys(report.obligations)[0]||'';
    renderFragments();options($('ob-page'),report.source.pages.map(x=>[String(x.page),String(x.page)]),report.source.pages.length?undefined:'未附 PDF');
    $('ob-download').hidden=!report.source.inputs.pdf;$('ob-download').href='/v1/documents/'+report.source.document_id+'/original-pdf';
    renderPage();selectObligation(selected);
  }
  function selectObligation(oid){selected=oid;dirty=false;invalidate();transfers=[];$('ob-return-graph').replaceChildren();$('ob-selection').value=oid;
    const item=report.obligations[oid],c=item?.content;
    $('ob-id').value=oid;$('ob-id').readOnly=Boolean(item);$('ob-kind').value=c?.kind||'claim';$('ob-obligation-title').value=c?.title||'';
    for(const name of ['objects','definitions','quantifiers','conclusion'])$('ob-'+name).value=c?.[name]||'';
    for(const [field,id] of [['assumptions','assumptions'],['references','references'],['depends_on','dependencies'],['questions','questions']])$('ob-'+id).value=(c?.[field]||[]).join('\n');
    selectedSources=structuredClone(c?.sources||[]);renderRefs();$('ob-version').textContent=item?'版本 '+item.version:'新草稿';$('ob-dirty').textContent='草稿与审核都不会自动构成证明。';
    $('ob-review-panel').hidden=!item;$('ob-research-panel').hidden=!item;
    const state=report.obligation_states[oid],historical=report.link_states.some(x=>x.obligation_id===oid&&x.status==='NOT_RECHECKED');$('ob-semantic-state').textContent=state?.semantic_state==='ACCEPTED'?'语义已确认 · '+(historical?'已关联历史 E4，待复核':state.math_verdict+'/'+state.evidence_level):'待重新审核';
    $('ob-semantic-state').classList.toggle('proved',state?.math_verdict==='PROVED');
    $('ob-confirmations').replaceChildren(...Object.entries(checks).map(([key,label])=>{const n=node('label',undefined,'ob-check'),input=node('input');input.type='checkbox';input.value=key;n.append(input,node('span',label));return n;}));
    const currentSource=report.source_state==='CURRENT';for(const control of $('ob-edit').querySelectorAll('input,textarea,select,button'))control.disabled=!currentSource;
    for(const id of ['ob-source-review','ob-source-withdraw','ob-review-preview','ob-research'])$(id).disabled=!currentSource;
    renderWorkspaces();
    if(item){const used=usedSources(oid);$('ob-extraction-list').replaceChildren(...used.map(b=>node('p',b.id+' · '+(b.label||b.kind)+' · '+(extractionChecked(b.id)?'提取已核对':'提取待核对'))));}
  }
  function usedSources(oid){const seen=new Set(),ids=new Set();function visit(key){if(seen.has(key))return;seen.add(key);const c=report.obligations[key].content;for(const ref of c.sources)ids.add(ref.block_id);c.depends_on.forEach(visit);}visit(oid);return report.source.blocks.filter(b=>ids.has(b.id));}
  function extractionChecked(bid){const event=[...report.source_reviews].reverse().find(x=>x.block_ids.includes(bid));return event?event.confirmed:report.source.blocks.find(x=>x.id===bid)?.extraction_reviewed;}
  function renderFragments(){const query=$('ob-source-search').value.toLowerCase();const values=report.source.blocks.filter(b=>(b.id+' '+b.label+' '+b.text).toLowerCase().includes(query));options($('ob-fragments'),values.map(b=>[b.id,b.id+' · '+(b.label||b.kind)+' · '+b.text.slice(0,70)]));$('ob-source-count').textContent=values.length+' / '+report.source.blocks.length;if(!values.some(x=>x.id===fragment))fragment=values[0]?.id||'';$('ob-fragments').value=fragment;renderFragment();}
  function renderFragment(){const b=report.source.blocks.find(x=>x.id===fragment);$('ob-fragment-text').textContent=b?.text||'当前筛选没有片段。';$('ob-fragment-location').textContent=b?b.source+' · '+b.id+(b.span?' · 字符 '+b.span.join('–'):'')+(b.pages.length?' · PDF 第 '+b.pages.join('、')+' 页（'+(b.page_match==='SUGGESTED'?'建议对应':'待核对')+'）':' · PDF 页码待定位'):'';
    if(b?.pages.length&&$('ob-page').options.length){$('ob-page').value=String(b.pages[0]);renderPage();}
  }
  function renderPage(){const page=Number($('ob-page').value),p=report.source.pages.find(x=>x.page===page);$('ob-page-image').replaceChildren();$('ob-page-note').textContent=p?'页码对应只是提取建议，请自行核对。':'本次导入未附可阅读的 PDF 页面。';if(p){const img=node('img');img.alt='固定论文的 PDF 第 '+page+' 页';img.loading='lazy';img.src='/v1/documents/'+report.source.document_id+'/pages/'+page;img.addEventListener('error',()=>{$('ob-page-note').textContent='原页当前不可用，不能据此确认提取。';});$('ob-page-image').append(img);}}
  function renderRefs(){const nodes=selectedSources.map((ref,index)=>{const b=report.source.blocks.find(x=>x.id===ref.block_id),n=node('div',undefined,'ob-ref'),view=node('button',ref.block_id+' · '+(b?.label||'')+' · '+roles[ref.role]);view.type='button';view.addEventListener('click',()=>{$('ob-source-search').value='';fragment=ref.block_id;renderFragments();});const remove=node('button','×');remove.type='button';remove.setAttribute('aria-label','移除来源 '+ref.block_id+' '+roles[ref.role]);remove.addEventListener('click',()=>{selectedSources.splice(index,1);markDirty();renderRefs();});remove.disabled=report.source_state!=='CURRENT';n.append(view,node('span',extractionChecked(ref.block_id)?'已核对':'待核对'),remove);return n;});$('ob-selected-sources').replaceChildren(...nodes);}
  function content(){return {title:$('ob-obligation-title').value,kind:$('ob-kind').value,sources:structuredClone(selectedSources),objects:$('ob-objects').value,definitions:$('ob-definitions').value,quantifiers:$('ob-quantifiers').value,assumptions:lines('ob-assumptions'),conclusion:$('ob-conclusion').value,references:lines('ob-references'),depends_on:lines('ob-dependencies'),questions:lines('ob-questions')};}
  function renderWorkspaces(){const values=report.workspace_states.filter(x=>x.obligation_id===selected);$('ob-workspaces').replaceChildren(...values.map(x=>{const n=node('div',undefined,'ob-workspace'),a=node('a','打开研究任务 ↗');a.href=x.href;n.append(a,node('p',x.status==='CURRENT'?'当前版本的完整目标；保存任务不会自动执行检查。':'已失效：'+x.reason));return n;}));options($('ob-return-workspace'),values.filter(x=>x.status==='CURRENT').map(x=>[x.workspace_id,x.study_id]),'选择当前研究任务');$('ob-return-panel').hidden=!values.some(x=>x.status==='CURRENT');const links=report.link_states.filter(x=>x.obligation_id===selected);recheckActions.hidden=!links.length;recheckButton.disabled=report.source_state!=='CURRENT';$('ob-links').replaceChildren(...links.map(x=>{const n=node('div',undefined,'ob-evidence-link'),label=x.status==='CURRENT'?'PROVED / E4 · 本次工件复核通过':x.status==='NOT_RECHECKED'?'历史 PROVED / E4 · 当前工件尚未复核':'历史证据 · 当前不可用';n.append(node('strong',label),node('p',x.note),node('p','审核人：'+x.reviewer+' · 本地具名对应'),node('p',x.graph_id+' / '+x.node_id, 'ob-muted'));if(x.status==='CURRENT')n.append(node('p','本次复核时刻：'+report.checked_at,'ob-muted'));else n.append(node('p',x.status==='NOT_RECHECKED'?'点击上方“复核”读取原验证器和当前工件；普通浏览不会重新执行 Lean。':x.reason,'ob-muted'));return n;}));}
  async function update(action,body,oid=selected){const id=current,token=epoch,value=await mutation(endpoint(action,id),{expected_revision:report.revision,...body});if(token!==epoch||id!==current)return;report=value;selected=oid;render();await listing(id);}
  function reviewer(){const value=$('ob-reviewer').value.trim();if(!value)throw Error('请填写本地审核人。');return value;}
  function note(){const value=$('ob-review-note').value.trim();if(!value)throw Error('请填写此次审核说明。');return value;}
  async function semantic(decision){saved();if(!preview)throw Error('请先展开当前完整对照。');await update('review',{obligation_id:selected,preview_fingerprint:preview.preview_fingerprint,decision,reviewer:reviewer(),note:note(),confirmations:[...$('ob-confirmations').querySelectorAll('input:checked')].map(x=>x.value)});message(decision==='accept'?'语义对应已记录，数学状态仍需真实验证。':'审核决定已记录，旧确认不会自动恢复。');}
  $('ob-edit').addEventListener('input',markDirty);
  $('ob-edit').addEventListener('submit',guarded(async event=>{event.preventDefault();const oid=$('ob-id').value;await update('save',{obligation_id:oid,content:content()},oid);message('完整义务已保存。请分别核对提取与数学语义。');}));
  $('ob-source-search').addEventListener('input',()=>{if(report)renderFragments();});
  $('ob-fragments').addEventListener('change',()=>{fragment=$('ob-fragments').value;renderFragment();});
  $('ob-page').addEventListener('change',renderPage);
  $('ob-add-source').addEventListener('click',()=>{if(!fragment||report.source_state!=='CURRENT')return;const ref={block_id:fragment,role:$('ob-role').value};if(selectedSources.some(x=>x.block_id===ref.block_id&&x.role===ref.role))return;selectedSources.push(ref);markDirty();renderRefs();});
  $('ob-selection').addEventListener('change',()=>{const next=$('ob-selection').value;if(keepEdits())selectObligation(next);else $('ob-selection').value=selected;});
  $('ob-new').addEventListener('click',()=>{if(keepEdits())selectObligation('');});
  $('ob-dossiers').addEventListener('change',guarded(async()=>{const key=$('ob-dossiers').value;if(!keepEdits()){$('ob-dossiers').value=current;return;}history.replaceState(null,'','#'+key);await choose(key);}));
  $('ob-refresh').addEventListener('click',guarded(async()=>{if(!keepEdits())return;await listing(current);if(current)await choose(current);}));
  $('ob-create').addEventListener('click',guarded(async()=>{if(!keepEdits())return;const id=$('ob-documents').value;if(!id)throw Error('请选择已经完成提取的论文。');const draft=await api('/v1/documents/'+encodeURIComponent(id)+'/draft');const value=await mutation('/v1/obligations',{document_id:id,expected_document_revision:draft.revision});history.replaceState(null,'','#'+value.dossier_id);await listing(value.dossier_id);await choose(value.dossier_id);message('已固定原文版本。选择片段后，开始填写数学义务。');}));
  for(const [id,confirmed] of [['ob-source-review',true],['ob-source-withdraw',false]])$(id).addEventListener('click',guarded(async()=>{saved();await update('source-review',{block_ids:usedSources(selected).map(x=>x.id),reviewer:reviewer(),note:note(),confirmed});message(confirmed?'所用片段提取已确认；数学语义需要另行审核。':'提取确认已撤回；相关语义确认和回链证据已失效。');}));
  $('ob-review-preview').addEventListener('click',guarded(async()=>{saved();const token=epoch,id=current,value=await api(endpoint('review-preview'),{obligation_id:selected});if(token!==epoch||id!==current)return;preview=value;$('ob-complete-statement').textContent=value.complete_statement;$('ob-prerequisites').replaceChildren(...value.prerequisites.map(x=>{const n=node('details'),s=node('summary','前置义务：'+x.id+' · '+x.content.title),p=node('p',report.obligation_states[x.id].complete_statement,'mra-math');n.append(s,p);return n;}));$('ob-semantic-preview').hidden=false;for(const input of $('ob-confirmations').querySelectorAll('input'))input.checked=false;message('请逐项核对原文、完整陈述与依赖，再记录审核决定。');}));
  for(const [id,decision] of [['ob-accept','accept'],['ob-reject','reject'],['ob-revoke','revoke']])$(id).addEventListener('click',guarded(()=>semantic(decision)));
  $('ob-research').addEventListener('click',guarded(async()=>{saved();await update('research',{obligation_id:selected,area:$('ob-area').value});message('已建立完整目标的研究任务。打开工作台，编辑候选并显式提交检查。');}));
  $('ob-load-tasks').addEventListener('click',guarded(async()=>{saved();evidence=null;$('ob-evidence').hidden=true;const value=await api(endpoint('research-tasks'),{workspace_id:$('ob-return-workspace').value});transfers=value.transfers.flatMap(t=>t.eligible_node_ids.map(n=>({...t,node_id:n})));options($('ob-return-graph'),transfers.map((t,i)=>[String(i),t.graph_id+' / '+t.node_id]),'选择已转交的完整目标');message(transfers.length?'任务已读取。下一步复核当前真实证据。':'尚无可回收的完整目标。请先在研究工作台转交并检查。');}));
  for(const id of ['ob-return-workspace','ob-return-graph'])$(id).addEventListener('change',()=>{evidence=null;$('ob-evidence').hidden=true;$('ob-evidence-confirm').checked=false;if(id==='ob-return-workspace'){transfers=[];$('ob-return-graph').replaceChildren();}});
  $('ob-evidence-preview').addEventListener('click',guarded(async()=>{saved();const t=transfers[Number($('ob-return-graph').value)];if($('ob-return-graph').value===''||!t)throw Error('请先选择已转交的完整目标。');message('正在核对原验证器、运行环境与证明工件，完成前不授予当前结论。此过程不重新执行 Lean。');evidence=await api(endpoint('evidence-preview'),{workspace_id:$('ob-return-workspace').value,graph_id:t.graph_id,node_id:t.node_id});$('ob-evidence-text').textContent='完整义务：\n'+evidence.identity.original+'\n\n实际检查的形式化目标：\n'+evidence.identity.formal_statement+'\n\n检查记录：'+evidence.identity.attempt_id+'\n\n工件摘要：\n'+JSON.stringify(evidence.identity.artifact_hashes,null,2);$('ob-evidence-confirm').checked=false;$('ob-evidence').hidden=false;message('当前完整目标通过原验证器的证据门禁。请核对对应范围后回链。');}));
  $('ob-bind').addEventListener('click',guarded(async()=>{saved();if(!evidence)throw Error('请先读取当前证据。');message('正在重新核对完整目标与当前工件，保存本次具名对应。');await update('bind',{workspace_id:evidence.workspace_id,graph_id:evidence.graph_id,node_id:evidence.node_id,preview_fingerprint:evidence.preview_fingerprint,reviewer:reviewer(),note:$('ob-evidence-note').value,confirmed:$('ob-evidence-confirm').checked});message('真实证据已回链并保留历史记录。可点击“复核这条义务的当前证据”查看本次当前状态；全文仍未验证。');}));
  recheckButton.addEventListener('click',guarded(async()=>{saved();message('正在复核这条完整义务的原验证器、运行环境和证明工件。完成前不授予当前结论；不重新执行 Lean。');const id=current,token=epoch,value=await api(endpoint('recheck'),{obligation_id:selected});if(id!==current||token!==epoch)return;report=value;render();message(value.obligation_states[selected].math_verdict==='PROVED'?'本次复核通过：本条完整义务 PROVED / E4。全文仍未验证。':'本次未取得当前有效证据，请查看对应状态。');}));
  $('ob-export').addEventListener('click',()=>{if(!report)return;const blob=new Blob([JSON.stringify(report,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download=current+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  window.addEventListener('hashchange',guarded(async()=>{if(keepEdits())await openHash();else history.replaceState(null,'',current?'#'+current:location.pathname);}));
  guarded(async()=>{session=await api('/v1/session');catalog=await api('/v1/obligations/catalog');await openHash();if(catalog.automatic_semantic_generation)throw Error('未知的自动语义生成状态，请核对服务版本。');})({});
})();
