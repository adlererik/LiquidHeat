import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source=fs.readFileSync('frontend/app.js','utf8');
const begin=source.indexOf('function mergeIncremental('),end=source.indexOf('\nasync function magnets(',begin);
const now=Date.now(),scope='lookback=14d&bucket=25&source=hyperliquid';
const old={times:[now-600000,now-300000],prices:[80000],matrix:[[0,0,10],[1,0,20]],
 candles:[],clld:[],time_resolution_s:300,normalization_p98:20};
const tail={...old,times:[now],matrix:[[0,0,30]],time_resolution_s:600,incremental:true};
const full={...tail,times:[now-600000,now],matrix:[[0,0,25],[1,0,30]],incremental:false};
const requests=[],errors=[];
let paints=0;
const context={URLSearchParams,Date,Map,Set,Error,Math,lookback:'14d',data:old,dataScope:scope,
 refreshing:false,pendingRefresh:false,lastFetch:0,
 query:()=>new URLSearchParams(scope),summary:()=>{},chartData:()=>paints++,magnets:async()=>{},
 toast:e=>errors.push(e),$:()=>({}),
 setTimeout:()=>{throw Error('Unexpected queued refresh');},
 fetch:async url=>{requests.push(new URL(url,'http://example.invalid'));return {ok:true,json:async()=>requests.length===1?tail:full};}};
vm.createContext(context);vm.runInContext(source.slice(begin,end),context);
await context.refresh(true);
assert.equal(requests.length,2);
assert.ok(requests[0].searchParams.has('since'));
assert.equal(requests[1].searchParams.has('since'),false);
assert.equal(context.data,full);
assert.equal(paints,1);assert.deepEqual(errors,[]);
assert.throws(()=>context.mergeIncremental(old,tail),/resolution changed/);

// At the same resolution, only a tail is requested and the untouched history remains.
requests.length=0;context.data=old;
context.fetch=async url=>{requests.push(new URL(url,'http://example.invalid'));return {ok:true,json:async()=>({...old,times:[now],matrix:[[0,0,30]],incremental:true})};};
await context.refresh(true);
assert.equal(requests.length,1);
assert.deepEqual(Array.from(context.data.times),[...old.times,now]);
assert.deepEqual(Array.from(context.data.matrix,r=>Array.from(r)),[[0,0,10],[1,0,20],[2,0,30]]);
assert.equal(paints,2);assert.deepEqual(errors,[]);
console.log('PASS resolution transition replaces the full grid; steady resolution retains incremental updates');
