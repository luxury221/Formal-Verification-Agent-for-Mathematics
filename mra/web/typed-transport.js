"use strict";
(() => {
  const queue = [], legacyDelays = [250, 750], busyDelays = [250, 750, 1500, 3000, 5000, 5000, 5000];
  let running = false;
  const stats = {requests:0, read_retries:0, busy_retries:0, discarded_reads:0};
  function current(job) {return job.encoded !== undefined || !job.isCurrent || job.isCurrent();}
  function stale() {const error=new Error('读取语境已变化，旧请求已丢弃。');error.name='AbortError';return error;}
  async function drain() {
    if (running) return;
    running = true;
    try {
      while (queue.length) {
        const job = queue.shift();
        try {
          let value;
          for (let attempt=0; ; attempt++) {
            if(!current(job))throw stale();
            try {
              const options = {cache:'no-store'};
              if (job.encoded !== undefined) Object.assign(options, {method:'POST', headers:{'Content-Type':'application/json','X-MRA-CSRF':(await window.MRA.session).csrf_token}, body:job.encoded});
              const response = await fetch(job.path, options);
              value = await response.json();
              if(!current(job))throw stale();
              if (!response.ok) {const error=new Error(value.error||'请求失败');error.status=response.status;error.busy=value.code==='WORKER_BUSY'&&value.retryable_read===true;throw error;}
              break;
            } catch (error) {
              if(!current(job))throw stale();
              if(job.encoded!==undefined||error.status!==503)throw error;
              const delays=error.busy?busyDelays:legacyDelays;
              if(attempt>=delays.length) {
                if(error.busy)error.message='证据仍在核对，本次自动重读已结束。请稍后手动刷新；数学检查不会重新提交。';
                throw error;
              }
              stats.read_retries++;if(error.busy)stats.busy_retries++;
              if(error.busy&&job.onBusy)job.onBusy({retry:attempt+1,delay_ms:delays[attempt]});
              await new Promise(resolve=>setTimeout(resolve,delays[attempt]));
            }
          }
          job.resolve(value);
        } catch (error) {if(error.name==='AbortError')stats.discarded_reads++;job.reject(error);}
      }
    } finally {running=false;}
  }
  window.MRATypedTransport = {
    request(path, body, {isCurrent,onBusy}={}) {
      const encoded = body === undefined ? undefined : JSON.stringify(body);
      return new Promise((resolve,reject)=>{
        if (queue.length >= 32) {reject(new Error('请求较多，请稍后重试。'));return;}
        stats.requests++;
        queue.push({path,encoded,isCurrent,onBusy,resolve,reject});void drain();
      });
    },
    snapshot:()=>({...stats,pending:queue.length,running}),
  };
})();
