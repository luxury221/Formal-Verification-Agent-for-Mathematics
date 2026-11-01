(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const names = {claim:'待验证结论', author_step:'作者证明步骤', external_fact:'引用事实', definition:'定义 / 记号', empirical:'实验陈述', context:'背景说明', needs_clarification:'需要澄清'};
  const kinds = {claim_candidate:'候选命题', definition_candidate:'候选定义', equation:'公式', passage:'原文段落', reference:'参考文献', caption:'图表说明'};
  const math = block => ['claim', 'author_step', 'external_fact'].includes(block.annotation?.classification);
  const uid = () => 'article-ui-' + crypto.randomUUID();
  let record = null, selected = null, section = '', view = 'source', busy = false, dirty = false;
  let preview = null, workspace = null, candidates = [];
  let recheckJobs = [], recheckJob = null, recheckHistory = null, recheckTimer = null;
  let articleEpoch = 0, recheckReadEpoch = 0, recheckReadRevision = null, recheckReadError = '', lastWrite = null;
  const checked = new Set();
  const runningRecheck = job => ['QUEUED', 'RUNNING'].includes(job?.state);

  function message(text, error = false) {
    $('ar-message').textContent = text;
    $('ar-message').classList.toggle('error', error);
  }
  async function api(path, data) {
    if (data?.request_id) lastWrite = data.request_id;
    const session = await window.MRA.session;
    const response = await fetch('/v1/article-audits' + path, {
      method:data === undefined ? 'GET' : 'POST', cache:'no-store',
      headers:data === undefined ? {} : {'Content-Type':'application/json', 'X-MRA-CSRF':session.csrf_token},
      body:data === undefined ? undefined : JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '请求未完成');
    return result;
  }
  function controls() {
    document.querySelectorAll('.article-page button,.article-page input,.article-page select,.article-page textarea').forEach(el => {el.disabled = busy || el.dataset.locked === 'true';});
    for (const id of ['ar-recheck', 'ar-export', 'ar-export-markdown']) $(id).disabled = busy || !record || id === 'ar-export-markdown' && !record.review_markdown;
    $('ar-recheck').disabled = busy || !record || dirty || recheckJobs.some(runningRecheck) || runningRecheck(recheckJob);
    const cancel = $('ar-recheck-cancel');
    if (cancel) cancel.disabled = busy || !runningRecheck(recheckJob) || recheckJob.cancel_requested;
    $('ar-research').querySelector('button').disabled = busy || dirty || !record?.blocks.some(b => b.id === selected && math(b));
  }
  function forgetEvidence() {
    if (!record) return;
    record.evidence_rechecked = false;
    record.checked_at = null;
    record.review_markdown = null;
    record.counts.current_verified_obligations = 0;
    record.counts.unverified_mathematical_obligations = record.blocks.filter(math).length;
    for (const block of record.blocks) {
      block.claim_verdict = 'UNKNOWN'; block.evidence_level = 'E0'; block.finding = null;
      block.blocked_by = block.annotation?.depends_on || [];
      block.step_status = block.blocked_by.length ? 'BLOCKED' : 'UNVERIFIED';
      block.author_proof_verdict = 'GAP_FOUND';
      for (const link of block.links) link.observation = {status:'NOT_RECHECKED', reason:'当前读取未完成；请显式复核证据。'};
    }
    if (record.workflow) {
      for (const node of record.workflow.dependency_graph) node.status = record.blocks.find(b => b.id === node.id).step_status;
      record.workflow.frontier = record.blocks.filter(math).map(b => ({block_id:b.id, statement:b.annotation.statement, ready:!b.blocked_by.length, blocked_by:b.blocked_by, downstream_obligations:0}));
      record.workflow.ready_obligations = record.workflow.frontier.filter(b => b.ready).length;
    }
  }
  async function action(fn) {
    if (busy) return;
    busy = true; lastWrite = null; controls();
    try {await fn();}
    catch (error) {forgetEvidence(); clearPreview(); render(); message(error.message + (lastWrite ? ' 请求编号：' + lastWrite + '。写操作不会自动重发；请刷新记录核对是否已经保存。' : ''), true);}
    finally {busy = false; controls();}
  }
  function requireSaved() {
    if (dirty) throw new Error('请先保存当前义务，或放弃未保存修改，再继续创建任务、切换原文或关联证据。');
  }
  function clearPreview() {
    preview = null;
    $('ar-return-confirm').checked = false;
    $('ar-return-confirmation').hidden = true;
    $('ar-return-target').hidden = true;
  }
  function clearReturn() {
    clearPreview(); workspace = null; candidates = [];
    $('ar-return').hidden = true; $('ar-return').reset();
  }
  async function list() {
    const data = await api('');
    $('ar-articles').innerHTML = '<option value="">选择已导入的论文</option>' + data.articles.map(item => '<option value="' + escape(item.article_id) + '">' + escape(item.title) + ' · ' + item.annotated + '/' + item.blocks + ' 处已整理</option>').join('');
    $('ar-articles').value = record?.article_id || '';
    return data.articles;
  }
  function mutation(extra) {return {request_id:uid(), expected_revision:record.revision, ...extra};}
  function resetRecheck() {
    recheckReadEpoch++;
    clearTimeout(recheckTimer); recheckJobs = []; recheckJob = null; recheckHistory = null;
    recheckReadRevision = null; recheckReadError = ''; renderRecheck();
  }
  function recheckElapsed(job) {
    const start = Date.parse(job?.started_at || job?.created_at);
    const end = job?.completed_at ? Date.parse(job.completed_at) : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end)) return '时间待更新';
    const seconds = Math.max(0, Math.floor((end - start) / 1000));
    return seconds < 60 ? seconds + ' 秒' : Math.floor(seconds / 60) + ' 分 ' + seconds % 60 + ' 秒';
  }
  function renderRecheck() {
    const panel = $('ar-recheck-job');
    if (!panel) return;
    panel.hidden = !record;
    if (!record) return;
    const job = recheckJob, progress = job?.progress || {};
    const labels = {QUEUED:'等待后台复核', RUNNING:'正在逐图读取既有证据', COMPLETED:'本次历史复核已结束', CANCELLED:'已取消本次复核', INTERRUPTED:'复核已中断', FAILED:'本次复核失败', STALE:'论文快照已变化'};
    const date = value => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN') : '时间见详细记录';
    const sameRevision = job?.snapshot_revision === record.revision && recheckReadRevision === record.revision;
    const summary = sameRevision && job?.snapshot_status === 'MATCHES_AT_READ' && job?.implementation_status === 'MATCHES_AT_READ' ? job.summary : null;
    const history = recheckHistory;
    const groups = history?.groups || [];
    const controlled = job?.read_control?.applicable_to_job_implementation && job.read_control.cancel_during_read;
    const completedGraphs = Math.max(0, Number(progress.completed_graphs) || 0);
    const totalGraphs = Math.max(0, Number(progress.total_graphs) || 0);
    const backendNames = {proof:'结构化证明', calculus:'微积分', pde:'偏微分方程', typed:'类型化数学'};
    const active = job?.active_graph;
    const versionLabel = value => ({MATCHES_AT_READ:'读取时一致', CHANGED:'已变化', UNAVAILABLE:'暂不可读取'}[value] || value || '待核对');
    const observations = {MATCHED_AS_OF_CHECK:'截至该次检查匹配', STALE_BINDING:'关联已变化', UNAVAILABLE:'证据不可用', BUSY:'验证器忙，未核定', DIFFERENT_EVIDENCE:'证据身份已变化'};
    panel.className = 'article-recheck-job';
    panel.dataset.state = job?.state || 'NOT_STARTED';
    panel.innerHTML = '<div class="article-recheck-heading"><div><p class="article-kicker">BACKGROUND EVIDENCE REVIEW</p><h2>' + escape(job ? labels[job.state] || job.state : '分步复核关联证据') + '</h2></div><span class="article-tag">历史观察 · 不授予实时证明状态</span></div>' +
      '<p class="muted">后台读取已有关联任务，逐图记录当时的工件与身份；不会执行新证明。下方历史观察独立于原文义务的 UNKNOWN 和全文状态。</p>' +
      (recheckJobs.length ? '<label>复核记录<select id="ar-recheck-select">' + recheckJobs.map(item => '<option value="' + escape(item.job_id) + '" ' + (item.job_id === job?.job_id ? 'selected' : '') + '>' + escape(date(item.created_at) + ' · ' + (labels[item.state] || item.state)) + '</option>').join('') + '</select></label>' : '') +
      (job ? '<div class="article-recheck-progress"><span>已读取 <strong>' + escape(completedGraphs) + ' / ' + escape(totalGraphs) + '</strong> 个图</span><span>已观察 ' + escape(progress.checked_links ?? 0) + ' / ' + escape(progress.total_links ?? 0) + ' 条关联</span><span>本次用时 ' + escape(recheckElapsed(job)) + '</span></div>' +
        '<progress class="article-recheck-meter" aria-label="已读取的关联图" max="' + Math.max(1, totalGraphs) + '" value="' + Math.min(completedGraphs, Math.max(1, totalGraphs)) + '"></progress>' +
        (active ? '<p class="article-recheck-active">正在核对 ' + escape(backendNames[active.backend] || active.backend) + '<code>' + escape(active.graph_id) + '</code></p>' : '') +
        (controlled && runningRecheck(job) ? '<p class="muted">可取消当前读取；单图读取预算 ' + escape(job.read_control.timeout_seconds) + ' 秒。超时会记录为证据暂不可用，请根据诊断另行复核。</p>' : '') +
        '<p class="muted">论文修订 ' + escape(job.snapshot_revision ?? '—') + ' · 原文版本：' + escape(versionLabel(job.snapshot_status)) + ' · 规则版本：' + escape(versionLabel(job.implementation_status)) + '<br>' + (job.completed_at ? '截至 ' + escape(date(job.completed_at)) : '开始于 ' + escape(date(job.started_at || job.created_at))) + '</p>' +
        (job.cancel_requested && runningRecheck(job) ? '<p class="article-recheck-notice">' + (controlled ? '取消请求已登记，正在停止并清理当前读取；未提交的观察不会发布。' : '取消请求已登记。此历史任务须等待当前读取返回，未提交的观察不会发布。') + '</p>' : '') +
        (job.error ? '<p class="article-recheck-notice">本次记录的诊断：' + escape(typeof job.error === 'string' ? job.error : JSON.stringify(job.error)) + '</p>' : '') +
        (!sameRevision || job.snapshot_status !== 'MATCHES_AT_READ' || job.implementation_status !== 'MATCHES_AT_READ' ? '<p class="article-recheck-notice">旧版本复核记录，不适用于当前内容；这里只保留历史观察。</p>' : '') +
        (summary ? '<p class="article-recheck-summary">本次历史观察：' + escape(summary.matched_links ?? 0) + ' 条匹配，' + escape(summary.issue_links ?? 0) + ' 条需要处理。计数不代表全文验证完成率。</p>' : '') +
        (recheckReadError ? '<p class="article-recheck-notice">状态读取未完成：' + escape(recheckReadError) + '。已显示的内容仅为历史。</p>' : '') +
        '<div class="article-recheck-actions"><button class="button secondary" type="button" id="ar-recheck-refresh">刷新这次记录</button>' + (runningRecheck(job) ? '<button class="button secondary" type="button" id="ar-recheck-cancel" ' + (job.cancel_requested ? 'disabled' : '') + '>取消后台复核</button>' : '') + '</div>' +
        (history ? '<details class="article-recheck-history"><summary>逐图历史观察 · ' + groups.length + ' 个图</summary>' + groups.map(group => '<article><header><code>' + escape(group.backend + ' · ' + group.graph_id) + '</code><span>' + escape(date(group.checked_at)) + '</span></header>' + (group.links || []).map(link => '<p><strong>' + escape(link.block_id + ' / ' + link.node_id) + '</strong> · ' + escape(observations[link.status] || link.status) + '<br>' + escape(link.reason || '') + '</p>').join('') + '</article>').join('') + '<details><summary>完整历史快照与报告引用</summary><pre>' + escape(JSON.stringify(history, null, 2)) + '</pre></details></details>' : '') : '<p class="muted">点击上方“复核关联证据”启动一个可取消的后台任务。</p>');
    controls();
  }
  async function readRecheck(jobId, includeResult = true) {
    const key = record?.article_id, token = articleEpoch, readToken = ++recheckReadEpoch;
    if (!key || !jobId) return;
    const current = () => token === articleEpoch && readToken === recheckReadEpoch && record?.article_id === key && recheckJob?.job_id === jobId;
    try {
      const job = await api('/' + key + '/rechecks/' + encodeURIComponent(jobId));
      if (!current()) return;
      recheckJob = job; recheckReadRevision = record.revision; recheckReadError = '';
      const index = recheckJobs.findIndex(item => item.job_id === jobId);
      if (index >= 0) recheckJobs[index] = job;
      if (includeResult && !runningRecheck(job)) {
        const value = await api('/' + key + '/rechecks/' + encodeURIComponent(jobId) + '/result');
        if (!current()) return;
        recheckHistory = value.result?.history || value.result || null;
        if (value.job) recheckJob = value.job;
      }
      renderRecheck(); scheduleRecheck();
    } catch (error) {
      if (!current()) return;
      throw error;
    }
  }
  function scheduleRecheck() {
    clearTimeout(recheckTimer);
    if (!runningRecheck(recheckJob)) return;
    const key = record.article_id, token = articleEpoch, jobId = recheckJob.job_id;
    recheckTimer = setTimeout(async () => {
      try {await readRecheck(jobId);}
      catch (error) {
        if (token !== articleEpoch || record?.article_id !== key || recheckJob?.job_id !== jobId) return;
        recheckReadError = error.message; renderRecheck(); scheduleRecheck();
      }
    }, 2500);
  }
  async function listRechecks() {
    const key = record?.article_id, token = articleEpoch;
    if (!key) return;
    const value = await api('/' + key + '/rechecks');
    if (token !== articleEpoch || record?.article_id !== key) return;
    recheckJobs = value.jobs || [];
    recheckJob = recheckJobs.find(runningRecheck) || recheckJobs.find(item => item.job_id === recheckJob?.job_id) || recheckJobs.slice().sort((a,b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))[0] || null;
    renderRecheck();
    if (recheckJob) await readRecheck(recheckJob.job_id);
  }
  function returnRelations() {
    const candidate = candidates[Number($('ar-return-task').value)];
    const labels = {equivalent:'对应这一完整义务', counterexample_specialization:'反例特例证书 · 仅限明确范围'};
    $('ar-return-relation').innerHTML = '<option value="">请明确选择原文关系</option>' + (candidate?.relations || []).map(value => '<option value="' + escape(value) + '">' + escape(labels[value] || value) + '</option>').join('');
    clearPreview();
  }
  async function open(key) {
    requireSaved(); articleEpoch++; resetRecheck(); const value = await api('/' + key);
    record = value; selected = null; section = ''; dirty = false; checked.clear(); clearReturn();
    $('ar-search').value = ''; $('ar-filter').value = 'all';
    history.replaceState(null, '', '#' + key); $('ar-detail').hidden = true; $('ar-bind').reset();
    render(); await list(); await listRechecks();
  }
  function setView(name) {
    view = name;
    const panels = {source:'ar-workspace', map:'ar-map', frontier:'ar-frontier', tasks:'ar-task-view'};
    for (const [value, id] of Object.entries(panels)) {
      $(id).hidden = !record || view !== value;
      const tab = $('ar-tab-' + value);
      tab.setAttribute('aria-selected', String(view === value)); tab.tabIndex = view === value ? 0 : -1;
    }
  }
  function render() {
    if (!record) return;
    $('ar-overview').hidden = false; $('ar-flow').hidden = false; $('ar-empty').hidden = true;
    $('ar-document-title').textContent = record.source.title; $('ar-source-url').href = record.source.url;
    $('ar-reading-note').textContent = record.evidence_rechecked ? '历史读取时间：' + new Date(record.checked_at).toLocaleString('zh-CN') + '；未执行新证明。' : '当前显示历史关联。后台复核将逐图记录截至检查时的观察，不授予实时有效状态。';
    const c = record.counts;
    $('ar-metrics').innerHTML = [[c.source_blocks,'原文索引块'], [c.unclassified,'尚待梳理'], [c.current_verified_obligations,'本次复核有效义务'], [c.unverified_mathematical_obligations,'已列出的未验证数学义务']].map(([n,label]) => '<div class="article-metric"><strong>' + n + '</strong><span>' + label + '</span></div>').join('');
    $('ar-sections').innerHTML = '<button class="article-section ' + (!section ? 'active' : '') + '" data-section="">全部章节</button>' + record.sections.map(item => '<button class="article-section ' + (section === item.id ? 'active' : '') + '" data-section="' + escape(item.id) + '">' + escape(item.title) + '</button>').join('');
    renderWorkflow(); renderBlocks(); setView(view); renderRecheck();
    if (selected) detail(selected, false);
    controls();
  }
  function visibleBlocks() {
    const query = $('ar-search').value.trim().toLowerCase(), filter = $('ar-filter').value;
    return record.blocks.filter(b => (!section || b.section === section) && (!query || (b.quote + ' ' + b.id + ' ' + (b.annotation?.statement || '')).toLowerCase().includes(query)) && (filter === 'all' || filter === 'unclassified' && !b.annotation || filter === 'claims' && b.kind === 'claim_candidate' || filter === 'math' && math(b) || filter === 'gaps' && math(b) && b.step_status !== 'VERIFIED'));
  }
  function renderBlocks() {
    if (!record) return;
    const blocks = visibleBlocks(); $('ar-list-count').textContent = blocks.length + ' 处';
    $('ar-selected-count').textContent = '已选 ' + checked.size + ' 处';
    $('ar-blocks').innerHTML = blocks.length ? blocks.map(b => {
      const eligible = !b.annotation && ['passage', 'reference', 'caption'].includes(b.kind) && b.quote.length <= 8000;
      const checkbox = eligible ? '<label class="article-block-select"><input type="checkbox" data-select="' + escape(b.id) + '" aria-label="选中 ' + escape(b.id) + '" ' + (checked.has(b.id) ? 'checked' : '') + '>整理</label>' : '';
      return '<div class="article-block-row">' + checkbox + '<button class="article-block ' + (selected === b.id ? 'selected' : '') + '" data-block="' + escape(b.id) + '"><div class="article-block-head"><strong>' + escape(b.id) + '</strong><span class="article-tags"><span class="article-tag">' + escape(b.annotation ? names[b.annotation.classification] : kinds[b.kind]) + '</span>' + (b.step_status === 'VERIFIED' ? '<span class="article-tag good">该义务已核查</span>' : math(b) ? '<span class="article-tag gap">' + (b.step_status === 'BLOCKED' ? '依赖未完成' : '尚未验证') + '</span>' : '') + (b.finding ? '<span class="article-tag gap">已有反例特例</span>' : '') + '</span></div><p>' + escape(b.quote.slice(0,240)) + (b.quote.length > 240 ? '…' : '') + '</p></button></div>';
    }).join('') : '<div class="article-panel muted">此范围没有匹配内容。</div>';
  }
  function renderWorkflow() {
    const w = record.workflow;
    $('ar-pipeline').innerHTML = [[1,'原文梳理',record.counts.classified + ' 处已整理','source'], [2,'义务与前提',w.dependency_graph.length + ' 个数学义务','map'], [3,'研究与形式化',w.research_tasks + ' 个关联任务','tasks'], [4,'真实证据',record.counts.current_verified_obligations + ' 个义务本次有效','frontier']].map(([n,title,note,destination]) => '<button data-go="' + destination + '"><span>' + n + '</span><div><strong>' + title + '</strong><small>' + escape(note) + '</small></div></button>').join('');
    const levels = new Map();
    for (const node of w.dependency_graph) {if (!levels.has(node.depth)) levels.set(node.depth, []); levels.get(node.depth).push(node);}
    $('ar-dependency-map').innerHTML = levels.size ? [...levels].map(([depth,nodes]) => '<div class="article-map-column"><p class="article-kicker">' + (depth === 0 ? '基础义务' : '推理层次 ' + depth) + '</p>' + nodes.map(n => '<button class="article-map-node ' + (n.status === 'VERIFIED' ? 'good' : '') + '" data-focus="' + escape(n.id) + '"><strong>' + escape(n.id) + '</strong><span>' + escape(names[n.classification]) + '</span><small>' + (n.status === 'VERIFIED' ? '该义务已核查' : n.status === 'BLOCKED' ? '等待前提' : '待验证') + '</small><p>' + (n.depends_on.length ? '← ' + escape(n.depends_on.join(', ')) : '尚未列出前置义务') + '</p></button>').join('') + '</div>').join('') : '<p class="muted">先在原文中保存数学义务与依赖。</p>';
    $('ar-frontier-items').innerHTML = w.frontier.length ? w.frontier.map((item,i) => '<button class="article-frontier-item" data-focus="' + escape(item.block_id) + '"><span class="article-frontier-number">' + String(i + 1).padStart(2,'0') + '</span><div><strong>' + escape(item.block_id) + '</strong><p>' + escape(item.statement.slice(0,180)) + '</p><small>' + (item.ready ? '所列前提已闭合，可继续研究' : '等待：' + escape(item.blocked_by.join(', '))) + ' · 影响 ' + item.downstream_obligations + ' 个后续义务</small></div><span class="article-tag ' + (item.ready ? 'good' : 'gap') + '">' + (item.ready ? '可推进' : '前提未完成') + '</span></button>').join('') : '<p class="muted">当前已列义务没有待处理项；仍需确认原文义务是否完整。</p>';
    $('ar-task-items').innerHTML = record.workspaces.length ? record.workspaces.map(item => '<div class="article-research-task"><div><button class="article-text-button" data-focus="' + escape(item.block_id) + '">' + escape(item.block_id) + '</button><p>' + escape(item.observation.reason) + '</p><small>' + escape(item.study_id) + ' · ' + (item.observation.rounds ?? '—') + ' 轮推演 · ' + (item.observation.transfers ?? '—') + ' 个验证候选</small></div><a class="button secondary" target="_blank" rel="noopener noreferrer" href="' + escape(item.href) + '">进入研究 ↗</a></div>').join('') : '<p class="muted">尚未创建关联任务。选择一个数学义务，从详情中开始。</p>';
    const area = $('ar-area').value;
    $('ar-area').innerHTML = w.areas.map(value => '<option>' + escape(value) + '</option>').join('');
    $('ar-area').value = w.areas.includes(area) ? area : '实分析与复分析';
  }
  function detail(id, scroll = true) {
    if (selected !== id && dirty) {message('请先保存当前义务，或放弃未保存修改后再切换。', true); return;}
    const changed = selected !== id, block = record.blocks.find(item => item.id === id);
    if (!block) return;
    if (changed) {$('ar-bind').reset(); clearReturn(); $('ar-context').value = '';}
    selected = id; $('ar-detail').hidden = false;
    $('ar-detail-location').textContent = block.section + ' / ' + block.id;
    $('ar-detail-title').textContent = block.annotation ? names[block.annotation.classification] : kinds[block.kind];
    $('ar-quote').textContent = block.quote;
    if (!dirty) {
      $('ar-class').value = block.annotation?.classification || (block.kind === 'claim_candidate' ? 'claim' : block.kind === 'definition_candidate' ? 'definition' : 'author_step');
      $('ar-statement').value = block.annotation?.statement || block.quote.slice(0,8000);
      $('ar-assumptions').value = (block.annotation?.assumptions || []).join('\n');
      $('ar-deps').value = (block.annotation?.depends_on || []).join(', '); $('ar-note').value = block.annotation?.note || '';
    }
    $('ar-dirty-note').textContent = dirty ? '有未保存修改' : '';
    $('ar-evidence').innerHTML = '<p class="article-proof-line">此义务结论：<strong>' + escape(block.claim_verdict) + '</strong> · 作者推理核查：<strong>' + (block.author_proof_verdict === 'GAP_FOUND' ? '尚有验证缺口' : '尚未完成') + '</strong>' + (block.blocked_by.length ? '<br>尚缺依赖：' + escape(block.blocked_by.join(', ')) : '') + '</p>' + block.links.map(link => '<div class="article-proof-line"><strong>' + (link.relation === 'equivalent' ? '完整义务对应' : '反例特例证书') + '</strong> · ' + escape(link.observation.status) + '<br>' + escape(link.observation.reason) + '<br>' + escape(link.scope) + '<details><summary>完整被检查目标</summary><pre>' + escape(link.identity.formal_statement) + '</pre></details><small>复核人：' + escape(link.reviewer) + ' · 本地对应声明</small></div>').join('');
    $('ar-linked-tasks').innerHTML = record.workspaces.filter(item => item.block_id === id).map(item => '<div class="article-linked-task"><span class="article-tag ' + (item.observation.status === 'CURRENT' ? '' : 'gap') + '">' + escape(item.observation.status === 'CURRENT' ? '对应当前义务' : '关联已失效') + '</span><a target="_blank" rel="noopener noreferrer" href="' + escape(item.href) + '">打开研究任务 ↗</a><button type="button" class="button secondary" data-research-results="' + escape(item.workspace_id) + '" data-locked="' + (item.observation.status !== 'CURRENT') + '">查看可回收候选</button></div>').join('');
    renderBlocks(); controls();
    if (scroll) $('ar-detail').scrollIntoView({behavior:'smooth', block:'start'});
  }
  function download(text, extension, type) {
    const url = URL.createObjectURL(new Blob([text], {type})), anchor = document.createElement('a');
    anchor.href = url; anchor.download = record.article_id + extension; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $('ar-import-toggle').addEventListener('click', () => {$('ar-import').hidden = !$('ar-import').hidden;});
  $('ar-import').addEventListener('submit', event => {event.preventDefault(); void action(async () => {
    requireSaved(); const file = $('ar-file').files[0]; if (!file || file.size > 1048576) throw new Error('请选择最多 1 MiB 的 HTML 文件');
    record = await api('', {request_id:uid(), title:$('ar-title').value, url:$('ar-url').value, html:await file.text()});
    articleEpoch++; resetRecheck();
    selected = null; section = ''; checked.clear(); clearReturn(); $('ar-detail').hidden = true; $('ar-bind').reset();
    $('ar-search').value = ''; $('ar-filter').value = 'all'; history.replaceState(null, '', '#' + record.article_id);
    $('ar-import').hidden = true; render(); await list(); message('原文索引已保存，尚需梳理推理义务。');
  });});
  $('ar-articles').addEventListener('change', () => {if ($('ar-articles').value) void action(() => open($('ar-articles').value));});
  $('ar-reload').addEventListener('click', () => void action(async () => {if (record) await open(record.article_id); else await list();}));
  $('ar-recheck').addEventListener('click', () => void action(async () => {
    requireSaved(); clearPreview(); forgetEvidence(); render();
    recheckReadEpoch++; clearTimeout(recheckTimer);
    const job = await api('/' + record.article_id + '/rechecks', mutation({}));
    recheckReadEpoch++;
    recheckJob = job; recheckJobs = [job, ...recheckJobs.filter(item => item.job_id !== job.job_id)];
    recheckHistory = null; recheckReadRevision = record.revision; recheckReadError = '';
    renderRecheck(); scheduleRecheck(); message('后台复核已登记。可查看逐图进度或取消；结果只记录各图读取时的历史状态。');
  }));
  $('ar-recheck-job')?.addEventListener('change', event => {
    if (event.target.id !== 'ar-recheck-select') return;
    recheckReadEpoch++; clearTimeout(recheckTimer); recheckHistory = null;
    recheckJob = recheckJobs.find(item => item.job_id === event.target.value) || null;
    renderRecheck();
    if (recheckJob) void action(() => readRecheck(recheckJob.job_id));
  });
  $('ar-recheck-job')?.addEventListener('click', event => {
    const button = event.target.closest('#ar-recheck-refresh,#ar-recheck-cancel');
    if (!button || !recheckJob) return;
    void action(async () => {
      if (button.id === 'ar-recheck-refresh') {await readRecheck(recheckJob.job_id); return;}
      const key = record.article_id, jobId = recheckJob.job_id;
      recheckReadEpoch++; clearTimeout(recheckTimer);
      try {
        const value = await api('/' + key + '/rechecks/' + encodeURIComponent(jobId) + '/cancel', {});
        recheckReadEpoch++;
        recheckJob = value; recheckJobs = recheckJobs.map(item => item.job_id === jobId ? value : item);
        renderRecheck();
        message('取消请求已登记；正在进行的图读取结束后停止。已有历史观察不会成为当前有效授权。');
      } finally {scheduleRecheck();}
    });
  });
  $('ar-sections').addEventListener('click', event => {const button = event.target.closest('[data-section]'); if (button) {section = button.dataset.section; render();}});
  $('ar-search').addEventListener('input', renderBlocks); $('ar-filter').addEventListener('change', renderBlocks);
  $('ar-blocks').addEventListener('click', event => {const button = event.target.closest('[data-block]'); if (button) detail(button.dataset.block);});
  $('ar-blocks').addEventListener('change', event => {const id = event.target.dataset.select; if (!id) return; if (event.target.checked) {if (checked.size >= 32) {event.target.checked = false; message('每批最多整理 32 处。', true); return;} checked.add(id);} else checked.delete(id); $('ar-selected-count').textContent = '已选 ' + checked.size + ' 处';});
  $('ar-clear-selection').addEventListener('click', () => {checked.clear(); renderBlocks();});
  $('ar-batch').addEventListener('submit', event => {event.preventDefault(); void action(async () => {
    requireSaved(); if (!checked.size) throw new Error('请先勾选已经阅读的未分类内容。');
    const annotations = [...checked].map(id => {const block = record.blocks.find(b => b.id === id); if (block.annotation) throw new Error('选中内容已有修改，请刷新后重新选择。'); return {block_id:id, classification:$('ar-batch-class').value, statement:block.quote, assumptions:[], depends_on:[], note:$('ar-batch-note').value};});
    record = await api('/' + record.article_id + '/annotate-many', mutation({annotations})); checked.clear(); render(); await list(); message('已原子保存本批分类；未产生数学证明。');
  });});
  $('ar-flow').addEventListener('click', event => {
    const tab = event.target.closest('[data-view]'), go = event.target.closest('[data-go]'), focus = event.target.closest('[data-focus]');
    if (tab || go) setView((tab || go).dataset.view || go.dataset.go);
    if (focus) detail(focus.dataset.focus);
  });
  document.querySelector('.article-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    const values = ['source','map','frontier','tasks']; let index = values.indexOf(view);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? 3 : (index + (event.key === 'ArrowRight' ? 1 : -1) + 4) % 4;
    event.preventDefault(); setView(values[index]); $('ar-tab-' + values[index]).focus();
  });
  $('ar-detail-close').addEventListener('click', () => {if (dirty) {message('请先保存或放弃未保存修改。', true); return;} $('ar-detail').hidden = true; selected = null; clearReturn(); renderBlocks();});
  $('ar-revert').addEventListener('click', () => {dirty = false; detail(selected, false); controls();});
  $('ar-annotate').addEventListener('input', () => {dirty = true; $('ar-confirm').checked = false; clearPreview(); $('ar-dirty-note').textContent = '有未保存修改'; controls();});
  $('ar-annotate').addEventListener('submit', event => {event.preventDefault(); void action(async () => {
    const value = await api('/' + record.article_id + '/annotate', mutation({block_id:selected, classification:$('ar-class').value, statement:$('ar-statement').value, assumptions:$('ar-assumptions').value.split('\n').map(s => s.trim()).filter(Boolean), depends_on:$('ar-deps').value.split(',').map(s => s.trim()).filter(Boolean), note:$('ar-note').value}));
    record = value; dirty = false; clearReturn(); render(); await list(); message('义务已保存；旧证据与研究任务将按当前版本重新核对。');
  });});
  $('ar-research').addEventListener('submit', event => {event.preventDefault(); void action(async () => {
    requireSaved(); clearReturn(); record = await api('/' + record.article_id + '/research', mutation({block_id:selected, area:$('ar-area').value, context:$('ar-context').value}));
    render(); message('研究任务已创建。打开任务，保存推演与完整候选，再显式执行真实验证。');
  });});
  $('ar-linked-tasks').addEventListener('click', event => {const button = event.target.closest('[data-research-results]'); if (button) void action(async () => {
    requireSaved(); clearReturn();
    const value = await api('/' + record.article_id + '/research-tasks/' + button.dataset.researchResults);
    workspace = value.workspace_id;
    candidates = value.transfers.flatMap(task => (task.candidate_nodes || task.eligible_node_ids.map(node_id => ({node_id, relations:['equivalent']}))).map(node => ({...node, graph_id:task.graph_id, backend:task.backend})));
    if (!candidates.length) {message('该任务尚无当前计划实际交接的候选。请先进入研究任务，保存完整原题或明确子问题的候选并交给验证器。'); return;}
    $('ar-return-task').innerHTML = candidates.map((item,i) => '<option value="' + i + '">' + escape(item.backend + ' · ' + (item.research_node_id || item.node_id) + ' → ' + item.graph_id + ' / ' + item.node_id) + '</option>').join('');
    returnRelations(); $('ar-return').hidden = false; message('已读取实际交接的候选。子问题不会自动被视为反例；请明确关系，再核对现有证明及全部语义。');
  });});
  $('ar-return-task').addEventListener('change', returnRelations);
  $('ar-return-relation').addEventListener('change', clearPreview);
  $('ar-return-preview').addEventListener('click', () => void action(async () => {
    requireSaved(); clearPreview(); const target = candidates[Number($('ar-return-task').value)];
    if (!target || !workspace) throw new Error('请选择当前研究任务中的完整候选。');
    const relation = $('ar-return-relation').value;
    if (!relation || !target.relations.includes(relation)) throw new Error('请明确选择与该候选相符的原文关系。');
    preview = await api('/' + record.article_id + '/research-preview', {workspace_id:workspace, graph_id:target.graph_id, node_id:target.node_id, relation});
    const specialization = preview.relation === 'counterexample_specialization';
    $('ar-return-target').innerHTML = '<p class="article-proof-line">已读取这一个目标的既有 PROVED/E4。' + (specialization ? '所选关系：反例特例证书。原文义务与全文仍为 UNKNOWN；反例关系由本地具名审核人负责。' : '所选关系：对应完整义务。语义对应仍需本地具名核对。') + '</p><h4>完整原文义务</h4><p class="mra-math">' + escape(preview.original) + '</p><h4>原文的全部前提</h4><p class="mra-math">' + escape(preview.assumptions.join('\n') || '未单独列出；仍须核对完整原文。') + '</p><h4>这次实际检查的研究目标</h4><p class="mra-math">' + escape(preview.identity.original) + '</p>' + (preview.research_node?.additional_assumptions?.length ? '<h4>子问题的额外条件性限制</h4><p class="mra-math">' + escape(preview.research_node.additional_assumptions.join('\n')) + '</p>' : '') + (preview.study_source ? '<details><summary>完整研究定义与上下文</summary><p class="mra-math">' + escape(preview.study_source.definitions) + '</p><p class="mra-math">' + escape(preview.study_source.assumptions || '未另行列出条件') + '</p></details>' : '') + '<h4>完整形式化命题</h4><pre>' + escape(preview.identity.formal_statement) + '</pre><p class="muted">' + escape(preview.scope) + '<br>预览指纹：' + escape(preview.preview_fingerprint) + '</p>';
    $('ar-return-target').hidden = false; $('ar-return-confirmation').hidden = false;
  }));
  $('ar-return').addEventListener('submit', event => {event.preventDefault(); void action(async () => {
    requireSaved(); if (!preview) throw new Error('请先读取并核对当前完整证据。');
    record = await api('/' + record.article_id + '/research-bind', mutation({workspace_id:preview.workspace_id, graph_id:preview.graph_id, node_id:preview.node_id, relation:preview.relation, preview_fingerprint:preview.preview_fingerprint, reviewer:$('ar-return-reviewer').value, scope:$('ar-return-scope').value, confirmed:$('ar-return-confirm').checked}));
    clearReturn(); render(); message('证据已关联回原文。点击“复核关联证据”记录逐图历史观察；原文结论分别保留。');
  });});
  $('ar-bind').addEventListener('submit', event => {event.preventDefault(); void action(async () => {
    requireSaved(); record = await api('/' + record.article_id + '/bind', mutation({block_id:selected, backend:$('ar-backend').value, graph_id:$('ar-graph').value, node_id:$('ar-node').value, formal_statement:$('ar-formal').value, relation:$('ar-relation').value, scope:$('ar-bind-scope').value, reviewer:$('ar-reviewer').value, confirmed:$('ar-confirm').checked}));
    $('ar-confirm').checked = false; clearPreview(); render(); message('已保存确切证据关联。点击“复核关联证据”记录读取时的状态；结果仅为历史观察。');
  });});
  $('ar-export').addEventListener('click', () => {if (record) download(JSON.stringify(record,null,2), '.json', 'application/json');});
  $('ar-export-markdown').addEventListener('click', () => {if (record?.review_markdown) download(record.review_markdown, '.md', 'text/markdown;charset=utf-8');});
  window.addEventListener('pagehide', () => clearTimeout(recheckTimer));
  void action(async () => {await window.MRA.session; const articles = await list(); const key = location.hash.slice(1) || (articles.length === 1 ? articles[0].article_id : ''); if (key) await open(key);});
})();
