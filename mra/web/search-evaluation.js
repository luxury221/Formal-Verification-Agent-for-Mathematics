"use strict";
(() => {
  const $ = id => document.getElementById(id);
  if (location.pathname === '/number-theory') {
    const connect = () => { const heading=document.querySelector('.page-heading');if(!heading)return false;const a=document.createElement('a');a.className='button secondary';a.href='/evaluations#search-comparison';a.textContent='查看搜索对照';heading.append(a);return true; };
    if(!connect()){const observer=new MutationObserver(()=>{if(connect())observer.disconnect();});observer.observe($('page'),{childList:true,subtree:true});}return;
  }
  if (location.pathname !== '/evaluations') return;
  const {escape:e, modal, notify} = window.MRA;
  const labels={PREPARED:'候选已准备 · 待审查',RUNNING:'验证中',COMPLETED:'已完成',INTERRUPTED:'已中断',EXECUTION_ERROR:'执行故障',UNAVAILABLE:'证据已停用'};
  const variants={direct:'直接引用 · 深度 1',composed:'多步组合 · 深度 6'};
  let generation=0;
  async function api(path){const response=await fetch(path,{cache:'no-store'});const value=await response.json();if(!response.ok)throw new Error(value.error||'本地搜索对照暂不可用');return value;}
  function command(result){
    const plan=result?.plan;
    const body=plan?`python3 -m mra search-evaluation-run ${result.evaluation_id} --plan-fingerprint ${plan.plan_fingerprint} --reviewer '本地操作者' --confirmed`:'python3 -m mra search-evaluation-prepare --split all';
    const dialog=modal(plan?'运行已审查的候选计划':'先准备两种搜索方案',`<p>${plan?'请先逐题核对原文、所有前提、完整结论与生成证明，再填写真实核对者姓名。命令会执行真实 Lean 和独立重放；中断后不会自动重试。':'此命令只进行有限搜索并保存待审查候选，不启动 Lean 或模型。准备后刷新本页，展开新计划查看完整内容。'}</p><pre class="se-code">${e(body)}</pre><p class="hint">在项目的 WSL 终端执行。两种方案每题都最多 3000 步、0.75 秒搜索；证明深度是本次对照变量。</p>`);
    const button=document.createElement('button');button.className='button secondary';button.textContent='复制命令';button.onclick=async()=>{try{await navigator.clipboard.writeText(body);notify('命令已复制，请在本地核对后执行。');}catch{notify('请手工选择并复制命令。');}};dialog.append(button);
  }
  function casesHTML(cases, results=[]){
    return cases.map(c=>`<details class="se-case"><summary><span><strong>${e(c.target.title)}</strong><small>${e(c.id)} · ${c.split==='development'?'开发组':'回归组'}</small></span><span>${c.searches?'两种方案候选':'完整目标'}</span></summary><p>${e(c.target.original)}</p><pre class="se-code">${e(c.formal_statement)}</pre><p class="hint">${e(c.note)}</p>${c.searches?Object.keys(variants).map(variant=>{const s=c.searches[variant],row=results.find(r=>r.case_id===c.id&&r.variant===variant);return `<div class="se-variant"><strong>${e(variants[variant])}</strong><span class="se-badge ${row?.math_verdict==='PROVED'?'se-proved':''}">${e(row?row.math_verdict+' / '+row.evidence_level:'UNKNOWN / E0')}</span><p>${s.candidate?'生成了待核验候选':'本次没有候选'} · ${e(row?.run_status||s.stop_reason)} · ${s.stats.expansions} 步</p><details><summary>完整搜索、证明及核验记录</summary><pre class="se-code">${e(JSON.stringify({search:s,verification_plan:c.verification_plans[variant],check:row?.check||null},null,2))}</pre></details></div>`;}).join(''):''}</details>`).join('');
  }
  async function open(id){
    const token=++generation,result=await api('/v1/number-theory-search-evaluations/'+id);if(token!==generation)return;
    const dialog=modal('有界数论搜索 · 固定预算对照',`<div class="se-result-heading"><span>${e(labels[result.state]||result.state)}</span><code>${e(id)}</code></div>${!result.integrity_ok?`<p class="error">${e(result.reason)}；整轮统计与证明展示已停用。</p>`:`<p class="hint">${e(result.plan.visibility)}<br>两种方案分别固定 3000 步、32 子项、0.75 秒；检查各自共享 30 秒 worker，环境核对另计。统计只描述这些可见作者题目。</p>${result.summary?`<div class="se-scores">${Object.keys(variants).map(v=>`<div><span>${e(variants[v])}</span><strong>${result.summary[v].proved}<small> / ${result.summary[v].denominator}</small></strong><p>真实已证 · 未知 ${result.summary[v].unknown}</p><small>候选 ${result.summary[v].candidates} · 搜索 ${result.summary[v].search_expansions} 步<br>worker ${result.summary[v].worker_seconds.toFixed(2)} 秒</small></div>`).join('')}</div><p class="se-delta">多步组合独有已证：${result.summary.composed_only_proved.length} 题</p>`:'<p class="se-delta">整轮统计尚不可用。准备、执行中、中断与故障都不显示完成分数。</p>'}<div class="se-actions">${result.state==='PREPARED'?'<button class="button primary" id="se-run-command">审查后查看运行命令</button>':''}<button class="button secondary" id="se-export">导出完整记录</button></div>${result.error?`<p class="error">${e(result.error)}</p>`:''}<p class="hint">本地具名声明：${e(result.reviewer||'尚未确认')}。历史读取不执行新的验证；姓名不等于身份认证。</p>${casesHTML(result.plan.cases,result.results)}`}`);
    if(dialog.querySelector('#se-run-command'))dialog.querySelector('#se-run-command').onclick=()=>command(result);
    if(dialog.querySelector('#se-export'))dialog.querySelector('#se-export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)+'\n'],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  }
  async function load(){
    const token=++generation;$('se-error').hidden=true;
    try{const data=await api('/v1/number-theory-search-evaluations');if(token!==generation)return;
      $('se-cases').innerHTML=casesHTML(data.definition.cases);
      $('se-meta').textContent=`${data.definition.cases.length} 个完整目标 · 无参考证明输入 · 作者已查看`;
      $('se-history').innerHTML=data.runs.length?data.runs.map(r=>`<button class="se-history" data-se-id="${e(r.evaluation_id)}"><span><strong>${e(labels[r.state]||r.state)}</strong><small>${e(r.evaluation_id)}<br>${e((r.created_at||'').slice(0,19).replace('T',' '))}</small></span><span>${r.summary?`${r.summary.direct.proved} → ${r.summary.composed.proved} / ${r.summary.composed.denominator}`:'查看计划与状态'}</span></button>`).join(''):'<p class="hint">还没有本机对照。先准备候选，再逐题审查与具名执行。</p>';
      document.querySelectorAll('[data-se-id]').forEach(button=>button.onclick=()=>{history.replaceState(null,'','/evaluations#'+button.dataset.seId);open(button.dataset.seId).catch(error=>notify(error.message));});
      const id=location.hash.slice(1);if(/^ns-eval-[a-f0-9]{32}$/.test(id))await open(id);
      else if(id==='search-comparison')$('search-comparison').scrollIntoView({block:'start'});
    }catch(error){if(token===generation){$('se-error').textContent=error.message;$('se-error').hidden=false;}}
  }
  function mount(){
    const anchor=document.querySelector('.evaluation-grid');if(!anchor)return false;if($('search-comparison'))return true;
    const panel=document.createElement('section');panel.id='search-comparison';panel.className='se-panel';
    panel.innerHTML=`<div class="se-heading"><div><span class="eyebrow">MEASURE THE SEARCH</span><h2>同一个目标，让两条路线交出证据。</h2><p>直接引用与多步组合逐题对照，保留完整前提、失败与未知。</p></div><span class="se-badge">数论 · 搜索对照</span></div><div class="se-intro"><strong>先生成候选，再审查，最后真实核验。</strong><p>题目没有附带手写证明。两种方案都受相同搜索步数和时间上限约束，证明深度分别为 1 与 6。这些题目由作者编写且已经查看，结果用于开发回归。</p></div><div class="se-toolbar"><span id="se-meta">正在读取完整目标…</span><button class="button secondary" id="se-prepare">准备候选的命令</button></div><p class="error" id="se-error" hidden></p><div id="se-cases"></div><div class="se-toolbar"><h3>本机计划与结果</h3><button class="text-button" id="se-refresh">刷新记录</button></div><div id="se-history"></div><p class="hint">搜索失败保持 UNKNOWN；仅真实 Lean 与独立重放通过且具名对齐有效时记为已证。不据此推断模型成绩、独立持出表现或原创难题求解能力。</p>`;
    anchor.before(panel);$('se-prepare').onclick=()=>command();$('se-refresh').onclick=load;
    window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(/^ns-eval-[a-f0-9]{32}$/.test(id))open(id).catch(error=>notify(error.message));else ++generation;});
    load();return true;
  }
  if(!mount()){const observer=new MutationObserver(()=>{if(mount())observer.disconnect();});observer.observe($('page'),{childList:true,subtree:true});}
})();
