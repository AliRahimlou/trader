import {escape as esc, money} from './model.mjs';

export const STRATEGY_VIEW_KEY='pivot.strategy-view.v1';
const VIEWS=new Set(['socrates','range_reversal','all']);
export const CRYPTO_MARKETS=['BTC/USD','ETH/USD','SOL/USD','LINK/USD','XRP/USD'];
export const normalizeStrategyView=value=>VIEWS.has(value)?value:'socrates';
export const STRATEGY_VIEW_SECTIONS={
  socrates:['socrates-control','socrates-family','session-review','socrates-sidebar','socrates-purchase','socrates-data','socrates-rules','socrates-results'],
  range_reversal:['range-control','range-family','crypto-management','crypto-watchlist','crypto-review'],
};

// This preference changes the complete strategy workspace. It has no API, broker, or
// settings dependency, and is safe when browser storage is unavailable.
export function bindStrategyView(document,{storage=()=>globalThis.localStorage,onChange=()=>{}}={}) {
  let selected='socrates';
  try { selected=normalizeStrategyView(storage()?.getItem(STRATEGY_VIEW_KEY)); } catch {}
  const selector=document.getElementById('strategy-view');
  function display() {
    selector.value=selected;
    for(const [family,ids] of Object.entries(STRATEGY_VIEW_SECTIONS)) {
      for(const id of ids)document.getElementById(id).hidden=selected!=='all' && selected!==family;
    }
    document.getElementById('strategy-workspace').dataset.view=selected;
    document.getElementById('family-control-grid').dataset.view=selected;
    document.getElementById('strategy-controls-title').textContent=selected==='all'?'All strategy controls':selected==='socrates'?'Socrates controls':'4H Range Reversal controls';
    document.getElementById('strategy-view-note').textContent=selected==='all'
      ? 'Showing all strategies. The controls below show which are enabled. Changing this view does not change trading.'
      : selected==='range_reversal'
      ? 'Viewing 4H Range Reversal. Socrates keeps its saved setting. Changing this view does not change trading.'
      : 'Viewing Socrates. 4H Range Reversal keeps its saved setting. Changing this view does not change trading.';
  }
  selector.addEventListener('change',()=>{
    selected=normalizeStrategyView(selector.value);
    try { storage()?.setItem(STRATEGY_VIEW_KEY,selected); } catch {}
    display();
    onChange(selected);
  });
  display();
  return {value:()=>selected};
}

export function globalCryptoAlert(snapshot) {
  const messages=(Array.isArray(snapshot?.crypto_execution?.incidents)?snapshot.crypto_execution.incidents:[])
    .map(row=>typeof row?.message==='string'?row.message:null).filter(Boolean);
  return messages.length?'4H Range Reversal needs attention: '+[...new Set(messages)].join(' '):'';
}

export function portfolioView(snapshot) {
  const p=snapshot?.portfolio;
  const globalOn=typeof p?.global_live_enabled==='boolean'?p.global_live_enabled:snapshot?.live_enabled===true;
  const families=[['socrates','Socrates'],['range_reversal','4H Range Reversal']].map(([id,label])=>{
    const raw=p?.[id] || {};
    const enabled=p?raw.enabled===true:id==='socrates';
    const available=id==='socrates'?snapshot?.execution_available===true:raw.execution_available===true;
    return {id,label,enabled,available,target:raw.target_dollars || (id==='socrates'?snapshot?.settings?.target_dollars:'5.00'),
      symbols:id==='socrates'?['QQQ']:(Array.isArray(raw.symbols)?raw.symbols.filter(s=>CRYPTO_MARKETS.includes(s)):['BTC/USD']),
      status:!enabled?'Off · no new entries':raw.review_required===true?'Review updated rules':!globalOn?'Selected · global Live Off':!available?'Enabled · execution unavailable':'On · entries enabled',
      reviewRequired:raw.review_required===true,
      policyVersion:raw.policy_version,policy:Array.isArray(raw.policy_summary)?raw.policy_summary:[],
      shortSupported:raw.capabilities?.short===true};
  });
  return {configured:!!p,globalOn,families,enabled:families.filter(f=>f.enabled),
    scope:families.filter(f=>f.enabled).map(f=>f.label).join(' + ') || 'No strategies enabled'};
}

export function portfolioStatus(snapshot,fallback) {
  const p=portfolioView(snapshot);
  if(!p.configured)return fallback;
  const trades=Array.isArray(snapshot.crypto_execution?.trades)?snapshot.crypto_execution.trades:[];
  const cryptoWorker=(snapshot.worker_health?.workers || []).find(row=>row.name==='crypto_execution');
  if(snapshot.crypto_worker_error || (cryptoWorker && ['stalled','stopped','error'].includes(cryptoWorker.status)
      && (p.families[1].enabled || trades.length)))return {title:'Crypto execution needs attention',
    text:snapshot.crypto_worker_error || 'The crypto worker is not progressing. Check its positions and broker orders; the app cannot confirm that exits are being managed.'};
  const gate=snapshot.deployment_gate;
  if(snapshot.review_required || snapshot.execution?.review_required || (gate?.configured===true &&
      (gate.locked===true || gate.hold_present===true || gate.error)))return fallback;
  const open=trades.length+(snapshot.execution?.trade?1:0);
  return {title:open?`Managing ${open} position${open===1?'':'s'}`:p.globalOn?`Live money on · ${p.scope}`:'Live money off · new entries paused',
    text:p.globalOn?`${p.scope}. Each enabled strategy waits for its own data and entry checks. Existing positions continue their exits.`:
      'No strategy can open a new position. Existing positions continue their exits; saved strategy selections are preserved.'};
}

export function strategySettingsMatch(snapshot,payload) {
  return !!snapshot?.portfolio && Object.entries(payload).every(([id,settings])=>{
    const saved=snapshot.portfolio[id];
    if(!saved)return false;
    return Object.entries(settings).every(([key,value])=>key==='policy_version'
      ? typeof value==='string' && value.length>0 && saved.policy_version===value
        && (saved.enabled===false || (saved.enabled===true && saved.review_required===false))
      : key==='symbols'
      ? Array.isArray(saved.symbols) && [...saved.symbols].sort().join('|')===[...value].sort().join('|')
      : key==='target_dollars'?Number(saved[key])===Number(value)
      : saved[key]===value);
  });
}

const knownQuantity=value=>(typeof value==='number' || (typeof value==='string' && value.trim()!==''))
  && Number.isFinite(Number(value)) && Number(value)>=0;

export function cryptoQuantityLabel(trade) {
  for(const [field,label] of [['remaining_qty','Remaining'],['net_entry_qty','Net acquired'],['filled_qty','Gross filled']]) {
    const value=trade?.[field];
    if(knownQuantity(value))return `${label}: ${value} units`;
  }
  return 'Quantity pending';
}

export function positionUnit(position) {
  const symbol=typeof position?.symbol==='string'?position.symbol.toUpperCase():'';
  return position?.asset_class==='crypto' || CRYPTO_MARKETS.some(s=>s===symbol||s.replace('/','')===symbol)?'units':'shares';
}

export function cryptoHistoryMarkup(history) {
  const rows=Array.isArray(history)?history.filter(row=>row && typeof row==='object').slice(0,10):[];
  return rows.length?rows.map(row=>{
    const gross=knownQuantity(row.filled_qty),net=knownQuantity(row.net_entry_qty);
    const outcome=(gross && Number(row.filled_qty)>0) || (net && Number(row.net_entry_qty)>0)?'Closed position':gross && Number(row.filled_qty)===0?'No fill':'Completed attempt';
    const quantities=[gross?`Gross filled: ${row.filled_qty} units`:null,net?`Net acquired: ${row.net_entry_qty} units`:null].filter(Boolean).join(' · ');
    return `<article><b>${esc(row.symbol || 'Crypto')} · ${outcome}</b><p class="help">${esc(time(row.completed_at))}</p><p>${esc(row.reason || 'Outcome recorded; fill details are unavailable.')}</p>${quantities?`<p class="help">${esc(quantities)}</p>`:''}</article>`;
  }).join(''):'<p>No completed crypto outcomes recorded.</p>';
}

const fresh=(at,now)=>{
  const elapsed=now-Date.parse(at);
  return Number.isFinite(elapsed) && elapsed>=0 && elapsed<=90000;
};
const positive=value=>typeof value==='number' && Number.isFinite(value) && value>0;
const time=value=>Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET'
  : 'Not available';

export function rangeFamilyView(snapshot,now=Date.now(),symbol='BTC/USD') {
  const root=snapshot?.strategy_families?.range_reversal;
  const family=root?.analyses?.[symbol] || (symbol==='BTC/USD'?root:null);
  const route=portfolioView(snapshot).families[1];
  const analysisFresh=!!family && fresh(family.analyzed_at,now)
    && (!family.last_refresh_at || fresh(family.last_refresh_at,now));
  const sourceVerified=['alpaca_crypto_us','alpaca_crypto'].includes(family?.source);
  const current=analysisFresh && fresh(family.observed_at,now) && sourceVerified;
  const waitingNow=analysisFresh && family.state==='DATA_WAITING';
  const labels={DATA_WAITING:'Waiting for data',RANGE_FORMING:'First range forming',WATCHING:'Waiting for an outside close',
    OUTSIDE_RANGE:'Waiting for a return inside',SETUP_OBSERVED:'Reversal observed'};
  let state=!family?'Waiting for analysis':waitingNow?'Waiting for data':!current?'Analysis out of date':labels[family.state] || 'Waiting for validated analysis';
  const detail=!family?`Waiting for the first native five-minute ${symbol} observation.`:!current && !waitingNow
    ? 'Waiting for a current update. Saved candles and observations below cannot authorize an entry.'
    : typeof family.detail==='string'?family.detail:'Waiting for the next completed candle.';
  const range=family?.range;
  const validRange=range && positive(range.low) && positive(range.high) && range.low<range.high ? range:null;
  const event=family?.current_event;
  const validEvent=event && event.status==='CONFIRMED' && event.current===true
    && ['long','short'].includes(event.direction) && [event.entry,event.stop,event.target].every(positive)
    && (event.direction==='long'?event.stop<event.entry&&event.entry<event.target:event.target<event.entry&&event.entry<event.stop);
  const currentSignal=current && family.state==='SETUP_OBSERVED' && validEvent ? event:null;
  const expiry=Date.parse(family?.signal_valid_until || event?.entry_valid_until);
  const signalReady=!!currentSignal && family.signal_ready===true && Number.isFinite(expiry) && expiry>now
    && fresh(currentSignal.confirmation_at,now);
  if(currentSignal && typeof family.signal_ready==='boolean')state=signalReady?'Ready signal':'Entry window expired';
  if(currentSignal?.direction==='short'&&!route.shortSupported)state='Short setup · unsupported';
  const history=(Array.isArray(family?.candidates)?family.candidates:[])
    .filter(row=>row?.status==='CONFIRMED' && ['long','short'].includes(row.direction)
      && [row.entry,row.stop,row.target].every(positive) && Number.isFinite(Date.parse(row.confirmation_at)))
    .slice(-5).reverse();
  return {state,detail,current,range:validRange,currentSignal,history,
    latestAt:family?.latest_bar_at,observedAt:family?.observed_at,analyzedAt:family?.analyzed_at,
    symbol:family?.symbol || symbol,
    source:sourceVerified
      ? 'Alpaca spot market · native five-minute candles':'Waiting for verified Alpaca spot data',
    archive:family?.archive_status,
    warnings:(Array.isArray(family?.interpretation_warnings)?family.interpretation_warnings:[]).filter(row=>typeof row==='string'),
    session:family?.session,
    coverage:family?.coverage,
    watchingOnly:!route.available,routeStatus:route.symbols.includes(symbol)?route.status:'Not selected for trading',enabled:route.enabled&&route.symbols.includes(symbol),signalReady,
    executionMessage:route.symbols.includes(symbol)?(snapshot?.crypto_execution?.markets?.[symbol] || snapshot?.crypto_execution?.message || null):'Watched for setups and data quality. Select this market in crypto settings to permit entries.',
    shortUnsupported:currentSignal?.direction==='short' && !route.shortSupported};
}

export function rangeFamilyMarkup(view) {
  const signal=view.currentSignal;
  const archive=typeof view.archive==='string'?view.archive:typeof view.archive?.status==='string'?view.archive.status:null;
  return `<article class="card range-observer"><div class="section-heading"><h3>${esc(view.symbol)} · ${esc(view.state)}</h3><span class="pill">${esc(view.watchingOnly?'Execution unavailable':view.routeStatus)}</span></div>
    <p class="observation">${esc(view.detail)}</p>
    <p class="help range-execution-note">${esc(view.watchingOnly?'Broker execution is not available for this strategy.':view.executionMessage || 'Alpaca spot execution buys on eligible long setups and sells owned units to exit. Short entries are unsupported.')}</p>
    ${view.symbol!=='BTC/USD'?'<p class="notice">This market is an unvalidated adaptation. The recording’s crypto examples describe Bitcoin.</p>':''}
    ${view.shortUnsupported?'<p class="notice">Short setup observed · unsupported by this spot route. No new short order will be sent.</p>':''}
    <div class="range-stat-grid"><div><span>Market</span><strong>${esc(view.symbol)}</strong><small>${esc(view.source)}</small></div>
    <div><span>Last completed candle</span><strong>${esc(time(view.latestAt))}</strong><small>${view.current?'Current provider receipt':'Current data unverified'} · ${esc(time(view.observedAt))}</small></div></div>
    ${view.range?`<div class="range-stat-grid range-boundaries"><div><span>Closed range low${view.current?'':' · saved'}</span><strong>${money(view.range.low)}</strong></div><div><span>Closed range high${view.current?'':' · saved'}</span><strong>${money(view.range.high)}</strong></div></div><p class="help">Range: ${esc(time(view.range.start_at))} → ${esc(time(view.range.end_at))}. Provisional midnight New York anchor; four elapsed hours.</p>`:'<p class="help">The range appears only after all 48 native five-minute candles close and pass coverage checks.</p>'}
    ${signal?`<div class="range-signal"><strong>${esc(signal.direction==='long'?'Long':'Short')} observation · ${esc(time(signal.confirmation_at))}</strong><div class="range-price-grid"><span>Entry reference<b>${money(signal.entry)}</b></span><span>Stop reference<b>${money(signal.stop)}</b></span><span>2R target reference<b>${money(signal.target)}</b></span></div><p class="help">Reference prices from completed candles, not submitted orders or guaranteed fills.</p></div>`:''}
    ${archive?`<p class="help">Observation archive: ${esc(archive.replaceAll('_',' '))}.</p>`:''}
    <details class="evidence" id="range-history-${esc(view.symbol.replace('/','-'))}"><summary>Recent range-reversal observations${view.history.length?` · ${view.history.length}`:''}</summary>
    ${view.history.length?view.history.map(row=>`<p><b>${esc(row.direction==='long'?'Long':'Short')} · ${esc(time(row.confirmation_at))}</b><span>Reference ${money(row.entry)} · Stop ${money(row.stop)} · 2R ${money(row.target)}</span></p>`).join(''):'<p>No confirmed reversals in the available current-day candles.</p>'}
    <p>These are chart observations, not orders or fills. Strategy returns have not been established.</p></details>
    <details class="evidence" id="range-rules-${esc(view.symbol.replace('/','-'))}"><summary>Video rules and unresolved details</summary><ol class="range-rule-list">
    <li>Mark the first fully closed four-hour candle’s high and low. The video uses New York display time. <small>Video 1:40–2:05</small></li>
    <li>Wait for a five-minute close outside, then a later close strictly inside that day’s range. Fade the failed breakout. <small>Video 2:08–2:54</small></li>
    <li>Use the breakout candle’s extreme for the basic stop and twice the risk distance for the target. Fresh excursions can create more observations that day. <small>Video 2:55–3:14; 5:59–6:10</small></li></ol>
    <h4>Interpretations still needing validation</h4>${view.warnings.length?`<ul class="range-rule-list">${view.warnings.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:'<p>The candle anchor and discretionary stop adjustment still need validation.</p>'}
    <p>This Alpaca spot strategy is separate from Bullpen and Socrates’ QQQ execution.</p></details></article>`;
}

export function cryptoWatchMarkup(snapshot,now=Date.now()) {
  const root=snapshot?.strategy_families?.range_reversal;
  const selected=portfolioView(snapshot).families[1].symbols;
  return CRYPTO_MARKETS.map(symbol=>{
    const view=rangeFamilyView(snapshot,now,symbol),analysis=root?.analyses?.[symbol] || (symbol==='BTC/USD'?root:null);
    const coverage=view.coverage;
    const complete=view.current&&analysis?.state!=='DATA_WAITING'&&coverage?.missing_count===0;
    const count=direction=>new Set((analysis?.candidates || []).filter(row=>row?.status==='CONFIRMED'&&row.direction===direction).map(row=>row.event_id||row.confirmation_at)).size;
    const data=coverage?`${coverage.received_completed_bars}/${coverage.expected_completed_bars} completed candles · ${coverage.missing_count} missing${coverage.publication_wait?' · latest candle within publication allowance':''}`:view.state;
    const chart=complete?`Today’s chart: ${count('long')} buy setups · ${count('short')} short setups`:'Setup totals unavailable until data is complete.';
    const gap=coverage?.opening_range_missing_count?`${coverage.opening_range_missing_count} missing in the opening four-hour range.`:coverage?.missing_count?'Later candle gaps prevent a complete strategy evaluation.':'';
    return `<article class="crypto-watch-row"><div><strong>${esc(symbol)}</strong><span class="pill">${esc(selected.includes(symbol)?view.routeStatus:'Not selected for trading')}</span></div><div><b>${esc(data)}</b><p>${esc(chart)}</p><small>${esc(gap || view.state)}</small></div></article>`;
  }).join('');
}

export function cryptoReviewMarkup(snapshot) {
  const review=snapshot?.crypto_execution?.decision_review;
  if(!review||review.status!=='available')return `<p class="notice">${esc(review?.detail || 'Recorded crypto checks are not available yet.')} Chart observations above do not prove that a live entry was attempted.</p>`;
  const setups=review.fresh_setups||{},recent=Array.isArray(review.recent_checks)?review.recent_checks:[];
  const latest=Object.values(review.latest_by_symbol||{});
  return `<p class="help">${esc(review.day)} · ET · Retained worker checks recorded since this logging version was installed. Earlier chart setups are not counted as live checks.</p><p class="help">Crypto worker last checked: ${esc(time(snapshot?.crypto_execution?.at))}.</p>
    <div class="crypto-review-counts"><span><b>${Number(setups.long)||0}</b> fresh buy setups checked</span><span><b>${Number(setups.short)||0}</b> fresh short setups checked</span><span><b>${Number(review.order_attempts)||0}</b> entry submissions attempted</span><span><b>${Number(review.filled_entries)||0}</b> entries with confirmed fills</span></div>
    ${latest.map(row=>`<p><strong>${esc(row.symbol)}</strong> · ${esc(row.reason)} <small>${esc(time(row.checked_at))}</small></p>`).join('') || '<p>No live checks recorded today yet.</p>'}
    <details class="evidence" id="crypto-check-history"><summary>Recent entry checks · ${recent.length}</summary>${recent.map(row=>`<p><b>${esc(row.symbol)} · ${esc(time(row.checked_at))}</b><span>${esc(row.reason)}</span></p>`).join('') || '<p>No checks recorded.</p>'}</details>`;
}
