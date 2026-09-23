// Live trade card (4.5.2): pure render helpers over the live_trade / last_trade payloads.
// The backend computes every number from the app's own trade record and quotes; this module
// only words and draws them. No DOM, no fetch, and nothing here can change a trade or an order.
import {escape as esc} from './model.mjs';

const text=value=>typeof value==='string'?value.trim():'';
const num=value=>{if(value===null||value===undefined||value==='')return null;const n=Number(value);return Number.isFinite(n)?n:null;};
const obj=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:null;
const usd=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
const etTime=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'numeric',minute:'2-digit'});
const etDay=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',weekday:'short',month:'short',day:'numeric'});
const etKey=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
// A live mark older than this reads as delayed on screen (the executor trades only on quotes under 15 s).
export const LIVE_STALE_SECONDS=30;
// How often the page asks for the live view while a trade is open.
export const LIVE_POLL_MS=3000;

export function price(value) {
  const n=num(value);if(n===null||n<=0)return '—';
  // Fractional shares of a ~$35 ETF move in cents; show 2 decimals, 3 for sub-cent averages.
  const digits=Math.abs(n*100-Math.round(n*100))>1e-6?3:2;
  return '$'+n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:digits});
}
export function signedMoney(value) {
  const n=num(value);if(n===null)return '—';
  const rounded=Math.round(n*100)/100;
  return (rounded>0?'+':rounded<0?'−':'')+usd.format(Math.abs(rounded));
}
export function signedPercent(value) {
  const n=num(value);if(n===null)return '—';
  return (n>0?'+':n<0?'−':'')+Math.abs(n).toFixed(2)+'%';
}
function signedR(value) {
  const n=num(value);if(n===null)return '';
  return (n>0?'+':n<0?'−':'')+Math.abs(n).toFixed(2)+'R';
}
export function timeET(at) { const t=Date.parse(at);return Number.isFinite(t)?etTime.format(t)+' ET':'—'; }
function dayET(at,now) {
  const t=Date.parse(at);if(!Number.isFinite(t))return '';
  return etKey.format(t)===etKey.format(now)?'today':etDay.format(t);
}
export function duration(seconds) {
  const s=num(seconds);if(s===null||s<0)return '—';
  if(s<60)return `${Math.floor(s)}s`;
  const minutes=Math.floor(s/60);if(minutes<60)return `${minutes} min`;
  return `${Math.floor(minutes/60)} h ${minutes%60} min`;
}
function shares(value) {
  const n=num(value);if(n===null)return '—';
  return n.toLocaleString('en-US',{maximumFractionDigits:6});
}
export function tone(live) {
  const n=num(obj(live)?.pnl?.dollars);
  return n===null?'flat':n>0.004?'up':n<-0.004?'down':'flat';
}

// Everything the card shows, derived once so tests can check the words without HTML.
export function liveTradeView(live,{now=Date.now(),elapsed=0}={}) {
  live=obj(live);if(!live)return null;
  const entry=obj(live.entry)||{},current=obj(live.current),pnl=obj(live.pnl),stop=obj(live.stop),target=obj(live.target);
  const symbol=text(live.symbol)||'QQQ',long=live.direction!=='short';
  const age=num(current?.age_seconds)===null?null:num(current.age_seconds)+Math.max(0,elapsed);
  const stale=!current||current.source!=='quote'||age===null||age>LIVE_STALE_SECONDS;
  const exit=obj(live.session_exit),left=exit&&num(exit.seconds_left)!==null?Math.max(0,num(exit.seconds_left)-Math.max(0,elapsed)):null;
  const stopLevel=num(stop?.price),targetLevel=num(target?.price),entryPrice=num(entry.price);
  let entryAt=null;
  if(stopLevel!==null&&targetLevel!==null&&entryPrice!==null&&stopLevel!==targetLevel)
    entryAt=Math.min(1,Math.max(0,(long?entryPrice-stopLevel:stopLevel-entryPrice)/(long?targetLevel-stopLevel:stopLevel-targetLevel)));
  const order=obj(stop?.order);
  const view={
    symbol,stage:text(live.stage),stageText:text(live.stage_text)||'Managing',label:text(live.label)||`Socrates ${symbol}`,
    proxy:live.proxy===true,tone:tone(live),
    bought:entryPrice!==null?`Bought ${shares(entry.qty)} ${symbol} at ${price(entry.price)}${entry.at?` · ${timeET(entry.at)}`:''}`:'Waiting for the purchase to fill',
    cost:entry.cost?price(entry.cost):'—',
    now:current?price(current.price):'—',
    nowLabel:current?.source==='quote'?(long?'Now (bid)':'Now (ask)'):'Now (last trade)',
    updated:!current?'Waiting for a live price':current.source==='quote'?(age<=2?'Price just now':`Price ${duration(age)} ago`):'Price from the Alpaca position',
    stale,
    pnl:pnl?signedMoney(pnl.dollars):'—',
    pnlDetail:pnl?[signedPercent(pnl.percent),signedR(pnl.r)].filter(Boolean).join(' · '):'Shown once the purchase has filled',
    value:pnl&&num(entry.cost)!==null&&num(pnl.dollars)!==null?price(num(entry.cost)+num(pnl.dollars)):'—',
    progress:num(live.progress),entryAt,levels:{stop:stopLevel,target:targetLevel,entry:entryPrice},
    stopPrice:price(stop?.price),targetPrice:price(target?.price),
    target:target?{price:price(target.price),result:signedMoney(target.result_if_hit),percent:signedPercent(target.result_percent),
      away:num(target.distance_percent)===null?'':num(target.distance_percent)<=0?'reached':`${Math.abs(num(target.distance_percent)).toFixed(2)}% away`}:null,
    stop:stop?{price:price(stop.price),result:signedMoney(stop.result_if_hit),percent:signedPercent(stop.result_percent),
      away:num(stop.distance_percent)===null?'':num(stop.distance_percent)<=0?'reached':`${Math.abs(num(stop.distance_percent)).toFixed(2)}% away`,
      order:text(order?.text)||'status not confirmed yet',working:order?.working===true}:null,
    close:exit?{at:timeET(exit.at),left:left===null?'':left<=0?'now':`in ${duration(left)}`}:null,
    signal:obj(live.signal),message:text(live.message),exitReason:text(obj(live.exit)?.reason),
    track:Array.isArray(live.track)?live.track:[],
  };
  view.headline=view.stage==='entering'?'Buying: waiting for the order to fill':view.stage==='exiting'?`Selling: ${view.exitReason||'closing the position'}`:
    view.stage==='attention'?`Needs your attention: ${view.exitReason||'check Alpaca'}`:view.label;
  return view;
}

// Price since the entry with the entry, stop and target drawn as lines. Pure SVG string.
export function sparkline(view) {
  const points=(view?.track||[]).map(row=>({t:Date.parse(row?.t),p:num(row?.p)})).filter(row=>Number.isFinite(row.t)&&row.p!==null&&row.p>0);
  if(points.length<2)return '<p class="live-track-empty">The price line fills in as new quotes arrive (one point every 15 seconds).</p>';
  const levels=Object.entries(view.levels||{}).filter(([,v])=>v!==null&&v>0);
  const values=[...points.map(row=>row.p),...levels.map(([,v])=>v)];
  let low=Math.min(...values),high=Math.max(...values);
  const pad=(high-low)*0.08||high*0.001;low-=pad;high+=pad;
  const W=600,H=140,t0=points[0].t,t1=points.at(-1).t,span=Math.max(1,t1-t0);
  const x=t=>((t-t0)/span*W).toFixed(1),y=p=>((high-p)/(high-low)*H).toFixed(1);
  const line=points.map(row=>`${x(row.t)},${y(row.p)}`).join(' ');
  const guides=levels.map(([kind,v])=>`<line class="guide ${kind}" x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" vector-effect="non-scaling-stroke"/>`).join('');
  const first=points[0],last=points.at(-1);
  return `<svg class="live-track" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(`${view.symbol} price from ${timeET(new Date(first.t).toISOString())} to ${timeET(new Date(last.t).toISOString())}: ${price(first.p)} to ${price(last.p)}`)}">
    ${guides}<polyline class="price ${view.tone}" points="${line}" fill="none" vector-effect="non-scaling-stroke"/></svg>
    <p class="live-track-axis"><span>${esc(timeET(new Date(first.t).toISOString()))}</span><span>${esc(timeET(new Date(last.t).toISOString()))}</span></p>`;
}

function ladder(view) {
  if(view.progress===null)return '';
  const at=value=>`${(value*100).toFixed(1)}%`;
  return `<div class="live-ladder" role="img" aria-label="${esc(`Price is ${Math.round(view.progress*100)}% of the way from the stop ${view.stopPrice} to the target ${view.targetPrice}`)}">
    <div class="ladder-track">${view.entryAt!==null?`<i class="ladder-entry" style="left:${at(view.entryAt)}" title="Entry"></i>`:''}<i class="ladder-now ${view.tone}" style="left:${at(view.progress)}"></i></div>
    <div class="ladder-labels"><span class="stop">Stop ${esc(view.stopPrice)}</span>${view.entryAt!==null&&view.entryAt>0.2&&view.entryAt<0.8?`<span class="entry" style="left:${at(view.entryAt)}">Entry</span>`:''}<span class="target">Target ${esc(view.targetPrice)}</span></div></div>`;
}

export function liveTradeMarkup(live,options={}) {
  const view=liveTradeView(live,options);if(!view)return '';
  const plan=[];
  if(view.target)plan.push(`<li class="plan-target"><div><b>Target ${esc(view.target.price)}</b><span>${esc(view.target.result)} (${esc(view.target.percent)}) if reached${view.target.away?` · ${esc(view.target.away)}`:''}</span></div>
    <small>The app sells at market as soon as the ${esc(view.symbol)} bid reaches the target. It checks every 5 seconds.</small></li>`);
  if(view.stop)plan.push(`<li class="plan-stop"><div><b>Stop ${esc(view.stop.price)}</b><span>${esc(view.stop.result)} (${esc(view.stop.percent)}) if hit${view.stop.away?` · ${esc(view.stop.away)}`:''}</span></div>
    <small>A stop order at Alpaca sells automatically if the price falls to the stop. Status: <span class="${view.stop.working?'ok':'wait'}">${esc(view.stop.order)}</span>.</small></li>`);
  plan.push(`<li class="plan-close"><div><b>Market close${view.close?` · ${esc(view.close.at)}`:''}</b><span>${view.close?esc(view.close.left):'Next session'}</span></div>
    <small>If neither is hit, the app sells at market 5 minutes before the close. Socrates never holds overnight.</small></li>`);
  const signal=view.proxy&&view.signal?`<p class="help live-proxy">This is a QQQ <b>short</b> setup. The account cannot short, so the app bought PSQ, which rises when QQQ falls. QQQ now ${esc(price(view.signal.current))} · QQQ entry ${esc(price(view.signal.entry))} · QQQ stop ${esc(price(view.signal.stop))} · QQQ target ${esc(price(view.signal.target))}.</p>`:'';
  return `<article class="card live-trade" data-tone="${view.tone}" data-stage="${esc(view.stage)}" aria-labelledby="live-trade-title">
  <div class="live-head"><h2 id="live-trade-title"><span class="live-dot${view.stale?' stale':''}" aria-hidden="true"></span>Live trade · ${esc(view.symbol)}</h2><span class="pill">${esc(view.stageText)}</span><span class="live-updated${view.stale?' stale':''}">${esc(view.updated)}</span></div>
  <p class="live-headline">${esc(view.headline)}</p>
  <div class="live-numbers">
    <div class="live-pnl"><span>Up or down since the purchase</span><strong>${esc(view.pnl)}</strong><em>${esc(view.pnlDetail)}</em></div>
    <div><span>${esc(view.nowLabel)}</span><b>${esc(view.now)}</b></div>
    <div><span>Paid</span><b>${esc(view.cost)}</b></div>
    <div><span>Worth now</span><b>${esc(view.value)}</b></div>
  </div>
  <p class="live-bought">${esc(view.bought)}</p>
  ${ladder(view)}
  <div class="live-chart">${sparkline(view)}</div>
  <h3 class="live-plan-title">How it will sell</h3>
  <ul class="live-plan">${plan.join('')}</ul>
  ${signal}
  ${view.message?`<p class="help">${esc(view.message)}</p>`:''}
</article>`;
}

export function lastTradeMarkup(last,{now=Date.now()}={}) {
  last=obj(last);if(!last||!last.entry_price)return '';
  const gross=num(last.gross_pnl),result=gross===null?'Result not verified yet':signedMoney(gross);
  const toneName=gross===null?'flat':gross>0.004?'up':gross<-0.004?'down':'flat';
  const sold=last.exit_price?`sold at ${price(last.exit_price)}${last.completed_at?` · ${timeET(last.completed_at)}`:''}`:'sale price not confirmed';
  const day=dayET(last.completed_at||last.entered_at,now);
  return `<article class="card live-trade last-trade" data-tone="${toneName}" aria-labelledby="last-trade-title">
  <div class="live-head"><h2 id="last-trade-title">No open trade</h2><span class="pill">Last trade${day?` · ${esc(day)}`:''}</span></div>
  <p class="live-headline">${esc(text(last.label)||'Socrates trade')}: <strong class="last-result">${esc(result)}</strong>${last.percent?` (${esc(signedPercent(last.percent))})`:''}</p>
  <p class="live-bought">Bought ${esc(shares(last.quantity))} ${esc(text(last.symbol))} at ${esc(price(last.entry_price))}${last.entered_at?` · ${esc(timeET(last.entered_at))}`:''}, ${esc(sold)}.</p>
  ${text(last.exit_reason)?`<p class="help">Why it sold: ${esc(text(last.exit_reason))}.</p>`:''}
  ${gross!==null?'<p class="help">Gross result from Alpaca fills; fees are not included.</p>':''}
</article>`;
}
