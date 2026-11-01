"use strict";
(() => {
  if (location.pathname !== '/research') return;
  const {escape:e, modal, notify, session} = window.MRA;
  const page = document.getElementById('page');
  let catalog = null, busy = false, generation = 0, filter = 'ACTIVE', query = '', expanded = false;
  const $ = id => document.getElementById(id);
  async function api(path = '', body) {
    const options = {cache:'no-store'};
    if (body !== undefined) {
      const info = await session;
      Object.assign(options, {method:'POST', headers:{'Content-Type':'application/json','X-MRA-CSRF':info.csrf_token}, body:JSON.stringify(body)});
    }
    const response = await fetch('/v1/research-capacity' + path, options), result = await response.json();
    if (!response.ok) throw Error(result.error || '项目整理尚未完成');
    return result;
  }
  async function refresh() {
    const token = ++generation, value = await api();
    if (token !== generation) return;
    catalog = value;
    render();
  }
  function mount() {
    if (!page.querySelector('.research-principle') || $('research-capacity-panel')) return;
    const panel = document.createElement('section');
    panel.id = 'research-capacity-panel';
    panel.className = 'panel research-capacity-panel';
    page.querySelector('.research-principle').after(panel);
    render();
  }
  function render() {
    const panel = $('research-capacity-panel');
    if (!panel) return;
    if (!catalog) { panel.innerHTML = '<p class="hint">正在读取项目容量…</p>'; return; }
    const counts = catalog.counts, limits = catalog.limits;
    panel.innerHTML = `<div class="research-capacity-heading"><div><div class="eyebrow">PROJECT COLLECTION</div><h2>为下一条研究线，留出空间。</h2></div><div class="research-capacity-counts" id="research-capacity-counts" aria-live="polite"><strong>${e(counts.active)} / ${e(limits.active_projects)}</strong><span>活跃项目 · ${e(counts.archived)} 个已归档</span></div><button type="button" class="text-button" id="research-capacity-refresh" ${busy?'disabled':''}>刷新容量</button></div><details id="research-capacity-details" ${expanded?'open':''}><summary>整理项目与恢复归档</summary><p class="hint">归档释放活跃名额，历史记录与证据直链继续可用。恢复需要一个空闲名额。归档用于整理列表，已有研究仍可通过直链继续。</p><div class="research-capacity-tools"><label>查找项目<input type="search" id="research-capacity-query" maxlength="200" placeholder="输入标题或问题中的关键词" value="${e(query)}"></label><label>显示范围<select id="research-capacity-filter"><option value="ACTIVE" ${filter==='ACTIVE'?'selected':''}>活跃项目</option><option value="ARCHIVED" ${filter==='ARCHIVED'?'selected':''}>已归档</option><option value="ALL" ${filter==='ALL'?'selected':''}>全部项目</option></select></label><span class="hint">保留 ${e(counts.retained)} / ${e(limits.retained_projects)} 个项目</span></div><div id="research-capacity-projects" class="research-capacity-projects"></div></details>`;
    $('research-capacity-details').ontoggle = event => { expanded = event.target.open; };
    $('research-capacity-query').oninput = event => { query = event.target.value; rows(); };
    $('research-capacity-filter').onchange = event => { filter = event.target.value; rows(); };
    $('research-capacity-refresh').onclick = () => refresh().catch(error => notify(error.message));
    rows();
  }
  function rows() {
    const selected = catalog.projects.filter(p => (filter==='ALL'||p.lifecycle.state===filter) && (p.title+' '+p.question).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    $('research-capacity-projects').innerHTML = selected.length ? selected.map(p => `<article class="research-capacity-project" data-capacity-project="${e(p.project_id)}"><div><span class="eyebrow">${p.lifecycle.state==='ARCHIVED'?'已归档':'活跃研究'} · ${e(p.branch_count)} 条研究线</span><h3>${e(p.title)}</h3><p>${e(p.question.slice(0,180))}</p><a class="text-button" href="/research#${e(p.project_id)}">打开研究记录 →</a></div><button type="button" class="button secondary" data-capacity-action="${p.lifecycle.state==='ARCHIVED'?'restore':'archive'}" data-project="${e(p.project_id)}" ${busy?'disabled':''}>${p.lifecycle.state==='ARCHIVED'?'恢复到活跃列表':'归档项目'}</button></article>`).join('') : '<p class="hint">当前范围没有匹配的项目。</p>';
    $('research-capacity-projects').querySelectorAll('[data-capacity-action]').forEach(button => {
      button.onclick = () => dialog(catalog.projects.find(p => p.project_id===button.dataset.project), button.dataset.capacityAction);
    });
  }
  function dialog(project, action) {
    if (busy) return;
    const restoring = action==='restore', label = restoring?'恢复到活跃列表':'归档项目';
    const box = modal(label, `<h3>${e(project.title)}</h3><p>${restoring?'恢复需要当前仍有空闲的活跃名额。':'归档后可在“已归档”列表恢复，原研究内容、证明记录和证据链接保留。'}</p><label for="capacity-reviewer">操作人</label><input id="capacity-reviewer" maxlength="200" autocomplete="off"><label for="capacity-reason">整理原因</label><input id="capacity-reason" maxlength="1000" autocomplete="off"><label class="check-row"><input type="checkbox" id="capacity-confirm"><span>已核对项目，确认${restoring?'恢复':'归档'}。</span></label><p class="hint" id="capacity-action-status" role="status"></p><div class="dialog-actions"><button type="button" class="button primary" id="capacity-submit">确认${restoring?'恢复':'归档'}</button></div>`);
    const requestId = 'capacity-web-' + crypto.randomUUID(), submit = box.querySelector('#capacity-submit');
    let payload = null;
    submit.onclick = async () => {
      if (busy) return;
      const status = box.querySelector('#capacity-action-status');
      try {
        if (!payload) {
          const reviewer = box.querySelector('#capacity-reviewer').value.trim(), reason = box.querySelector('#capacity-reason').value.trim();
          if (!reviewer || !reason || !box.querySelector('#capacity-confirm').checked) throw Error('请填写操作人、原因并确认。');
          payload = {request_id:requestId, expected_revision:project.lifecycle.revision, reviewer, reason, confirmed:true};
        }
        busy = true;
        submit.disabled = true;
        box.querySelectorAll('input').forEach(input => { input.disabled = true; });
        await api('/'+encodeURIComponent(project.project_id)+'/'+action, payload);
        await refresh();
        box.close();
        window.dispatchEvent(new CustomEvent('mra-project-capacity-change'));
        notify(restoring?'项目已恢复到活跃列表。':'项目已归档，历史记录与证据链接保留。');
      } catch(error) {
        status.textContent = error.message + (payload?' 请求编号：'+requestId+'。写操作未自动重发，请刷新核对；再次确认会重用同一编号。':'');
      } finally {
        busy = false;
        submit.disabled = false;
        if ($('research-capacity-refresh')) $('research-capacity-refresh').disabled = false;
        if (catalog && $('research-capacity-projects')) rows();
      }
    };
  }
  new MutationObserver(mount).observe(page, {childList:true, subtree:true});
  mount();
  refresh().catch(error => notify(error.message));
})();
