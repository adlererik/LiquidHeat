import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';

const source=fs.readFileSync('frontend/app.js','utf8');
const functions=source.slice(source.indexOf('function zoomRange('),source.indexOf('function viewportRange('));
const {zoomRange,priceFit,wheelZoomAxis}=new Function(functions+';return {zoomRange,priceFit,wheelZoomAxis};')();
const market=JSON.parse(fs.readFileSync(process.argv[2]||'artifacts/heatmap-final.json','utf8'));
assert.ok(market.candles.length>10,'Requires a real exchange heatmap export');
const step=market.time_resolution_s*1000;
const domain=[Math.min(...market.prices,...market.candles.map(r=>r[3]))-market.bucket,Math.max(...market.prices,...market.candles.map(r=>r[4]))+market.bucket];

test('price zoom preserves its cursor anchor',()=>{
 const before={start:30,end:70},anchor=45;
 const after=zoomRange(before.start,before.end,.5,anchor);
 assert.equal(after.end-after.start,20);
 assert.equal((anchor-after.start)/(after.end-after.start),(anchor-before.start)/(before.end-before.start));
});
test('zoom clamps at domain edges without changing the requested span',()=>{
 const range=zoomRange(0,20,2,10);
 assert.deepEqual(range,{start:0,end:40});
 assert.deepEqual(zoomRange(80,100,2,90),{start:60,end:100});
 assert.deepEqual(zoomRange(30,70,10,40),{start:0,end:100});
 assert.ok(zoomRange(40,60,.00001,50,1).end-zoomRange(40,60,.00001,50,1).start>=1);
});
test('fit includes real visible candle wicks and padding',()=>{
 const start=market.candles.at(-30)[0],end=market.candles.at(-2)[0];
 const fit=priceFit(market.candles,start,end,step,market.bucket,domain,market.mark);
 const low=domain[0]+fit.start/100*(domain[1]-domain[0]),high=domain[0]+fit.end/100*(domain[1]-domain[0]);
 for(const c of market.candles.filter(r=>r[0]>=start&&r[0]-step<=end)){assert.ok(c[3]>low);assert.ok(c[4]<high);}
 assert.ok(fit.start>=0&&fit.end<=100&&fit.start<fit.end);
});
test('fit uses only candles overlapping the visible time window',()=>{
 const start=market.candles.at(-20)[0],end=market.candles.at(-5)[0];
 const visible=market.candles.filter(r=>r[0]>=start&&r[0]-step<=end);
 assert.deepEqual(priceFit(market.candles,start,end,step,market.bucket,domain,market.mark),priceFit(visible,start,end,step,market.bucket,domain,market.mark));
});
test('empty time windows fit around the observed mark',()=>{
 const fit=priceFit([],0,1,step,market.bucket,domain,market.mark);
 const center=domain[0]+(fit.start+fit.end)/200*(domain[1]-domain[0]);
 assert.ok(Math.abs(center-market.mark)<1e-7);
 assert.ok(fit.end>fit.start);
});
test('wheel routes Shift to price and ordinary scrolling to time exclusively',()=>{
 assert.equal(wheelZoomAxis(true,false,false,true),'price');
 assert.equal(wheelZoomAxis(true,false,false,false),'time');
 assert.equal(wheelZoomAxis(false,true,false,false),'price');
 assert.equal(wheelZoomAxis(false,false,true,true),'time');
 assert.equal(wheelZoomAxis(false,false,false,true),null);
});
