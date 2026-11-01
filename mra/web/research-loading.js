"use strict";
(() => {
  if (location.pathname !== "/research") return;
  const pending = [], failures = new Set(), delays = [250, 750], maximumPending = 32;
  const stats = {accepted: 0, completed: 0, failed: 0, discarded: 0, read_retries: 0};
  let context = 0, running = false;

  function obsolete(job) { return job.read && job.context !== context; }
  function superseded() {
    const error = new Error("研究语境已切换");
    error.name = "AbortError";
    return error;
  }
  function status(kind, message) {
    const heading = document.querySelector("#rf-panel .panel-heading");
    if (!heading) return;
    let box = document.getElementById("research-read-status");
    if (!box) {
      box = document.createElement("p");
      box.id = "research-read-status";
      box.className = "research-read-status hint";
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      box.setAttribute("aria-atomic", "true");
      heading.after(box);
    }
    box.dataset.state = kind;
    box.textContent = message;
  }
  function reset() {
    context++;
    failures.clear();
    for (let i = pending.length - 1; i >= 0; i--) {
      if (obsolete(pending[i])) {
        stats.discarded++;
        pending.splice(i, 1)[0].reject(superseded());
      }
    }
  }
  async function drain() {
    if (running) return;
    running = true;
    try {
      while (pending.length) {
        const job = pending.shift();
        try {
          let value;
          for (let attempt = 0; ; attempt++) {
            if (obsolete(job)) throw superseded();
            if (job.context === context) status("loading", job.read ? "正在读取研究来源…" : "正在处理当前操作…");
            try {
              value = await job.work();
              break;
            } catch (error) {
              if (obsolete(job)) throw superseded();
              if (!job.read || error.status !== 503 || attempt >= delays.length) throw error;
              stats.read_retries++;
              status("retrying", `来源暂时忙碌，正在重新读取（${attempt + 1} / ${delays.length}）…`);
              await new Promise(resolve => setTimeout(resolve, delays[attempt]));
            }
          }
          if (obsolete(job)) throw superseded();
          stats.completed++;
          if (job.context === context) {
            if (job.read) failures.delete(job.key);
            status(failures.size ? "error" : "ready", failures.size ? "部分研究来源未载入，可在相应面板手动重读。" : "研究面板已更新");
          }
          job.resolve(value);
        } catch (error) {
          if (error.name === "AbortError") stats.discarded++;
          else {
            stats.failed++;
            if (job.context === context) {
              if (job.read) failures.add(job.key);
              status("error", job.read ? "读取暂不可用，可在面板中手动重读。" : "操作未确认完成，请核对记录后手动重试。");
            }
          }
          job.reject(error);
        }
      }
    } finally {
      running = false;
    }
  }
  function run(work, read, version, key) {
    return new Promise((resolve, reject) => {
      if (read && version !== context) { stats.discarded++; reject(superseded()); return; }
      if (pending.length >= maximumPending) { reject(new Error("研究面板请求较多，请稍后重试。")); return; }
      stats.accepted++;
      pending.push({work, read, context: version, key, resolve, reject});
      void drain();
    });
  }
  function json(url, body, session, fallback) {
    const version = context;
    const encoded = body === undefined ? undefined : JSON.stringify(body);
    return run(async () => {
      const options = {cache: "no-store"};
      if (body !== undefined) {
        const auth = await session;
        Object.assign(options, {method: "POST", headers: {"Content-Type": "application/json", "X-MRA-CSRF": auth.csrf_token}, body: encoded});
      }
      const response = await fetch(url, options);
      const value = await response.json();
      if (!response.ok) {
        const error = new Error(value.error || fallback);
        error.status = response.status;
        throw error;
      }
      return value;
    }, body === undefined, version, url);
  }
  window.MRAResearchTransport = {
    json,
    snapshot: () => ({...stats, context, pending: pending.length, running, unavailable_reads: failures.size}),
  };
  window.addEventListener("mra-research-render", reset);
  window.addEventListener("mra-flow-render", reset);
})();
