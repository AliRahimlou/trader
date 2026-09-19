import {money, escape as esc, age, ago, sizeHint, settingsError, vixStatus, appStatus, releaseStatus, loggingStatus, strategyViews, leaderOverview, marketOverview, operationStatus, sessionReview} from './model.mjs';
import {bindStrategyView, rangeFamilyView, rangeFamilyMarkup, portfolioView, portfolioStatus, strategySettingsMatch, cryptoQuantityLabel, cryptoHistoryMarkup, positionUnit} from './strategy-families.mjs';
const localPreview = location.port === '5173' && ['127.0.0.1','localhost'].includes(location.hostname);
const api = localPreview ? new URL(`http://${location.hostname}:8011/api/`) : new URL('./api/',document.baseURI);
const $ = id => document.getElementById(id);
let snapshot=null, dirty=false, saving=false, fetching=false, generation=0, saveError="", toggling=false, liveError='';
let pendingLive=null;
let familyConnectionUnavailable=false;
let strategySaving=false,rangeDirty=false,strategyError='',pendingStrategy=null,strategyReview=null;
let cryptoRechecking=false,cryptoRecheckMessage='',cryptoRecheckCompleted=false;
let liveReviewFingerprint=null,strategyReviewFingerprint=null;
function permissionFingerprint() {
  const p=portfolioView(snapshot);
  return JSON.stringify({version:snapshot?.execution_policy?.version,globalOn:p.globalOn,families:p.families});
}
const LIVE_RECONCILE_MS=60000;
bindStrategyView(document,{onChange:()=>renderStrategyFamilies()});
function renderStrategyFamilies() {
  const open=new Set([...$('range-content').querySelectorAll('details[open]')].map(node=>node.id));
  const symbols=portfolioView(snapshot).families[1].symbols;
  $('range-content').innerHTML=(symbols.length?symbols:['BTC/USD']).map(symbol=>{
    const view=rangeFamilyView(snapshot,Date.now(),symbol);
    if(familyConnectionUnavailable)Object.assign(view,{current:false,currentSignal:null,state:'App connection unavailable',
      detail:'Reconnecting to verify current observations. Saved analysis cannot authorize an entry.'});
    return rangeFamilyMarkup(view);
  }).join('');
  for(const node of $('range-content').querySelectorAll('details'))node.open=open.has(node.id);
}
function reconcileLive(next) {
  if(!pendingLive)return;
  if(Date.now()>pendingLive.expiresAt){pendingLive=null;return;}
  if(typeof next.live_enabled==='boolean' && portfolioView(next).globalOn===pendingLive.enabled){
    pendingLive=null;liveError='';$('live-error').textContent='';$('live-dialog').close();
  }
}
function renderStrategyControls() {
  const p=portfolioView(snapshot),busy=strategySaving||saving||toggling||familyConnectionUnavailable;
  $('strategy-run-summary').textContent=`Global Live ${p.globalOn?'On':'Off'} · ${p.scope}. Changing the strategy view does not change these settings.`;
  $('execution-scope').textContent=`Global Live · ${p.scope}`;
  for(const f of p.families){
    const prefix=f.id==='socrates'?'socrates':'range';
    $(prefix+'-run-state').textContent=f.status;
    $(prefix+'-toggle').textContent=f.enabled?'Turn strategy Off':'Enable strategy';
    $(prefix+'-toggle').disabled=busy||!p.configured||(!f.enabled&&!f.available);
    $(prefix+'-run-detail').textContent=`${money(f.target)} per purchase · ${f.symbols.join(', ')}. `+
      (f.id==='socrates'?(snapshot.execution?.message || ''):(snapshot.crypto_execution?.message || 'Waiting for execution checks.'));
  }
  $('run-both').disabled=busy||!p.configured||p.families.some(f=>!f.available)||p.families.every(f=>f.enabled);
  $('range-review').hidden=!p.families[1].reviewRequired;
  $('range-review').disabled=busy||!p.families[1].available;
  if(!rangeDirty&&!strategySaving){const f=p.families[1];$('range-amount').value=f.target;$('range-btc').checked=f.symbols.includes('BTC/USD');$('range-eth').checked=f.symbols.includes('ETH/USD');}
  $('save-range').disabled=busy||!p.configured||!rangeDirty;
  $('strategy-save-message').textContent=strategyError || (strategySaving?'Saving strategy settings…':rangeDirty?'Unsaved crypto settings. Saving does not change global Live.':'Strategy Off stops its new entries. Existing positions continue their exits.');
  const execution=snapshot.crypto_execution || {},trades=Array.isArray(execution.trades)?execution.trades:[];
  const incidents=Array.isArray(execution.incidents)?execution.incidents:[];
  $('crypto-execution-status').textContent=execution.message || 'Crypto execution is not configured.';
  $('crypto-incidents').innerHTML=[...(snapshot.crypto_worker_error?[snapshot.crypto_worker_error]:[]),...incidents].map(row=>`<p class="notice warning">${esc(typeof row==='string'?row:row.reason || row.message || 'Crypto execution needs attention.')}</p>`).join('');
  $('crypto-recheck').hidden=!incidents.length;
  $('crypto-recheck').disabled=busy||cryptoRechecking;
  $('crypto-recheck').textContent=cryptoRechecking?'Checking broker records…':'Recheck crypto incidents';
  $('crypto-recheck-message').hidden=!incidents.length&&!cryptoRecheckMessage&&!cryptoRechecking&&!cryptoRecheckCompleted;
  const recheckResult=cryptoRecheckCompleted?`${cryptoRecheckMessage?'Last recheck: '+cryptoRecheckMessage+' ':''}${incidents.length?`${incidents.length} incident${incidents.length===1?'':'s'} still need${incidents.length===1?'s':''} attention in the latest update.`:'No crypto incidents in the latest update.'}`:cryptoRecheckMessage;
  $('crypto-recheck-message').textContent=cryptoRechecking?'Checking saved incidents against broker records. No orders or trading settings are changed.':recheckResult || 'Checks broker evidence only. An incident clears only when its safe resolution is verified.';
  $('crypto-trades').innerHTML=trades.length?trades.map(row=>`<div class="holding"><strong>${esc(row.symbol)}</strong><span>${esc(row.stage || 'Managing')} · ${esc(cryptoQuantityLabel(row))}</span><b>${money(row.amount)} purchase</b><p class="help">Stop ${money(row.stop)} · Target ${money(row.target)}${row.reason?' · '+esc(row.reason):''}</p></div>`).join(''):'<p class="muted">No app-managed crypto positions in this snapshot. Broker positions and orders are listed below.</p>';
  $('crypto-history').innerHTML=cryptoHistoryMarkup(execution.history);
}
async function recheckCryptoIncidents() {
  if(!snapshot||cryptoRechecking||saving||toggling||strategySaving||familyConnectionUnavailable
      || !Array.isArray(snapshot.crypto_execution?.incidents) || !snapshot.crypto_execution.incidents.length)return;
  cryptoRechecking=true;cryptoRecheckMessage='';cryptoRecheckCompleted=false;const version=++generation;renderStrategyControls();
  try {
    const response=await fetch(new URL('crypto/reconcile',api),{method:'POST',headers:{'Content-Type':'application/json','X-Pivot-Intent':'settings'},body:'{}',signal:AbortSignal.timeout(30000)});
    const next=await response.json();
    if(!response.ok)throw Error(next.detail || 'Could not recheck crypto incidents');
    if(!next.portfolio || typeof next.live_enabled!=='boolean' || !Array.isArray(next.crypto_execution?.incidents))throw Error('The recheck response was incomplete. Refreshing incident status.');
    const proofMessage=typeof next.crypto_reconciliation?.message==='string'?next.crypto_reconciliation.message:next.crypto_execution.message || '';
    if(version===generation){
      generation++;snapshot=next;familyConnectionUnavailable=false;reconcileLive(next);reconcileStrategies(next);
      cryptoRecheckCompleted=true;cryptoRecheckMessage=proofMessage;
    }else cryptoRecheckMessage=`${proofMessage?proofMessage+' ':''}Refreshing the current incident list.`;
  }catch(error){cryptoRecheckMessage=error.name==='TimeoutError'?'Recheck result is uncertain. Refreshing status; no automatic retry will be sent.':error.message;}
  finally{cryptoRechecking=false;render();refresh();}
}
function reconcileStrategies(next) {
  if(!pendingStrategy)return;
  if(Date.now()>pendingStrategy.expiresAt){pendingStrategy=null;return;}
  if(strategySettingsMatch(next,pendingStrategy.payload)){
    pendingStrategy=null;strategyError='';rangeDirty=false;strategyReview=null;
    $('strategy-dialog-error').textContent='';$('strategy-dialog').close();
  }
}
function reviewStrategies(payload) {
  if(!snapshot||strategySaving||toggling||saving)return;
  const p=portfolioView(snapshot),selected=p.families.filter(f=>payload[f.id]?.enabled===true);
  strategyReview=payload;strategyReviewFingerprint=permissionFingerprint();pendingStrategy=null;
  $('strategy-dialog-title').textContent=selected.length===2?'Run both strategies':'Review strategy settings';
  $('strategy-dialog-summary').textContent=selected.length?`${selected.map(f=>f.label).join(' + ')}. Global Live is ${p.globalOn?'On: enabling permits new entries when checks pass.':'Off: this saves the selection; turn global Live On separately to permit entries.'}`:'Review the crypto purchase amount and selected markets. Global Live is unchanged.';
  const lines=selected.flatMap(f=>f.id==='socrates'?(snapshot.execution_policy?.summary || []):f.policy);
  if(payload.range_reversal){
    if(!selected.some(f=>f.id==='range_reversal'))lines.push(...p.families[1].policy);
    lines.push(`Crypto target: ${money(payload.range_reversal.target_dollars || p.families[1].target)} per purchase. Markets: ${(payload.range_reversal.symbols || p.families[1].symbols).join(', ')}.`);
    lines.push('BTC/USD is the video market. ETH/USD is an unvalidated adaptation. This spot route supports long entries only.');
  }
  $('strategy-dialog-policy').innerHTML=lines.map(line=>`<li>${esc(line)}</li>`).join('');
  $('strategy-dialog-error').textContent='';$('accept-strategy-policy').checked=false;$('confirm-strategy').disabled=true;$('strategy-dialog').showModal();
}
async function changeStrategies(payload) {
  if(!snapshot||strategySaving||toggling||saving)return;
  strategySaving=true;generation++;strategyError='';pendingStrategy={payload,expiresAt:Date.now()+LIVE_RECONCILE_MS};
  $('confirm-strategy').disabled=true;$('cancel-strategy').disabled=true;
  try{
    const response=await fetch(new URL('strategies',api),{method:'PUT',headers:{'Content-Type':'application/json','X-Pivot-Intent':'settings'},body:JSON.stringify(payload),signal:AbortSignal.timeout(30000)});
    const next=await response.json();if(!response.ok)throw Error(next.detail || 'Could not save strategy settings');
    snapshot=next;reconcileStrategies(next);if(pendingStrategy)throw Error('Strategy settings are not yet confirmed. Checking the saved state.');
  }catch(error){strategyError=error.name==='TimeoutError'?'The strategy update result is uncertain. Checking the saved settings before retrying.':error.message;$('strategy-dialog-error').textContent=strategyError;}
  finally{strategySaving=false;$('cancel-strategy').disabled=false;$('confirm-strategy').disabled=!$('accept-strategy-policy').checked;render();refresh();}
}
function formSettings() { return {sizing_mode:'target',target_dollars:$('amount').value}; }
function formChanged() {
  const settings=formSettings();
  $('size-explanation').textContent=sizeHint(settings.target_dollars);
  const error=settingsError(settings,snapshot);
  $('save-size').disabled=saving || !dirty || !!error;
  if (dirty && !saving) $('save-message').textContent=saveError || error || 'Unsaved change · does not enable real trading.';
}
function render() {
  const s=snapshot; if(!s)return;
  renderStrategyFamilies();
  renderStrategyControls();
  $('runtime-status').textContent=s.hosting?.message || 'Checking where the app is running…';
  const release=releaseStatus(s);
  $('installed-version').textContent=release.label;
  $('installed-version').title=release.revision || '';
  $('deployment-status').textContent=release.detail;
  document.querySelector('.release-badge').dataset.state=release.tone;
  const stale=age(s.account_at)>60 || !!s.account_error;
  $('balance').textContent=money(s.account?.equity);
  $('account-mode').textContent=s.account ? `${s.account.mode} account · ${s.live_enabled?'execution on':'execution off'}` : '';
  const change=Number(s.account?.equity)-Number(s.account?.last_equity);
  $('day-change').textContent=s.account ? `${change>=0?'+':''}${money(change)} since previous close` : 'Waiting for account data';
  $('cash').textContent=`Cash ${money(s.account?.cash)}`;
  $('buying-power').textContent=`Buying power ${money(s.account?.buying_power)}`;
  $('account-updated').textContent=ago(s.account_at);
  $('account-warning').hidden=!stale;
  $('account-warning').textContent=s.account_error || 'Account information is not current yet. Amounts shown may be out of date.';
  $('analysis-updated').textContent=ago(s.analysis_at);
  const status=portfolioStatus(s,appStatus(s)), vixDisplay=vixStatus(s.data_health?.vix);
  $('status-title').textContent=status.title;
  $('live-status').innerHTML=`Live money <strong>${s.live_enabled?'On':'Off'}</strong><span class="switch" aria-hidden="true"></span>`;
  $('live-status').classList.toggle('is-on',s.live_enabled);
  $('live-status').disabled=toggling || strategySaving || (!portfolioView(s).globalOn && !portfolioView(s).enabled.some(f=>f.available));
  $('live-status').setAttribute('aria-label', s.live_enabled?'Global live money on. Turn off all new entries':'Global live money off. Review enabled strategies and turn on');
  $('live-status').title='Controls new entries for all enabled strategies. Existing positions continue their exits.';
  $('status-text').textContent=liveError || status.text;
  const operations=operationStatus(s), session=sessionReview(s);
  if(!s.portfolio && operations.workerLabel==='Needs attention' && !s.execution?.trade){
    $('status-title').textContent='App worker needs attention';
    $('status-text').textContent=operations.workerDetail+'. Data and order progress must be checked.';
  }
  $('operation-incident').hidden=!operations.incident;
  $('operation-incident').textContent=operations.incident;
  $('direction-capability').textContent=operations.directionNote;
  $('method-timing').textContent=operations.timingNote;
  const sessionOpen=$('session-review').querySelector('details')?.open;
  $('session-review').innerHTML=`<div class="section-heading"><h2>Socrates · ${session.orders?.confirmed_entries?'today’s trade checks':'why no trade today?'}</h2><span> ${esc(session.day || 'Waiting for records')} · ET</span></div><p class="help">${esc(session.detail)}</p>${session.available?`<div class="session-counts"><div><strong>${esc(session.events)}</strong><span>Level events</span></div><div><strong>${esc(session.orders.submission_attempts || 0)}</strong><span>Entry attempts</span></div><div><strong>${esc(session.orders.confirmed_entries || 0)}</strong><span>Confirmed entries</span></div></div><details class="evidence"><summary>See checks and blockers</summary><p>${esc(session.checkpoints)} recorded checkpoints. Passing a signal check does not authorize an order. This summary refreshes every 30 seconds.</p>${session.stages.map(row=>`<p><b>${esc(row.label)}</b><span>${esc(row.count)} distinct events passed</span></p>`).join('')}${session.blockers.map(row=>`<p><b>${esc(row.scope)} · ${esc(row.name)}</b><span>Latest recorded result for ${esc(row.count)} events</span></p>`).join('')}<h3>Recent observations</h3>${session.checks.map(row=>`<p><b>${esc(new Date(row.at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}))}</b><span>${esc(row.blocker)}</span></p>`).join('') || '<p>No analysis recorded today.</p>'}</details>`:''}`;
  if(sessionOpen && $('session-review').querySelector('details'))$('session-review').querySelector('details').open=true;
  const views=strategyViews(s), leaders=leaderOverview(s), overview=marketOverview(s);
  const overviewOpen=$('market-overview').querySelector('details')?.open;
  $('market-overview').innerHTML=`<article class="card"><div class="section-heading"><h3>Nasdaq overview</h3><span>Broader price structure</span></div><div class="context-frames">${overview.frames.map(frame=>`<div><span class="help">${esc(frame.label)}</span><strong>${esc(frame.value)}</strong><span class="help">${frame.latestAt?`Candle ${esc(new Date(frame.latestAt).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}))}`:'Waiting for candles'}</span></div>`).join('')}</div><p class="help">${esc(overview.detail)}</p><details class="evidence"><summary>How to read the overview</summary><p>${esc(overview.explanation)}</p>${overview.frames.map(frame=>`<p><b>${esc(frame.label)}</b><span>${esc(frame.detail)}</span></p>`).join('')}</details></article>`;
  if(overviewOpen)$('market-overview').querySelector('details').open=true;
  const openMethods=new Set([...$('strategy-cards').querySelectorAll('details[open]')].map(node=>node.dataset.method));
  $('strategy-cards').innerHTML=views.map(method=>`<article class="card strategy"><div class="section-heading"><h3>${esc(method.label)}</h3><span class="pill ${method.qualified?'green':''}">${esc(method.state)}</span></div><p class="observation">${esc(method.detail)}</p>${method.area?`<p class="help">Nearest watched QQQ area: ${money(method.area.low)}${method.area.low!==method.area.high?` – ${money(method.area.high)}`:''}</p>`:''}${method.event_at?`<p class="help">Event confirmed ${esc(new Date(method.event_at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}))}${method.event_expires_at?` · expires ${esc(new Date(method.event_expires_at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}))}`:''}</p>`:''}${method.direction?`<div class="levels"><span>${esc(method.direction.toUpperCase())}${method.selected?' · leading analysis':''}</span><span>Signal reference ${money(method.entry)} · Stop ${money(method.stop)} · Target ${money(method.target)}</span></div>`:''}<details class="evidence" data-method="${esc(method.id)}"><summary>Current observations & checks</summary>${method.checks.map(c=>`<p><b class="${c.passed?'pass':'wait'}">${c.passed?'✓':'○'} ${esc(c.name)}</b><span>${esc(c.detail)}</span></p>`).join('') || '<p>Waiting for valid data.</p>'}</details></article>`).join('')+`<article class="card"><div class="section-heading"><h3>Technology leaders</h3><span>Shared confirmation</span></div><p class="help leader-summary">${esc(leaders.detail)}</p><div class="leader-chips">${leaders.rows.map(row=>`<span class="leader-chip ${row.vote?'pass':'wait'}" title="${esc(row.reason)}${row.latestAt?' · Candle '+esc(new Date(row.latestAt).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})):''}"><b>${esc(row.symbol)}</b> ${esc(row.label)}${row.reactionLabel?`<small>Reaction ${esc(row.reactionLabel)}</small>`:''}</span>`).join('')}</div>${leaders.alignment?`<p class="help">${esc(leaders.alignment)}</p>`:''}${leaders.ruleText?`<p class="help">${esc(leaders.ruleText)} ${esc(leaders.timing)}</p>`:''}${leaders.scope?`<p class="help">${esc(leaders.scope)}</p>`:''}<p class="help">${esc(vixDisplay.headline)} ${esc(vixDisplay.quoteNote)}</p><p class="help">QQQ · Nasdaq ETF proxy. Both methods are checked independently; only one position can be open.</p></article>`;
  for(const node of $('strategy-cards').querySelectorAll('details')) node.open=openMethods.has(node.dataset.method);
  $('holding-count').textContent=`${s.positions.length} positions · ${s.orders.length} orders`;
  $('holdings').innerHTML=(!s.positions.length&&!s.orders.length)?`<p class="muted">${stale?'No positions in the last snapshot. Awaiting a fresh broker update.':'No open positions or working orders.'}</p>`:
    s.positions.map(p=>`<div class="holding"><strong>${esc(p.symbol)}</strong><span>${esc(p.qty)} ${positionUnit(p)} · ${esc(p.side)}</span><b>${money(p.unrealized_pl)}</b></div>`).join('')+s.orders.map(o=>`<div class="holding"><strong>${esc(o.symbol)}</strong><span>${esc(o.side)} ${esc(o.qty)} · ${esc(o.type)}</span><b>${esc(o.status)}</b></div>`).join('');
  $('current-size').textContent=s.settings.sizing_mode==='target'?`Saved target: ${money(s.settings.target_dollars)} per purchase.`:'Saved mode: automatic allocation.';
  if(!dirty&&!saving) {
    $('amount').value=s.settings.target_dollars;
  }
  const health=s.data_health, stocks=health?.stocks, vix=health?.vix, quote=s.quote_health;
  const logging=loggingStatus(s);
  const healthStale=age(stocks?.checked_at)>90;
  const current=stocks?.instruments.filter(i=>i.status==='current').length || 0;
  const detailOpen=$('data-connections').querySelector('details')?.open;
  const candleTime=value=>value?new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'Missing';
  $('data-connections').innerHTML=`<div><span>Account & orders</span><b>${stale?'Updating':'Connected'}</b></div>
    <div><span>Market clock</span><b>${stale?'Updating':s.clock?.is_open?'Open':'Closed'}</b></div>
    <div><span>QQQ bid / ask</span><b>${quote?.status==='current'&&age(quote.latest_at)<=30?'Current':quote?.status==='market_closed'?'Market closed':'Waiting'}</b></div>
    ${quote?.latest_at?`<p class="help">${money(quote.bid)} / ${money(quote.ask)} · ${ago(quote.latest_at)}</p>`:''}
    <div><span>Stock candles</span><b>${healthStale?'Updating':`${current}/8 current`}</b></div>
    <p class="help">${esc(stocks?.coverage || 'Checking coverage')} · $0 data budget</p>
    <div><span>Actual VIX</span><b class="${vixDisplay.tone}">${vixDisplay.label}</b></div>
    <p class="help">${esc(vix?.source || 'Checking VIX source')} · ${vixDisplay.quoteNote}</p>
    ${vixDisplay.error?`<p class="help error">${esc(vixDisplay.error)}</p>`:''}
    ${quote?.error?`<p class="help error">${esc(quote.error)}</p>`:''}
    <div><span>Decision logging</span><b id="logging-status" class="${logging.tone}">${logging.label}</b></div>
    <p id="logging-detail" class="help">${esc(logging.detail)}</p>
    <div><span>App workers</span><b>${esc(operations.workerLabel)}</b></div><p class="help">${esc(operations.workerDetail)}</p>
    <div><span>Complete input history</span><b>${esc(operations.archiveLabel)}</b></div>
    ${s.input_archive?.status==='unavailable'?`<p class="help error">${esc(s.input_archive.detail)}</p>`:''}
    <details class="feed-details"><summary>Check every data input</summary>
      ${stocks?.instruments.map(i=>`<article><b>${esc(i.symbol)}</b><span class="${i.status==='current'&&!healthStale?'pass':'wait'}">${healthStale?'Refresh overdue':esc(i.status.replaceAll('_',' '))}</span>
        <ul>${i.frames.map(f=>`<li><span>${esc(f.label)} · ${f.count} candles</span><span>${esc(candleTime(f.latest_at))}${f.status!=='current'?` · ${esc(f.reason)}`:''}</span></li>`).join('')}</ul></article>`).join('') || '<p>Waiting for the first validated update.</p>'}
      <article><b>VIX · actual index</b><p>${vix?.latest_at?`${esc(vix.bar_count)} completed candles · ${esc(candleTime(vix.latest_at))}`:'No verified completed candles available'}</p><p>${esc(vix?.verification?.timeframe || 'Real-time access unverified')}</p>
        ${vixDisplay.lastQuote?`<p class="help">${esc(vixDisplay.lastQuote)}</p>`:''}
        ${vixDisplay.budgetText?`<p class="help">${esc(vixDisplay.budgetText)}</p>`:''}
        ${vixDisplay.nextRefreshAt?`<p class="help">Next candle check: ${esc(candleTime(vixDisplay.nextRefreshAt))}</p>`:''}
        ${vixDisplay.retryAt?`<p class="help">Candle recovery eligible: ${esc(candleTime(vixDisplay.retryAt))}</p>`:''}
        ${vixDisplay.quoteRetryAt?`<p class="help">Entry quote recovery eligible: ${esc(candleTime(vixDisplay.quoteRetryAt))}</p>`:''}
        ${vix?.verification?.history_received_at?`<p class="help">History received: ${esc(candleTime(vix.verification.history_received_at))}</p>`:''}
      </article>
      <article><b>Five-minute validation history</b><p>${esc(({captured:'Saved for validation',incomplete:'Incomplete',collecting:'Collecting',waiting:'Waiting',unavailable:'Unavailable'})[s.native_history?.status] || 'Not collected')}</p><p class="help">${esc(s.native_history?.detail || 'Separate historical evidence has not been collected.')}</p><p class="help">This is historical research data, not a continuously updated five-minute trading feed.</p></article>
      <p class="help">Last stock refresh: ${stocks?.fetch_seconds!=null?stocks.fetch_seconds.toFixed(1)+'s':'—'} · ${esc(stocks?.refresh_mode || 'starting')}. Updated ${ago(stocks?.checked_at)}.</p>
      <p class="help">${esc(stocks?.frame_policy || '')}</p>
    </details>`+s.data_errors.filter(e=>!e.startsWith('VIX:')).map(e=>`<p class="help error">${esc(e)}</p>`).join('');
  if(detailOpen)$('data-connections').querySelector('details').open=true;
  $('journal').innerHTML=s.events.length?s.events.map(e=>`<p><b>${esc(e.kind.replaceAll('_',' '))}</b> · ${esc(new Date(e.at).toLocaleString())}${e.detail?.reason?`<br>${esc(e.detail.reason)}`:''}</p>`).join(''):'<p class="muted">No saved changes yet.</p>';
  $('trade-results').innerHTML=(s.trade_results || []).length?s.trade_results.map(t=>`<p><b>${esc(t.symbol)} · ${esc(t.direction || 'Trade')}</b>${t.completed_at?` · ${esc(new Date(t.completed_at).toLocaleString())}`:''}<br>${t.status==='verified_gross'?`Gross result ${money(t.gross_pnl)} · ${esc(t.quantity)} shares. Fees are not included; net profit is not verified.`:'Result unverified: complete broker fill evidence is unavailable.'}</p>`).join(''):'<p class="muted">No completed trades recorded. Trading profit has not been established.</p>';
  formChanged();
}
async function refresh() {
  if(fetching||saving||toggling||strategySaving)return; fetching=true; const version=generation;
  try {const response=await fetch(new URL('snapshot',api),{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw Error(); const next=await response.json(); if(version===generation&&!saving&&!toggling&&!strategySaving){snapshot=next;familyConnectionUnavailable=false;reconcileLive(next);reconcileStrategies(next);render();}}
  catch { if(version!==generation||saving||toggling||strategySaving)return; familyConnectionUnavailable=true;renderStrategyFamilies();if(snapshot)renderStrategyControls(); $('status-title').textContent='App connection unavailable';$('status-text').textContent='Displayed information may be outdated. Reconnecting…';$('save-size').disabled=true;$('live-status').disabled=true;$('live-status').innerHTML='Live money <strong>Unknown</strong>';$('installed-version').textContent='Version check unavailable';$('deployment-status').textContent='Connection lost · reconnecting to verify the installed version.';document.querySelector('.release-badge').dataset.state='unknown';if($('logging-status')){$('logging-status').textContent='Unverified';$('logging-status').className='wait';$('logging-detail').textContent='Connection lost. Reconnecting to verify that checks are being saved.';} }
  finally{fetching=false;if(version!==generation&&!saving&&!toggling&&!strategySaving)refresh();}
}
async function changeLive(enabled) {
  if(toggling||strategySaving||saving)return;
  liveError='';toggling=true;generation++;pendingLive={enabled,expiresAt:Date.now()+LIVE_RECONCILE_MS};$('live-status').disabled=true;$('confirm-live').disabled=true;$('cancel-live').disabled=true;
  $('live-error').textContent='';
  try {
    const response=await fetch(new URL('live',api),{method:'PUT',headers:{'Content-Type':'application/json','X-Pivot-Intent':'live-control'},body:JSON.stringify({enabled,policy_version:snapshot.execution_policy.version}),signal:AbortSignal.timeout(30000)});
    const result=await response.json();
    if(!response.ok)throw Error(result.detail || 'Could not update live money');
    snapshot=result;reconcileLive(result);
    if(pendingLive)throw Error('Live money change is not confirmed. Checking the saved setting.');
  } catch(error) {
    const message=error.name==='TimeoutError'?'The result is uncertain. Check the refreshed switch before retrying.':error.message;
    liveError=message;$('live-error').textContent=message;
    $('status-text').textContent=message;
  } finally {
    toggling=false;$('cancel-live').disabled=false;$('confirm-live').disabled=!$('accept-policy').checked;render();refresh();
  }
}
$('live-status').addEventListener('click',()=>{
  if(!snapshot||toggling||strategySaving||saving)return;
  if(portfolioView(snapshot).globalOn){changeLive(false);return;}
  pendingLive=null;
  const p=portfolioView(snapshot);
  liveReviewFingerprint=permissionFingerprint();
  $('live-summary').textContent=`Global Live · ${p.scope}. ${p.enabled.map(f=>f.label+': '+money(f.target)+' per purchase').join(' · ')}`;
  $('live-policy').innerHTML=p.enabled.flatMap(f=>f.id==='socrates'?snapshot.execution_policy.summary:f.policy).map(line=>`<li>${esc(line)}</li>`).join('');
  $('live-data-note').textContent='Global Live permits new entries for the enabled strategies above. Each strategy uses its own data and broker checks. Turning it Off stops all new entries; existing positions continue their exits.';
  $('live-error').textContent='';$('accept-policy').checked=false;$('confirm-live').disabled=true;$('live-dialog').showModal();
});
$('cancel-live').addEventListener('click',()=>$('live-dialog').close());
$('accept-policy').addEventListener('change',()=>{$('confirm-live').disabled=toggling || !$('accept-policy').checked;});
$('live-form').addEventListener('submit',event=>{event.preventDefault();if(!$('accept-policy').checked)return;
  if(liveReviewFingerprint!==permissionFingerprint()){$('live-error').textContent='Strategy settings changed while this review was open. Close it and review the current settings before turning on.';return;}
  changeLive(true);
});
for(const [button,id] of [['socrates-toggle','socrates'],['range-toggle','range_reversal']])$(button).addEventListener('click',()=>{
  const family=portfolioView(snapshot).families.find(f=>f.id===id);
  if(!snapshot?.portfolio||!family||strategySaving||toggling||saving)return;
  if(family.enabled){changeStrategies({[id]:{enabled:false}});return;}
  if(!family.available)return;
  reviewStrategies({[id]:{enabled:true,...(id==='range_reversal'?{policy_version:family.policyVersion}:{})}});
});
$('run-both').addEventListener('click',()=>{
  const p=portfolioView(snapshot);if(!p.configured||p.families.some(f=>!f.available))return;
  reviewStrategies({socrates:{enabled:true},range_reversal:{enabled:true,policy_version:p.families[1].policyVersion}});
});
$('range-review').addEventListener('click',()=>{const f=portfolioView(snapshot).families[1];if(f.available&&f.reviewRequired)reviewStrategies({range_reversal:{enabled:true,policy_version:f.policyVersion}});});
$('crypto-recheck').addEventListener('click',recheckCryptoIncidents);
$('range-settings-form').addEventListener('input',()=>{rangeDirty=true;strategyError='';if(snapshot)renderStrategyControls();});
$('range-settings-form').addEventListener('submit',event=>{
  event.preventDefault();if(!snapshot?.portfolio||strategySaving||toggling||saving)return;
  const value=$('range-amount').value,amount=Number(value),symbols=[...($('range-btc').checked?['BTC/USD']:[]),...($('range-eth').checked?['ETH/USD']:[])];
  if(!/^\d+(\.\d{1,2})?$/.test(value)||!Number.isFinite(amount)||amount<1||amount>1e12||!symbols.length){strategyError='Choose at least one crypto market and enter a target of $1 or more with up to two decimal places.';renderStrategyControls();return;}
  reviewStrategies({range_reversal:{target_dollars:amount.toFixed(2),symbols,policy_version:portfolioView(snapshot).families[1].policyVersion}});
});
$('cancel-strategy').addEventListener('click',()=>{strategyReview=null;$('strategy-dialog').close();});
$('accept-strategy-policy').addEventListener('change',()=>{$('confirm-strategy').disabled=strategySaving||!$('accept-strategy-policy').checked;});
$('strategy-review-form').addEventListener('submit',event=>{event.preventDefault();if(!strategyReview||!$('accept-strategy-policy').checked)return;
  if(strategyReviewFingerprint!==permissionFingerprint()){$('strategy-dialog-error').textContent='Strategy settings changed while this review was open. Close it and review the current settings before applying.';return;}
  changeStrategies(strategyReview);
});
$('settings-form').addEventListener('input',()=>{dirty=true;saveError='';formChanged();});
$('settings-form').addEventListener('submit',async event=>{
  event.preventDefault();if(saving||strategySaving||toggling)return;const settings=formSettings();const error=settingsError(settings,snapshot);if(error){$('save-message').textContent=error;return;}
  saving=true;generation++;saveError='';formChanged();$('save-message').textContent='Saving…';
  try{const response=await fetch(new URL('settings',api),{method:'PUT',headers:{'Content-Type':'application/json','X-Pivot-Intent':'settings'},body:JSON.stringify(settings),signal:AbortSignal.timeout(8000)});const result=await response.json();if(!response.ok)throw Error(result.detail || 'Could not save');snapshot.settings=result;if(snapshot.portfolio?.socrates)snapshot.portfolio.socrates.target_dollars=result.target_dollars;dirty=false;$('save-message').textContent='Purchase target saved.';}
  catch(error){saveError=error.name==='TimeoutError'?'Save status is uncertain. Refresh before retrying.':error.message;}
  finally{saving=false;render();}
});
refresh();setInterval(refresh,10000);
