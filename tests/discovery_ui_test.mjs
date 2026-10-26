import fs from 'node:fs';
import assert from 'node:assert/strict';
const src=fs.readFileSync('frontend/app.js','utf8');
const begin=src.indexOf('function sampleSummary('),end=src.indexOf('function sourceView(',begin);
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{textContent:''});return elements.get(id);};
const render=new Function('$',src.slice(begin,end)+';return sampleSummary;')($);
render({snapshot_age_s:1,checked:100,discovered:1000,current_sample:{selected_positions:30,plotted_positions:10,
 selected_share_of_market_gross:.32,plotted_share_of_market_gross:.18,null_thresholds:5,outside_grid_positions:15},
 discovery:{never_checked:900,median_position_age_s:120,stale_positions:0,
 first_btc_discoveries:[{source:'pre-expansion',accounts:20},{source:'public_subaccount',accounts:3}]}});
assert.equal($('coverage-sub').textContent,'32.0% gross OI sampled · 18.0% plotted');
assert.equal($('discovery-new').textContent,'3 accounts since queue upgrade');
assert.equal($('discovery-sources').textContent,'subaccounts: 3');
assert.match($('discovery-queue').textContent,/100 \/ 1,000 checked/);
render({snapshot_age_s:1,current_sample:{selected_positions:0,selected_share_of_market_gross:0,plotted_share_of_market_gross:0}});
assert.match($('coverage-sub').textContent,/0.0% gross OI sampled · 0.0% plotted/);
render({snapshot_age_s:null,current_sample:{selected_positions:0}});
assert.match($('coverage-sub').textContent,/— gross OI sampled · — plotted/);
assert.match($('coverage').textContent,/stale/);
console.log('PASS sampled/plotted labels, source attribution, unknown versus zero and stale coverage');
