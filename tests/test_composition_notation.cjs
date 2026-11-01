'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {parse,format}=require('../mra/web/composition-notation.js');
test('full Nat language survives a semantic round trip',()=>{
  for(const value of ['a | b + c','gcd(a, b) = 1','coprime(a, b)','a | a - b <-> b = 0 || a <= b','(a = b && b <= c) || coprime(c, 0)']){
    const ast=parse(value);assert.deepEqual(parse(format(ast)),ast);
  }
});
test('noncommutative order and arithmetic precedence remain exact',()=>{
  assert.deepEqual(parse('a - b * c','term'),{sub:[{var:'a'},{mul:[{var:'b'},{var:'c'}]}]});
  assert.deepEqual(parse('gcd(a + 1, b)','term'),{gcd:[{add:[{var:'a'},{nat:1}]},{var:'b'}]});
});
test('type errors, implicit multiplication and unsupported syntax fail closed',()=>{
  for(const value of ['a < b','-1 = 0','a | b | c','gcd(a = b, c) = 0','coprime(a, b) + 1 = 2','a b = 0','1000001 = 0','a -> b','a = b trailing'])assert.throws(()=>parse(value));
  assert.throws(()=>parse('a = b','term'));assert.throws(()=>parse('a'));
});
test('bounded parsing and formatting reject overlong or unrecognized input',()=>{
  assert.throws(()=>parse('('.repeat(16)+'a = a'+')'.repeat(16)));
  assert.throws(()=>parse('a'.repeat(8193)));assert.throws(()=>format({var:'x',extra:true}));
  assert.throws(()=>format({unknown:[{nat:1},{nat:2}]}));
});
