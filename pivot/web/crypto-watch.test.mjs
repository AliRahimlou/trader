import test from 'node:test';
import assert from 'node:assert/strict';
import {CRYPTO_MARKETS,cryptoWatchMarkup,cryptoReviewMarkup,rangeFamilyView,portfolioView} from './strategy-families.mjs';

const at='2026-09-19T21:40:20Z',now=Date.parse(at);
const analysis=(symbol,extra={})=>({symbol,source:'alpaca_crypto_us',analyzed_at:at,observed_at:at,last_refresh_at:at,state:'WATCHING',
  coverage:{expected_completed_bars:212,received_completed_bars:212,missing_count:0,opening_range_missing_count:0},candidates:[],...extra});
const state=()=>({portfolio:{global_live_enabled:true,socrates:{enabled:true},range_reversal:{enabled:true,execution_available:true,symbols:['BTC/USD'],target_dollars:'5.00'}},
  entry_allowance:{status:'available',session_day:'2026-09-19',timezone:'America/New_York',limit:2,used:0,remaining:2},
  strategy_families:{range_reversal:{analyses:Object.fromEntries(CRYPTO_MARKETS.map(symbol=>[symbol,analysis(symbol)]))}},
  crypto_execution:{markets:{'BTC/USD':'BTC waiting','SOL/USD':'SOL stale'},message:'Unrelated global message'}});

test('all five markets are watched without implying new trading permissions',()=>{
  const s=state(),before=JSON.stringify(s),html=cryptoWatchMarkup(s,now);
  for(const symbol of CRYPTO_MARKETS)assert.ok(html.includes(symbol));
  assert.equal((html.match(/Not selected for trading/g)||[]).length,4);
  assert.equal((html.match(/On · entries enabled/g)||[]).length,1);
  assert.deepEqual(portfolioView(s).families[1].symbols,['BTC/USD']);
  assert.equal(JSON.stringify(s),before);
});

test('chart totals distinguish unsupported shorts and count events once',()=>{
  const s=state(),a=s.strategy_families.range_reversal.analyses['BTC/USD'];
  a.candidates=[{status:'CONFIRMED',direction:'short',event_id:'one'},{status:'CONFIRMED',direction:'short',event_id:'two'},{status:'CONFIRMED',direction:'short',event_id:'one'}];
  assert.match(cryptoWatchMarkup(s,now),/Today’s chart: 0 buy setups · 2 short setups/);
});

test('incomplete history exposes gaps and does not report zero opportunities',()=>{
  const s=state();s.strategy_families.range_reversal.analyses['ETH/USD']=analysis('ETH/USD',{state:'DATA_WAITING',
    coverage:{expected_completed_bars:212,received_completed_bars:201,missing_count:11,opening_range_missing_count:2}});
  const html=cryptoWatchMarkup(s,now).split('ETH/USD')[1].split('</article>')[0];
  assert.match(html,/201\/212 completed candles · 11 missing/);
  assert.match(html,/2 missing in the opening/);
  assert.match(html,/Setup totals unavailable/);
  assert.doesNotMatch(html,/0 buy setups/);
});

test('market cards use their own blocker and do not label a watched-only market On',()=>{
  const s=state();assert.equal(rangeFamilyView(s,now,'BTC/USD').executionMessage,'BTC waiting');
  const sol=rangeFamilyView(s,now,'SOL/USD');assert.equal(sol.enabled,false);assert.equal(sol.routeStatus,'Not selected for trading');
  assert.match(sol.executionMessage,/Select this market/);
  s.portfolio.range_reversal.symbols.push('SOL/USD');assert.equal(rangeFamilyView(s,now,'SOL/USD').executionMessage,'SOL stale');
});

test('stale watch data cannot report current complete setup totals',()=>{
  assert.doesNotMatch(cryptoWatchMarkup(state(),now+90001),/Today’s chart:/);
});

test('daily checks are separate from reconstructed chart counts and safely escaped',()=>{
  const s=state();s.crypto_execution.decision_review={status:'available',day:'2026-09-19',fresh_setups:{long:0,short:2},order_attempts:0,filled_entries:0,
    latest_by_symbol:{BTC:{symbol:'BTC/USD',checked_at:at,reason:'Short unsupported <script>'}},recent_checks:[]};
  const html=cryptoReviewMarkup(s);assert.match(html,/Earlier chart setups are not counted as live checks/);
  assert.match(html,/entry submissions attempted/);assert.match(html,/entries with confirmed fills/);
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
  assert.match(cryptoReviewMarkup({}),/not available yet/);
});
