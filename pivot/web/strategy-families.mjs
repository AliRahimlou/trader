import {escape as esc, money} from './model.mjs';

export const STRATEGY_VIEW_KEY='pivot.strategy-view.v1';
const VIEWS=new Set(['socrates','range_reversal','all']);
export const normalizeStrategyView=value=>VIEWS.has(value)?value:'socrates';

// This preference only changes visible analysis. It has no API, broker, or
// settings dependency, and is safe when browser storage is unavailable.
export function bindStrategyView(document,{storage=()=>globalThis.localStorage,onChange=()=>{}}={}) {
  let selected='socrates';
  try { selected=normalizeStrategyView(storage()?.getItem(STRATEGY_VIEW_KEY)); } catch {}
  const selector=document.getElementById('strategy-view');
  function display() {
    selector.value=selected;
    document.getElementById('socrates-family').hidden=selected==='range_reversal';
    document.getElementById('range-family').hidden=selected==='socrates';
    document.getElementById('strategy-view-note').textContent=selected==='all'
      ? 'Showing both strategies’ analysis. This does not enable simultaneous trading. Live money controls Socrates only.'
      : selected==='range_reversal'
      ? '4H Range Reversal is watching only. Socrates live execution remains visible below and is controlled by Live money.'
      : 'Viewing Socrates. The Live money switch controls this strategy; changing this view does not change trading permission.';
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

const fresh=(at,now)=>{
  const elapsed=now-Date.parse(at);
  return Number.isFinite(elapsed) && elapsed>=0 && elapsed<=90000;
};
const positive=value=>typeof value==='number' && Number.isFinite(value) && value>0;
const time=value=>Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET'
  : 'Not available';

export function rangeFamilyView(snapshot,now=Date.now()) {
  const family=snapshot?.strategy_families?.range_reversal;
  const analysisFresh=!!family && fresh(family.analyzed_at,now)
    && (!family.last_refresh_at || fresh(family.last_refresh_at,now));
  const sourceVerified=['alpaca_crypto_us','alpaca_crypto'].includes(family?.source);
  const current=analysisFresh && fresh(family.observed_at,now) && sourceVerified;
  const waitingNow=analysisFresh && family.state==='DATA_WAITING';
  const labels={DATA_WAITING:'Waiting for data',RANGE_FORMING:'First range forming',WATCHING:'Waiting for an outside close',
    OUTSIDE_RANGE:'Waiting for a return inside',SETUP_OBSERVED:'Reversal observed'};
  const state=!family?'Waiting for analysis':waitingNow?'Waiting for data':!current?'Analysis out of date':labels[family.state] || 'Waiting for validated analysis';
  const detail=!family?'Waiting for the first native five-minute Bitcoin observation.':!current && !waitingNow
    ? 'Waiting for a current update. Saved candles and observations below cannot authorize an entry.'
    : typeof family.detail==='string'?family.detail:'Waiting for the next completed candle.';
  const range=family?.range;
  const validRange=range && positive(range.low) && positive(range.high) && range.low<range.high ? range:null;
  const event=family?.current_event;
  const validEvent=event && event.status==='CONFIRMED' && event.current===true
    && ['long','short'].includes(event.direction) && [event.entry,event.stop,event.target].every(positive)
    && (event.direction==='long'?event.stop<event.entry&&event.entry<event.target:event.target<event.entry&&event.entry<event.stop);
  const currentSignal=current && family.state==='SETUP_OBSERVED' && validEvent ? event:null;
  const history=(Array.isArray(family?.candidates)?family.candidates:[])
    .filter(row=>row?.status==='CONFIRMED' && ['long','short'].includes(row.direction)
      && [row.entry,row.stop,row.target].every(positive) && Number.isFinite(Date.parse(row.confirmation_at)))
    .slice(-5).reverse();
  return {state,detail,current,range:validRange,currentSignal,history,
    latestAt:family?.latest_bar_at,observedAt:family?.observed_at,analyzedAt:family?.analyzed_at,
    symbol:family?.symbol || 'BTC/USD',
    source:sourceVerified
      ? 'Alpaca spot market · native five-minute candles':'Waiting for verified Alpaca spot data',
    archive:family?.archive_status,
    warnings:(Array.isArray(family?.interpretation_warnings)?family.interpretation_warnings:[]).filter(row=>typeof row==='string'),
    session:family?.session,
    watchingOnly:true};
}

export function rangeFamilyMarkup(view) {
  const signal=view.currentSignal;
  const archive=typeof view.archive==='string'?view.archive:typeof view.archive?.status==='string'?view.archive.status:null;
  return `<article class="card range-observer"><div class="section-heading"><h3>${esc(view.state)}</h3><span class="pill">Watching only</span></div>
    <p class="observation">${esc(view.detail)}</p>
    <p class="help range-execution-note">No orders from this strategy. The Live money switch controls Socrates only.</p>
    <div class="range-stat-grid"><div><span>Market</span><strong>${esc(view.symbol)}</strong><small>${esc(view.source)}</small></div>
    <div><span>Last completed candle</span><strong>${esc(time(view.latestAt))}</strong><small>${view.current?'Current provider receipt':'Current data unverified'} · ${esc(time(view.observedAt))}</small></div></div>
    ${view.range?`<div class="range-stat-grid range-boundaries"><div><span>Closed range low${view.current?'':' · saved'}</span><strong>${money(view.range.low)}</strong></div><div><span>Closed range high${view.current?'':' · saved'}</span><strong>${money(view.range.high)}</strong></div></div><p class="help">Range: ${esc(time(view.range.start_at))} → ${esc(time(view.range.end_at))}. Provisional midnight New York anchor; four elapsed hours.</p>`:'<p class="help">The range appears only after all 48 native five-minute candles close and pass coverage checks.</p>'}
    ${signal?`<div class="range-signal"><strong>${esc(signal.direction==='long'?'Long':'Short')} observation · ${esc(time(signal.confirmation_at))}</strong><div class="range-price-grid"><span>Entry reference<b>${money(signal.entry)}</b></span><span>Stop reference<b>${money(signal.stop)}</b></span><span>2R target reference<b>${money(signal.target)}</b></span></div><p class="help">Reference prices from completed candles, not submitted orders or guaranteed fills.</p></div>`:''}
    ${archive?`<p class="help">Observation archive: ${esc(archive.replaceAll('_',' '))}.</p>`:''}
    <details class="evidence" id="range-history"><summary>Recent range-reversal observations${view.history.length?` · ${view.history.length}`:''}</summary>
    ${view.history.length?view.history.map(row=>`<p><b>${esc(row.direction==='long'?'Long':'Short')} · ${esc(time(row.confirmation_at))}</b><span>Reference ${money(row.entry)} · Stop ${money(row.stop)} · 2R ${money(row.target)}</span></p>`).join(''):'<p>No confirmed reversals in the available current-day candles.</p>'}
    <p>These are chart observations, not orders or fills. Strategy returns have not been established.</p></details>
    <details class="evidence" id="range-rules"><summary>Video rules and unresolved details</summary><ol class="range-rule-list">
    <li>Mark the first fully closed four-hour candle’s high and low. The video uses New York display time. <small>Video 1:40–2:05</small></li>
    <li>Wait for a five-minute close outside, then a later close strictly inside that day’s range. Fade the failed breakout. <small>Video 2:08–2:54</small></li>
    <li>Use the breakout candle’s extreme for the basic stop and twice the risk distance for the target. Fresh excursions can create more observations that day. <small>Video 2:55–3:14; 5:59–6:10</small></li></ol>
    <h4>Interpretations still needing validation</h4>${view.warnings.length?`<ul class="range-rule-list">${view.warnings.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:'<p>The candle anchor and discretionary stop adjustment still need validation. This strategy remains observational.</p>'}
    <p>This Bitcoin spot analysis is separate from Bullpen and from Socrates’ QQQ execution.</p></details></article>`;
}
