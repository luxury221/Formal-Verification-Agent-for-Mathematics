"use strict";
/* Text notation compiles to data only; the server still validates every obligation. */
(() => {
  const symbols={add:'+',mul:'*',sub:'-',eq:'=',le:'≤',lt:'<',and:'∧',or:'∨',imp:'→'};
  const names=/^[a-z][a-z0-9_]{0,15}$/;
  const reserved=new Set(['by','in','do','if','then','else','let','fun','match','with','forall','exists']);
  function parse(text,domain='Nat'){
    if(!['Nat','Int'].includes(domain))throw new Error('请选择明确的自然数或整数数学域。');
    if(typeof text!=='string'||text.length>4096)throw new Error('公式最多 4096 个字符。');
    const tokens=[];let offset=0;
    while(offset<text.length){
      if(/\s/.test(text[offset])){offset++;continue;}
      const match=/^(?:forall\b|exists\b|[a-z][a-z0-9_]*|\d+|<=|>=|!=|&&|\|\||->|[()+*\-=<>≤≥≠∧∨→¬!∀∃,])/.exec(text.slice(offset));
      if(!match)throw new Error(`公式第 ${offset+1} 个字符无法识别；请使用帮助中列出的符号。`);
      tokens.push(match[0]);offset+=match[0].length;
      if(tokens.length>512)throw new Error('公式过长，请拆分为较小的步骤。');
    }
    let cursor=0,nodes=0;
    const peek=()=>tokens[cursor];
    const take=value=>{if(peek()!==value)throw new Error(`公式此处需要 ${value}。`);cursor++;};
    const node=(value,type)=>{if(++nodes>256)throw new Error('公式超过 256 个节点。');return {value,type};};
    const table={'+':[50,'add','term'],'-':[50,'sub','term'],'*':[60,'mul','term'],'=':[40,'eq','relation'],'<':[40,'lt','relation'],'<=':[40,'le','relation'],'≤':[40,'le','relation'],'>':[40,'lt','reverse'],'>=':[40,'le','reverse'],'≥':[40,'le','reverse'],'!=':[40,'eq','unequal'],'≠':[40,'eq','unequal'],'&&':[30,'and','logic'],'∧':[30,'and','logic'],'||':[20,'or','logic'],'∨':[20,'or','logic'],'->':[10,'imp','logic'],'→':[10,'imp','logic']};
    function expression(minimum=0,depth=0){
      if(depth>24)throw new Error('公式嵌套过深。');
      let left;const token=tokens[cursor++];
      if(token==='('){left=expression(0,depth+1);take(')');}
      else if(token==='-'&&domain==='Int'){
        if(/^\d+$/.test(peek()||'')){const value=-Number(tokens[cursor++]);if(!Number.isSafeInteger(value)||value < -1000000)throw new Error('整数常量须在 -1000000 到 1000000 之间。');left=node({op:'num',value},'term');}
        else {const body=expression(70,depth+1);if(body.type!=='term')throw new Error('整数取负需要算术表达式。');left=node({op:'neg',body:body.value},'term');}
      }
      else if(['!','¬'].includes(token)){const body=expression(35,depth+1);if(body.type!=='prop')throw new Error('否定符号后需要一个命题。');left=node({op:'not',body:body.value},'prop');}
      else if(['forall','exists','∀','∃'].includes(token)){const name=tokens[cursor++];if(!names.test(name||'')||reserved.has(name))throw new Error('量词变量须使用小写英文字母开头的名称。');take(',');const body=expression(0,depth+1);if(body.type!=='prop')throw new Error('量词后需要一个命题。');left=node({op:['forall','∀'].includes(token)?'forall':'exists',name,body:body.value},'prop');}
      else if(/^\d+$/.test(token||'')){const value=Number(token);if(!Number.isSafeInteger(value)||value>1000000)throw new Error('自然数常量须在 0 到 1000000 之间。');left=node({op:'num',value},'term');}
      else if(names.test(token||'')&&!reserved.has(token)){left=node({op:'var',name:token},'term');}
      else throw new Error('公式需要变量、自然数或括号；不支持负数、除法、幂及隐式乘法。');
      while(table[peek()]&&table[peek()][0]>=minimum){
        const [rank,op,kind]=table[tokens[cursor++]];
        const right=expression(op==='imp'?rank:rank+1,depth+1);
        const expected=kind==='logic'?'prop':'term';
        if(left.type!==expected||right.type!==expected)throw new Error('公式类型不符；连续比较请写成两个比较并用 ∧ 连接。');
        let value={op,left:kind==='reverse'?right.value:left.value,right:kind==='reverse'?left.value:right.value};
        if(kind==='unequal'){value=node(value,'prop').value;value={op:'not',body:value};}
        left=node(value,kind==='term'?'term':'prop');
      }
      return left;
    }
    const result=expression();
    if(cursor!==tokens.length)throw new Error(`公式还有未处理的符号 ${peek()}；乘法请显式写 *。`);
    if(result.type!=='prop')throw new Error('请填写包含 =、< 或 ≤ 等关系的完整命题。');
    return result.value;
  }
  function format(value,depth=0){
    if(depth>24||!value||typeof value!=='object')throw new Error('此公式无法在表单中表示，请使用 JSON 编辑。');
    const child=item=>format(item,depth+1),op=value.op;
    const fields=op==='num'?['op','value']:op==='var'?['op','name']:['not','neg'].includes(op)?['op','body']:['forall','exists'].includes(op)?['op','name','body']:['op','left','right'];
    if(Object.keys(value).sort().join()!==fields.sort().join())throw new Error('公式含额外或缺失字段，请使用 JSON 编辑。');
    if(op==='num')return String(value.value);
    if(op==='var')return value.name;
    if(op==='not')return `¬ (${child(value.body)})`;
    if(op==='neg')return `- (${child(value.body)})`;
    if(['forall','exists'].includes(op))return `(${op} ${value.name}, ${child(value.body)})`;
    if(symbols[op])return `(${child(value.left)} ${symbols[op]} ${child(value.right)})`;
    throw new Error('此公式操作尚不支持表单编辑。');
  }
  const same=(a,b)=>{
    if(a===b)return true;
    if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
    const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>Object.hasOwn(b,key)&&same(a[key],b[key]));
  };
  function attach(host,raw,onChange){
    const e=window.MRA.escape;
    let mode='form';
    host.innerHTML=`<div class="annotation-toolbar"><div class="annotation-modes" role="group" aria-label="标注编辑方式"><button type="button" id="annotation-form-mode" aria-pressed="true">表单编辑</button><button type="button" id="annotation-json-mode" aria-pressed="false">JSON 编辑</button></div><span id="annotation-status" role="status">编辑完成后，准备并核对检查义务</span></div><div id="annotation-form"><div class="annotation-section-heading"><span class="annotation-number">01</span><div><h3>原文与版本</h3><p>保留你的原文，再标注要检查的具体行。</p></div></div><div class="annotation-fields"><div class="wide"><label for="annotation-domain">本次数学域 · 类型与结论一起确认</label><select id="annotation-domain"><option value="Nat">自然数 ℕ · 截断减法</option><option value="Int">整数 ℤ · 允许负数与取负</option></select></div><div class="wide"><label for="annotation-title">审查标题</label><input id="annotation-title" maxlength="200" placeholder="例如：自然数加法的消去律"></div><div><label for="annotation-id">文档标识</label><input id="annotation-id" maxlength="128" spellcheck="false"></div><div><label for="annotation-version">原文版本</label><input id="annotation-version" type="number" min="1" max="1000000"></div></div><label for="annotation-source">定理与作者原证明 · 最多 12000 字符</label><textarea id="annotation-source" rows="6" maxlength="12000" placeholder="每个证明步骤可单独写一行，便于对应原文。"></textarea><div class="annotation-source-preview" id="annotation-source-preview" aria-label="带行号的原文预览"></div><div class="annotation-section-heading"><span class="annotation-number">02</span><div><h3>命题与明确前提</h3><p>变量均属于所选数学域。切换数学域后，请重新核对原文的对象范围。</p></div></div><div class="annotation-fields"><div><label for="annotation-variables">变量 · 逗号分隔，最多四个</label><input id="annotation-variables" placeholder="n, m" spellcheck="false"></div><div><label for="annotation-conclusion">结论</label><input id="annotation-conclusion" class="formula-input" maxlength="4096" placeholder="n + m = m + n" spellcheck="false"></div><div class="wide"><label for="annotation-assumptions">明确前提 · 每行一个，最多四个；无前提可留空</label><textarea id="annotation-assumptions" class="formula-input" rows="2" maxlength="16384" placeholder="0 < n\nn * m = n * k" spellcheck="false"></textarea></div></div><div id="annotation-claim-controls"></div><details class="annotation-help"><summary>公式怎么填写？</summary><p>可写 <code>n + 2 > n</code>、<code>n * m = n * k</code>、<code>(n = 0) ∨ (0 &lt; n)</code>。加减乘使用 <code>+ - *</code>；关系使用 <code>= != &lt; &lt;= &gt; &gt;=</code> 或 ≤ ≥ ≠；逻辑使用 ∧ ∨ → ¬ 或 <code>&amp;&amp; || -&gt; !</code>。量词示例：<code>exists k, n = k + 1</code>。</p><p>ℕ 使用截断减法，例如 0 - 1 = 0；ℤ 使用整数减法，支持 -1 与 -(n + 1)。切换域不会改写原文。乘法必须写 *；不支持除法、幂、隐式乘法或 LaTeX 命令。公式不会从原文自动抽取，仍需你逐项核对。</p></details><div class="annotation-section-heading"><span class="annotation-number">03</span><div><h3>作者证明步骤</h3><p>最多四步，只能引用此前标注步骤；无步骤时，原证明不会被判定已证明。</p></div><button type="button" id="annotation-add-step" class="button secondary">＋ 添加步骤</button></div><div id="annotation-steps"></div><p id="annotation-empty" class="annotation-empty">还没有添加作者步骤。可先检查命题本身，再补充原论证。</p></div><div id="annotation-json-panel" hidden><p class="hint">直接编辑完整结构化标注。切回表单时会检查能否无损转换，失败时保留原 JSON。</p></div><div id="annotation-validation" class="annotation-validation" role="status" hidden></div>`;
    host.querySelector('#annotation-json-panel').append(raw);
    const $=id=>host.querySelector('#'+id);
    const currentDomain=()=>$('annotation-domain').value;
    const list=text=>text.trim()?text.trim().split(/[\s,，]+/):[];
    const integer=(value,label,min,max)=>{if(!/^-?\d+$/.test(value)||!Number.isSafeInteger(+value)||+value<min||+value>max)throw new Error(`${label}须为 ${min} 到 ${max} 的整数。`);return +value;};
    function controls(key,plan,span){return `<div class="annotation-plan" data-plan="${e(key)}"><div><label for="${e(key)}-first">原文起始行</label><input id="${e(key)}-first" data-field="first" type="number" min="1" value="${e(span[0])}"></div><div><label for="${e(key)}-last">原文结束行</label><input id="${e(key)}-last" data-field="last" type="number" min="1" value="${e(span[1])}"></div><div><label for="${e(key)}-strategy">检查计划</label><select id="${e(key)}-strategy" data-field="strategy">${[['none','暂不检查（保留未知）'],['prove:omega','尝试证明 · omega'],['prove:simp','尝试证明 · simp'],['prove:decide','尝试证明 · decide'],['refute:omega','尝试反驳 · omega'],['refute:simp','尝试反驳 · simp'],['refute:decide','尝试反驳 · decide'],['refute:counterexample','核验显式反例']].map(([value,label])=>`<option value="${value}" ${value===(plan?plan.direction+':'+plan.strategy:'none')?'selected':''}>${label}</option>`).join('')}</select></div><div data-witness-box ${plan?.strategy==='counterexample'?'':'hidden'}><label for="${e(key)}-witnesses">反例取值 · 按变量顺序填写</label><input id="${e(key)}-witnesses" data-field="witnesses" value="${e(plan?.witnesses.join(', ')||'')}" placeholder="例如 0, 1, 0"></div></div>`;}
    function sourcePreview(){const lines=$('annotation-source').value.split(/\r\n|\n|\r/);$('annotation-source-preview').innerHTML=lines.map((line,i)=>`<div><span>${i+1}</span><p>${e(line)||' '}</p></div>`).join('');}
    function steps(){return [...$('annotation-steps').children];}
    function updateCount(){$('annotation-add-step').disabled=steps().length>=4;$('annotation-empty').hidden=steps().length>0;}
    function addStep(step){
      const key='annotation-step-'+crypto.randomUUID(),row=document.createElement('section');row.className='annotation-step';
      row.innerHTML=`<div class="annotation-step-heading"><strong>作者步骤 ${steps().length+1}</strong><button type="button" class="text-button" data-remove>移除此步骤</button></div><div class="annotation-fields"><div><label for="${key}-id">步骤标识</label><input id="${key}-id" data-field="id" value="${e(step.id)}" maxlength="32" spellcheck="false"></div><div><label for="${key}-dependencies">引用此前步骤 · 逗号分隔，无引用留空</label><input id="${key}-dependencies" data-field="dependencies" value="${e(step.depends_on.join(', '))}" placeholder="例如 s1" spellcheck="false"></div><div class="wide"><label for="${key}-formula">这一步声称的公式</label><input id="${key}-formula" data-field="formula" class="formula-input" value="${e(step.formula?format(step.formula):'')}" maxlength="4096" spellcheck="false" placeholder="例如 n + 1 > n"></div></div>${controls(key,step.plan,step.span)}`;
      row.querySelector('[data-remove]').addEventListener('click',()=>{row.remove();steps().forEach((item,i)=>item.querySelector('strong').textContent='作者步骤 '+(i+1));updateCount();changed();});
      $('annotation-steps').append(row);updateCount();
    }
    function planFor(box,lines,variables,label){
      const field=name=>box.querySelector('[data-field="'+name+'"]').value;
      const first=integer(field('first'),label+'起始行',1,lines),last=integer(field('last'),label+'结束行',first,lines);
      const choice=field('strategy');if(choice==='none')return {span:[first,last],plan:null};
      const [direction,strategy]=choice.split(':');
      const witnesses=strategy==='counterexample'?list(field('witnesses')).map(value=>integer(value,'反例取值',currentDomain()==='Int'?-1000000:0,1000000)):[];
      if(strategy==='counterexample'&&(!witnesses.length||witnesses.length>variables.length))throw new Error(label+'反例需要按变量顺序填写 1 到 '+variables.length+' 个取值。');
      return {span:[first,last],plan:{direction,strategy,witnesses}};
    }
    function collect(){
      const id=$('annotation-id').value,title=$('annotation-title').value,text=$('annotation-source').value;
      if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(id))throw new Error('文档标识须为 1–128 个英文字母、数字、下划线或连字符，且以字母或数字开头。');
      if(!title.trim()||!text.trim())throw new Error('请先填写审查标题和定理原文。');
      if(title.length>200||text.length>12000)throw new Error('标题或原文超过长度限制。');
      const variables=list($('annotation-variables').value);
      if(variables.length>4||new Set(variables).size!==variables.length||variables.some(name=>!names.test(name)||reserved.has(name)))throw new Error('请填写最多四个不重复的自然数变量；名称以小写英文字母开头。');
      const formulas=$('annotation-assumptions').value.split(/\r\n|\n|\r/).map(line=>line.trim()).filter(Boolean);
      if(formulas.length>4)throw new Error('明确前提最多四个，每行填写一个。');
      const readFormula=(text,label)=>{try{return parse(text,currentDomain());}catch(error){throw new Error(label+'：'+error.message);}};
      const assumptions=formulas.map((line,i)=>readFormula(line,'前提 '+(i+1))),conclusion=readFormula($('annotation-conclusion').value,'结论');
      const lines=text.split(/\r\n|\n|\r/);if(lines.at(-1)==='')lines.pop();
      const claim=planFor($('annotation-claim-controls'),lines.length,variables,'命题');const previous=new Set();
      const authored=steps().map((row,i)=>{
        const field=name=>row.querySelector('[data-field="'+name+'"]').value,key=field('id'),label='步骤 '+(i+1);
        if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/.test(key)||key==='claim'||previous.has(key))throw new Error(label+'标识无效或重复；不能使用 claim。');
        const dependencies=list(field('dependencies'));
        if(new Set(dependencies).size!==dependencies.length||dependencies.some(key=>!previous.has(key)))throw new Error(label+'只能引用此前存在的步骤；移除步骤后请明确修改其后续引用。');
        const data=planFor(row,lines.length,variables,label);previous.add(key);
        return {id:key,span:data.span,formula:readFormula(field('formula'),label),depends_on:dependencies,plan:data.plan};
      });
      const checkScope=(formula,scope)=>{if(formula.op==='var'&&!scope.includes(formula.name))throw new Error('公式中的变量 '+formula.name+' 尚未声明。');if(['forall','exists'].includes(formula.op)){if(scope.includes(formula.name)||scope.length>=8)throw new Error('量词变量重复或超出范围。');checkScope(formula.body,[...scope,formula.name]);}else for(const key of ['left','right','body'])if(formula[key])checkScope(formula[key],scope);};
      [...assumptions,conclusion,...authored.map(step=>step.formula)].forEach(formula=>checkScope(formula,variables));
      return {schema:currentDomain()==='Int'?'int-paper-review-v1':'nat-paper-review-v1',document:{id,version:integer($('annotation-version').value,'原文版本',1,1000000),title,text},variables,assumptions,conclusion,claim_span:claim.span,claim_plan:claim.plan,steps:authored};
    }
    function changed(){
      onChange();
      try{if(mode==='form')raw.value=JSON.stringify(collect(),null,2);$('annotation-validation').hidden=true;$('annotation-status').textContent='标注已修改 · 需要重新准备与确认';}
      catch(error){$('annotation-validation').textContent=error.message;$('annotation-validation').hidden=false;$('annotation-status').textContent='请补全标注';}
    }
    function setMode(next){mode=next;$('annotation-form').hidden=mode!=='form';$('annotation-json-panel').hidden=mode!=='json';for(const name of ['form','json'])$('annotation-'+name+'-mode').setAttribute('aria-pressed',String(name===mode));}
    function populate(bundle){
      if(!['nat-paper-review-v1','int-paper-review-v1'].includes(bundle.schema))throw new Error('标注版本不支持表单编辑。');
      $('annotation-domain').value=bundle.schema==='int-paper-review-v1'?'Int':'Nat';
      const formulaNodes=[...bundle.assumptions,bundle.conclusion,...bundle.steps.map(step=>step.formula)];
      formulaNodes.forEach(formula=>{if(!same(parse(format(formula),currentDomain()),formula))throw new Error('公式不能无损转换到表单。');});
      for(const [name,value] of Object.entries({title:bundle.document.title,id:bundle.document.id,version:bundle.document.version,source:bundle.document.text,variables:bundle.variables.join(', '),conclusion:format(bundle.conclusion),assumptions:bundle.assumptions.map(formula=>format(formula)).join('\n')}))$('annotation-'+name).value=value;
      $('annotation-claim-controls').innerHTML=controls('annotation-claim',bundle.claim_plan,bundle.claim_span);
      $('annotation-steps').replaceChildren();bundle.steps.forEach(addStep);updateCount();sourcePreview();
      if(!same(collect(),bundle))throw new Error('JSON 含额外字段或表单无法表示的内容；已保留原 JSON。');
    }
    function load(bundle){raw.value=JSON.stringify(bundle,null,2);try{populate(bundle);setMode('form');$('annotation-validation').hidden=true;$('annotation-status').textContent='已载入标注 · 尚未执行检查';}catch(error){setMode('json');$('annotation-validation').textContent=error.message;$('annotation-validation').hidden=false;}onChange();}
    $('annotation-form').addEventListener('input',event=>{if(event.target.id==='annotation-source')sourcePreview();if(event.target.dataset.field==='strategy')event.target.closest('.annotation-plan').querySelector('[data-witness-box]').hidden=event.target.value!=='refute:counterexample';changed();});
    $('annotation-form').addEventListener('change',event=>{if(event.target.tagName==='SELECT'){if(event.target.dataset.field==='strategy')event.target.closest('.annotation-plan').querySelector('[data-witness-box]').hidden=event.target.value!=='refute:counterexample';changed();}});
    raw.addEventListener('input',changed);
    $('annotation-add-step').addEventListener('click',()=>{if(steps().length>=4)return;const used=steps().map(row=>row.querySelector('[data-field="id"]').value);let index=1;while(used.includes('s'+index))index++;addStep({id:'s'+index,span:[1,1],formula:null,depends_on:[],plan:{direction:'prove',strategy:'omega',witnesses:[]}});changed();});
    $('annotation-json-mode').addEventListener('click',()=>{if(mode==='json')return;try{raw.value=JSON.stringify(collect(),null,2);setMode('json');}catch(error){$('annotation-validation').textContent=error.message+' 请补全表单后再切换，或重新载入示例。';$('annotation-validation').hidden=false;}});
    $('annotation-form-mode').addEventListener('click',()=>{if(mode==='form')return;try{populate(JSON.parse(raw.value));setMode('form');$('annotation-validation').hidden=true;}catch(error){$('annotation-validation').textContent='未切换：'+error.message;$('annotation-validation').hidden=false;}});
    return {load,read:()=>mode==='form'?collect():JSON.parse(raw.value),newDocument:(text='')=>{if(text.length>12000)throw new Error('草稿超过 12000 字符，请先选取一条定理片段。');load({schema:currentDomain()==='Int'?'int-paper-review-v1':'nat-paper-review-v1',document:{id:'document-'+crypto.randomUUID().replaceAll('-',''),version:1,title:'我的单定理审查',text},variables:['n'],assumptions:[],conclusion:{op:'eq',left:{op:'var',name:'n'},right:{op:'var',name:'n'}},claim_span:[1,1],claim_plan:{direction:'prove',strategy:'omega',witnesses:[]},steps:[]});setMode('form');$('annotation-conclusion').value='';changed();$('annotation-title').focus();}};
  }
  window.MRAPaperNotation={parse,format,same,attach};
})();
(() => {
  if (location.pathname !== '/papers') return;
  const {escape:e,icon,modal,notify,session} = window.MRA;
  const $=id=>document.getElementById(id);
  let prepared=null,current=null,examples=[],requestId=crypto.randomUUID(),polling=false,epoch=0,editor;
  const active=report=>report&&['QUEUED','RUNNING'].includes(report.state);
  const verdictLabel={PROVED:'已证明',REFUTED:'已反驳',GAP_FOUND:'发现具体缺口',UNKNOWN:'尚未确定'};
  const stateLabel={QUEUED:'等待检查',RUNNING:'真实检查中',COMPLETED:'本轮已结束',FAILED:'执行失败',CANCELLED:'已取消',REVOKED:'确认已撤销'};
  async function api(path,body){const info=await session;const response=await fetch(path,{cache:'no-store',...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MRA-CSRF':info.csrf_token},body:JSON.stringify(body)})});const data=await response.json();if(!response.ok)throw new Error(data.error||'审查请求暂时无法完成。');return data;}
  function fail(error){$('paper-review-error').textContent=error.message;$('paper-review-error').hidden=false;}
  async function run(action){$('paper-review-error').hidden=true;try{await action();}catch(error){fail(error);}}
  function invalidate(){++epoch;prepared=null;requestId=crypto.randomUUID();$('paper-reviewed').checked=false;$('paper-start').disabled=true;$('paper-plan-preview').hidden=true;}
  function bundle(){const value=editor.read();if(new TextEncoder().encode(JSON.stringify(value)).length>65536)throw new Error('标注内容最多 64 KiB。');return value;}
  function renderPlan(plan){
    $('paper-plan-preview').hidden=false;
    $('paper-source-title').textContent=plan.document.title+' · '+plan.domain+' · v'+plan.document.version;
    $('paper-source-lines').replaceChildren(...plan.document.text.split(/\r\n|\n|\r/).map((line,i)=>{const row=document.createElement('div');row.className='review-source-line';row.dataset.line=i+1;const number=document.createElement('span');number.textContent=String(i+1).padStart(2,'0');const text=document.createElement('p');text.textContent=line;row.append(number,text);return row;}));
    $('paper-obligations').innerHTML=plan.obligations.map(item=>`<button type="button" class="paper-obligation" data-obligation="${e(item.key)}"><span>${item.key==='claim'?'命题检查':e(item.key)} · 原文 ${item.span[0]}–${item.span[1]} 行</span><strong>${e(item.quote)}</strong><code>${e(item.formal_statement)}</code><small>引用步骤：${e(item.dependencies.join('、')||'无')} · ${item.plan?e(item.plan.direction+' / '+item.plan.strategy):'未提供检查计划'}</small></button>`).join('');
    $('paper-obligations').querySelectorAll('[data-obligation]').forEach(button=>button.addEventListener('click',()=>{const item=plan.obligations.find(item=>item.key===button.dataset.obligation);$('paper-source-lines').querySelectorAll('[data-line]').forEach(line=>line.classList.toggle('highlight',+line.dataset.line>=item.span[0]&&+line.dataset.line<=item.span[1]));$('paper-obligations').querySelectorAll('button').forEach(item=>item.classList.toggle('selected',item===button));}));
    $('paper-plan-budget').textContent=`本轮最多 ${plan.budget.maximum_checks} 次真实验证，每次候选与独立重放共享 30 秒；环境核对另计。模型调用 0。`;
    $('paper-plan-fingerprint').textContent=plan.plan_fingerprint;
  }
  function renderReport(report){
    current=report;const box=$('paper-review-result');box.hidden=false;
    const usable=report.results_available;
    const results=Object.fromEntries(report.checks.map(item=>[item.key,item]));
    box.innerHTML=`<div class="panel-heading"><div><div class="eyebrow">CLAIM & AUTHOR PROOF · SEPARATE VERDICTS</div><h2>${e(report.bundle.document.title)}</h2></div><span class="draft-badge">${e(stateLabel[report.state]||report.state)}</span></div><p class="task-id">${e(report.review_id)} · 原文版本 ${report.bundle.document.version}</p><div class="paper-verdicts"><div><span>命题本身</span><strong>${e(verdictLabel[report.claim_verdict])}</strong><code>${e(report.claim_verdict)}</code></div><div><span>作者标注的证明步骤</span><strong>${e(verdictLabel[report.author_proof_verdict])}</strong><code>${e(report.author_proof_verdict)}</code></div></div><p class="inline-note">${report.artifact_status==='CHANGED_OR_MISSING'?'工件或审查协议已变化，当前结论已停用。':report.revoked?'原文与标注的确认已撤销，结论已停用。':active(report)?'正在逐项完成真实 Lean 检查，完成后发布双通道结果。':usable?'历史工件摘要一致；结论只适用于具名确认的这些原文与标注。':'当前没有可发布的审查结论。'}${report.error?' · '+e(report.error):''}</p><div class="paper-check-list">${report.plan.obligations.map(item=>{const check=results[item.key];return `<div><div><strong>${item.key==='claim'?'命题检查':e(item.key)}</strong><small>原文 ${item.span[0]}–${item.span[1]} 行 · ${e(check?.result.run_status||'未执行或依赖尚未验证')}${usable&&check?.result.math_verdict==='REFUTED'&&item.plan?.strategy==='counterexample'?' · 核验反例输入：'+e(item.plan.witnesses.join('、')):''}</small></div><span>${usable?e(verdictLabel[check?.result.math_verdict]||'未闭合'):'待发布'}</span>${usable&&check?`<button class="text-button" data-paper-evidence="${e(item.key)}">检查工件 ${icon('chevron')}</button>`:''}</div>`;}).join('')}</div>${report.diagnostics.map(item=>`<div class="paper-diagnostic ${item.confidence==='INCONCLUSIVE'?'inconclusive':''}"><strong>${e(item.key)} · ${e(item.kind)} · 原文 ${item.span[0]}–${item.span[1]} 行</strong><p>${e(item.message)}</p>${item.formal_statement?`<code>${e(item.formal_statement)}</code>`:''}</div>`).join('')}<div class="dialog-actions"><button class="button secondary" id="paper-cancel" ${active(report)?'':'disabled'}>取消检查</button><button class="button secondary" id="paper-revoke" ${report.revoked?'disabled':''}>撤销本次确认</button><button class="button secondary" id="paper-export">导出审查记录</button>${usable&&report.checks.some(check=>check.result.math_verdict==='PROVED'&&check.result.evidence_level==='E4_ALIGNED_AND_VERIFIED')?`<a class="button primary" href="/library#source-${e(report.review_id)}">存入私有知识库 ${icon('arrow')}</a>`:''}</div><details class="paper-event-log"><summary>执行过程 · ${e(report.stage)}</summary><ol>${report.events.map(item=>`<li>${e(item.at)} · ${e(item.kind)} ${e(item.key||'')}</li>`).join('')}</ol></details><p class="hint">命题的独立证明不会替代原证明的步骤检查。未闭合义务不代表作者数学错误；此版本仅审查人工标注的自然数或整数单定理。</p>`;
    $('paper-cancel').addEventListener('click',()=>run(async()=>{renderReport(await api('/v1/paper-reviews/'+current.review_id+'/cancel',{}));await history();}));
    $('paper-revoke').addEventListener('click',()=>run(async()=>{renderReport(await api('/v1/paper-reviews/'+current.review_id+'/revoke',{}));await history();}));
    $('paper-export').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)+'\n'],{type:'application/json'}));const anchor=document.createElement('a');anchor.href=url;anchor.download=current.review_id+'.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    box.querySelectorAll('[data-paper-evidence]').forEach(button=>button.addEventListener('click',()=>run(async()=>{const id=current.review_id,key=button.dataset.paperEvidence;const evidence=await api('/v1/paper-reviews/'+id+'/evidence/'+encodeURIComponent(key));if(current.review_id!==id)return;modal('单项真实检查工件',`<p class="hint">${evidence.available?'历史回执与报告，非当前环境重新认证。':'工件当前不可用，请重新读取审查记录。'}</p><pre class="command-box">${e(JSON.stringify(evidence,null,2))}</pre>`);}))); 
    $('paper-start').disabled=!prepared||!$('paper-reviewed').checked||active(report);
  }
  async function history(){const data=await api('/v1/paper-reviews');$('paper-review-history').innerHTML=data.reviews.length?data.reviews.map(item=>`<button type="button" class="paper-history-item" data-review="${e(item.review_id)}"><strong>${e(item.title)}</strong><small>v${item.version} · ${e(stateLabel[item.state]||item.state)}</small></button>`).join(''):'<p class="muted">还没有真实审查记录。可先准备一个作者示例，查看检查义务。</p>';$('paper-review-history').querySelectorAll('[data-review]').forEach(button=>button.addEventListener('click',()=>run(()=>load(button.dataset.review))));}
  async function load(id){const generation=++epoch;const report=await api('/v1/paper-reviews/'+encodeURIComponent(id));if(generation!==epoch)return;invalidate();editor.load(report.bundle);renderPlan(report.plan);renderReport(report);window.history.replaceState(null,'','/papers#'+report.review_id);}
  async function init(){
    const section=document.createElement('section');section.className='paper-review-area';section.id='single-theorem-review';
    section.innerHTML=`<div class="section-title"><div><div class="eyebrow">SINGLE THEOREM REVIEW · REAL LEAN</div><h2>让命题与原证明，分别接受检查。</h2></div><span class="draft-badge">ℕ / ℤ · 人工标注</span></div><p class="muted">在表单中填写原文、前提与证明步骤，或从作者示例开始。准备后逐项核对，再启动真实 Lean 检查。不需要配置模型。</p><div id="paper-review-error" class="error" role="alert" hidden></div><section class="panel paper-review-controls"><label for="paper-review-example">从一个完整例子开始</label><div class="sample-row"><select id="paper-review-example" aria-label="单定理审查示例"></select><button class="button secondary" id="paper-load-example">载入标注</button><button class="button primary" id="paper-prepare">准备检查义务 ${icon('arrow')}</button></div><p class="hint">示例文本与标注由本项目作者编写，不是模型输出；载入或准备不会启动 Lean。</p><div class="annotation-actions"><button type="button" class="text-button" id="annotation-new">＋ 新建我的审查</button><button type="button" class="text-button" id="annotation-use-draft">使用上方文本草稿</button><button type="button" class="text-button" id="annotation-export">导出当前标注</button></div><div id="paper-annotation-editor" class="annotation-editor"></div><textarea id="paper-annotations" rows="16" maxlength="65536" spellcheck="false" aria-label="单定理结构化标注 JSON"></textarea></section><section class="panel" id="paper-plan-preview" hidden><div class="panel-heading"><h2 id="paper-source-title"></h2><span class="draft-badge">待逐项核对</span></div><div class="paper-plan-grid"><div class="paper-source-lines" id="paper-source-lines"></div><div id="paper-obligations"></div></div><p id="paper-plan-budget" class="hint"></p><details><summary>本次原文与标注指纹</summary><code id="paper-plan-fingerprint"></code></details><div class="paper-confirm"><label for="paper-reviewer">本地确认者姓名</label><input id="paper-reviewer" maxlength="200" placeholder="填写你的姓名"><label class="check-row"><input type="checkbox" id="paper-reviewed"><span>我已逐项核对原文、变量、全部前提、结论、步骤与引用，确认这些形式化义务忠实表达本次标注。姓名是本地声明。</span></label><button class="button primary" id="paper-start" disabled>确认并运行真实审查 ${icon('arrow')}</button></div></section><section class="panel" id="paper-review-result" hidden></section><section class="panel"><div class="panel-heading"><h2>近期单定理审查</h2><button class="text-button" id="paper-history-refresh">${icon('refresh')} 刷新</button></div><div id="paper-review-history" class="paper-review-history"></div></section>`;
    const footer=document.querySelector('.page-footer');footer.before(section);
    editor=window.MRAPaperNotation.attach($('paper-annotation-editor'),$('paper-annotations'),invalidate);
    $('annotation-new').addEventListener('click',()=>run(async()=>editor.newDocument()));
    $('annotation-use-draft').addEventListener('click',()=>run(async()=>{const draft=document.getElementById('paper-draft')?.value||sessionStorage.getItem('mra.paper.draft')||'';if(!draft.trim())throw new Error('请先在上方导入或编辑文本草稿。');editor.newDocument(draft);notify('原文已带入；请手工填写变量、前提、结论与步骤。');}));
    $('annotation-export').addEventListener('click',()=>run(async()=>{const value=bundle(),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'})),anchor=document.createElement('a');anchor.href=url;anchor.download=value.document.id+'-annotations.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
    $('paper-reviewed').addEventListener('change',()=>{$('paper-start').disabled=!prepared||!$('paper-reviewed').checked||active(current);});
    $('paper-load-example').addEventListener('click',()=>{invalidate();editor.load(examples[Number($('paper-review-example').value)].bundle);notify('已载入作者标注，尚未执行检查。');});
    $('paper-prepare').addEventListener('click',()=>run(async()=>{$('paper-prepare').disabled=true;const generation=++epoch;try{const input=bundle();const plan=await api('/v1/paper-reviews/prepare',{bundle:input});if(generation!==epoch||JSON.stringify(input)!==JSON.stringify(bundle()))return;prepared=plan;requestId=crypto.randomUUID();$('paper-reviewed').checked=false;$('paper-start').disabled=true;renderPlan(plan);$('paper-plan-preview').scrollIntoView({block:'start',behavior:'smooth'});}finally{$('paper-prepare').disabled=false;}}));
    $('paper-start').addEventListener('click',()=>run(async()=>{if(!prepared||!$('paper-reviewed').checked)throw new Error('请先准备并逐项确认本次标注。');const reviewer=$('paper-reviewer').value.trim();if(!reviewer)throw new Error('请填写本地确认者姓名。');$('paper-start').disabled=true;try{const report=await api('/v1/paper-reviews',{request_id:requestId,bundle:bundle(),plan_fingerprint:prepared.plan_fingerprint,reviewer,confirmed:true});++epoch;renderReport(report);window.history.replaceState(null,'','/papers#'+report.review_id);await history();$('paper-review-result').scrollIntoView({block:'start',behavior:'smooth'});}finally{$('paper-start').disabled=active(current)||!prepared||!$('paper-reviewed').checked;}}));
    $('paper-history-refresh').addEventListener('click',()=>run(history));
    const catalogs=await Promise.all([api('/v1/paper-examples'),api('/v1/paper-examples/integers')]);examples=catalogs.flatMap(data=>data.examples);$('paper-review-example').replaceChildren(...examples.map((item,i)=>new Option((item.bundle.schema==='int-paper-review-v1'?'ℤ · ':'ℕ · ')+item.bundle.document.title,i)));editor.load(examples[1].bundle);$('paper-review-example').value='1';await history();if(location.hash.startsWith('#paper-'))await load(location.hash.slice(1));
    if(location.hash==='#research-import'){
      const saved=sessionStorage.getItem('mra.research.annotation');
      if(saved){
        if(saved.length>100000)throw new Error('研究节点导入超出预算。');
        const imported=JSON.parse(saved);invalidate();editor.load(imported.bundle);
        if(/^\/research#research-[a-f0-9]{32}$/.test(imported.return_href)){
          const link=document.createElement('a');link.className='text-button';link.href=imported.return_href;link.textContent='返回研究项目并绑定检查结果';
          document.querySelector('.annotation-actions').append(link);
        }
        sessionStorage.removeItem('mra.research.annotation');
        section.scrollIntoView({block:'start'});notify('已带入研究节点的完整命题；请准备并逐项确认后运行检查。');
      }
    }
    window.addEventListener('hashchange',()=>{if(location.hash.startsWith('#paper-'))run(()=>load(location.hash.slice(1)));});
    setInterval(()=>{if(polling||!active(current)||document.hidden)return;polling=true;const id=current.review_id,generation=epoch;run(async()=>{const report=await api('/v1/paper-reviews/'+id);if(generation===epoch&&current?.review_id===id){renderReport(report);if(!active(report))await history();}}).finally(()=>polling=false);},1200);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>init().catch(error=>notify(error.message)),{once:true});else init().catch(error=>notify(error.message));
})();
