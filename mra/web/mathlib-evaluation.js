"use strict";
(() => {
  if(location.pathname!=='/evaluations')return;
  const {api,escape:e,icon,modal,notify,copy,date}=window.MRA;
  const $=id=>document.getElementById(id);
  const labels={COMPLETED:'已完成',REJECTED:'证明未通过',PRECHECK_REJECTED:'定义未开放',TIMEOUT:'检查超时',POLICY_BLOCKED:'检查受阻',INTERRUPTED:'已中断',EXECUTION_ERROR:'执行异常',UNAVAILABLE:'工件不可用',RUNNING:'执行中'};
  let plan=null,generation=0,detailGeneration=0;
  const formula=value=>`<pre class="mb-formula">${e(value||'当前语言无法表达，保留原问题')}</pre>`;
  function command(){return `python3 -m mra mathlib-benchmark --split ${plan.split} --plan-fingerprint ${plan.plan_fingerprint} --reviewer '本地评测核对者' --confirmed`;}
  function commandDialog(){if(!plan)return;const text=command(),d=modal('运行真实 Mathlib 功能评测',`<p>先逐题核对原文、完整条件、证明输入和预算。这是作者结构化输入的检查，不产生模型分数。命令中的姓名请改为实际核对者。</p><pre class="mb-code">${e(text)}</pre><p class="hint">每题最多一次候选检查与独立重放，共享30秒worker预算；环境完整性校验另计时。请等待整轮结束，Ctrl+C中断后不报告通过率，不自动续跑。</p><div class="dialog-actions"><button class="button primary" id="mb-copy">${icon('copy')} 复制命令</button></div>`);d.querySelector('#mb-copy').onclick=()=>copy(text);}
  function renderPlan(){
    $('mb-plan-meta').textContent=`${plan.cases.length} 道作者题目 · 每题 1 次 · ${plan.budget.maximum_worker_seconds} 秒 worker 总上限`;
    $('mb-cases').innerHTML=plan.cases.map(row=>`<details class="mb-case"><summary><span class="mb-dot ${row.preflight.supported?'':'is-unknown'}"></span><span><strong>${e(row.title)}</strong><small>${e(row.family)} · ${row.split==='development'?'开发组':'回归组'}</small></span><span class="mb-tag">${row.preflight.supported?'可提交检查':'语言边界'}</span></summary><div class="mb-case-body"><p>${e(row.original)}</p>${formula(row.preflight.formal_statement)}${row.preflight.reason?`<p class="mb-reason">${e(row.preflight.reason)}</p>`:''}<details><summary>完整目标与证明输入</summary><pre class="mb-code">${e(JSON.stringify(row.request,null,2))}</pre></details><p class="hint">作者预期的运行状态：${e(labels[row.expected_run_status]||row.expected_run_status)}；预期不授予数学结论。</p></div></details>`).join('');
    $('mb-command').disabled=false;
  }
  async function openResult(id){
    const token=++detailGeneration;
    const result=await api('/v1/mathlib-evaluations/'+id);
    if(token!==detailGeneration)return;
    const d=modal('Mathlib 评测 · 完整检查记录',`<div class="mb-result-heading"><span class="mb-tag">${e(labels[result.state]||result.state)}</span><span>真实模型调用 0</span></div><p class="task-id">${e(id)}</p>${!result.integrity_ok?`<p class="mb-reason">工件缺失、变化或协议不一致，通过率已隐藏。${e(result.reason||'请检查逐题证据。')}</p>`:''}${result.summary?`<div class="mb-stat-grid"><div><strong>${e(result.summary.processed)} / ${e(result.summary.total)}</strong><span>完成题目</span></div><div><strong>${e(result.summary.proved)}</strong><span>已证明</span></div><div><strong>${e(result.summary.unknown)}</strong><span>仍然未知</span></div></div><p class="hint">实际候选进程 ${e(result.summary.candidate_processes)} · 独立重放进程 ${e(result.summary.replay_processes)} · worker ${Number(result.summary.worker_seconds).toFixed(2)} 秒</p>`:''}<p class="mb-rate">作者输入检查通过率 <strong>${result.authored_plan_pass_rate===null?'—':(100*result.authored_plan_pass_rate).toFixed(1)+'%'}</strong><span>模型求解率 —</span></p><p class="hint">分母包含定义阻塞、证明失败和未知；整轮中断或工件不完整时不报告通过率。此记录不是自然语言形式化、独立盲测或原创研究能力评估。</p><div class="mb-results">${result.results.map(row=>`<details class="mb-case"><summary><span class="mb-dot ${row.math_verdict==='PROVED'?'':'is-unknown'}"></span><span><strong>${e(row.title)}</strong><small>${e(labels[row.run_status]||row.run_status)}</small></span><span class="mb-tag">${e(row.math_verdict)} / ${e(row.evidence_level)}</span></summary><div class="mb-case-body"><p>${e(row.original)}</p>${formula(row.preflight.formal_statement)}${row.reason?`<pre class="mb-code mb-reason">${e(row.reason)}</pre>`:''}<p class="hint">预期状态${row.expected_status_matches?'一致':'不一致'}；检查未通过不能当作数学反证。</p><details><summary>完整输入、来源与实际检查工件</summary><pre class="mb-code">${e(JSON.stringify(row,null,2))}</pre></details></div></details>`).join('')}</div><div class="dialog-actions"><button class="button secondary" id="mb-export">${icon('down')} 导出完整报告</button></div>`);
    d.querySelector('#mb-export').onclick=()=>{const blob=new Blob([JSON.stringify(result,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  }
  async function load(){
    const token=++generation;
    $('mb-error').hidden=true;
    try{
      const data=await api('/v1/mathlib-evaluations');
      if(token!==generation)return;
      plan=data.plan;$('mb-split').value='all';renderPlan();
      $('mb-history').innerHTML=data.runs.length?data.runs.map(row=>`<button class="mb-history-row" data-mb-run="${e(row.evaluation_id)}"><span>${icon('chart')}<span><strong>${e(row.evaluation_id.slice(0,23))}…</strong><small>${e(date(row.created_at))} · 作者输入 · ${e(row.split)}</small></span></span><span>${e(labels[row.state]||row.state)} ${icon('arrow')}</span></button>`).join(''):'<div class="mb-empty">还没有真实 Mathlib 评测记录。核对题目后，可复制命令在本机执行。</div>';
      document.querySelectorAll('[data-mb-run]').forEach(button=>button.onclick=()=>openResult(button.dataset.mbRun).catch(error=>notify(error.message)));
      const id=location.hash.slice(1);if(/^ml-bench-[a-f0-9]{32}$/.test(id))await openResult(id);
    }catch(error){if(token===generation){$('mb-error').hidden=false;$('mb-error').textContent=error.message;}}
  }
  function mount(){
    const anchor=document.querySelector('.evaluation-grid');if(!anchor)return false;
    if($('mathlib-evaluation'))return true;
    const section=document.createElement('section');section.id='mathlib-evaluation';section.className='mb-panel';section.innerHTML=`<div class="mb-heading"><div><span class="eyebrow">CHECK THE BOUNDARIES</span><h2>也让失败，进入衡量进步的尺度。</h2><p>真实检查完整命题，保留每一道题的成功、阻塞与未知。</p></div><span class="mb-tag">Mathlib · 功能评测</span></div><div class="mb-intro"><div><strong>作者输入，不产生模型分数</strong><p>包含组合证明、截断减法、错误证明、遗漏前提及未开放定义。题目已被作者查看，用于核对功能边界。</p></div><div class="mb-budget"><strong>30<span>s</span></strong><small>每题两阶段共享</small></div></div><div class="mb-toolbar"><label>题目组 <select id="mb-split"><option value="all">全部题目</option><option value="development">开发组</option><option value="regression">回归组</option></select></label><span id="mb-plan-meta">正在核对计划…</span><button class="button secondary" id="mb-command" disabled>${icon('code')} 运行命令</button></div><p class="error" id="mb-error" hidden></p><div id="mb-cases"></div><div class="mb-history-heading"><h3>本机真实记录</h3><button class="text-button" id="mb-refresh">${icon('refresh')} 刷新</button></div><div id="mb-history"></div>`;
    anchor.before(section);$('mb-command').onclick=commandDialog;$('mb-refresh').onclick=load;
    $('mb-split').onchange=async()=>{const token=++generation;$('mb-command').disabled=true;try{const value=await api('/v1/mathlib-evaluations/plan?split='+encodeURIComponent($('mb-split').value));if(token!==generation)return;plan=value;renderPlan();}catch(error){if(token===generation){$('mb-error').hidden=false;$('mb-error').textContent=error.message;}}};
    window.addEventListener('hashchange',()=>{const id=location.hash.slice(1);if(/^ml-bench-[a-f0-9]{32}$/.test(id))openResult(id).catch(error=>notify(error.message));});
    load();return true;
  }
  if(!mount()){const observer=new MutationObserver(()=>{if(mount())observer.disconnect();});observer.observe($('page'),{childList:true,subtree:true});}
})();
