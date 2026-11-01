/* Frontend notation tests protect the exact AST sent for named alignment. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const context = {window: {}, location: {pathname: '/test'}};
vm.runInNewContext(fs.readFileSync(path.join(root, 'mra/web/papers.js'), 'utf8'), context);
const {parse, format, same} = context.window.MRAPaperNotation;
const plain = value => JSON.parse(JSON.stringify(value));
const num = value => ({op: 'num', value});
const variable = name => ({op: 'var', name});
const binary = (op, left, right) => ({op, left, right});
let count = 0;
function test(name, action) { action(); count++; console.log('PASS ' + name); }

test('arithmetic precedence preserves the exact target', () => {
  assert.deepEqual(plain(parse('n + 2 * m = k')), binary('eq', binary('add', variable('n'), binary('mul', num(2), variable('m'))), variable('k')));
  assert.deepEqual(plain(parse('(n + 2) * m = k')), binary('eq', binary('mul', binary('add', variable('n'), num(2)), variable('m')), variable('k')));
});
test('natural subtraction stays left associative', () => {
  assert.deepEqual(plain(parse('n - m - k = 0')), binary('eq', binary('sub', binary('sub', variable('n'), variable('m')), variable('k')), num(0)));
});
test('reversed comparisons and inequality keep their meaning', () => {
  assert.deepEqual(plain(parse('n + 2 > n')), plain(parse('n < n + 2')));
  assert.deepEqual(plain(parse('n >= m')), plain(parse('m ≤ n')));
  assert.deepEqual(plain(parse('n != m')), {op: 'not', body: binary('eq', variable('n'), variable('m'))});
});
test('logic binds comparisons before conjunction, disjunction and implication', () => {
  const a = binary('eq', variable('n'), num(0));
  const b = binary('eq', variable('m'), num(0));
  const c = binary('lt', num(0), variable('n'));
  assert.deepEqual(plain(parse('n = 0 || m = 0 && 0 < n -> n = 0 -> m = 0')), binary('imp', binary('or', a, binary('and', b, c)), binary('imp', a, b)));
  assert.deepEqual(plain(parse('!n = 0')), {op: 'not', body: a});
});
test('quantifiers round trip without dropping scope', () => {
  for (const input of ['forall n, exists k, n = k + 1', '(∀ n, n = n) ∧ (∃ m, m = 0)']) {
    const value = parse(input);
    assert(same(value, parse(format(value))));
  }
});
test('all authored paper formulas survive lossless form conversion', () => {
  for (const file of ['valid-proof.json', 'true-claim-bad-proof.json', 'missing-condition.json']) {
    const bundle = JSON.parse(fs.readFileSync(path.join(root, 'evals/paper-review-v1', file), 'utf8'));
    for (const formula of [...bundle.assumptions, bundle.conclusion, ...bundle.steps.map(step => step.formula)]) {
      assert(same(formula, parse(format(formula))), file);
    }
  }
});
test('unsupported or ambiguous mathematical notation is rejected', () => {
  for (const input of ['', 'n', 'n < m < k', 'n / m = 1', 'n ^ 2 = m', '-1 < n', '2n = m', 'n = m trailing', 'n + (m = 0) = k', '(n = 0) * m = 0', 'forall n n = n', 'n = 0; alert(1)', '\\forall n, n=n']) assert.throws(() => parse(input), undefined, input);
});
test('formula budgets reject oversized literals, nesting and input', () => {
  assert.throws(() => parse('n = 1000001'));
  assert.throws(() => parse('('.repeat(26) + 'n=n' + ')'.repeat(26)));
  assert.throws(() => parse('n+'.repeat(260) + 'n=n'));
  assert.throws(() => parse(' '.repeat(4097)));
  assert.doesNotThrow(() => parse('n = 1000000'));
});
test('form conversion cannot silently discard unknown AST fields', () => {
  assert.throws(() => format({...binary('eq', variable('n'), variable('n')), injected: true}));
  assert.throws(() => format({op: 'pow', left: variable('n'), right: num(2)}));
  assert(!same({a: 1}, {a: 1, b: 2}));
});
test('integer negative literals and unary negation preserve exact AST and precedence', () => {
  assert.deepEqual(plain(parse('-1 < n', 'Int')), binary('lt', num(-1), variable('n')));
  assert.deepEqual(plain(parse('-n * 2 = 0', 'Int')), binary('eq', binary('mul', {op:'neg', body:variable('n')}, num(2)), num(0)));
  const value = {op:'neg', body:num(1)};
  const formula = binary('eq', value, num(-1));
  assert(same(parse(format(formula), 'Int'), formula));
  assert.throws(() => parse('-1 < n', 'Nat'));
  assert.throws(() => parse('-(n + 1) < n', 'Nat'));
  assert.throws(() => parse('n = -1000001', 'Int'));
  assert.throws(() => parse('n = n', 'Real'));
});
test('integer author examples retain every formula through notation conversion', () => {
  for (const name of ['valid-proof', 'true-claim-bad-proof', 'missing-condition']) {
    const bundle = JSON.parse(fs.readFileSync(path.join(root, 'evals/integer-review-v1', name + '.json'), 'utf8'));
    for (const formula of [...bundle.assumptions, bundle.conclusion, ...bundle.steps.map(step => step.formula)]) assert(same(parse(format(formula), 'Int'), formula));
  }
});
console.log(JSON.stringify({all_passed: true, tests: count, purpose: 'Data conversion only; no mathematical certification or model measurement'}));
