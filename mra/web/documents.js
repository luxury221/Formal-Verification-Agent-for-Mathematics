"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const kinds = {section:'章节',definition:'定义 / 记号',claim:'定理 / 结论',proof:'证明',equation:'公式',reference:'引用',notation:'记号',passage:'段落',unreadable:'不可读页面'};
  const methods = {HTML_SOURCE:'HTML 源文件',LATEX_SOURCE:'LaTeX 源文件',PDF_TEXT_WITH_OCR_FALLBACK:'PDF 文本 / OCR',PDF_TEXT:'PDF 文本层',OCR:'本地 OCR'};
  const states = {QUEUED:'等待提取',RUNNING:'正在提取',READY:'结构草稿已生成',FAILED:'提取未完成',CANCELLED:'已取消',INTERRUPTED:'提取已中断'};
  const stageNames = {SOURCE_STRUCTURE:'读取源文件结构',PDF_PAGE:'读取并渲染 PDF 页面',OCR_PAGE:'文本不足，正在尝试本地 OCR'};
  let session, current = '', record = null, draft = null, selected = null, page = 1, tab = 'all', epoch = 0, timer = null, dirty = false, sources = [];
  const uid = prefix => prefix + '-' + crypto.randomUUID();
  const pendingMutations = new Map();
  const el = (tag, text, className) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  const automationLink=el('a','自动处理全文 →','button secondary');automationLink.href='/automation';document.querySelector('.document-toolbar').append(automationLink);
  const linkStates={LOCATED:'来源已定位 · 适用关系待核对',SELF_REFERENCE:'当前片段内位置 · 已包含于原文',MISSING_TARGET:'原文中未找到锚点',AMBIGUOUS_TARGET:'锚点重复或多义',MULTIPLE_FRAGMENTS:'目标包含多个片段 · 需人工整理完整范围',UNINDEXED_TARGET:'目标未形成可用片段',DIFFERENT_DOCUMENT:'跨文档链接 · 需另行核对',NO_FRAGMENT:'没有片段锚点',INVALID_LINK:'链接格式待核对',UNSAFE_SCHEME:'链接类型不作自动定位',INVALID_FRAGMENT:'锚点编码或格式待核对',BASE_URL_REQUIRES_REVIEW:'原文设有 base 地址 · 相对链接需人工核对',AMBIGUOUS_HREF_ATTRIBUTE:'同一链接有多个 href · 不自动选择'};
  const htmlReferencePanel=el('details',undefined,'document-html-references');htmlReferencePanel.id='di-html-references';htmlReferencePanel.hidden=true;$('di-review').before(htmlReferencePanel);
  function message(text, error = false) {$('di-message').textContent=text;$('di-message').classList.toggle('error',error);}
  function endpoint(id = current, suffix = '') {return '/v1/documents/'+encodeURIComponent(id)+suffix;}
  async function api(path, data) {
    const options = {cache:'no-store'};
    if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-MRA-CSRF':session.csrf_token};options.body=JSON.stringify(data);}
    const response = await fetch(path,options), value = await response.json();
    if(!response.ok)throw Error(value.error||('请求未完成：HTTP '+response.status));
    return value;
  }
  async function mutation(path, body, prefix) {
    const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([path,body])));
    const key=Array.from(new Uint8Array(hash),x=>x.toString(16).padStart(2,'0')).join('');
    if(!pendingMutations.has(key))pendingMutations.set(key,uid(prefix));
    const value=await api(path,{...body,request_id:pendingMutations.get(key)});
    pendingMutations.delete(key);
    return value;
  }
  const guarded = fn => async event => {try{await fn(event);}catch(error){message(error.message,true);}};
  function clearPoll(){if(timer!==null)clearTimeout(timer);timer=null;}
  function options(select, items, placeholder) {
    select.replaceChildren();
    if(placeholder!==undefined)select.append(new Option(placeholder,''));
    for(const [value,title] of items)select.append(new Option(title,value));
  }
  async function listing(preferred) {
    const value=await api('/v1/documents');
    options($('di-documents'),value.documents.map(x=>[x.document_id,x.title+' · '+(states[x.state]||x.state)]),'选择一个论文版本');
    $('di-documents').value=preferred||current;
  }
  function reset() {
    clearPoll();record=draft=selected=null;dirty=false;page=1;
    $('di-workspace').hidden=true;$('di-detail').hidden=true;$('di-job').hidden=true;$('di-empty').hidden=false;$('di-export').disabled=true;
  }
  async function choose(id) {
    const token=++epoch;current=id;reset();message('');
    $('di-documents').value=id;
    if(!id)return;
    const value=await api(endpoint(id));
    if(token!==epoch)return;
    record=value;renderJob();
    $('di-import').hidden=true;
    if(value.state==='READY')await loadDraft(token);
    else if(['QUEUED','RUNNING'].includes(value.state))schedule(token);
  }
  function renderJob() {
    if(!record)return;
    automationLink.href='/automation#'+encodeURIComponent(record.document_id);
    $('di-job').hidden=false;$('di-empty').hidden=true;
    $('di-job-title').textContent=states[record.state]||record.state;
    const option=[...$('di-documents').options].find(x=>x.value===record.document_id);
    if(option)option.textContent=record.title+' · '+(states[record.state]||record.state);
    const p=record.progress;
    $('di-job-detail').textContent=record.error||(p?(stageNames[p.stage]||'处理原文')+(p.total?' · 第 '+p.page+' / '+p.total+' 页':''):(record.state==='READY'?'可以对照原页，核对自动提取的结构。':'刷新页面后仍可继续查看；取消不会发布未完成草稿。'));
    $('di-cancel').hidden=!['QUEUED','RUNNING'].includes(record.state);
    $('di-cancel').disabled=record.cancel_requested;
    $('di-cancel').textContent=record.cancel_requested?'正在取消…':'取消提取';
    $('di-progress').hidden=!['QUEUED','RUNNING'].includes(record.state);
    if(p&&p.total){$('di-progress').max=p.total;$('di-progress').value=p.page;}else $('di-progress').removeAttribute('value');
  }
  function schedule(token) {
    clearPoll();
    timer=setTimeout(async()=>{
      try{const value=await api(endpoint(current));if(token!==epoch)return;record=value;renderJob();
        if(value.state==='READY')await loadDraft(token);else if(['QUEUED','RUNNING'].includes(value.state))schedule(token);
      }catch(error){if(token===epoch)message('读取进度未完成：'+error.message+'。可点击刷新重查。',true);}
    },900);
  }
  async function loadDraft(token) {
    const id=current,value=await api(endpoint(id,'/draft'));
    if(token!==epoch||id!==current)return;
    draft=value;$('di-workspace').hidden=false;$('di-empty').hidden=true;$('di-export').disabled=false;
    $('di-document-title').textContent=draft.title;
    let obligationsLink=$('di-obligations');
    if(!obligationsLink){obligationsLink=el('a','整理数学义务 →','button primary');obligationsLink.id='di-obligations';$('di-publish').before(obligationsLink);}
    obligationsLink.href='/obligations#'+id;
    $('di-publish').disabled=!draft.url;
    $('di-publish').title=draft.url?'创建固定版本，继续整理语义和验证义务':'导入时填写版本来源链接后，可建立全文整理记录';
    options($('di-page-select'),draft.pages.map(x=>[String(x.page),String(x.page)]));
    $('di-page-controls').hidden=!draft.pages.length;
    $('di-zoom').hidden=!draft.pages.length;
    $('di-page-image').dataset.zoom='100';$('di-zoom').textContent='原页缩放：100%';
    $('di-download-pdf').hidden=!draft.pages.length;
    $('di-download-pdf').href=endpoint(id,'/original-pdf');
    options($('di-source-select'),record.files.map((x,i)=>[String(i),x.name]),'选择源文件');
    $('di-source-select').value=record.files.length?'0':'';
    $('di-source-preview').hidden=true;
    renderSummary();renderWarnings();renderBlocks();renderPage();renderExports();
    if(record.files.length)await loadSource();
  }
  function checklist(b){return ['claim','proof','equation','definition','reference'].includes(b.kind);}
  function unresolved(b){return b.kind==='unreadable'||b.page_match==='UNLOCATED'||b.unresolved_references?.length||b.extraction==='OCR'||b.unresolved;}
  function renderSummary() {
    const candidate=draft.blocks.filter(checklist).length,reviewed=draft.blocks.filter(x=>x.extraction_reviewed).length;
    const items=[['内容读取方式',methods[draft.method]||draft.method,'源文件优先，OCR 兜底'],['原文页数',draft.pages.length||'未附 PDF','可回看固定版本原页'],['候选核查项',candidate,'定义、结论、证明、公式与引用'],['已核对提取',reviewed+' / '+draft.blocks.length,'不是数学验证通过率']];
    $('di-summary').replaceChildren(...items.map(([label,value,note])=>{const n=el('div');n.append(el('span',label),el('strong',String(value)),el('small',note));return n;}));
  }
  function warning(value) {
    const translations=[['PDF text/OCR does not guarantee','PDF 文本和 OCR 无法保证公式识别准确，请与原页逐项对照。'],['Source-to-PDF page matches','源文件与 PDF 的页码对应是文字匹配建议，尚未确认两个文件的语义一致性。'],['HTML structure is extracted','已按 HTML 结构提取；隐含条件和证明依赖仍待核对。']];
    for(const [prefix,text] of translations)if(value.startsWith(prefix))return text;
    const prefixes=[['TeX macros retained without execution or automatic expansion in ','以下文件的 TeX 宏未执行或自动展开：'],['TeX external commands, images, packages and bibliography are not executed or fetched in ','以下文件的外部命令、图片、宏包及文献未执行或下载：'],['Unresolved TeX include in ','未找到被引用的 TeX 文件：'],['Supplied TeX files not reached from the entrypoint: ','主文件没有引用到这些上传文件：'],['Cyclic or excessively deep TeX include: ','TeX 引用存在循环或层级过深：'],['HTML source could not be indexed; attempting the next input: ','HTML 源文件未能完成索引，已尝试备用输入：'],['TeX source could not be indexed; attempting PDF fallback: ','LaTeX 源文件未能完成索引，已尝试 PDF 兜底：']];
    for(const [prefix,text] of prefixes)if(value.startsWith(prefix))return text+value.slice(prefix.length);
    return value;
  }
  function renderWarnings(){
    $('di-warning-list').replaceChildren(...draft.warnings.map(x=>el('li',warning(x))));
    for(const p of draft.pages.filter(x=>x.issue))$('di-warning-list').append(el('li','第 '+p.page+' 页：'+pageIssue(p.issue)));
  }
  function pageIssue(code){return ({OCR_REQUIRED_UNAVAILABLE:'本页需要 OCR，但本地 OCR 尚未就绪。',OCR_TIMEOUT:'OCR 超时，保留原页待处理。',OCR_FAILED:'OCR 未成功，保留原页待处理。',OCR_NO_TEXT:'OCR 未能恢复文字。',OCR_DISABLED_TEXT_INSUFFICIENT:'文本不足且未启用 OCR。',SOURCE_USED_PDF_PAGE_MATCH_UNAVAILABLE:'已使用源文件内容；本页文字不足，可能无法自动对应页码。',PDF_TEXT_EXTRACTION_FAILED:'本页文本层读取失败。'})[code]||code;}
  function locationText(b){
    const base=b.source+(b.span?' · 字符 '+b.span[0]+'–'+b.span[1]:'');
    if(!b.pages.length)return base+(draft.pages.length?' · PDF 页码待定位':'');
    return base+' · 第 '+b.pages.join('、')+' 页'+(b.page_match==='SUGGESTED'?'（建议对应）':b.page_match==='AMBIGUOUS'?'（存在多个候选）':'');
  }
  function renderBlocks() {
    if(!draft)return;
    const query=$('di-search').value.trim().toLowerCase(),kind=$('di-kind').value;
    const values=draft.blocks.filter(b=>(tab==='all'||(tab==='checklist'?checklist(b):unresolved(b)))&&(!kind||b.kind===kind)&&(!query||b.text.toLowerCase().includes(query)));
    $('di-list-count').textContent='显示 '+values.length+' / '+draft.blocks.length+' 处结构片段；尚未进行全文数学认证。';
    const nodes=values.map(b=>{const button=el('button',undefined,'document-block');button.type='button';button.dataset.block=b.id;button.setAttribute('aria-pressed',String(selected===b.id));
      const header=el('header');header.append(el('span',kinds[b.kind]||b.kind,'document-tag'),el('span',b.id),el('span',b.extraction_reviewed?'已核对提取':'待核对','document-tag '+(b.extraction_reviewed?'':'pending')));
      button.append(header,el('p',b.text.length>260?b.text.slice(0,260)+'…':b.text),el('small',locationText(b)));
      button.addEventListener('click',()=>openBlock(b.id));return button;});
    $('di-blocks').replaceChildren(...(nodes.length?nodes:[el('p','当前筛选没有匹配片段。','document-muted')]));
    for(const key of ['all','checklist','unresolved'])$('di-tab-'+key).setAttribute('aria-selected',String(tab===key));
  }
  function renderPage() {
    if(!draft?.pages.length){$('di-page-image').replaceChildren(el('p','这份导入未附 PDF，可在下方查看源文件。','document-muted'));$('di-page-note').textContent='';return;}
    page=Math.min(Math.max(1,page),draft.pages.length);const value=draft.pages[page-1];
    $('di-page-select').value=String(page);$('di-page-prev').disabled=page===1;$('di-page-next').disabled=page===draft.pages.length;
    const image=el('img');image.src=endpoint(current,'/pages/'+page);image.alt=draft.title+'，PDF 第 '+page+' 页';image.loading='eager';
    image.addEventListener('error',()=>{if(image.isConnected)message('原页图像读取失败，请刷新重查。',true);});
    $('di-page-image').replaceChildren(image);
    $('di-page-note').textContent='第 '+page+' / '+draft.pages.length+' 页 · '+(methods[value.method]||value.method)+(value.issue?' · '+pageIssue(value.issue):'')+(value.method==='OCR'?' · OCR 公式需人工对照。':'');
  }
  async function loadSource() {
    const value=$('di-source-select').value,token=epoch,id=current;
    if(value===''){$('di-source-preview').hidden=true;return;}
    const source=await api(endpoint(id,'/sources/'+value));
    if(token!==epoch||id!==current||$('di-source-select').value!==value)return;
    $('di-source-preview').textContent=source.content;$('di-source-preview').hidden=false;
  }
  function openBlock(id) {
    if(dirty&&!confirm('当前修正尚未保存，是否放弃并查看另一处？'))return;
    const b=draft.blocks.find(x=>x.id===id);if(!b)return;selected=id;dirty=false;
    $('di-detail').hidden=false;$('di-detail-location').textContent=b.id+' · '+(kinds[b.kind]||b.kind);
    $('di-detail-mapping').textContent=locationText(b);
    const mathDelimited=b.text.includes('$')||b.text.includes('\\[')||b.text.includes('\\(');
    $('di-detail-text').replaceChildren(document.createTextNode(b.kind==='equation'&&!mathDelimited?'\\['+b.text+'\\]':b.text));
    $('di-original-text').textContent=b.extracted_text;$('di-review-kind').value=b.kind;$('di-review-text').value=b.text;$('di-review-note').value=b.note||'';$('di-reviewed').checked=b.extraction_reviewed;
    $('di-dependencies').textContent=(b.dependencies.length?b.dependencies.join('、'):'未识别显式依赖')+(b.unresolved_references?.length?'；未解析：'+b.unresolved_references.join('、'):'')+'。隐含依赖及全部前提仍需语义核对。';
    renderHtmlLinks(b);
    if(b.pages.length){page=b.pages[0];renderPage();}
    const sourceIndex=record.files.findIndex(x=>x.name===b.source);
    if(sourceIndex>=0){$('di-source-select').value=String(sourceIndex);loadSource().catch(e=>message(e.message,true));}
    renderBlocks();$('di-detail').scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  function renderHtmlLinks(block){
    const links=block.html_references||[];htmlReferencePanel.hidden=!links.length;
    htmlReferencePanel.replaceChildren(el('summary','HTML 原文中的显式链接 · '+links.length+' 处'),el('p','字符位置指向最初的 HTML 源文件。文字修正后仍需核对引用关系；来源定位不表示数学依赖已确认。','document-muted'));
    for(const ref of links){
      const card=el('div',undefined,'document-html-reference');
      card.append(el('strong',linkStates[ref.status]||ref.status),el('code',ref.href||'（空链接）'),el('small','原 HTML 字符 '+ref.span.join('–')));
      if(ref.status==='LOCATED'){
        const target=draft.blocks.find(b=>b.source===block.source&&b.span[0]===ref.target_block_span[0]&&b.span[1]===ref.target_block_span[1]);
        if(target){const button=el('button','查看引用片段 '+target.id,'button secondary');button.type='button';button.addEventListener('click',()=>openBlock(target.id));card.append(button);}
      }
      htmlReferencePanel.append(card);
    }
  }
  function renderExports(){
    $('di-article-links').replaceChildren(...draft.exports.map(x=>{const link=el('a','打开全文整理记录 · 修订 '+x.revision);link.href='/articles#'+x.article_id;return link;}));
  }
  async function sourceFiles() {
    const selectedFiles=[...$('di-sources').files,...$('di-folder').files].filter(f=>/\.(html?|tex)$/i.test(f.name));
    sources=selectedFiles.map(file=>({file,name:file.webkitRelativePath?file.webkitRelativePath.split('/').slice(1).join('/'):file.name}));
    const tex=sources.filter(x=>/\.tex$/i.test(x.name));
    const previous=$('di-entry').value;
    options($('di-entry'),tex.map(x=>[x.name,x.name]));
    $('di-entry-label').hidden=!tex.length;
    if(tex.some(x=>x.name===previous))$('di-entry').value=previous;
    else if(tex.some(x=>x.name==='main.tex'))$('di-entry').value='main.tex';
    $('di-files-note').textContent=($('di-pdf').files.length?'1 份 PDF':'未附 PDF')+' · '+sources.length+' 份源文件';
  }
  async function readBase64(file) {
    return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(Error('PDF 文件读取失败'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file);});
  }
  async function readSource(file) {
    try{return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer());}
    catch{throw Error('源文件需使用 UTF-8 编码：'+file.name);}
  }
  async function init() {
    $('di-review-kind').replaceChildren(...Object.entries(kinds).map(([value,title])=>new Option(title,value)));
    document.querySelector('.document-structure').append($('di-detail'));
    session=await api('/v1/session');
    const capabilities=await api('/v1/documents/capabilities');
    $('di-capabilities').textContent=capabilities.pdf?('本地 PDF 解析已就绪；'+(capabilities.ocr?'英文 OCR 兜底已就绪。':'OCR 尚未就绪，无法读取的页面会明确保留待处理。')):'本地 PDF 解析尚未准备；HTML / LaTeX 源文件仍可导入。';
    await listing();
    const id=location.hash.slice(1);if(id){$('di-documents').value=id;await choose(id);}
  }
  $('di-import').addEventListener('submit',guarded(async event=>{
    event.preventDefault();if(dirty&&!confirm('当前修正尚未保存，是否放弃并导入另一篇论文？'))return;$('di-submit').disabled=true;
    try{await sourceFiles();const pdf=$('di-pdf').files[0];
      if(pdf&&pdf.size>16777216)throw Error('PDF 最多 16 MiB。');
      if(sources.length>16||sources.reduce((n,x)=>n+x.file.size,0)>1048576)throw Error('最多 16 份源文件，合计不超过 1 MiB。');
      if(!pdf&&!sources.length)throw Error('请选择 PDF、HTML 或 LaTeX 源文件。');
      const data={title:$('di-title').value.trim(),url:$('di-url').value.trim(),pdf:pdf?{name:pdf.name,base64:await readBase64(pdf)}:null,sources:await Promise.all(sources.map(async x=>({name:x.name,content:await readSource(x.file)}))),entrypoint:$('di-entry').value||null,ocr:$('di-ocr').value};
      const value=await mutation('/v1/documents',data,'import');history.replaceState(null,'','#'+value.document_id);await listing(value.document_id);await choose(value.document_id);
    }finally{$('di-submit').disabled=false;}
  }));
  for(const id of ['di-pdf','di-sources','di-folder'])$(id).addEventListener('change',guarded(sourceFiles));
  $('di-new').addEventListener('click',guarded(async()=>{$('di-import').reset();await sourceFiles();$('di-import').hidden=false;$('di-import').scrollIntoView({behavior:'smooth',block:'start'});}));
  $('di-hide-import').addEventListener('click',()=>{$('di-import').hidden=true;});
  $('di-documents').addEventListener('change',guarded(async()=>{const id=$('di-documents').value;if(dirty&&!confirm('修正尚未保存，是否切换论文？')){$('di-documents').value=current;return;}history.replaceState(null,'',id?'#'+id:location.pathname);await choose(id);}));
  $('di-refresh').addEventListener('click',guarded(async()=>{if(dirty&&!confirm('修正尚未保存，是否刷新？'))return;await listing();if(current)await choose(current);}));
  $('di-cancel').addEventListener('click',guarded(async()=>{const token=epoch,id=current;$('di-cancel').disabled=true;const value=await api(endpoint(id,'/cancel'),{});if(token!==epoch)return;record=value;renderJob();if(value.state==='RUNNING')schedule(token);}));
  for(const key of ['all','checklist','unresolved'])$('di-tab-'+key).addEventListener('click',()=>{tab=key;renderBlocks();});
  $('di-search').addEventListener('input',renderBlocks);$('di-kind').addEventListener('change',renderBlocks);
  $('di-zoom').addEventListener('click',()=>{const node=$('di-page-image'),zoom=node.dataset.zoom==='150'?'200':node.dataset.zoom==='200'?'100':'150';node.dataset.zoom=zoom;$('di-zoom').textContent='原页缩放：'+zoom+'%';});
  $('di-page-prev').addEventListener('click',()=>{page--;renderPage();});$('di-page-next').addEventListener('click',()=>{page++;renderPage();});$('di-page-select').addEventListener('change',()=>{page=Number($('di-page-select').value);renderPage();});
  $('di-source-select').addEventListener('change',guarded(loadSource));
  $('di-detail-close').addEventListener('click',()=>{if(dirty&&!confirm('是否放弃尚未保存的修正？'))return;dirty=false;$('di-detail').hidden=true;selected=null;renderBlocks();});
  $('di-review').addEventListener('input',()=>{dirty=true;});
  $('di-review').addEventListener('submit',guarded(async event=>{
    event.preventDefault();const id=current,token=epoch,block=selected;$('di-review-save').disabled=true;
    try{const value=await mutation(endpoint(id,'/review'),{expected_revision:draft.revision,block_id:block,text:$('di-review-text').value,kind:$('di-review-kind').value,note:$('di-review-note').value,reviewed:$('di-reviewed').checked},'review');
      if(token!==epoch)return;draft=value;dirty=false;renderSummary();renderBlocks();openBlock(block);message('修正已保存，原始提取结果保留。该片段仍未完成数学验证。');
    }finally{$('di-review-save').disabled=false;}
  }));
  $('di-export').addEventListener('click',()=>{if(!draft)return;const blob=new Blob([JSON.stringify(draft,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download=current+'-draft.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  $('di-publish').addEventListener('click',guarded(async()=>{
    if(dirty)throw Error('请先保存当前修正，再建立固定版本的全文整理记录。');
    const token=epoch,id=current;$('di-publish').disabled=true;
    try{await mutation(endpoint(id,'/article'),{expected_revision:draft.revision},'article');if(token===epoch){await loadDraft(token);message('已建立固定版本的全文整理记录，可继续逐项核对语义与验证。');}}
    finally{if(token===epoch)$('di-publish').disabled=!draft?.url;}
  }));
  window.addEventListener('hashchange',guarded(()=>{if(dirty&&!confirm('当前修正尚未保存，是否放弃并切换论文？')){history.replaceState(null,'',current?'#'+current:location.pathname);return;}return choose(location.hash.slice(1));}));
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  init().catch(error=>message(error.message,true));
})();
