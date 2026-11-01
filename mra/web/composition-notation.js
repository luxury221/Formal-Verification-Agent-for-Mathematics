"use strict";
/* Bounded data notation, shared by the editor and round-trip tests. */
(() => {
  const symbols={add:'+',mul:'*',sub:'-',dvd:'∣',eq:'=',le:'≤',and:'∧',or:'∨',iff:'↔'};
  const termOps=new Set(['add','mul','sub','gcd']);
  function parse(text,want='prop'){
    if(typeof text!=='string'||text.length>8192)throw Error('公式最多 8192 字符。');
    const tokens=[];let pos=0;
    while(pos<text.length){
      if(/\s/.test(text[pos])){pos++;continue;}
      const m=/^(?:<->|<=|&&|\|\||[a-z][a-z0-9_]*|\d+|[()+*\-=≤∣|∧∨↔,])/.exec(text.slice(pos));
      if(!m)throw Error(`公式第 ${pos+1} 个字符无法识别。`);
      tokens.push(m[0]);pos+=m[0].length;
      if(tokens.length>1024)throw Error('公式过长。');
    }
    let cursor=0,nodes=0;
    const peek=()=>tokens[cursor],take=v=>{if(tokens[cursor++]!==v)throw Error(`此处需要 ${v}。`);};
    const node=(v,type)=>{if(++nodes>240)throw Error('公式超过 240 节点。');return {v,type};};
    const table={'+':[50,'add'],'-':[50,'sub'],'*':[60,'mul'],'|':[40,'dvd'],'∣':[40,'dvd'],'=':[40,'eq'],'<=':[40,'le'],'≤':[40,'le'],'&&':[30,'and'],'∧':[30,'and'],'||':[20,'or'],'∨':[20,'or'],'<->':[10,'iff'],'↔':[10,'iff']};
    function expr(min=0,depth=0){
      if(depth>14)throw Error('公式嵌套超过 14 层。');
      const t=tokens[cursor++];let left;
      if(t==='('){left=expr(0,depth+1);take(')');}
      else if((t==='gcd'||t==='coprime')&&peek()==='('){
        take('(');const a=expr(0,depth+1);take(',');const b=expr(0,depth+1);take(')');
        if(a.type!=='term'||b.type!=='term')throw Error('gcd / coprime 的参数须为自然数表达式。');
        left=node({[t]:[a.v,b.v]},t==='gcd'?'term':'prop');
      }else if(/^\d+$/.test(t||'')){
        const n=Number(t);if(!Number.isSafeInteger(n)||n>1000000)throw Error('自然数常量范围为 0–1000000。');
        left=node({nat:n},'term');
      }else if(/^[a-z][a-z0-9_]{0,19}$/.test(t||'')){left=node({var:t},'term');}
      else throw Error('请填写变量、自然数或带括号的表达式。');
      while(table[peek()]&&table[peek()][0]>=min){
        const [rank,op]=table[tokens[cursor++]];
        const right=expr(rank+1,depth+1),expected=['and','or','iff'].includes(op)?'prop':'term';
        if(left.type!==expected||right.type!==expected)throw Error('公式类型不符；多个比较请用 ∧ 连接。');
        left=node({[op]:[left.v,right.v]},termOps.has(op)?'term':'prop');
      }
      return left;
    }
    const result=expr();if(cursor!==tokens.length||result.type!==want)throw Error(want==='term'?'需要完整自然数表达式。':'需要完整命题；例如 a | b 或 coprime(a, b)。');
    return result.v;
  }
  function format(v,depth=0){
    if(depth>14||!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).length!==1)throw Error('无法无损显示此公式。');
    const [op,args]=Object.entries(v)[0],child=x=>format(x,depth+1);
    if(op==='var'&&typeof args==='string'&&/^[a-z][a-z0-9_]{0,19}$/.test(args))return args;
    if(op==='nat'&&Number.isSafeInteger(args)&&args>=0&&args<=1000000)return String(args);
    if(Array.isArray(args)&&args.length===2){
      if(['gcd','coprime'].includes(op))return `${op}(${child(args[0])}, ${child(args[1])})`;
      if(symbols[op])return `(${child(args[0])} ${symbols[op]} ${child(args[1])})`;
    }
    throw Error('此表达式超出受控 Nat 语言。');
  }
  const api={parse,format};
  if(typeof module!=='undefined')module.exports=api;else window.MRACompositionNotation=api;
})();
