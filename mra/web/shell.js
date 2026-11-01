"use strict";
(() => {
  const paths = {
    home:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    idea:'<path d="M9 18h6m-5 3h4M8 14a6 6 0 1 1 8 0c-1 .8-1 1.5-1 2H9c0-.5 0-1.2-1-2Z"/><path d="m12 1 0 1M3 8H1m22 0h-2"/>',
    paper:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 12h8m-8 4h5"/>',
    graph:'<circle cx="5" cy="6" r="3"/><circle cx="19" cy="6" r="3"/><circle cx="12" cy="19" r="3"/><path d="m7 8 4 8m6-8-4 8M8 6h8"/>',
    verify:'<path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6Z"/><path d="m8 12 3 3 5-6"/>',
    chart:'<path d="M4 3v17h17M8 16v-5m5 5V7m5 9V4"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    flow:'<rect x="8" y="2" width="8" height="5" rx="1"/><rect x="2" y="17" width="8" height="5" rx="1"/><rect x="14" y="17" width="8" height="5" rx="1"/><path d="M12 7v5M6 17v-5h12v5"/>',
    settings:'<path d="m10 2 4 0 .6 3 2 .9 2.7-1 2 3.4-2.2 2v2.4l2.2 2-2 3.4-2.7-1-2 .9-.6 3h-4l-.6-3-2-.9-2.7 1-2-3.4 2.2-2v-2.4l-2.2-2 2-3.4 2.7 1 2-.9Z"/><circle cx="12" cy="12" r="3"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    arrow:'<path d="M4 12h15m-5-5 5 5-5 5"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>',
    down:'<path d="m6 9 6 6 6-6"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 8.5a2.5 2.5 0 1 1 4 2c-1.2.8-1.5 1-1.5 2.5M12 17h.01"/>',
    menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    upload:'<path d="M12 16V3m-4 4 4-4 4 4M4 14v6h16v-6"/>',
    code:'<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>',
    external:'<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',
    refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-2l2 2M4 17l2 2a7 7 0 0 0 12-2"/>',
    leaf:'<path d="M20 3C7 1 1 13 8 18s14-2 12-15ZM6 21 17 8"/>',
    copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>'
  };
  const icon = (name, extra='') => `<svg class="icon ${extra}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.paper}</svg>`;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const nav = [
    ['/', 'home', '研究总览'], ['/studio','idea','通用研究','新'], ['/ideas','idea','猜想探索'], ['/research','flow','研究项目'], ['/typed-research','code','数学研究','新'], ['/analysis','leaf','分析研究','新'], ['/proof','flow','分析证明组合','新'], ['/pde','leaf','PDE 研究','新'], ['/articles','paper','全文推理','新'], ['/papers','paper','单定理审查','试验'],
    ['/library','graph','知识图谱','私有'], ['/mathlib','code','Mathlib 实验','新'], ['/campaigns','flow','研究批次'], ['/compositions','flow','引理组合'], ['/proof-library','graph','组合成果库'], ['/verify','verify','形式验证'],
    ['/evaluations','chart','评测中心'], ['/activity','clock','研究记录'],
    ['/workflow','flow','产品全景'], ['/settings','settings','工作区设置']
  ];
  const current = nav.find(item => item[0] === (['/number-theory','/strategies'].includes(location.pathname)?'/mathlib':location.pathname==='/library-compositions'?'/compositions':location.pathname==='/typed-research/lemmas'?'/typed-research':location.pathname)) || nav[0];
  document.title = `MRA · ${current[2]}`;
  const sidebar = document.createElement('aside'); sidebar.className = 'sidebar'; sidebar.id = 'navigation';
  sidebar.innerHTML = `<a class="brand" href="/" aria-label="MRA 研究总览"><span class="brand-symbol">∴</span><span class="brand-type">MRA<small>MATH RESEARCH AGENT</small></span></a>
    <button class="workspace-switch" type="button" id="workspace-switch"><span class="workspace-avatar">M</span><span>个人研究空间<small>本地工作区</small></span>${icon('down')}</button>
    <div class="nav-caption">研究空间</div><nav aria-label="主要导航">${nav.map((item,i) => `${i===5?'<div class="nav-caption tools-caption">研究工具</div>':''}<a class="nav-item ${item[0]===current[0]?'is-active':''}" href="${item[0]}" ${item[0]===current[0]?'aria-current="page"':''}>${icon(item[1])}<span>${item[2]}</span>${item[3]?`<small class="nav-preview">${item[3]}</small>`:''}</a>`).join('')}</nav>
    <div class="sidebar-bottom"><div class="sidebar-note"><span class="note-mark">⊢</span><p>好问题，值得严格求证。<small>从想法到可追溯的证据</small></p></div><button id="help-button" class="sidebar-help" type="button">${icon('help')}<span>使用指南</span><kbd>?</kbd></button><div class="local-user"><span class="user-avatar">研</span><span>研究者<small>Local workspace</small></span><span class="online-dot" title="本地工作区"></span></div></div>`;
  document.body.prepend(sidebar);
  const scrim = document.createElement('button'); scrim.className='nav-scrim'; scrim.tabIndex=-1; scrim.setAttribute('aria-label','关闭导航'); document.body.append(scrim);
  const header = document.createElement('header'); header.className='appbar';
  header.innerHTML = `<div class="appbar-start"><button class="icon-button mobile-menu" id="menu-button" aria-label="打开导航" aria-expanded="false" aria-controls="navigation">${icon('menu')}</button><span class="breadcrumb">个人研究空间 ${icon('chevron')} <strong>${current[2]}</strong></span></div><div class="appbar-actions"><button class="search-trigger" id="search-trigger">${icon('search')}<span>搜索研究记录</span><kbd>Ctrl K</kbd></button><span class="connection" id="connection"><span class="online-dot"></span><span>连接中</span></span><a class="header-avatar" href="/settings" aria-label="工作区设置">研</a></div>`;
  document.querySelector('main').prepend(header);
  const toast = document.createElement('div'); toast.className='toast'; toast.setAttribute('role','status'); document.body.append(toast);
  const dialog = document.createElement('dialog'); dialog.className='app-dialog'; document.body.append(dialog);
  let toastTimer;
  function notify(message) { toast.textContent=message; toast.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>toast.classList.remove('show'),2800); }
  function modal(title, body) {
    dialog.innerHTML=`<div class="dialog-heading"><h2>${escape(title)}</h2><button class="icon-button" aria-label="关闭对话框" data-close>${icon('close')}</button></div><div class="dialog-body">${body}</div>`;
    dialog.querySelector('[data-close]').addEventListener('click',()=>dialog.close());
    if (!dialog.open) dialog.showModal();
    return dialog;
  }
  dialog.addEventListener('click', event => {if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
  function guide() { modal('从一个明确的问题开始', `<p class="dialog-lead">先确认“要验证什么”，再确认“证明是否成立”。</p><ol class="guide-steps"><li><span>01</span><div><strong>写下猜想</strong><p>明确对象、量词和必要前提，也可以先保存，稍后继续。</p></div></li><li><span>02</span><div><strong>核对形式化候选</strong><p>检查模型是否忠实表达原意，并具名确认对应关系。</p></div></li><li><span>03</span><div><strong>查看真实验证与证据</strong><p>Lean 检查原式或完整否定。失败与超时会保留为未知。</p></div></li></ol><div class="dialog-actions"><a class="button secondary" href="/workflow">浏览产品全景</a><a class="button primary" href="/ideas">开始探索 ${icon('arrow')}</a></div>`); }
  document.getElementById('help-button').addEventListener('click',guide);
  document.getElementById('workspace-switch').addEventListener('click',()=>modal('当前研究空间',`<div class="workspace-option"><span class="workspace-avatar">M</span><div><strong>个人研究空间</strong><p>猜想、验证任务与记录保存在当前机器。</p></div>${icon('check')}</div><p class="muted">当前为单人本地工作区。团队协作尚未开放。</p><a class="button secondary" href="/settings">管理工作区 ${icon('arrow')}</a>`));
  function closeMenu(){document.body.classList.remove('nav-open');document.getElementById('menu-button').setAttribute('aria-expanded','false');}
  document.getElementById('menu-button').addEventListener('click',()=>{const open=document.body.classList.toggle('nav-open');document.getElementById('menu-button').setAttribute('aria-expanded',String(open));});
  scrim.addEventListener('click',closeMenu);
  async function api(path) { const response=await fetch(path,{cache:'no-store'}); if(!response.ok)throw new Error('暂时无法读取工作区，请稍后刷新。');return response.json(); }
  const session = api('/v1/session').then(value=>{const el=document.getElementById('connection');el.classList.toggle('is-warning',!!value.worker.error);el.querySelector('span:last-child').textContent=value.worker.error?'工作进程受阻':'本地已连接';return value;}).catch(error=>{const el=document.getElementById('connection');el.classList.add('is-warning');el.querySelector('span:last-child').textContent='连接中断';throw error;});
  session.catch(()=>{});
  const records = () => Promise.all([api('/v1/ideas'),api('/v1/tasks'),api('/v1/paper-reviews'),api('/v1/research'),api('/v1/mathlib-knowledge/search'),api('/v1/mathlib-reuse/search')]).then(([ideas,tasks,papers,research,knowledge,reuse])=>[...reuse.items.map(item=>({id:item.reuse_id,title:item.title,type:'引用',state:item.state,at:item.created_at,href:item.href,searchText:item.original+' '+item.formal_statement})),...knowledge.items.map(item=>({id:item.item_id,title:item.title,type:'知识',state:item.state,at:item.created_at,href:item.href,searchText:item.original+' '+item.formal_statement})),...research.projects.map(item=>({id:item.project_id,title:item.title,type:'研究',state:item.state,at:item.created_at,href:'/research#'+item.project_id})),...papers.reviews.map(item=>({id:item.review_id,title:item.title,type:'审查',state:item.state,at:item.created_at,href:'/papers#'+item.review_id})),...ideas.ideas.map(item=>({id:item.idea_id,title:item.source.statement,type:'猜想',state:item.state,at:item.created_at,href:'/ideas#'+item.idea_id})),...tasks.tasks.map(item=>({id:item.task_id,title:item.case_id,type:'验证',state:item.state,at:item.created_at,href:'/verify#'+item.task_id}))].sort((a,b)=>String(b.at).localeCompare(String(a.at))));
  async function search() {
    const d=modal('搜索研究记录',`<label class="search-input">${icon('search')}<input id="global-search" placeholder="输入猜想、任务名称或 ID" autocomplete="off" aria-label="搜索研究记录"></label><div id="search-results" class="search-results"><p class="muted">正在读取记录…</p></div>`);
    const input=d.querySelector('input');input.focus();
    try {const items=await records();const render=()=>{const q=input.value.toLowerCase().trim();const matches=items.filter(item=>(item.title+' '+item.id+' '+(item.searchText||'')).toLowerCase().includes(q)).slice(0,8);d.querySelector('#search-results').innerHTML=matches.length?matches.map(item=>`<a href="${escape(item.href)}" class="search-result">${icon(['知识','引用'].includes(item.type)?'graph':item.type==='猜想'?'idea':item.type==='审查'?'paper':item.type==='研究'?'flow':'verify')}<span>${escape(item.title)}<small>${item.type} · ${escape(item.id.slice(0,18))}…</small></span>${icon('chevron')}</a>`).join(''):'<div class="small-empty">没有找到匹配记录。可以从一个新猜想开始。</div>';};input.addEventListener('input',render);render();}catch(error){d.querySelector('#search-results').textContent=error.message;}
  }
  document.getElementById('search-trigger').addEventListener('click',search);
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();search();}if(event.key==='Escape')closeMenu();if(event.key==='?'&&!/INPUT|TEXTAREA/.test(event.target.tagName))guide();});
  async function copy(text) {try{await navigator.clipboard.writeText(text);notify('已复制到剪贴板');}catch{modal('复制内容',`<textarea class="copy-area" readonly aria-label="待复制内容">${escape(text)}</textarea><p class="muted">请选中内容后复制。</p>`);dialog.querySelector('textarea').select();}}
  function prefill(text){try{sessionStorage.setItem('mra.idea.prefill',text);}catch{}location.href='/ideas';}
  const stateNames={WAITING_FOR_MODEL:'等待模型配置',WAITING_FOR_REVIEW:'待语义确认',QUEUED:'等待执行',RUNNING:'运行中',COMPLETED:'已结束',FAILED:'执行失败',CANCELLED:'已取消',REVOKED:'确认已撤销',NEEDS_CLARIFICATION:'待澄清',UNSUPPORTED:'超出支持范围',INTERRUPTED:'已中断'};
  const state = value => `<span class="status-pill ${['RUNNING','QUEUED'].includes(value)?'status-green':value==='FAILED'?'status-red':value==='WAITING_FOR_REVIEW'?'status-amber':''}">${escape(stateNames[value]||value)}</span>`;
  const date = value => {const d=new Date(value);return Number.isNaN(+d)?'—':d.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})+' '+d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});};
  window.MRA={icon,escape,api,session,records,modal,notify,copy,prefill,state,date,guide};
})();
