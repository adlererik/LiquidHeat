import fs from 'node:fs';
import assert from 'node:assert/strict';
const src=fs.readFileSync('frontend/app.js','utf8');
const a=src.indexOf('function mergeIncremental('),b=src.indexOf('\nasync function refresh(',a);
const merge=new Function('lookback',src.slice(a,b)+';return mergeIncremental;')('24h');
const now=Date.now(),values=[100000,1000000,...Array(1000).fill(1)],policy={method:'significant-p98.5'};
const data={times:[now],prices:values.map((_,i)=>70000+i*25),matrix:values.map((v,i)=>[0,i,v]),
 candles:[],clld:[],source:'cex',cex_model:'sharp',time_resolution_s:300,normalization:policy};
const cex=merge(data,data);
assert.equal(cex.normalization_p98,986500);
assert.deepEqual(cex.matrix,data.matrix);
const hl={...data,source:'hyperliquid',cex_model:undefined,normalization:undefined};
assert.equal(merge(hl,hl).normalization_p98,1,'Hyperliquid retains the original full-distribution p98');
assert.throws(()=>merge(data,{...data,cex_model:'fast_decay'}),/model changed/);
const expected="const palette=[[0,[11,14,20,0]],[.1,[22,11,57,1]],[.3,[66,10,104,1]],[.5,[147,38,103,1]],[.7,[221,81,58,1]],[.88,[252,165,10,1]],[1,[255,255,0,1]]];";
assert.ok(src.includes(expected),'Original seven-stop colormap stays byte-for-byte unchanged');
console.log('PASS CEX significant-cell normalization; Hyperliquid p98 and palette unchanged; model mixing rejected');
