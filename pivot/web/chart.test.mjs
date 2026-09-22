import test from 'node:test';
import assert from 'node:assert/strict';
import {chartWidth,priceScale,ticks,candleLayout,sessionMarks,levelLabel,chartModel,priceChartModel,vixChartModel,drawPriceChart,drawVixChart,levelsTableRows,chartStatus,validBars,dayLabel} from './chart.mjs';
// A recording 2D context: every method call and property assignment is kept in order.
function fakeContext(){
  const calls=[];
  const ctx=new Proxy({},{get:(target,name)=>name==='calls'?calls:name==='measureText'?text=>({width:String(text).length*5}):(...args)=>{calls.push([name,...args]);},
    set:(target,name,value)=>{calls.push(['set',name,value]);return true;}});
  return ctx;
}
const texts=ctx=>ctx.calls.filter(c=>c[0]==='fillText').map(c=>c[1]);
const bar=(t,o,h,l,c)=>({t,o,h,l,c});
const hour=n=>new Date(Date.UTC(2026,8,22,13,30)+n*3600000).toISOString();
const bars=[bar(hour(1),600,602,599,601),bar(hour(2),601,603,600.5,602.5),bar(hour(3),602.5,604,601,601.5),bar(hour(4),601.5,602,598,599),bar(hour(5),599,601,598.5,600.8)];
const data=()=>({available:true,symbol:'QQQ',at:hour(5),reference:600.8,bars:{'60':bars,'240':[bar(hour(4),600,604,598,600.8)]},
  levels:[{low:601,high:602.2,source:'4h repeated interaction',established_at:hour(-30),touches:3},{low:640,high:641.3,source:'4h repeated interaction',established_at:hour(-10),touches:2},
    {low:596.2,high:596.2,source:'previous-day low',established_at:hour(-20)},{low:611.5,high:611.5,source:'previous-day high',established_at:hour(-20)}],
  previous_day:{high:611.5,low:596.2},
  events:[{method:'four_hour_retest',label:'4-hour areas / hourly break and retest',state:'CONFIRMING',zone:{low:601,high:602.2,source:'4h repeated interaction',touches:3},
    origin_at:hour(2),retest_at:hour(4),direction:'long',entry:600.8,stop:597.9,target:611.5,selected:true}],
  vix:{bars15:[bar(hour(1),18,18.4,17.9,18.3),bar(hour(2),18.3,18.5,18.1,18.2),bar(hour(3),18.2,18.3,17.8,17.9)],zones:[{low:18.3,high:18.7,source:'4h repeated pivot',touches:2}],
    reaction:{zone:{low:18.3,high:18.7,source:'4h repeated pivot'},at:hour(3),direction:'short'}},
  leaders:{}});
test('canvas width fits a phone container by scrolling instead of shrinking below the minimum',()=>{
  assert.equal(chartWidth(358),560);assert.equal(chartWidth(900.7),900);assert.equal(chartWidth(NaN),560);assert.equal(chartWidth(300,400),400);
});
test('price scale maps the padded range onto the plot height and inverts',()=>{
  const scale=priceScale(598,604,10,210);
  assert.equal(scale.min,597.7);assert.equal(scale.max,604.3);
  assert.equal(scale.y(scale.max),10);assert.equal(scale.y(scale.min),210);
  assert.ok(Math.abs(scale.price(scale.y(600))-600)<1e-9);
  assert.ok(scale.y(604)<scale.y(598),'higher prices sit higher on the canvas');
  const flat=priceScale(600,600,0,100);assert.ok(flat.max>flat.min,'a flat range still has height');
  assert.equal(priceScale(NaN,1,0,10),null);
});
test('ticks use round steps inside the range',()=>{
  assert.deepEqual(ticks(597.7,604.3,5),[598,600,602,604]);
  assert.deepEqual(ticks(597.7,604.3,8),[598,599,600,601,602,603,604]);
  assert.deepEqual(ticks(17.8,18.7,4),[18,18.25,18.5],'quarter steps keep two decimals');
  assert.deepEqual(ticks(17.8,18.7,8),[17.8,18,18.2,18.4,18.6],'a 0.11 raw step rounds up to 0.2');
  assert.deepEqual(ticks(5,5),[]);
});
test('candles are centred in equal slots with a body narrower than the slot',()=>{
  const layout=candleLayout(5,8,508);
  assert.equal(layout.slot,100);assert.equal(layout.x(0),58);assert.equal(layout.x(4),458);assert.ok(layout.width<layout.slot&&layout.width>=1);
  assert.equal(candleLayout(0,8,508).slot,0);
});
test('session marks appear where the New York date changes',()=>{
  const rows=[bar(hour(1),1,2,1,1),bar(hour(2),1,2,1,1),bar(hour(25),1,2,1,1),bar(hour(26),1,2,1,1)];
  assert.deepEqual(sessionMarks(rows),[{index:0,label:'Sep 22'},{index:2,label:'Sep 23'}]);
  assert.equal(dayLabel('not-a-date'),'');
});
test('band labels carry the source and touch count, an app interpretation of hand-drawn lines',()=>{
  assert.equal(levelLabel({source:'4h repeated interaction',touches:3}),'4h repeated interaction · 3 touches');
  assert.equal(levelLabel({source:'4h repeated interaction',touches:1}),'4h repeated interaction · 1 touch');
  assert.equal(levelLabel({source:'previous-day high'}),'previous-day high');
  assert.equal(levelLabel(null),'area');
});
test('the model keeps only bands inside the visible range, the previous-day lines and the selected event',()=>{
  const model=priceChartModel(data(),'60',600,300);
  assert.equal(model.bars.length,5);
  assert.deepEqual(model.bands.map(b=>b.low),[596.2,601,611.5],'the 640 band is off-screen');
  assert.deepEqual(model.lines.map(l=>l.label),['Prev-day high','Prev-day low']);
  assert.equal(model.event.originIndex,1);assert.equal(model.event.retestIndex,3);
  assert.ok(model.scale.min<=597.9&&model.scale.max>=611.5,'stop and target are inside the range');
  assert.equal(model.marks.length,1);
  assert.equal(priceChartModel(data(),'240',600,300).bars.length,1);
  assert.equal(priceChartModel(data(),'bogus',600,300).bars.length,5,'unknown timeframes fall back to hourly');
});
test('invalid candles and levels are dropped before drawing',()=>{
  assert.equal(validBars([bar(hour(1),1,0.5,2,1),bar('nope',1,2,1,1),null,bar(hour(1),1,2,1,1)]).length,1);
  const model=chartModel({bars:[],width:400,height:200});
  assert.equal(model.scale,null);assert.equal(model.message,'Waiting for candles');
  const ctx=fakeContext();drawPriceChart(ctx,{bars:{'60':[]}},{width:400,height:200});
  assert.deepEqual(texts(ctx),['Waiting for candles']);
});
test('drawing places each candle body and wick at its slot and labels bands, lines, event and stop/target',()=>{
  const ctx=fakeContext();
  const model=drawPriceChart(ctx,data(),{timeframe:'60',width:600,height:300});
  const labels=texts(ctx);
  assert.ok(labels.includes('4h repeated interaction · 3 touches'));
  assert.ok(labels.includes('previous-day low'));
  assert.ok(labels.includes('Prev-day high 611.50')&&labels.includes('Prev-day low 596.20'));
  assert.ok(labels.includes('Stop 597.90 · app choice')&&labels.includes('Target 611.50 · app choice'));
  assert.ok(labels.includes('4-hour areas / hourly break and retest · confirming · long'));
  assert.ok(labels.includes('break')&&labels.includes('retest'));
  assert.ok(labels.includes('Sep 22'));
  const bodyIndexes=ctx.calls.map((c,i)=>c[0]==='fillRect'?i:-1).filter(i=>i>=0).slice(-5);
  const fillBefore=at=>{for(let i=at;i>=0;i--)if(ctx.calls[i][0]==='set'&&ctx.calls[i][1]==='fillStyle')return ctx.calls[i][2];};
  bodyIndexes.forEach((at,index)=>{
    const [,x,y,w,h]=ctx.calls[at];
    assert.ok(Math.abs(x+w/2-(Math.round(model.layout.x(index))+0.5))<1e-9,`body ${index} centred on its slot`);
    assert.ok(w===model.layout.width&&h>=1);
    const top=model.scale.y(Math.max(bars[index].o,bars[index].c));
    assert.ok(Math.abs(y-top)<1e-9);
  });
  assert.deepEqual(bodyIndexes.map(at=>fillBefore(at)==='#517b46'?'up':'down'),['up','up','down','down','up']);
  const wicks=ctx.calls.filter(c=>c[0]==='moveTo').slice(-5);
  wicks.forEach((call,index)=>assert.ok(Math.abs(call[2]-model.scale.y(bars[index].h))<1e-9,'wick starts at the high'));
});
test('the VIX chart highlights the reaction area and reports missing candles',()=>{
  const ctx=fakeContext();
  const model=drawVixChart(ctx,data(),{width:600,height:200});
  assert.equal(model.bars.length,3);assert.equal(model.bands.length,1);assert.equal(model.bands[0].fill,'#e0b84e88');
  assert.ok(texts(ctx).includes('4h repeated pivot · 2 touches'));
  const extra=vixChartModel({vix:{bars15:data().vix.bars15,zones:[],reaction:{zone:{low:18.0,high:18.2,source:'4h repeated pivot'}}}},600,200);
  assert.equal(extra.bands.length,1,'a reaction area missing from the zone list is still drawn');
  const empty=fakeContext();drawVixChart(empty,{vix:{}},{width:300,height:100});
  assert.deepEqual(texts(empty),['Waiting for actual VIX candles']);
});
test('the levels table sorts nearest first with formatted bands, dates and touches',()=>{
  const rows=levelsTableRows(data().levels,600.8);
  assert.deepEqual(rows.map(r=>r.band),['$601.00 – $602.20','$596.20','$611.50','$640.00 – $641.30']);
  assert.deepEqual(rows.map(r=>r.distance),['+0.03%','−0.77%','+1.78%','+6.52%']);
  assert.deepEqual(rows.map(r=>r.touches),['3','—','—','2']);
  assert.equal(rows[0].established,'Sep 21');
  assert.equal(levelsTableRows(data().levels,null)[0].distance,'—');
  assert.equal(levelsTableRows([{low:600,high:601}],600.5)[0].distance,'at level');
  assert.deepEqual(levelsTableRows('nope',1),[]);
});
test('status text explains missing, stale and current chart data',()=>{
  const now=Date.parse(hour(5))+30000;
  assert.match(chartStatus(null,now).text,/not available yet/);
  assert.equal(chartStatus({available:false,detail:'Waiting for the first completed analysis'},now).text,'Waiting for the first completed analysis');
  assert.equal(chartStatus(data(),now).text,'5 hourly candles · 4 established areas · analysis under a minute old.');
  assert.equal(chartStatus(data(),now).tone,'pass');
  const stale=chartStatus(data(),now+10*60000);
  assert.equal(stale.tone,'wait');assert.match(stale.text,/10 min old · waiting for a fresh analysis/);
  assert.equal(chartStatus({available:true,bars:{'60':[]}},now).text,'Waiting for completed QQQ candles.');
});
// app.js wiring: the real script runs against a fake canvas; it must fetch the chart on the
// existing poll cycle, size the canvas for the container, redraw on the toggle, and never write.
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {createDisplayClock,escape as esc} from './model.mjs';
import {bindStrategyView,portfolioView} from './strategy-families.mjs';
import * as chart from './chart.mjs';
const source=(await readFile(new URL('./app.js',import.meta.url),'utf8')).replace(/^import .*?;\n/gm,'').replace('refresh();setInterval(refresh,10000);','');
function appHarness({container=358,canvas=true}={}){
  const elements=new Map(),calls=[],contexts=new Map(),windowListeners={};
  const element=id=>{
    if(!elements.has(id)){
      const node={id,hidden:false,value:'',textContent:'',innerHTML:'',className:'',dataset:id==='chart-1h'?{timeframe:'60'}:id==='chart-4h'?{timeframe:'240'}:{},attributes:{},listeners:{},style:{},width:0,height:0,
        addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(name,value){this.attributes[name]=value;},querySelectorAll(){return [];},querySelector(){return element(id+':tbody');},parentElement:{clientWidth:container}};
      if(canvas&&(id==='qqq-chart'||id==='vix-chart')){const ctx=fakeContext();contexts.set(id,ctx);node.getContext=()=>ctx;}
      elements.set(id,node);
    }
    return elements.get(id);
  };
  let frame=null;
  const context=vm.createContext({URL,Date,esc,createDisplayClock,portfolioView,...chart,bindStrategyView:(document,options)=>bindStrategyView(document,{...options,storage:()=>null}),
    location:{hostname:'example.test',port:''},AbortSignal:{timeout:()=>({})},setInterval(){},
    requestAnimationFrame:fn=>{frame=fn;return 1;},cancelAnimationFrame(){},getComputedStyle:()=>({getPropertyValue:name=>name==='--chart-up'?' #112233 ':''}),
    window:{devicePixelRatio:2,addEventListener(type,fn){windowListeners[type]=fn;}},
    document:{baseURI:'https://example.test/pivot/',getElementById:element,querySelector:()=>({dataset:{}})},
    fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url:String(url),options,resolve,reject}))});
  vm.runInContext(source+`\nrender=()=>{};renderStrategyFamilies=()=>{};renderStrategyControls=()=>{};globalThis.app={refresh,state:()=>({chartData,chartTimeframe})};`,context);
  return {app:context.app,calls,element,contexts,windowListeners,frame:()=>{const fn=frame;frame=null;fn?.();},fire:(id,type='click')=>element(id).listeners[type]?.({preventDefault(){}})};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('app.js fetches the chart after each snapshot, sizes the canvas for a phone and redraws on the toggle',async()=>{
  const h=appHarness();
  h.app.refresh();
  assert.equal(h.calls.length,1);
  h.calls[0].resolve({ok:true,json:async()=>({server_at:hour(5),live_enabled:false,settings:{target_dollars:'15.00'}})});await flush();
  assert.equal(h.calls.length,2);assert.equal(h.calls[1].url,'https://example.test/pivot/api/chart');assert.equal(h.calls[1].options.method,undefined);
  h.calls[1].resolve({ok:true,json:async()=>data()});await flush();h.frame();
  assert.equal(h.app.state().chartData.symbol,'QQQ');
  const qqq=h.element('qqq-chart');
  assert.equal(qqq.style.width,'560px','a 358px phone container scrolls a 560px chart');assert.equal(qqq.width,1120);assert.equal(qqq.height,600);
  assert.ok(h.contexts.get('qqq-chart').calls.some(c=>c[0]==='setTransform'&&c[1]===2));
  assert.ok(texts(h.contexts.get('qqq-chart')).includes('4h repeated interaction · 3 touches'));
  assert.ok(h.contexts.get('qqq-chart').calls.some(c=>c[0]==='set'&&c[1]==='fillStyle'&&c[2]==='#112233'),'theme tokens reach the canvas');
  assert.ok(texts(h.contexts.get('vix-chart')).includes('4h repeated pivot · 2 touches'));
  assert.match(h.element('levels-table:tbody').innerHTML,/\$601\.00 – \$602\.20/);
  assert.equal(h.element('chart-status').className,'help pass');
  assert.equal(h.element('chart-1h').attributes['aria-pressed'],'true');
  h.fire('chart-4h');h.frame();
  assert.equal(h.app.state().chartTimeframe,'240');assert.equal(h.element('chart-4h').attributes['aria-pressed'],'true');assert.equal(h.element('chart-1h').attributes['aria-pressed'],'false');
  assert.equal(typeof h.windowListeners.resize,'function');
  assert.equal(h.calls.filter(c=>['PUT','POST'].includes(c.options?.method)).length,0);
});
test('an unavailable chart shows the service reason and a connection loss clears the drawing',async()=>{
  const h=appHarness();
  h.app.refresh();h.calls[0].resolve({ok:true,json:async()=>({server_at:hour(5)})});await flush();
  h.calls[1].resolve({ok:false,json:async()=>({available:false,detail:'Waiting for the first completed analysis'})});await flush();h.frame();
  assert.equal(h.element('chart-status').textContent,'Waiting for the first completed analysis');
  assert.deepEqual(texts(h.contexts.get('qqq-chart')),['Waiting for candles']);
  h.app.refresh();h.calls[2].reject(Error('offline'));await flush();h.frame();
  assert.equal(h.app.state().chartData,null);assert.match(h.element('chart-status').textContent,/not available yet/);
});
test('without canvas support the wiring stays silent and sends no chart request',async()=>{
  const h=appHarness({canvas:false});
  h.app.refresh();h.calls[0].resolve({ok:true,json:async()=>({server_at:hour(5)})});await flush();
  assert.equal(h.calls.length,1);
});
