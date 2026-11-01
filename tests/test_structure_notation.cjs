const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ctx={window:{}};vm.runInNewContext(fs.readFileSync('mra/web/structure-notation.js','utf8'),ctx);const {expression:e,proof:p}=ctx.window.MRAStructureNotation;
assert.equal(e({forall:{binders:[{name:'x',type:'g'}],body:{eq:[{var:'x'},{call:{name:'map',args:[{var:'x'}]}}]}}}),'∀ x : g, (x = map(x))');
assert.equal(e({nat_fold:{index:{var:'n'},zero:{nat:0},step:{index:'k',value:'acc',body:{add:[{var:'acc'},{var:'k'}]}}}}),'fold(n; 0; (k, acc) ↦ (acc + k))');
assert.equal(e({cast:{to:'Rat',value:{int:-1}}}),'(-1 : Rat)');assert.equal(e({rat:[-1,2]}),'-1/2');
assert.equal(p({method:'nat_induction',variable:'n',generalize:['m'],base:{method:'rfl'},step:{method:'rewrite',steps:[{rule:'@ih',reverse:true}],finish:'rfl'}}),'对 n 作自然数归纳 · 泛化 m；基础：rfl；归纳步：逆向 归纳假设；结束 rfl');
console.log('Structure notation semantic checks passed');
