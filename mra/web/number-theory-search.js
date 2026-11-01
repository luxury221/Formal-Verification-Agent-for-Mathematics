"use strict";
(() => {
  if (location.pathname !== '/number-theory') return;
  const $ = id => document.getElementById(id);
  const fields = ['title', 'original', 'variables', 'assumptions', 'goal', 'proof', 'timeout'];
  const reasons = {candidate_found:'找到待核验的证明候选', expansion_limit:'已用完搜索步数', wall_time_limit:'已用完搜索时间', depth_limit:'已达到搜索深度', bounded_space_exhausted:'当前有限规则中未找到候选', proof_language_limit:'候选超过现有证明语言上限'};

  function mount() {
    if (!$('ml-editor')) return false;
    let generation = 0, record = null, candidateGeneration = -1, snapshot = '', busy = false;
    const panel = document.createElement('section');
    panel.className = 'ns-panel'; panel.id = 'ns-panel'; panel.setAttribute('aria-labelledby', 'ns-heading');
    panel.innerHTML = `<div class="ns-heading"><div><span class="eyebrow">BOUNDED PROOF SEARCH</span><h3 id="ns-heading">让引理组成一条证明路线。</h3></div><span class="draft-badge">候选 · UNKNOWN / E0</span></div><p class="hint">根据完整前提与结论，组合 22 条受控引理。先生成候选，再核对并提交真实 Lean。</p><div class="ns-controls"><label>搜索深度<input id="ns-depth" type="number" min="1" max="8" value="6"></label><label>搜索步数<input id="ns-expansions" type="number" min="1" max="6000" value="3000"></label><button class="button secondary" id="ns-search" type="button">搜索证明候选</button></div><p class="ns-limit">最多 32 个已有自然数子项，搜索时间上限 0.75 秒。此预算独立于下方 Lean 检查预算。</p><p id="ns-status" role="status" aria-live="polite">可直接搜索，无需预先填写证明项。</p><p id="ns-error" class="error" role="alert" hidden></p><div id="ns-result" hidden><div class="ns-stats" id="ns-stats"></div><pre class="ml-formula" id="ns-statement"></pre><p id="ns-explanation" class="hint"></p><details><summary>查看候选证明与引用</summary><pre class="ml-log" id="ns-proof"></pre></details><details><summary>搜索记录、边界与来源</summary><pre class="ml-log" id="ns-record"></pre></details><div class="ns-actions"><button class="button primary" id="ns-adopt" type="button">带入证明栏，重新核对</button><button class="button secondary" id="ns-export" type="button">导出搜索记录</button></div></div>`;
    $('ml-proof').closest('label').before(panel);
    document.querySelector('.ml-scope-note').textContent = '支持 Nat 整除、gcd 与互素的结构化证明及有界引理搜索。搜索候选保持 UNKNOWN；具名确认后才进行真实检查。结果独立保存，尚未接入旧研究图或私有归档；无需配置模型。';
    const badge = $('ml-editor').querySelector('.panel-heading .draft-badge');
    if (badge) badge.textContent = '结构化输入 · 可搜索候选';

    function invalidate() {
      generation++; record = null; candidateGeneration = -1;
      $('ns-result').hidden = true; $('ns-adopt').disabled = true; $('ns-error').hidden = true;
      $('ns-status').textContent = busy ? '输入已修改，本次搜索返回后将丢弃过期候选。' : '输入已更新，可按当前完整命题重新搜索。';
    }
    function target() {
      const variables = $('ml-variables').value.trim();
      return {schema:'mathlib-nat-gcd-v1', title:$('ml-title').value, original:$('ml-original').value, variables:variables ? variables.split(',').map(v=>v.trim()) : [], assumptions:JSON.parse($('ml-assumptions').value), goal:JSON.parse($('ml-goal').value), timeout_seconds:Number($('ml-timeout').value)};
    }
    fields.forEach(name => $('ml-' + name).addEventListener('input', invalidate));
    ['ns-depth', 'ns-expansions'].forEach(id => $(id).addEventListener('input', invalidate));
    // Example loading and reuse set values programmatically. Capture their user
    // actions as well as ordinary input, including edits later reverted to old text.
    $('ml-example').addEventListener('change', invalidate, true);
    document.addEventListener('click', event => { if (event.target.closest('#ml-reuse')) invalidate(); }, true);

    $('ns-search').addEventListener('click', async () => {
      if (busy) return;
      invalidate(); const version = generation;
      busy = true; $('ns-search').disabled = true; $('ns-status').textContent = '正在有限规则与预算内寻找候选…';
      try {
        const value = target(); snapshot = JSON.stringify(value);
        const body = {schema:'mra-number-theory-search-v1', target:value, budget:{max_depth:Number($('ns-depth').value), max_expansions:Number($('ns-expansions').value)}};
        const info = await window.MRA.session;
        const response = await fetch('/v1/number-theory/search', {method:'POST', cache:'no-store', headers:{'Content-Type':'application/json', 'X-MRA-CSRF':info.csrf_token}, body:JSON.stringify(body)});
        const result = await response.json();
        if (version !== generation || snapshot !== JSON.stringify(target())) {
          $('ns-status').textContent = '已丢弃过期搜索结果。请按当前输入重新搜索。'; return;
        }
        if (!response.ok) throw new Error(result.error || '搜索请求失败');
        record = result; candidateGeneration = version;
        $('ns-status').textContent = `${reasons[result.stop_reason] || '搜索已结束'} · UNKNOWN / E0`;
        $('ns-result').hidden = false;
        $('ns-stats').textContent = `${result.stats.expansions} 步 · 深度 ${result.stats.last_depth}/${result.budget.max_depth} · ${result.stats.used_terms} 个子项 · ${Number(result.stats.elapsed_seconds).toFixed(3)} 秒${result.stats.term_pool_truncated ? ' · 子项池已截断' : ''}`;
        $('ns-statement').textContent = result.formal_statement;
        $('ns-explanation').textContent = result.candidate ? '候选尚未经过 Lean 检查。带入后，请核对原文、完整前提、结论和证明，再具名确认。' : '本次没有找到可提交候选。搜索能力与预算有限，无法据此判断命题为假；可调整预算、补充有依据的条件，或手工提供证明。';
        $('ns-proof').textContent = JSON.stringify({proof:result.candidate?.proof || null, references:result.references}, null, 2);
        $('ns-record').textContent = JSON.stringify(result, null, 2);
        $('ns-adopt').hidden = !result.candidate; $('ns-adopt').disabled = !result.candidate;
      } catch (error) {
        if (version === generation) { $('ns-error').textContent = error.message; $('ns-error').hidden = false; $('ns-status').textContent = '搜索未完成，未产生证明结论。'; }
      } finally { busy = false; $('ns-search').disabled = false; }
    });
    $('ns-adopt').addEventListener('click', () => {
      if (!record?.candidate || candidateGeneration !== generation || snapshot !== JSON.stringify(target())) return;
      const proof = record.candidate.proof;
      $('ml-proof').value = JSON.stringify(proof, null, 2);
      $('ml-proof').dispatchEvent(new Event('input', {bubbles:true}));
      $('ns-status').textContent = '已带入候选并清除旧确认。请点击“核对完整公式”，重新审查和具名确认。';
      $('ml-proof').focus();
    });
    $('ns-export').addEventListener('click', () => {
      if (!record) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2) + '\n'], {type:'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = 'number-theory-search-' + record.record_sha256.slice(0, 12) + '.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    return true;
  }
  if (!mount()) {
    const observer = new MutationObserver(() => { if (mount()) observer.disconnect(); });
    observer.observe($('page'), {childList:true, subtree:true});
  }
})();
