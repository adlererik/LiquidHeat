import fs from 'node:fs';
import assert from 'node:assert/strict';
const source=fs.readFileSync('frontend/app.js','utf8');
const start=source.indexOf('let diagnosticRequest='),end=source.indexOf('async function loadDiagnostics(',start);
let count=0,open=0,clock=1000000,resolve;
const load=()=>{count++;return new Promise(r=>{resolve=r;});};
const diagnostics=new Function('loadDiagnostics','$','Date',source.slice(start,end)+';return diagnostics;')(
 load,()=>({showModal(){open++;}}),{now:()=>clock});
const pending=diagnostics(),joined=diagnostics(),manual=diagnostics(true);
assert.equal(count,1,'Event bursts share one in-flight request');
resolve(true);await Promise.all([pending,joined,manual]);assert.equal(open,1);
assert.equal(await diagnostics(),false);assert.equal(count,1,'Background bursts are throttled');
clock+=5001;const next=diagnostics();assert.equal(count,2);resolve(true);await next;
const forced=diagnostics(true);assert.equal(count,3,'Explicit diagnostics bypass time throttle');resolve(true);await forced;
console.log('PASS diagnostics coalescing, background throttle, completion recovery and explicit access');
