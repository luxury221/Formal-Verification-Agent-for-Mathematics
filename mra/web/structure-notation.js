"use strict";
(() => {
  // Render only protocol data as text. Callers escape it before inserting HTML.
  function expression(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return String(value);
    const [op,x] = Object.entries(value)[0] || [];
    if (['var','nat','int'].includes(op)) return String(x);
    if (op==='rat') return x[0]+'/'+x[1];
    if (op==='call') return x.name+'('+x.args.map(expression).join(', ')+')';
    if (op==='cast') return '('+expression(x.value)+' : '+x.to+')';
    if (['forall','exists'].includes(op)) return (op==='forall'?'∀ ':'∃ ')+x.binders.map(b=>b.name+' : '+b.type).join(', ')+', '+expression(x.body);
    if (op==='nat_fold') return 'fold('+expression(x.index)+'; '+expression(x.zero)+'; ('+x.step.index+', '+x.step.value+') ↦ '+expression(x.step.body)+')';
    if (['not','neg'].includes(op)) return (op==='not'?'¬ ':'−')+expression(x);
    const symbol={eq:'=',add:'+',sub:'−',mul:'×',div:'/',le:'≤',lt:'<',and:'∧',or:'∨',imp:'→',iff:'↔'}[op];
    return symbol?'('+x.map(expression).join(' '+symbol+' ')+')':JSON.stringify(value);
  }
  function proof(value) {
    if (value.method==='nat_induction') return '对 '+value.variable+' 作自然数归纳'+(value.generalize.length?' · 泛化 '+value.generalize.join(', '):'')+'；基础：'+proof(value.base)+'；归纳步：'+proof(value.step);
    if (value.method==='rewrite') return value.steps.map(s=>(s.reverse?'逆向 ':'')+(s.rule==='@ih'?'归纳假设':s.rule)).join(' → ')+'；结束 '+value.finish;
    return value.method;
  }
  window.MRAStructureNotation={expression,proof};
})();
