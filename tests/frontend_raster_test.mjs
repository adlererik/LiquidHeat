import fs from 'node:fs';
import assert from 'node:assert/strict';
const source=fs.readFileSync('frontend/app.js','utf8');
const code=source.slice(source.indexOf('function poolRaster('),source.indexOf('function selected('));
const poolRaster=new Function(code+';return poolRaster;')();
const d=JSON.parse(fs.readFileSync(process.argv[2]||'artifacts/hyperliquid/final-live.json','utf8'));
assert.ok(d.matrix.length&&d.prices.length&&d.times.length,'Real market export required');
const before=JSON.stringify(d),columns=Array.from({length:d.times.length},()=>new Set());for(const p of d.matrix)columns[p[0]].add(p[2]);
for(const maxRows of [1,3,25,200,100000]){
 const r=poolRaster(d.matrix,d.times,d.prices,d.time_resolution_s*1000,d.bucket,maxRows);
 assert.ok(r.ny<=maxRows&&r.ny<=r.nativeRows);
 for(let column=0;column<d.times.length;column++){
  const input=[...columns[column]];
  const index=Math.round((d.times[column]-r.firstTime)/(d.time_resolution_s*1000));
  const pixels=Array.from({length:r.ny},(_,y)=>r.values[y*r.nx+index]);
  assert.equal(Math.max(0,...pixels),Math.max(0,...input),'Strongest true bucket must survive overview downsampling');
  for(const value of pixels)assert.ok(value===0||columns[column].has(value),'Rendering cannot invent an exposure value');
 }
}
assert.equal(JSON.stringify(d),before,'Rendering must not mutate source values');
console.log(JSON.stringify({passed:true,real_cells:d.matrix.length,resolutions:5,peak_preserved:true,no_invented_values:true}));
