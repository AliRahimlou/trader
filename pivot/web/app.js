import {money, escape as esc, age, ago, sizeHint, settingsError, vixStatus, appStatus} from './model.mjs';
const localPreview = location.port === '5173' && ['127.0.0.1','localhost'].includes(location.hostname);
const api = localPreview ? new URL(`http://${location.hostname}:8011/api/`) : new URL('./api/',document.baseURI);
const $ = id => document.getElementById(id);
let snapshot=null, dirty=false, saving=false, fetching=false, generation=0, saveError="", toggling=false, liveError='';
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
  $('runtime-status').textContent=s.hosting?.message || 'Checking where the app is running…';
  $('deployment-status').hidden=s.hosting?.mode!=='hosted';
  $('deployment-status').textContent=`${s.revision?`Version ${s.revision.slice(0,7)} · `:''}${s.deployment?.message || 'Checking update status…'}`;
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
  const status=appStatus(s), vixDisplay=vixStatus(s.data_health?.vix);
  $('status-title').textContent=status.title;
  $('live-status').innerHTML=`Live money <strong>${s.live_enabled?'On':'Off'}</strong><span class="switch" aria-hidden="true"></span>`;
  $('live-status').classList.toggle('is-on',s.live_enabled);
  $('live-status').disabled=toggling || !s.execution_available;
  $('live-status').setAttribute('aria-label', s.live_enabled?'Live money on. Turn off new entries':'Live money off. Review and turn on');
  $('status-text').textContent=liveError || status.text;
  const setup=s.setup, failed=setup?.checks.find(c=>!c.passed);
  const headline=failed?.name==='Magnificent Seven at their zones'?'Waiting for the tech stocks to react together at their levels.':failed?.detail || 'Waiting for current market observations.';
  const stage=(age(s.analysis_at)>90?'STALE_OBSERVATIONS':setup?.state || 'WAITING_FOR_DATA').replaceAll('_',' ').toLowerCase();
  const evidenceOpen=$('strategy-cards').querySelector('details')?.open;
  $('strategy-cards').innerHTML=`<article class="card strategy"><div class="section-heading"><h3>Nasdaq levels & confirmation</h3><span class="pill">${esc(stage)}</span></div><p class="observation">${esc(headline)}</p><p class="help">Observing QQQ · Nasdaq ETF proxy</p><ol class="steps"><li>Premark repeated 4-hour levels or previous-day extremes</li><li>Wait for the matching break/retest or sweep</li><li>Confirm the technology stocks at their own levels</li><li>Confirm the actual VIX reacting from its level</li></ol>${setup?.direction?`<div class="levels"><span>${esc(setup.direction.toUpperCase())} · ${esc(setup.event)}</span><span>Signal reference ${money(setup.entry)} · Stop ${money(setup.stop)} · Target ${money(setup.target)}</span></div>`:''}<details class="evidence"><summary>Current observations & checks</summary>${setup?setup.checks.map(c=>`<p><b class="${c.passed?'pass':'wait'}">${c.passed?'✓':'○'} ${esc(c.name)}</b><span>${esc(c.detail)}</span></p>`).join(''):'<p>Waiting for valid data.</p>'}${s.observations.map(o=>`<p><b>${esc(o.symbol)} · ${money(o.price)}</b><span>${esc(o.kind)} · ${ago(o.at)}</span></p>`).join('')}</details></article><div class="notice"><strong>${esc(vixDisplay.headline)}</strong><p>Entries require the full video setup, your live-money permission and current broker checks. The first clip’s tape and VWAP rules are not required.</p></div>`;
  if(evidenceOpen)$('strategy-cards').querySelector('details').open=true;
  $('holding-count').textContent=`${s.positions.length} positions · ${s.orders.length} orders`;
  $('holdings').innerHTML=(!s.positions.length&&!s.orders.length)?`<p class="muted">${stale?'No positions in the last snapshot. Awaiting a fresh broker update.':'No open positions or working orders.'}</p>`:
    s.positions.map(p=>`<div class="holding"><strong>${esc(p.symbol)}</strong><span>${esc(p.qty)} shares · ${esc(p.side)}</span><b>${money(p.unrealized_pl)}</b></div>`).join('')+s.orders.map(o=>`<div class="holding"><strong>${esc(o.symbol)}</strong><span>${esc(o.side)} ${esc(o.qty)} · ${esc(o.type)}</span><b>${esc(o.status)}</b></div>`).join('');
  $('current-size').textContent=s.settings.sizing_mode==='target'?`Saved target: ${money(s.settings.target_dollars)} per purchase.`:'Saved mode: automatic allocation.';
  if(!dirty&&!saving) {
    $('amount').value=s.settings.target_dollars;
  }
  const health=s.data_health, stocks=health?.stocks, vix=health?.vix, quote=s.quote_health;
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
    <details class="feed-details"><summary>Check every data input</summary>
      ${stocks?.instruments.map(i=>`<article><b>${esc(i.symbol)}</b><span class="${i.status==='current'&&!healthStale?'pass':'wait'}">${healthStale?'Refresh overdue':esc(i.status.replaceAll('_',' '))}</span>
        <ul>${i.frames.map(f=>`<li><span>${esc(f.label)} · ${f.count} candles</span><span>${esc(candleTime(f.latest_at))}${f.status!=='current'?` · ${esc(f.reason)}`:''}</span></li>`).join('')}</ul></article>`).join('') || '<p>Waiting for the first validated update.</p>'}
      <article><b>VIX · actual index</b><p>${vix?.latest_at?`${esc(vix.bar_count)} completed candles · ${esc(candleTime(vix.latest_at))}`:'No verified completed candles available'}</p><p>${esc(vix?.verification?.timeframe || 'Real-time access unverified')}</p>
        ${vixDisplay.lastQuote?`<p class="help">${esc(vixDisplay.lastQuote)}</p>`:''}
        ${vixDisplay.budgetText?`<p class="help">${esc(vixDisplay.budgetText)}</p>`:''}
        ${vixDisplay.nextRefreshAt?`<p class="help">Next candle check: ${esc(candleTime(vixDisplay.nextRefreshAt))}</p>`:''}
        ${vix?.verification?.history_received_at?`<p class="help">History received: ${esc(candleTime(vix.verification.history_received_at))}</p>`:''}
      </article>
      <p class="help">Last stock refresh: ${stocks?.fetch_seconds!=null?stocks.fetch_seconds.toFixed(1)+'s':'—'} · ${esc(stocks?.refresh_mode || 'starting')}. Updated ${ago(stocks?.checked_at)}.</p>
      <p class="help">${esc(stocks?.frame_policy || '')}</p>
    </details>`+s.data_errors.filter(e=>!e.startsWith('VIX:')).map(e=>`<p class="help error">${esc(e)}</p>`).join('');
  if(detailOpen)$('data-connections').querySelector('details').open=true;
  $('journal').innerHTML=s.events.length?s.events.map(e=>`<p><b>${esc(e.kind.replaceAll('_',' '))}</b> · ${esc(new Date(e.at).toLocaleString())}${e.detail?.reason?`<br>${esc(e.detail.reason)}`:''}</p>`).join(''):'<p class="muted">No saved changes yet.</p>';
  $('trade-results').innerHTML=(s.trade_results || []).length?s.trade_results.map(t=>`<p><b>${esc(t.symbol)} · ${esc(t.direction || 'Trade')}</b>${t.completed_at?` · ${esc(new Date(t.completed_at).toLocaleString())}`:''}<br>${t.status==='verified_gross'?`Gross result ${money(t.gross_pnl)} · ${esc(t.quantity)} shares. Fees are not included; net profit is not verified.`:'Result unverified: complete broker fill evidence is unavailable.'}</p>`).join(''):'<p class="muted">No completed trades recorded. Trading profit has not been established.</p>';
  formChanged();
}
async function refresh() {
  if(fetching||saving||toggling)return; fetching=true; const version=generation;
  try {const response=await fetch(new URL('snapshot',api),{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw Error(); const next=await response.json(); if(version===generation&&!saving&&!toggling){snapshot=next;render();}}
  catch { $('status-title').textContent='App connection unavailable';$('status-text').textContent='Displayed information may be outdated. Reconnecting…';$('save-size').disabled=true;$('live-status').disabled=true;$('live-status').innerHTML='Live money <strong>Unknown</strong>'; }
  finally{fetching=false;}
}
async function changeLive(enabled) {
  if(toggling)return;
  liveError='';toggling=true;generation++;$('live-status').disabled=true;$('confirm-live').disabled=true;$('cancel-live').disabled=true;
  $('live-error').textContent='';
  try {
    const response=await fetch(new URL('live',api),{method:'PUT',headers:{'Content-Type':'application/json','X-Pivot-Intent':'live-control'},body:JSON.stringify({enabled,policy_version:snapshot.execution_policy.version}),signal:AbortSignal.timeout(30000)});
    const result=await response.json();
    if(!response.ok)throw Error(result.detail || 'Could not update live money');
    snapshot=result;$('live-dialog').close();
  } catch(error) {
    const message=error.name==='TimeoutError'?'The result is uncertain. Check the refreshed switch before retrying.':error.message;
    liveError=message;$('live-error').textContent=message;
    $('status-text').textContent=message;
  } finally {
    toggling=false;$('cancel-live').disabled=false;$('confirm-live').disabled=!$('accept-policy').checked;render();refresh();
  }
}
$('live-status').addEventListener('click',()=>{
  if(!snapshot||toggling)return;
  if(snapshot.live_enabled){changeLive(false);return;}
  $('live-summary').textContent=`Real Alpaca account · ${money(snapshot.settings.target_dollars)} target per purchase.`;
  $('live-policy').innerHTML=snapshot.execution_policy.summary.map(line=>`<li>${esc(line)}</li>`).join('');
  $('live-data-note').textContent='Turning on permits orders only after the full strategy, completed VIX candles, a fresh VIX quote and broker checks pass. The free-data request limit can pause new entries; the app will not buy extra data.';
  $('live-error').textContent='';$('accept-policy').checked=false;$('confirm-live').disabled=true;$('live-dialog').showModal();
});
$('cancel-live').addEventListener('click',()=>$('live-dialog').close());
$('accept-policy').addEventListener('change',()=>{$('confirm-live').disabled=toggling || !$('accept-policy').checked;});
$('live-form').addEventListener('submit',event=>{event.preventDefault();if($('accept-policy').checked)changeLive(true);});
$('settings-form').addEventListener('input',()=>{dirty=true;saveError='';formChanged();});
$('settings-form').addEventListener('submit',async event=>{
  event.preventDefault();if(saving)return;const settings=formSettings();const error=settingsError(settings,snapshot);if(error){$('save-message').textContent=error;return;}
  saving=true;generation++;saveError='';formChanged();$('save-message').textContent='Saving…';
  try{const response=await fetch(new URL('settings',api),{method:'PUT',headers:{'Content-Type':'application/json','X-Pivot-Intent':'settings'},body:JSON.stringify(settings),signal:AbortSignal.timeout(8000)});const result=await response.json();if(!response.ok)throw Error(result.detail || 'Could not save');snapshot.settings=result;dirty=false;$('save-message').textContent='Purchase target saved.';}
  catch(error){saveError=error.name==='TimeoutError'?'Save status is uncertain. Refresh before retrying.':error.message;}
  finally{saving=false;render();}
});
refresh();setInterval(refresh,10000);
