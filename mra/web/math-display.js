"use strict";
(() => {
  // This layer only reads explicitly marked plain text. It never changes stored
  // statements, form values, Lean targets, or the meaning of an evidence record.
  const records = new WeakMap(), selector = '.mra-math';
  const forbidden = /\\(?:href|url|includegraphics|html[a-z]*|class|cssId|style|def|gdef|edef|xdef|let|futurelet|newcommand|renewcommand|providecommand|newenvironment|renewenvironment|global|require|input|include|write|read|openout|closeout|catcode|csname|endcsname|expandafter|noexpand)(?![a-zA-Z])/i;
  const maxFormula = 8192, maxFormulas = 64, maxText = 131072;
  let sequence = 0;
  function escaped(text, index) {
    let slashes = 0;
    while (index > 0 && text[--index] === '\\') slashes++;
    return slashes % 2 === 1;
  }
  function split(text) {
    const parts = []; let start = 0, i = 0, count = 0;
    if (text.length > maxText) return [{text}];
    while (i < text.length && count < maxFormulas) {
      let open, close, display = false;
      if (!escaped(text, i)) {
        if (text.startsWith('$$', i)) {open = close = '$$'; display = true;}
        else if (text.startsWith('\\[', i)) {open = '\\['; close = '\\]'; display = true;}
        else if (text.startsWith('\\(', i)) {open = '\\('; close = '\\)';}
        else if (text[i] === '$' && !/\s/.test(text[i + 1] || ' ')) open = close = '$';
      }
      if (!open) {i++; continue;}
      let end = i + open.length;
      while (end < text.length) {
        if (text.startsWith(close, end) && !escaped(text, end) &&
            (close !== '$' || (!/\s/.test(text[end - 1]) && text[end + 1] !== '$'))) break;
        end++;
      }
      if (end === text.length) {i += open.length; continue;}
      if (i > start) parts.push({text: text.slice(start, i)});
      parts.push({text: text.slice(i, end + close.length), formula: text.slice(i + open.length, end), display});
      count++; start = i = end + close.length;
    }
    if (start < text.length) parts.push({text: text.slice(start)});
    return parts;
  }
  function render(element) {
    if (element.closest('pre,code,textarea,input,select,option,script,style,button,a,[data-mra-no-math]')) return;
    const previous = records.get(element);
    if (previous && ((previous.plain && element.textContent === previous.text) ||
        (element.childNodes.length === 3 && element.firstChild === previous.view && element.lastChild === previous.source))) return;
    // Markers belong on a complete, plain-text paragraph. Never reinterpret
    // existing rich HTML or nested controls as a paper's original source.
    if (element.children.length) return;
    const text = element.textContent, parts = split(text);
    if (!parts.some(part => part.formula !== undefined)) {delete element.dataset.mathFallback;records.set(element, {plain: true, text}); return;}
    const view = document.createElement('span'), source = document.createElement('span'), toggle = document.createElement('button');
    view.className = 'mra-math-view'; source.className = 'mra-math-source'; source.textContent = text; source.hidden = true;
    source.id = 'mra-math-source-' + ++sequence;
    toggle.type = 'button'; toggle.className = 'mra-math-toggle'; toggle.textContent = '查看原始文本 / TeX';
    toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-controls', source.id);
    let failed = false;
    for (const part of parts) {
      if (part.formula === undefined) {view.append(document.createTextNode(part.text)); continue;}
      const formula = document.createElement('span');
      formula.className = part.display ? 'mra-formula mra-formula-display' : 'mra-formula';
      try {
        if (!window.katex || !part.formula.trim() || part.formula.length > maxFormula || forbidden.test(part.formula)) throw new Error('source only');
        window.katex.render(part.formula, formula, {
          displayMode: part.display, output: 'htmlAndMathml', trust: false,
          throwOnError: true, strict: 'error', maxExpand: 100, maxSize: 20,
          macros: {}, globalGroup: false,
        });
      } catch (_) {
        failed = true; formula.className = 'mra-formula-fallback'; formula.textContent = part.text;
        formula.title = '公式按原文显示，可切换查看完整原始文本。';
      }
      view.append(formula);
    }
    if (failed) element.dataset.mathFallback = 'true'; else delete element.dataset.mathFallback;
    toggle.addEventListener('click', () => {
      source.hidden = !source.hidden; view.hidden = !source.hidden;
      toggle.textContent = source.hidden ? '查看原始文本 / TeX' : '显示公式排版';
      toggle.setAttribute('aria-expanded', String(!source.hidden));
    });
    records.set(element, {text, view, source}); element.replaceChildren(view, toggle, source);
  }
  function scan(root) {
    if (root.nodeType === Node.ELEMENT_NODE && root.matches(selector)) render(root);
    if (root.querySelectorAll) root.querySelectorAll(selector).forEach(render);
  }
  scan(document);
  new MutationObserver(changes => {
    const roots = new Set();
    for (const change of changes) {
      const parent = change.target.nodeType === Node.ELEMENT_NODE ? change.target : change.target.parentElement;
      const marked = parent?.closest(selector); if (marked) roots.add(marked);
      for (const node of change.addedNodes) if (node.nodeType === Node.ELEMENT_NODE) roots.add(node);
    }
    roots.forEach(scan);
  }).observe(document.body, {childList: true, subtree: true, characterData: true});
})();
