import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {liveTradeView,liveTradeMarkup,lastTradeMarkup,sparkline,signedMoney,signedPercent,price,duration,LIVE_STALE_SECONDS} from './live-trade.mjs';

const NOW=Date.parse('2026-09-23T18:30:00Z');
// A PSQ proxy trade as the backend sends it (pivot/live_trade.py).
function live(extra={}) {
  return {trade_id:'t1',stage:'open',stage_text:'Holding',symbol:'PSQ',direction:'long',label:'Socrates short via PSQ (inverse QQQ)',
    proxy:true,signal_direction:'short',amount:'15.00',
    entry:{price:'35',qty:'0.428571',cost:'15.00',at:'2026-09-23T14:32:00Z',source:'fill'},
    current:{price:'35.35',bid:'35.35',ask:'35.36',at:'2026-09-23T18:29:58Z',source:'quote',age_seconds:2,fresh:true},
    pnl:{dollars:'0.15',per_share:'0.3500',percent:'1.00',r:'1.00'},
    stop:{price:'34.65',result_if_hit:'-0.15',result_percent:'-1.00',distance:'0.7000',distance_percent:'1.98',
      order:{status:'new',text:'working at Alpaca',working:true}},
    target:{price:'35.70',result_if_hit:'0.30',result_percent:'2.00',distance:'0.3500',distance_percent:'0.99'},
    progress:'0.667',session_exit:{at:'2026-09-23T19:55:00Z',close_at:'2026-09-23T20:00:00Z',seconds_left:5100},
    signal:{symbol:'QQQ',direction:'short',entry:'600',stop:'606',target:'588',current:'596.40'},
    exit:null,message:'Managing PSQ (inverse-ETF proxy for the QQQ short): stop $34.65, target $35.70. Broker protection: new.',
    track:[{t:'2026-09-23T14:32:15Z',p:'35.01'},{t:'2026-09-23T16:00:00Z',p:'34.90'},{t:'2026-09-23T18:29:58Z',p:'35.35'}],...extra};
}

test('the card says how much the trade is up, where it sells and when',()=>{
  const view=liveTradeView(live(),{now:NOW});
  assert.equal(view.tone,'up');
  assert.equal(view.pnl,'+$0.15');assert.equal(view.pnlDetail,'+1.00% · +1.00R');
  assert.equal(view.now,'$35.35');assert.equal(view.nowLabel,'Now (bid)');assert.equal(view.cost,'$15.00');assert.equal(view.value,'$15.15');
  assert.match(view.bought,/^Bought 0\.428571 PSQ at \$35\.00 · 10:32 AM ET$/);
  assert.deepEqual(view.target,{price:'$35.70',result:'+$0.30',percent:'+2.00%',away:'0.99% away'});
  assert.equal(view.stop.result,'−$0.15');assert.equal(view.stop.away,'1.98% away');assert.ok(view.stop.working);
  assert.deepEqual(view.close,{at:'3:55 PM ET',left:'in 1 h 25 min'});
  assert.equal(view.updated,'Price just now');assert.equal(view.stale,false);
  const html=liveTradeMarkup(live(),{now:NOW});
  for(const words of ['Live trade · PSQ','Up or down since the purchase','How it will sell','Target $35.70','Stop $34.65',
    'Market close · 3:55 PM ET','working at Alpaca','QQQ <b>short</b> setup','QQQ now $596.40','never holds overnight'])
    assert.ok(html.includes(words),words);
  assert.match(html,/class="ladder-now up" style="left:66\.7%"/);
  assert.match(html,/class="ladder-entry" style="left:33\.3%"/);
});

test('a losing trade reads as down, and time passing ages the price and the countdown',()=>{
  const losing=live({pnl:{dollars:'-0.09',percent:'-0.60',r:'-0.60'},current:{...live().current,price:'34.79',age_seconds:4}});
  const view=liveTradeView(losing,{now:NOW,elapsed:LIVE_STALE_SECONDS});
  assert.equal(view.tone,'down');assert.equal(view.pnl,'−$0.09');assert.equal(view.pnlDetail,'−0.60% · −0.60R');
  assert.equal(view.stale,true);assert.equal(view.updated,'Price 34s ago');
  assert.equal(view.close.left,'in 1 h 24 min');
  assert.match(liveTradeMarkup(losing,{now:NOW,elapsed:40}),/live-dot stale/);
  const position=liveTradeView(live({current:{price:'35.2',source:'position',age_seconds:null}}),{now:NOW});
  assert.equal(position.nowLabel,'Now (last trade)');assert.equal(position.updated,'Price from the Alpaca position');assert.ok(position.stale);
});

test('buying, selling and attention stages say what is happening',()=>{
  const buying=liveTradeView(live({stage:'entering',stage_text:'Buying',entry:{},current:null,pnl:null,progress:null}),{now:NOW});
  assert.equal(buying.headline,'Buying: waiting for the order to fill');assert.equal(buying.pnl,'—');assert.equal(buying.bought,'Waiting for the purchase to fill');
  const selling=liveTradeView(live({stage:'exiting',stage_text:'Selling',exit:{reason:'Target reached; closing the held shares'}}),{now:NOW});
  assert.equal(selling.headline,'Selling: Target reached; closing the held shares');
  assert.match(liveTradeView(live({stage:'attention',exit:{reason:'Check Alpaca'}}),{now:NOW}).headline,/^Needs your attention: Check Alpaca/);
  assert.equal(liveTradeMarkup(null),'');assert.equal(liveTradeView('x'),null);
});

test('backend text is escaped and odd values never render as numbers',()=>{
  const html=liveTradeMarkup(live({symbol:'<img src=x>',message:'<script>x</script>',label:'"q"',entry:{price:'NaN',qty:'abc'}}),{now:NOW});
  assert.ok(!html.includes('<img')&&!html.includes('<script>'));
  assert.equal(price('abc'),'—');assert.equal(price('-1'),'—');assert.equal(signedMoney(null),'—');assert.equal(signedPercent(''),'—');
  assert.equal(signedMoney('0.001'),'$0.00');assert.equal(duration(-1),'—');assert.equal(duration(59),'59s');
});

test('the price line spans the stop and target and needs two quotes',()=>{
  const view=liveTradeView(live(),{now:NOW});
  const svg=sparkline(view);
  assert.match(svg,/<svg class="live-track"/);assert.match(svg,/class="guide stop"/);assert.match(svg,/class="guide target"/);assert.match(svg,/class="guide entry"/);
  assert.match(svg,/<polyline class="price up" points="0\.0,[\d.]+ [\d.]+,[\d.]+ 600\.0,[\d.]+"/);
  assert.match(svg,/aria-label="PSQ price from 10:32 AM ET to 2:29 PM ET: \$35\.01 to \$35\.35"/);
  assert.match(sparkline({...view,track:[{t:'2026-09-23T14:32:15Z',p:'35'}]}),/fills in as new quotes arrive/);
});

test('with no open trade the last trade shows its prices, result and reason',()=>{
  const last={label:'Socrates short via PSQ (inverse QQQ)',symbol:'PSQ',quantity:'0.428571',entry_price:'35',entered_at:'2026-09-23T14:32:00Z',
    exit_price:'35.1200',completed_at:'2026-09-23T19:55:04Z',gross_pnl:'0.05',percent:'0.34',status:'verified_gross',exit_reason:'Closing the position before the session ends'};
  const html=lastTradeMarkup(last,{now:NOW});
  for(const words of ['No open trade','Last trade · today','+$0.05','(+0.34%)','Bought 0.428571 PSQ at $35.00 · 10:32 AM ET, sold at $35.12 · 3:55 PM ET.',
    'Why it sold: Closing the position before the session ends.','fees are not included'])assert.ok(html.includes(words),words);
  assert.match(html,/data-tone="up"/);
  assert.match(lastTradeMarkup({...last,gross_pnl:null,completed_at:'2026-09-20T19:55:04Z'},{now:NOW}),/Result not verified yet[^]*Last trade · |Last trade · Sun, Sep 20/);
  assert.equal(lastTradeMarkup(null),'');assert.equal(lastTradeMarkup({label:'x'}),'');
});

test('the live card sits at the top of the page, outside the strategy view switch',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  const {STRATEGY_VIEW_SECTIONS}=await import('./strategy-families.mjs');
  assert.ok(html.indexOf('id="live-trade"')<html.indexOf('id="socrates-readiness"'));
  assert.ok(!Object.values(STRATEGY_VIEW_SECTIONS).flat().includes('live-trade'));
  assert.ok(!html.includes('id="socrates-trade"'));
  const app=await readFile(new URL('./app.js',import.meta.url),'utf8');
  assert.match(app,/new URL\('live-trade',api\)/);
  // The live read is a GET: no method, body or intent header that could change anything.
  const call=app.slice(app.indexOf("new URL('live-trade',api)"),app.indexOf("new URL('live-trade',api)")+120);
  assert.doesNotMatch(call,/method|X-Pivot-Intent|body/);
});
