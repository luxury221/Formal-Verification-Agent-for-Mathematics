"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../mra/web/research-loading.js"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve}; };
const reply = (value = {}, status = 200) => ({ok: status < 400, status, json: async () => value});
function harness(fetch, manualTimers = false) {
  const listeners = new Map(), delays = [], timers = [], box = {dataset: {}, setAttribute() {}};
  const window = {addEventListener: (event, handler) => listeners.set(event, handler)};
  const document = {querySelector: () => ({after() {}}), getElementById: () => box};
  vm.runInNewContext(source, {window, document, location: {pathname: "/research"}, fetch,
    setTimeout: (fn, ms) => {delays.push(ms); if (manualTimers) timers.push(fn); else queueMicrotask(fn);}});
  const transport = window.MRAResearchTransport;
  return {transport, box, delays, timers, reset: () => listeners.get("mra-flow-render")(),
    read: url => transport.json(url, undefined, null, "unavailable"),
    write: (url, body) => transport.json(url, body, Promise.resolve({csrf_token: "local-token"}), "unavailable")};
}

test("all panels serialize through complete JSON bodies, not just response headers", async () => {
  const gate = deferred(), calls = [];
  const h = harness(async url => {calls.push(url); return url === "one" ? {ok: true, json: () => gate.promise} : reply({url});});
  const first = h.read("one"), second = h.read("two"), third = h.write("three", {confirmed: true});
  await tick(); assert.deepEqual(calls, ["one"]);
  gate.resolve({value: 1});
  assert.deepEqual(await first, {value: 1}); await Promise.all([second, third]);
  assert.deepEqual(calls, ["one", "two", "three"]); assert.equal(h.transport.snapshot().running, false);
});
test("503 reads recover after two bounded delays with no cached response", async () => {
  let count = 0;
  const h = harness(async () => ++count < 3 ? reply({error: "busy"}, 503) : reply({fresh: count}));
  assert.deepEqual(await h.read("source"), {fresh: 3});
  assert.deepEqual(h.delays, [250, 750]); assert.equal(h.transport.snapshot().read_retries, 2);
  assert.equal(h.box.dataset.state, "ready");
});
test("exhaustion leaves the failed panel visible while unrelated work continues", async () => {
  let fail = true, calls = 0;
  const h = harness(async url => {calls++; return fail && url === "bad" ? reply({error: "busy"}, 503) : reply({url});});
  await assert.rejects(h.read("bad"), error => error.status === 503);
  assert.equal(calls, 3); await h.read("other");
  assert.equal(h.transport.snapshot().unavailable_reads, 1); assert.equal(h.box.dataset.state, "error");
  fail = false; await h.read("bad");
  assert.equal(h.transport.snapshot().unavailable_reads, 0); assert.equal(h.box.dataset.state, "ready");
});
test("validation, network and malformed-body errors are never retried", async () => {
  for (const response of [() => reply({error: "invalid"}, 400), () => {throw new TypeError("offline");},
    () => ({ok: true, json: async () => {throw new SyntaxError("malformed");}})]) {
    let calls = 0;
    const h = harness(async () => {calls++; return calls === 1 ? response() : reply({ok: true});});
    await assert.rejects(h.read("bad")); assert.equal(calls, 1); assert.equal(h.delays.length, 0);
    assert.deepEqual(await h.read("good"), {ok: true});
  }
});
test("a failed confirmed POST is sent once and only an explicit retry sends it again", async () => {
  const calls = [];
  const h = harness(async (url, opts) => {calls.push({url, ...opts}); return reply({error: "busy"}, 503);});
  const body = {request_id: "stable-id", confirmed: true};
  await assert.rejects(h.write("submit", body)); assert.equal(calls.length, 1); assert.equal(h.delays.length, 0);
  await assert.rejects(h.write("submit", body)); assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], calls[1]); assert.equal(calls[0].headers["X-MRA-CSRF"], "local-token");
});
test("queued write captures the reviewed body before mutable UI data changes", async () => {
  const gate = deferred(), bodies = [];
  const h = harness(async (url, opts) => {if (url === "hold") await gate.promise; else bodies.push(opts.body); return reply();});
  const hold = h.read("hold"), body = {plan: {fingerprint: "reviewed"}}, write = h.write("save", body);
  body.plan.fingerprint = "edited"; gate.resolve(); await Promise.all([hold, write]);
  assert.deepEqual(JSON.parse(bodies[0]), {plan: {fingerprint: "reviewed"}});
});
test("switching projects drops unsent old reads but drains the sent response first", async () => {
  const gate = deferred(), calls = [];
  const h = harness(async url => {calls.push(url); if (url === "old-active") await gate.promise; return reply({url});});
  const active = assert.rejects(h.read("old-active"), error => error.name === "AbortError");
  const queued = assert.rejects(h.read("old-queued"), error => error.name === "AbortError");
  h.reset(); const current = h.read("current");
  await tick(); assert.deepEqual(calls, ["old-active"]);
  gate.resolve(); await Promise.all([active, queued, current]);
  assert.deepEqual(calls, ["old-active", "current"]); assert.equal(h.transport.snapshot().discarded, 2);
});
test("switching contexts during retry backoff suppresses the old retry", async () => {
  let calls = 0;
  const h = harness(async () => {calls++; return reply({error: "busy"}, 503);}, true);
  const old = assert.rejects(h.read("old"), error => error.name === "AbortError");
  await tick(); assert.equal(h.timers.length, 1); h.reset(); h.timers.shift()(); await old;
  assert.equal(calls, 1); assert.equal(h.transport.snapshot().running, false);
});
test("already accepted writes retain their exact target when navigation discards reads", async () => {
  const gate = deferred(), calls = [];
  const h = harness(async (url, opts) => {calls.push({url, method: opts.method || "GET"}); if (url === "hold") await gate.promise; return reply({url});});
  const hold = assert.rejects(h.read("hold"), error => error.name === "AbortError");
  const write = h.write("original-flow/submit", {request_id: "named-original"});
  h.reset(); gate.resolve(); await hold; await write;
  assert.deepEqual(calls, [{url: "hold", method: "GET"}, {url: "original-flow/submit", method: "POST"}]);
});
test("bounded queue rejects overflow and resumes after draining", async () => {
  const gate = deferred();
  const h = harness(async url => {if (url === "hold") await gate.promise; return reply({url});});
  const active = h.read("hold"), queued = Array.from({length: 32}, (_, i) => h.read("queued-" + i));
  await assert.rejects(h.read("overflow"), /请求较多/);
  gate.resolve(); await Promise.all([active, ...queued]);
  assert.deepEqual(await h.read("next"), {url: "next"});
});
test("identical reads remain independent fresh audits", async () => {
  let calls = 0;
  const h = harness(async (url, opts) => {assert.equal(opts.cache, "no-store"); return reply({revision: ++calls});});
  const values = await Promise.all([h.read("same"), h.read("same")]);
  assert.deepEqual(values, [{revision: 1}, {revision: 2}]);
});
test("loading coordinator does not run on unrelated pages", () => {
  const window = {};
  vm.runInNewContext(source, {window, location: {pathname: "/ideas"}});
  assert.equal(window.MRAResearchTransport, undefined);
});
