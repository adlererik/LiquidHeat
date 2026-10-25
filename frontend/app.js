'use strict';
const $ = id => document.getElementById(id);
const money = (n, digits=1) => n==null?'—':Math.abs(n)>=1e9?'$'+(n/1e9).toFixed(digits)+'B':Math.abs(n)>=1e6?'$'+(n/1e6).toFixed(digits)+'M':Math.abs(n)>=1e3?'$'+(n/1e3).toFixed(digits)+'K':'$'+n.toFixed(0);
const price = n => n==null?'—':'$'+n.toLocaleString('en-US',{maximumFractionDigits:1,minimumFractionDigits:1});
const utc = t => new Date(t).toLocaleString('en-GB',{timeZone:'UTC',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
let chart=echarts.init($('chart'),null,{renderer:'canvas'}), data=null,lookback='24h',cumulative=false,aborter=null,refreshing=false,lastFetch=0,pendingRefresh=false;
let liveProfiles={},liveCandle=null,renderScope='',dataScope='',source='hyperliquid';
const TIME_ZOOM='time-window',PRICE_ZOOM='price-window';
const palette=[[0,[11,14,20,0]],[.1,[22,11,57,1]],[.3,[66,10,104,1]],[.5,[147,38,103,1]],[.7,[221,81,58,1]],[.88,[252,165,10,1]],[1,[255,255,0,1]]];
function color(v){v=Math.min(1,Math.max(0,v));let i=1;while(i<palette.length-1&&v>palette[i][0])i++;const a=palette[i-1],b=palette[i],t=(v-a[0])/(b[0]-a[0]);const c=a[1].map((x,j)=>x+(b[1][j]-x)*t);return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${c[3]})`;}
function poolRaster(points,times,prices,step,bucket,maxRows){
 const firstTime=times[0]||Date.now(),lastTime=times.at(-1)||firstTime;
 const lowPrice=prices[0]||0,highPrice=prices.at(-1)||lowPrice;
 const nx=Math.max(1,Math.round((lastTime-firstTime)/step)+1),nativeRows=Math.max(1,Math.round((highPrice-lowPrice)/bucket)+1);
 const ny=Math.max(1,Math.min(nativeRows,Math.floor(maxRows))),values=new Float64Array(nx*ny);
 let peak=0;
 for(const r of points){
  const x=Math.round((times[r[0]]-firstTime)/step),nativeY=nativeRows-1-Math.round((prices[r[1]]-lowPrice)/bucket);
  const y=Math.min(ny-1,Math.floor(nativeY*ny/nativeRows)),at=y*nx+x;
  if(r[2]>values[at])values[at]=r[2];peak=Math.max(peak,r[2]);
 }
 return {firstTime,lastTime,lowPrice,highPrice,nx,ny,nativeRows,values,peak};
}
function selected(id){return [...$(id).querySelectorAll('input:checked')].map(x=>x.value);}
function query(){return new URLSearchParams({lookback,bucket:$('bucket').value,source,...(source==='cex'?{exchanges:selected('exchange-filters').join(','),leverage:selected('leverage-filters').join(',')}:{})});}
function toast(s){$('toast').textContent=s;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),6000);}
function zoomRange(start,end,factor,anchor,minSpan=.01){
 const span=Math.max(minSpan,Math.min(100,(end-start)*factor));
 const ratio=span/Math.max(.000001,end-start);
 const low=Math.max(0,Math.min(100-span,anchor-(anchor-start)*ratio));
 return {start:low,end:low+span};
}
function priceFit(candles,startTime,endTime,step,bucket,domain,fallback){
 // Candles are timestamped at interval end and drawn at interval center.
 const visible=candles.filter(r=>r[0]>=startTime&&r[0]-step<=endTime);
 const lows=visible.map(r=>r[3]),highs=visible.map(r=>r[4]);
 const low=visible.length?Math.min(...lows):fallback,high=visible.length?Math.max(...highs):fallback;
 const center=(low+high)/2,span=Math.max((high-low)*1.3,center*.015,bucket*12);
 let a=Math.max(domain[0],center-span/2),b=Math.min(domain[1],center+span/2);
 if(b<=a){a=domain[0];b=domain[1];}
 return {start:100*(a-domain[0])/(domain[1]-domain[0]),end:100*(b-domain[0])/(domain[1]-domain[0])};
}
function wheelZoomAxis(main,profile,clld,shift){
 return profile||(main&&shift)?'price':main||clld?'time':null;
}
function viewportRange(axis){
 const option=chart.getOption(),zoom=option.dataZoom.find(z=>z.id===(axis==='price'?PRICE_ZOOM:TIME_ZOOM));
 const base=axis==='price'?option.yAxis[0]:option.xAxis[0];
 return {start:zoom?.start??0,end:zoom?.end??100,min:base.min,max:base.max};
}
function recordViewport(){
 if(!data)return;
 const x=viewportRange('time'),y=viewportRange('price');
 for(const [key,value] of Object.entries({timeStart:x.start,timeEnd:x.end,timeMin:x.min+(x.max-x.min)*x.start/100,timeMax:x.min+(x.max-x.min)*x.end/100,priceStart:y.start,priceEnd:y.end,priceMin:y.min+(y.max-y.min)*y.start/100,priceMax:y.min+(y.max-y.min)*y.end/100}))$('chart').dataset[key]=value;
}
function fitPrice(resetTime=false){
 if(!data)return;
 const x=viewportRange('time'),y=viewportRange('price'),span=x.max-x.min;
 const fit=priceFit(data.candles,resetTime?x.min:x.min+span*x.start/100,resetTime?x.max:x.min+span*x.end/100,data.time_resolution_s*1000,data.bucket,[y.min,y.max],data.mark||(y.min+y.max)/2);
 const batch=[{dataZoomId:PRICE_ZOOM,...fit}];
 if(resetTime)batch.push({dataZoomId:TIME_ZOOM,start:0,end:100});
 chart.dispatchAction({type:'dataZoom',batch});
 recordViewport();
}
function zoomAxis(axis,factor,anchor){
 if(!data)return;
 const v=viewportRange(axis),minimum=100*(axis==='price'?data.bucket:data.time_resolution_s*1000)/(v.max-v.min);
 const range=zoomRange(v.start,v.end,factor,anchor??(v.start+v.end)/2,Math.min(100,minimum));
 chart.dispatchAction({type:'dataZoom',dataZoomId:axis==='price'?PRICE_ZOOM:TIME_ZOOM,...range});
 recordViewport();
}
function chartData(){
 if(!data)return;
 const scope=query().toString();const previousZoom=scope===renderScope?(chart.getOption().dataZoom||[]):[];renderScope=scope;
 const mark=data.mark,threshold=+$('threshold').value/100,norm=Math.max(1,data.normalization_p98),step=data.time_resolution_s*1000,bucket=data.bucket;
 // Rasterize real bucket values once per history/filter change. ECharts then
 // transforms one Canvas image during zoom and live candle/profile updates.
 const points=data.matrix.filter(r=>r[2]>=norm*threshold);
 const heatCanvas=document.createElement('canvas');
 const priceZoom=previousZoom.find(z=>z.id===PRICE_ZOOM);
 const span=priceZoom?Math.max(.0001,(priceZoom.end-priceZoom.start)/100):(source==='hyperliquid'?1:.0001);
 const maxRows=Math.ceil(Math.max(100,chart.getHeight()-210)*(window.devicePixelRatio||1)/span);
 const raster=poolRaster(points,data.times,data.prices,step,bucket,maxRows);
 const {firstTime,lastTime,lowPrice,highPrice,nx,ny}=raster;
 heatCanvas.width=nx;heatCanvas.height=ny;
 const heatContext=heatCanvas.getContext('2d'),pixels=heatContext.createImageData(nx,ny);
 const lut=Array.from({length:256},(_,i)=>{const c=color(i/255).slice(5,-1).split(',').map(Number);return [c[0],c[1],c[2],Math.round(c[3]*255)];});
 for(let at=0;at<raster.values.length;at++){const value=raster.values[at];if(value<=0)continue;pixels.data.set(lut[Math.min(255,Math.floor(value/norm*255))],at*4);}
 $('chart').dataset.nativePriceRows=raster.nativeRows;$('chart').dataset.rasterPriceRows=ny;
 $('chart').dataset.rasterMode='Strongest native bucket per display pixel';$('chart').dataset.rasterPeak=raster.peak;
 heatContext.putImageData(pixels,0,0);
 const candlesticks=data.candles.map(r=>[r[0]-step/2,...r.slice(1)]);
 const minTime=Math.min(...data.candles.map(r=>r[0]),...data.times,Date.now()-3600000),maxTime=Math.max(Date.now(),...data.times);
 const ys=[Math.min(lowPrice-bucket/2,...data.candles.map(r=>r[3]-bucket)),Math.max(highPrice+bucket/2,...data.candles.map(r=>r[4]+bucket))];
 const initialPrice=source==='hyperliquid'?{start:0,end:100}:priceFit(data.candles,minTime,maxTime,step,bucket,ys,mark||(ys[0]+ys[1])/2);
 const profile=data.profile;
 const profiles=cumulative?[profile.map(r=>[r.cumulative_long_usd,r.price]),profile.map(r=>[r.cumulative_short_usd,r.price])]:[profile.map(r=>[r.long_usd,r.price]),profile.map(r=>[r.short_usd,r.price])];
 const markLine=$('mark-overlay').checked&&mark?[{yAxis:mark,lineStyle:{color:'#c7d7e6',width:.8,type:'dashed'},label:{show:true,position:'end',formatter:price(mark),color:'#c6d1df',fontSize:9}}]:[];
 const axis={axisLine:{lineStyle:{color:'#303b4e'}},axisTick:{show:false},axisLabel:{color:'#71819d',fontSize:9},splitLine:{show:true,lineStyle:{color:'#273144',opacity:.35}},axisPointer:{label:{backgroundColor:'#28354c'}}};
 const sliderStyle={showDetail:false,showDataShadow:false,borderColor:'#293448',backgroundColor:'#101722',fillerColor:'#b48d5320',handleStyle:{color:'#8e7757',borderColor:'#cfac77'}};
 const zoomSettings=[{id:TIME_ZOOM,type:'inside',xAxisIndex:[0,2],filterMode:'none',zoomOnMouseWheel:false,moveOnMouseMove:true},{id:PRICE_ZOOM,type:'inside',yAxisIndex:[0,1],filterMode:'none',zoomOnMouseWheel:false,moveOnMouseMove:true,...initialPrice},{id:'time-slider',type:'slider',xAxisIndex:[0,2],bottom:12,height:11,left:45,right:'26%',...sliderStyle},{id:'price-slider',type:'slider',orient:'vertical',yAxisIndex:[0,1],filterMode:'none',left:'75.8%',top:15,bottom:157,width:9,...sliderStyle,...initialPrice}];
 if(source==='hyperliquid'&&!previousZoom.length&&data.times.length){const first=Math.min(firstTime-step*3,maxTime-3600000),start=Math.max(0,100*(first-minTime)/(maxTime-minTime));for(const z of zoomSettings.filter(z=>z.xAxisIndex))z.start=start;}
 for(let i=0;i<zoomSettings.length;i++){if(previousZoom[i]){zoomSettings[i].start=previousZoom[i].start;zoomSettings[i].end=previousZoom[i].end;}}
 crispCanvas();
 chart.setOption({animation:false,backgroundColor:'#0b0e14',textStyle:{fontFamily:'Inter, Segoe UI, Arial, sans-serif'},
 grid:[{left:15,right:'26%',top:15,bottom:157,containLabel:true},{left:'79%',right:29,top:15,bottom:157,containLabel:false},{left:15,right:'26%',bottom:41,height:72,containLabel:true}],
 tooltip:{trigger:'item',backgroundColor:'#111a29ee',borderColor:'#43516b',textStyle:{color:'#d4deeb',fontSize:10},formatter:p=>{
 const v=p.value;if(p.seriesIndex===0)return `${utc(v[0])} UTC<br>Price ${price(v[1])}<br>Estimated exposure <b>${money(v[2],2)}</b>`;
 if(p.seriesIndex===1)return `${utc(v[0])} UTC<br>O ${price(v[1])} · C ${price(v[2])}<br>L ${price(v[3])} · H ${price(v[4])}`;
 if(p.seriesIndex===4)return `${utc(v[0])} UTC<br>CLLD ${(v[1]*100).toFixed(1)}%`;
 return `${price(v[1])}<br>${p.seriesName} ${money(v[0],2)}`;}},
 xAxis:[{...axis,type:'time',gridIndex:0,min:minTime,max:maxTime,splitNumber:7,axisLabel:{...axis.axisLabel,formatter:t=>utc(t)},splitLine:{show:false}},{...axis,type:'value',gridIndex:1,position:'bottom',axisLabel:{...axis.axisLabel,formatter:n=>money(n,0)},splitNumber:3,splitLine:{show:false}},{...axis,type:'time',gridIndex:2,min:minTime,max:maxTime,axisLabel:{show:false},splitLine:{show:false}}],
 yAxis:[{...axis,type:'value',gridIndex:0,min:ys[0],max:ys[1],position:'right',scale:true,axisLabel:{...axis.axisLabel,formatter:n=>(n/1000).toFixed(1)+'K'},splitNumber:9},{...axis,type:'value',gridIndex:1,min:ys[0],max:ys[1],scale:true,axisLabel:{show:false},splitLine:{show:false}},{...axis,type:'value',gridIndex:2,min:-1,max:1,name:source==='hyperliquid'?'CLLD · TRACKED OVERHANG':'CLLD · EST. OVERHANG',nameLocation:'end',nameTextStyle:{fontSize:8,color:'#798aa5',align:'left',padding:[0,0,0,0]},axisLabel:{...axis.axisLabel,formatter:n=>(n*100).toFixed(0)+'%'},splitNumber:2}],
 dataZoom:zoomSettings,
 series:[{name:source==='hyperliquid'?'Reported sampled exposure':'Estimated exposure',type:'custom',coordinateSystem:'cartesian2d',silent:true,tooltip:{show:false},renderItem:(params,api)=>{const topLeft=api.coord([firstTime-step,highPrice+bucket/2]),bottomRight=api.coord([lastTime,lowPrice-bucket/2]);return {type:'image',style:{image:heatCanvas,x:topLeft[0],y:topLeft[1],width:bottomRight[0]-topLeft[0],height:bottomRight[1]-topLeft[1]},clipPath:{type:'rect',shape:{x:params.coordSys.x,y:params.coordSys.y,width:params.coordSys.width,height:params.coordSys.height}}};},data:[[firstTime,data.mark||lowPrice]],encode:{x:0,y:1},z:1},
 {name:'Trade candles',type:'candlestick',xAxisIndex:0,yAxisIndex:0,data:candlesticks,dimensions:['time','open','close','low','high'],encode:{x:0,y:[1,2,3,4]},barMaxWidth:5,itemStyle:{color:'#43b8a0',color0:'#e16059',borderColor:'#43b8a0',borderColor0:'#e16059'},markLine:{silent:true,symbol:'none',data:markLine},z:5},
 ...profiles.map((values,i)=>({name:i?'Short exposure':'Long exposure',type:cumulative?'line':'custom',xAxisIndex:1,yAxisIndex:1,coordinateSystem:'cartesian2d',data:values,encode:{x:0,y:1},symbol:'none',lineStyle:{color:i?'#df7a78':'#42b99d',width:1.3},areaStyle:cumulative?{color:i?'#df7a7810':'#42b99d10'}:undefined,renderItem:cumulative?undefined:(params,api)=>{const p=api.coord([api.value(0),api.value(1)]),zero=api.coord([0,api.value(1)]),h=Math.max(1,Math.abs(api.size([0,bucket])[1])*.8);const rect=echarts.graphic.clipRectByRect({x:zero[0],y:p[1]-h/2,width:Math.max(0,p[0]-zero[0]),height:h},params.coordSys);return rect&&{type:'rect',shape:rect,style:{fill:i?'#c96c6db5':'#38aa94b5'}};},z:3})),
 {name:'CLLD normalized',type:'bar',xAxisIndex:2,yAxisIndex:2,data:data.clld.map(r=>({value:[r.ts,r.normalized],itemStyle:{color:r.usd>=0?'#43b69c':'#d87574'}})),encode:{x:0,y:1},barMaxWidth:6,z:2,markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:'#7787a2',width:.5},data:[{yAxis:0}]}}
 ]},{notMerge:true,lazyUpdate:true});
 $('chart').dataset.points=points.length;$('chart').dataset.candles=candlesticks.length;$('chart').dataset.axisAlignment='shared UTC interval centers and numeric USD price';
 window.__heatmapRendered={points:points.length,candles:candlesticks.length,profile:profile.length,estimated:source==='cex',source,axisMode:'shared numeric price / shared UTC time'};
}
// Route each wheel gesture to exactly one axis. ECharts 5's native modifier
// options otherwise let Shift-wheel zoom both time and price simultaneously.
$('chart').addEventListener('wheel',event=>{
 if(!data||event.ctrlKey||event.metaKey)return; // Keep browser zoom available.
 const rect=$('chart').getBoundingClientRect(),point=[(event.clientX-rect.left)*chart.getWidth()/rect.width,(event.clientY-rect.top)*chart.getHeight()/rect.height];
 const main=chart.containPixel({gridIndex:0},point),profile=chart.containPixel({gridIndex:1},point),clld=chart.containPixel({gridIndex:2},point);
 const axis=wheelZoomAxis(main,profile,clld,event.shiftKey);
 if(!axis)return;
 const v=viewportRange(axis);
 const value=chart.convertFromPixel(axis==='price'?{yAxisIndex:0}:{xAxisIndex:0},axis==='price'?point[1]:point[0]);
 const delta=(event.deltaY||event.deltaX)*(event.deltaMode===1?16:event.deltaMode===2?chart.getHeight():1);
 if(!delta||!Number.isFinite(value))return;
 event.preventDefault();event.stopImmediatePropagation();
 zoomAxis(axis,Math.exp(Math.max(-.5,Math.min(.5,delta*.002))),Math.max(v.start,Math.min(v.end,100*(value-v.min)/(v.max-v.min))));
},{capture:true,passive:false});
function crispCanvas(){let changed=false;for(const canvas of $('chart').querySelectorAll('canvas')){const ctx=canvas.getContext('2d');if(ctx.imageSmoothingEnabled){ctx.imageSmoothingEnabled=false;changed=true;}}$('chart').dataset.crispBuckets=String([...$('chart').querySelectorAll('canvas')].every(c=>!c.getContext('2d').imageSmoothingEnabled));if(changed)chart.getZr().refresh();}
chart.on('rendered',crispCanvas);
let rasterTimer;function scheduleRaster(){clearTimeout(rasterTimer);rasterTimer=setTimeout(chartData,180);}
chart.on('datazoom',()=>{recordViewport();scheduleRaster();});chart.on('finished',recordViewport);
function summary(){
 if(!data)return;const long=data.profile.reduce((s,r)=>s+r.long_usd,0),short=data.profile.reduce((s,r)=>s+r.short_usd,0);
 $('long').textContent=money(long);$('short').textContent=money(short);$('clld').textContent=money(long-short);$('clld').style.color=long>=short?'#86d8c1':'#efa5a2';
 $('mark').textContent=price(data.mark);$('metric-mark').textContent=price(data.mark);$('p98').textContent=money(data.normalization_p98);
 $('candle-source').textContent=data.candle_exchange[0].toUpperCase()+data.candle_exchange.slice(1);
 $('resolution').textContent=`UTC · ${data.time_resolution_s/60}m display · $${data.bucket} buckets`;
 const latest=data.provenance.map(p=>p.mark_ts).filter(Boolean).sort().at(-1);$('asof').textContent=latest?'Mark '+new Date(latest).toLocaleTimeString('en-GB',{timeZone:'UTC',hour12:false})+' UTC':'No live mark yet';
 $('empty').hidden=data.matrix.length>0;
 if(source==='hyperliquid'){sampleSummary(data.sample);if(!data.matrix.length&&data.sample?.known_btc_positions){$('empty-title').textContent='No plottable reported thresholds yet';$('empty-note').textContent='Accounts are still being scanned. Null prices and thresholds outside the displayed grid are excluded; no prices are guessed.';}}
}
function learningView(d){
 if(!d)return;
 const sides=['long','short'].map(side=>d.progress.find(r=>r.side===side)||{eligible:0,scored:0,excluded:0,positive:0});
 const ready=Math.min(...sides.map(r=>r.eligible)),candidate=d.models.find(r=>r.state==='candidate'),champion=d.models.find(r=>r.state==='champion');
 const paused=d.worker&&(d.worker.status!=='live'||Date.now()-Date.parse(d.worker.updated_at)>120000);
 const limited=sides.some(r=>r.scored>=4&&r.eligible/r.scored<.8);
 $('learning-status').textContent=paused?'Learning paused · check diagnostics':champion?'Learned forecast active':candidate?'Candidate under future test':limited?'Coverage limits learning':'Collecting audited outcomes';
 $('learning-progress').textContent=champion?`${(champion.report.relative_improvement*100).toFixed(1)}% lower log-loss in its fixed future test. Scope: recorded Bybit flow.`:candidate?`Fixed future test ends ${utc(candidate.decision_at)} UTC. Promotion requires measured improvement.`:`${ready} / ${d.requirements.training_windows_per_side} audited windows per side · minimum ${d.requirements.minimum_training_span_days} days · positive windows L ${sides[0].positive} / S ${sides[1].positive} (need ${d.requirements.positive_windows_per_side}) · ${sides.reduce((s,r)=>s+r.excluded,0)} gap-affected labels excluded`;
 $('learning-progress-bar').max=d.requirements.training_windows_per_side;$('learning-progress-bar').value=ready;
 for(const side of ['long','short']){
  const f=d.forecasts.find(r=>r.side===side),warm=!f||(!f.champion_id&&(f.provenance?.baseline_history_n||0)<30);
  const expired=f&&Date.now()>Date.parse(f.target_end);
  $('learning-'+side).textContent=expired?'Unavailable':warm?'Collecting':money(f.champion_usd,2);
  $('learning-window-'+side).textContent=expired?'Forecast expired · waiting for fresh data':f?`${f.champion_id?'Learned':'Baseline'} · ${utc(f.target_start)}–${new Date(f.target_end).toLocaleTimeString('en-GB',{timeZone:'UTC',hour:'2-digit',minute:'2-digit',hour12:false})} UTC`:'Waiting for a fresh model state';
 }
 $('learning-note').textContent='This separate learning experiment does not change reported Hyperliquid thresholds. CEX heatmap exposure remains uncalibrated. Forecasts target recorded Bybit liquidation notional; candidates need 14 future test days and must beat baselines. Improvement is not guaranteed.';
 $('learning-summary').textContent=`The target is recorded Bybit long/short liquidation notional over the next 15 minutes. Training needs ${d.requirements.training_windows_per_side} audited windows, ${d.requirements.minimum_training_span_days} days and ${d.requirements.positive_windows_per_side} positive windows per side. Candidates then face ${d.requirements.fixed_future_holdout_days} fixed future test days and at least ${d.requirements.audited_holdout_windows} audited test windows. Automatic promotion requires at least 10% lower log-loss than the best recorded reference, a positive day-block lower improvement bound, and no material USD error degradation. Underperforming promoted forecasts can roll back. These tests do not calibrate hidden heatmap exposure.`;
 $('learning-status').dataset.protocol=d.protocol;$('learning-status').dataset.audited=ready;
}
function mergeIncremental(old,part){
 const days={'24h':1,'3d':3,'7d':7,'14d':14,'30d':30}[lookback],cutoff=Date.now()-days*86400000;
 const times=[...new Set([...old.times,...part.times])].filter(t=>t>=cutoff).sort((a,b)=>a-b),prices=[...new Set([...old.prices,...part.prices])].sort((a,b)=>a-b);
 const ti=new Map(times.map((v,i)=>[v,i])),pi=new Map(prices.map((v,i)=>[v,i])),replace=new Set(part.times);
 const matrix=[];
 for(const r of old.matrix){const t=old.times[r[0]];if(ti.has(t)&&!replace.has(t))matrix.push([ti.get(t),pi.get(old.prices[r[1]]),r[2]]);}
 for(const r of part.matrix){const t=part.times[r[0]];if(ti.has(t))matrix.push([ti.get(t),pi.get(part.prices[r[1]]),r[2]]);}
 const mergeSeries=(a,b,key)=>[...new Map([...a,...b].filter(r=>key(r)>=cutoff).map(r=>[key(r),r])).values()].sort((a,b)=>key(a)-key(b));
 const values=matrix.map(r=>r[2]).sort((a,b)=>a-b),at=(values.length-1)*.98,lo=Math.max(0,Math.floor(at)),hi=Math.max(0,Math.ceil(at));
 const norm=values.length?values[lo]+(values[hi]-values[lo])*(at-lo):1;
 return {...part,times,prices,matrix,candles:mergeSeries(old.candles,part.candles,r=>r[0]),clld:mergeSeries(old.clld,part.clld,r=>r.ts),normalization_p98:norm};
}
async function refresh(incremental=false){
 if(refreshing){pendingRefresh=true;return;}refreshing=true;lastFetch=Date.now();
 const scope=query().toString(),q=query();
 const delta=incremental&&data&&dataScope===scope&&data.times.length;
 if(delta)q.set('since',String(data.times.at(-1)-data.time_resolution_s*1000));
 try{const r=await fetch('/api/v1/heatmap?'+q);if(!r.ok)throw Error(`Heatmap API ${r.status}`);const part=await r.json();if(query().toString()!==scope){pendingRefresh=true;return;}data=delta?mergeIncremental(data,part):part;dataScope=scope;summary();chartData();await magnets();}
 catch(e){toast(e.message);$('empty').hidden=!!data?.matrix.length;}
 finally{refreshing=false;if(pendingRefresh){pendingRefresh=false;setTimeout(()=>refresh(true),1000);}}
}
async function magnets(){try{const scope=query().toString(),q=query();q.set('bucket','100');q.set('limit','5');const r=await fetch('/api/v1/magnets?'+q);if(!r.ok)return;const m=await r.json();if(scope!==query().toString())return;const root=$('magnet-list');root.replaceChildren();for(const [i,z] of m.zones.entries()){const el=document.createElement('div');el.className='magnet';const sm=document.createElement('small');sm.textContent=`0${i+1} / ${source==='hyperliquid'?(z.side==='long'?'LONG POSITIONS':'SHORT POSITIONS'):(z.side==='long'?'LONGS BELOW':'SHORTS ABOVE')}`;const b=document.createElement('b');b.textContent=price(z.price);const span=document.createElement('span');span.textContent=money(z.usd)+' · '+(z.distance_pct>=0?'+':'')+z.distance_pct.toFixed(2)+'%';const bar=document.createElement('div');bar.className='bar';const fill=document.createElement('i');fill.style.width=(z.intensity*100)+'%';bar.append(fill);const em=document.createElement('em');em.textContent='Z-score '+z.z_score.toFixed(2);el.append(sm,b,span,bar,em);root.append(el);}if(!m.zones.length)root.textContent=source==='hyperliquid'?'No fresh reported liquidation thresholds in the current sample.':'Model is warming up; no exposure is seeded.';}catch{}}
async function diagnostics(show=false){try{const r=await fetch('/api/v1/diagnostics');if(!r.ok)throw Error('Diagnostics unavailable');const d=await r.json();learningView(d.learning);$('diagnostic-json').textContent=JSON.stringify(d,null,2);const models=Object.values(d.model_health);const live=models.filter(x=>x.lag_s<900).length;$('coverage').textContent=live+' / 3 models current';$('coverage-sub').textContent='Public feeds · structural priors';if(source==='hyperliquid')sampleSummary(d.hyperliquid);const root=$('diagnostic-summary');root.replaceChildren();for(const f of d.coverage){const row=document.createElement('div');row.className='diag-feed';const a=document.createElement('span');a.textContent=f.exchange+' / '+f.feed;const b=document.createElement('b');b.textContent=f.status;b.className=['live','healthy','available'].includes(f.status)?'':'warn';row.append(a,b);root.append(row);}if(source==='cex'&&data&&Date.now()-lastFetch>30000&&selected('exchange-filters').some(ex=>{const h=d.model_health[ex],v=data.provenance.find(p=>p.exchange===ex);return h&&(!v||Date.parse(h.last_ts)>Date.parse(v.snapshot_ts));}))refresh(true);if(show)$('detail').showModal();}catch(e){if(show)toast(e.message);}}
function websocket(){const ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/stream');ws.onopen=()=>{$('connection').textContent='Live transport';$('connection-dot').className='live';};ws.onmessage=e=>{const m=JSON.parse(e.data);if(source==='hyperliquid'){if(m.type==='hyperliquid'){if(Date.now()-lastFetch>5000)refresh(true);diagnostics();}if(m.type==='mark'&&m.data.exchange==='hyperliquid'){$('mark').textContent=price(m.data.price);$('asof').textContent='Hyperliquid mark · '+new Date(m.data.ts).toLocaleTimeString('en-GB',{timeZone:'UTC',hour12:false})+' UTC';}if(m.type==='learning')diagnostics();return;}if(m.data?.exchange==='hyperliquid'||m.type==='hyperliquid')return;if(m.type==='mark'&&selected('exchange-filters').includes(m.data.exchange)){const d=m.data;$('asof').textContent=d.exchange+' mark · '+new Date(d.ts).toLocaleTimeString('en-GB',{timeZone:'UTC',hour12:false})+' UTC';$('mark').textContent=price(d.price);}
 if(m.type==='heatmap'&&Date.now()-lastFetch>30000)refresh(true);
 if(m.type==='liquidation')diagnostics();
 if(m.type==='learning')diagnostics();
 // Compact profile updates arrive without retransmitting historical matrices.
 if(m.type==='profile'&&data&&selected('leverage-filters').length===5){liveProfiles[m.data.exchange]=m.data;const map=new Map();for(const ex of selected('exchange-filters')){const p=liveProfiles[ex];if(!p)continue;for(const [i,l,s] of p.buckets){const price=Math.floor((p.grid_start+i*p.width)/data.bucket)*data.bucket+(data.bucket-25)/2;const v=map.get(price)||[0,0];v[0]+=l;v[1]+=s;map.set(price,v);}}
 if(selected('exchange-filters').every(ex=>liveProfiles[ex])){let cl=0,cs=0;const keys=[...map.keys()].sort((a,b)=>a-b);const all=keys.map(price=>({price,long_usd:map.get(price)[0],short_usd:map.get(price)[1]}));for(let i=all.length-1;i>=0;i--){cl+=all[i].long_usd;all[i].cumulative_long_usd=cl;}for(const r of all){cs+=r.short_usd;r.cumulative_short_usd=cs;}data.profile=all;summary();chart.setOption({series:[{},{},...['long','short'].map(side=>({data:all.map(r=>[r[cumulative?'cumulative_'+side+'_usd':side+'_usd'],r.price])}))]});}}
 if(m.type==='candle'&&data&&m.data.exchange===data.candle_exchange){const c=m.data,t=Math.floor(c.ts/(data.time_resolution_s*1000))*data.time_resolution_s*1000+data.time_resolution_s*1000;const arr=data.candles,last=arr.at(-1);if(!last||t>last[0])arr.push([t,+c.o,+c.c,+c.l,+c.h]);else if(t===last[0]){last[2]=+c.c;last[3]=Math.min(last[3],+c.l);last[4]=Math.max(last[4],+c.h);}chart.setOption({series:[{},{data:arr.map(r=>[r[0]-data.time_resolution_s*500,...r.slice(1)])}]});}
 };ws.onclose=()=>{$('connection').textContent='Reconnecting';$('connection-dot').className='';setTimeout(websocket,5000);};ws.onerror=()=>ws.close();}
$('lookbacks').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;lookback=b.dataset.value;for(const x of $('lookbacks').children)x.classList.toggle('selected',x===b);liveProfiles={};refresh();});
for(const id of ['bucket','exchange-filters','leverage-filters'])$(id).addEventListener('change',e=>{if(id!=='bucket'&&!selected(id).length){e.target.checked=true;toast('Select at least one '+(id==='exchange-filters'?'exchange':'leverage tier')+'.');return;}liveProfiles={};refresh();});
$('threshold').addEventListener('input',()=>{$('threshold-value').value=$('threshold').value+'%';chartData();});$('mark-overlay').addEventListener('change',chartData);$('profile-mode').onclick=()=>{cumulative=!cumulative;$('profile-mode').textContent=cumulative?'Cumulative ⇄ Bands':'Bands ⇄ Cumulative';$('profile-mode').setAttribute('aria-pressed',String(cumulative));chartData();};
$('fit-liquidity').onclick=()=>{if(data){chart.dispatchAction({type:'dataZoom',dataZoomId:PRICE_ZOOM,start:0,end:100});recordViewport();}};
$('fit-price').onclick=()=>fitPrice();$('reset-view').onclick=()=>fitPrice(true);
$('price-in').onclick=()=>zoomAxis('price',.75);$('price-out').onclick=()=>zoomAxis('price',1/.75);
for(const id of ['diagnostics','methodology','rail-info','learning-details'])$(id).onclick=()=>diagnostics(true);$('close-detail').onclick=()=>$('detail').close();
$('export').onclick=()=>{if(!data)return;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'}));a.download='btc-liquidation-'+source+'.json';a.click();URL.revokeObjectURL(a.href);};
$('fullscreen').onclick=()=>{const el=document.querySelector('.workspace');if(document.fullscreenElement)document.exitFullscreen();else el.requestFullscreen();};
new ResizeObserver(()=>{chart.resize();crispCanvas();scheduleRaster();}).observe($('chart'));sourceView();refresh();diagnostics();websocket();setInterval(()=>{if(!data||source==='hyperliquid')refresh(true);diagnostics();},60000);

function sampleSummary(s){
 if(!s)return;const a=s.current_sample||s.sample;const stale=s.snapshot_age_s==null||s.snapshot_age_s>600;
 $('coverage').textContent=(a?.selected_positions||0)+' sampled BTC positions'+(stale?' · stale':'');
 const share=a?.selected_share_of_market_gross;
 $('coverage-sub').textContent=`${s.checked||0} / ${s.discovered||0} accounts checked`+(share==null?'':` · ${(share*100).toFixed(1)}% gross OI sampled`);
 $('source-description').textContent=`Reported prices · ${a?.null_thresholds||0} null / ${a?.outside_grid_positions||0} outside grid excluded · ≤15m account age · global rank unknown`;
}
function sourceView(){
 const hl=source==='hyperliquid';
 $('exchange-filters').hidden=hl;$('leverage-filters').hidden=hl;$('reported-filters').hidden=!hl;
 $('method-badge').textContent=hl?'REPORTED · SAMPLED':'ESTIMATED';$('instrument-name').textContent=hl?'BTC / USDC':'BTC / USDT';$('mark-venue').textContent=hl?'HYPERLIQUID':'VENUE MEAN';
 $('method-heading').textContent=hl?'Reported Hyperliquid liquidation thresholds, sampled from public accounts.':'Estimated exposure from public Binance, Bybit and OKX market data.';
 $('long-label').textContent=hl?'TRACKED LONG OVERHANG':'EST. LONG OVERHANG';$('short-label').textContent=hl?'TRACKED SHORT OVERHANG':'EST. SHORT OVERHANG';
 $('source-description').textContent=hl?'Reported prices · incomplete account sample · no assumed leverage tiers':'Inferred private positions · leverage priors · uncalibrated exposure';
 $('reading-title').textContent=hl?'Reported positions, measured coverage.':'Exposure, with uncertainty.';
 $('reading-note').textContent=hl?'Bright bands group sampled public positions by reported liquidation price. Account discovery is incomplete. Thresholds can move with margin and other positions. This is not a global top-1,000 census.':'Bright bands show modeled liquidation vulnerability, not resting orders or known private positions. Historical gaps remain blank.';
 $('empty-title').textContent=hl?'Collecting public account observations':'Building the observed market history';
 $('empty-note').textContent=hl?'Position history records from collection onward. Native price candles do not supply past account states.':'The chart appears after paired open-interest and mark-price candles arrive. No synthetic positions are seeded.';
 for(const b of $('sources').children)b.classList.toggle('selected',b.dataset.value===source);
}
$('sources').addEventListener('click',e=>{const b=e.target.closest('button');if(!b||b.dataset.value===source)return;source=b.dataset.value;data=null;dataScope='';renderScope='';liveProfiles={};chart.clear();$('empty').hidden=false;sourceView();refresh();diagnostics();});
