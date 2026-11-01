"use strict";
// Network scheduling fixtures only; no mathematical evidence is simulated.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('mra/web/typed-transport.js','utf8');
function transport(fetch,clock=setTimeout){const window={MRA:{session:Promise.resolve({csrf_token:'unit'})}};vm.runInNewContext(source,{window,fetch,setTimeout:clock});return window.MRATypedTransport;}
const response=(value,status=200)=>({ok:status<400,status,json:async()=>value});
const busy=()=>response({code:'WORKER_BUSY',retryable_read:true,error:'busy'},503);
const instant=fn=>{queueMicrotask(fn);};
async function main(){
 let release,start,bodyDone=false;const calls=[],started=new Promise(resolve=>start=resolve);
 const t=transport(async(path,options)=>{calls.push([path,options]);if(path==='/first')return {ok:true,status:200,json:()=>new Promise(r=>{release=()=>{bodyDone=true;r({ok:1});};start();})};assert(bodyDone);return response({ok:2});});
 const first=t.request('/first'),body={nested:{value:1}},second=t.request('/second',body);body.nested.value=9;
 await started;assert.equal(calls.length,1);release();await first;await second;
 assert.equal(JSON.parse(calls[1][1].body).nested.value,1);assert.equal(calls[1][1].headers['X-MRA-CSRF'],'unit');
 let reads=0,writes=0;const retry=transport(async(path)=>{if(path==='/read'){reads++;return response({error:'busy'},reads<3?503:200);}writes++;return busy();},instant);
 await retry.request('/read');assert.equal(reads,3);await assert.rejects(retry.request('/write',{}));assert.equal(writes,1);
 let invalid=0;const malformed=transport(async()=>{invalid++;return {ok:true,json:async()=>{throw Error('invalid JSON');}};});await assert.rejects(malformed.request('/bad'));assert.equal(invalid,1);
 let unblock,holdStarted;const held=new Promise(resolve=>holdStarted=resolve),bounded=transport(async(path)=>path==='/hold'?{ok:true,json:()=>new Promise(r=>{unblock=()=>r({});holdStarted();})}:response({}));
 const pending=[bounded.request('/hold')];for(let i=0;i<32;i++)pending.push(bounded.request('/queued/'+i));await held;await assert.rejects(bounded.request('/overflow'));unblock();await Promise.all(pending);
 assert.equal(bounded.snapshot().pending,0);
 let n=0;const delays=[],notices=[];const recover=transport(async()=>++n<6?busy():response({current:'E4 requires actual server audit'}),(fn,ms)=>{delays.push(ms);instant(fn);});
 await recover.request('/report',undefined,{onBusy:v=>notices.push(v)});assert.equal(n,6);assert.deepEqual(delays,[250,750,1500,3000,5000]);assert.equal(notices.length,5);assert.equal(recover.snapshot().busy_retries,5);
 let total=0;const endDelays=[];const exhausted=transport(async()=>{total++;return busy();},(fn,ms)=>{endDelays.push(ms);instant(fn);});await assert.rejects(exhausted.request('/report'),/自动重读已结束/);assert.equal(total,8);assert.equal(endDelays.reduce((a,b)=>a+b,0),20500);
 let ordinary=0;const nonbusy=transport(async()=>{ordinary++;return response({error:'storage fault'},503);},instant);await assert.rejects(nonbusy.request('/report'));assert.equal(ordinary,3);assert.equal(nonbusy.snapshot().busy_retries,0);
 for(const status of [400,403,404,500]){let count=0;const failures=transport(async()=>{count++;return response({error:'unavailable'},status);},instant);await assert.rejects(failures.request('/report'));assert.equal(count,1);}
 let active=true,fetches=0;const cancelled=transport(async()=>{fetches++;return busy();},fn=>{active=false;instant(fn);});await assert.rejects(cancelled.request('/report',undefined,{isCurrent:()=>active}),e=>e.name==='AbortError');assert.equal(fetches,1);assert.equal(cancelled.snapshot().discarded_reads,1);
 let finish,entered;const begun=new Promise(r=>entered=r);active=true;fetches=0;const staleResult=transport(async()=>{fetches++;entered();return new Promise(r=>finish=()=>r(response({old:true})));});const oldRead=staleResult.request('/old',undefined,{isCurrent:()=>active});await begun;active=false;finish();await assert.rejects(oldRead,e=>e.name==='AbortError');assert.equal(fetches,1);
 let wake,admitted;const head=new Promise(r=>admitted=r),order=[];const queued=transport(async p=>{order.push(p);if(p==='/head')return new Promise(r=>{wake=()=>r(response({}));admitted();});return response({});});const a=queued.request('/head'),gone=queued.request('/gone',undefined,{isCurrent:()=>false}),goneRejection=assert.rejects(gone,e=>e.name==='AbortError'),write=queued.request('/confirmed',{}, {isCurrent:()=>false});await head;wake();await Promise.all([a,goneRejection,write]);assert.deepEqual(order,['/head','/confirmed']);
 let network=0;const offline=transport(async()=>{network++;throw Error('offline');},instant);await assert.rejects(offline.request('/report'));assert.equal(network,1);
 console.log('Typed transport: 12 scheduling and recovery contracts passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
