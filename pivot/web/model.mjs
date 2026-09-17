export const money = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(Number(value)) : '—';
export const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function age(at, now=Date.now()) { const ms = now-Date.parse(at); return Number.isFinite(ms) && ms >= -5000 ? Math.max(ms,0)/1000 : Infinity; }
export function ago(at) { const seconds=age(at); return !Number.isFinite(seconds) ? 'Not updated' : seconds<60 ? `${Math.floor(seconds)}s ago` : `${Math.floor(seconds/60)}m ago`; }
export function sizeHint(value) { return `Target purchase: ${money(value)}. Planned amount must be within 1% below the target. Unsupported quantities are skipped; actual fills may differ.`; }
export function vixStatus(vix, now=Date.now()) {
  const verification=vix?.verification || {};
  const expires=Date.parse(vix?.valid_until);
  const ready=vix?.status==='current' && vix.candles_current!==false && Number.isFinite(expires) && expires>=now;
  const closed=vix?.status==='market_closed';
  const budget=verification.budget;
  const budgetValid=budget && [budget.used,budget.limit].every(Number.isSafeInteger) && budget.used>=0 && budget.limit>0 && budget.used<=budget.limit;
  const countsValid=budgetValid && [budget.actual_requests,budget.reserved].every(Number.isSafeInteger) && budget.actual_requests>=0 && budget.reserved>=0 && budget.actual_requests+budget.reserved===budget.used;
  const remaining=budgetValid?(budget.limit-budget.used).toLocaleString('en-US'):'';
  const next=Date.parse(verification.next_refresh_at);
  const value=verification.latest_value;
  const quoteAge=age(verification.latest_value_at,now);
  const quoteValid=typeof value==='number' && Number.isFinite(value) && value>0 && Number.isFinite(quoteAge);
  return {
    label: closed?'Market closed':ready?'Candles ready':'Waiting',
    tone: ready?'pass':'wait',
    headline: closed?'Market closed · saved VIX history':ready?'Actual VIX candles are ready.':'Waiting for verified VIX candles.',
    quoteNote: 'A fresh VIX quote is checked before an entry.',
    lastQuote: quoteValid?`Last quote check: ${value.toFixed(2)} · ${quoteAge<60?`${Math.floor(quoteAge)}s`:`${Math.floor(quoteAge/60)}m`} ago`:'',
    budgetText: countsValid?`${budget.actual_requests.toLocaleString('en-US')} app requests used · ${budget.reserved.toLocaleString('en-US')} reserved · ${remaining} available`:budgetValid?`${remaining} requests available within the app limit`:'',
    nextRefreshAt: Number.isFinite(next)?new Date(next).toISOString():null,
    error: vix?.error || (!closed && vix?.status==='current' && !ready?'VIX candle verification expired; waiting for the next update.':null),
  };
}
export function appStatus(snapshot, now=Date.now()) {
  const trade=snapshot.execution?.trade;
  if(trade) return {title:`QQQ · ${trade.stage}`,text:snapshot.execution?.message || 'Managing the open position.'};
  if(snapshot.review_required || snapshot.execution?.review_required) return {
    title:'Review updated live rules',
    text:'The execution rules have changed. Use the Live money switch at the top right to review the updated rules before allowing new entries.',
  };
  const gate=snapshot.deployment_gate;
  if(gate?.configured===true && (gate.locked===true || gate.hold_present===true || gate.error)) return {
    title:snapshot.live_enabled?'Live money on · entries paused for update':'New entries paused for update',
    text:gate.error?'The app cannot verify its update lock. New entries remain paused; existing positions continue their exits. Your saved Live money setting is unchanged.':
      gate.hold_present===true && gate.hold_valid!==true?'A saved update hold needs verification. New entries remain paused until it is resolved. Existing positions continue their exits; your saved Live money setting is unchanged.':
      'An app update is temporarily holding new entries. Existing positions continue their exits; your saved Live money setting is unchanged.',
  };
  if(snapshot.clock?.is_open===false && age(snapshot.account_at,now)<=60 && !snapshot.account_error) return {
    title:snapshot.live_enabled?'Live money on · market closed':'Market closed · watching account status',
    text:'The regular market session is closed. New entries wait for the next session and fresh strategy checks.',
  };
  if (snapshot.data_health?.stocks && snapshot.data_health.stocks.status!=='current') return {
    title:'Waiting for complete stock data',
    text:'One or more required stock candles are missing or out of date. Open Data connections to see the affected input.',
  };
  const title=age(snapshot.analysis_at,now)>90?'Waiting for a current analysis':snapshot.live_enabled?
    (vixStatus(snapshot.data_health?.vix,now).label==='Candles ready'?'Live money on · watching for a setup':'Live money on · waiting for VIX'):'Watching both Nasdaq methods';
  return {title,text:snapshot.execution?.message || 'Live money is off. Analysis continues.'};
}
export function strategyViews(snapshot, now=Date.now()) {
  const current=age(snapshot?.analysis_at,now)<=90;
  const marketClosed=snapshot?.clock?.is_open===false && age(snapshot.account_at,now)<=60 && !snapshot.account_error;
  const methods=snapshot?.setup?.strategies;
  if (!Array.isArray(methods) || !methods.length) return [{id:'waiting',label:'Nasdaq methods',state:'Waiting for analysis',
    detail:'Waiting for the first complete analysis of both entry methods.',checks:[],current:false,selected:false}];
  return methods.map(method=>{
    const checks=Array.isArray(method.checks)?method.checks:[];
    const blocked=checks.find(check=>check.passed!==true);
    const qualified=method.state==='SETUP_READY' && checks.length>0 && !blocked;
    const state=marketClosed?'Market closed':!current?'Analysis out of date':qualified?'Setup found':String(method.state || 'WATCHING').replaceAll('_',' ').toLowerCase();
    const detail=marketClosed?'Waiting for the next regular session and fresh completed candles. Saved history remains available under Data connections.':!current?'Waiting for a current analysis. Saved observations cannot authorize an entry.':
      qualified?'The strategy checks pass. Fresh broker checks and your Live money permission are still required.':
      blocked?.name==='Premarked levels'?(method.id==='prior_day_sweep'?'Waiting for verified previous-day high and low.':'Waiting for repeated historical touches or crossings to establish an area.'):
      blocked?.name==='Magnificent Seven at their zones'?'Waiting for at least four technology leaders to agree, with none opposing.':
      blocked?.detail || 'Waiting for the next qualifying observation.';
    const reference=snapshot?.observations?.find(row=>row.symbol==='QQQ')?.price;
    const levels=(Array.isArray(method.levels)?method.levels:[]).filter(z=>[z.low,z.high].every(n=>typeof n==='number'&&Number.isFinite(n)&&n>0)&&z.low<=z.high);
    const distance=z=>Math.max(z.low-reference,reference-z.high,0);
    const area=typeof reference==='number' && Number.isFinite(reference)?levels.sort((a,b)=>distance(a)-distance(b)||a.low-b.low)[0]:null;
    return {...method,checks,state,detail,current,area:current?area:null,qualified:current&&!marketClosed&&qualified,selected:snapshot.setup.strategy_id===method.id};
  });
}
export function leaderOverview(snapshot, now=Date.now()) {
  const names=['AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA'];
  if (snapshot?.clock?.is_open===false && age(snapshot.account_at,now)<=60 && !snapshot.account_error)
    return {current:false,detail:'Market closed · five-minute signals resume with the next regular session.',
      rows:names.map(symbol=>({symbol,vote:null,label:'Closed',reason:'Market closed; saved candle history is available under Data connections.'}))};
  const trace=snapshot?.decision_trace;
  if (!trace || trace.current_at_snapshot!==true || age(trace.captured_at,now)>90 || snapshot.diagnostic_error)
    return {current:false,detail:'Waiting for current saved leader observations.',rows:[]};
  const rows=names.map(symbol=>{
    const observation=trace.leaders?.[symbol] || {};
    const vote=['long','short'].includes(observation.vote)?observation.vote:null;
    return {symbol,vote,label:vote==='long'?'Up':vote==='short'?'Down':'Waiting',reason:observation.reason || 'Observation missing'};
  });
  const up=rows.filter(row=>row.vote==='long').length, down=rows.filter(row=>row.vote==='short').length;
  return {current:true,rows,detail:`5-minute leaders · ${up} up / ${down} down. Need 4 agreeing and none opposing.`};
}
export function loggingStatus(snapshot, now=Date.now()) {
  const signal=snapshot?.decision_trace, execution=snapshot?.execution_check;
  const fresh=(record,seconds)=>{
    const elapsed=now-Date.parse(record?.captured_at);
    return record?.persisted===true && record.current_at_snapshot===true &&
      Number.isFinite(elapsed) && elapsed>=0 && elapsed<=seconds*1000;
  };
  const signalCurrent=fresh(signal,90) && !snapshot?.diagnostic_error;
  const executionCurrent=fresh(execution,30) && execution.from_current_process===true && !snapshot?.execution_diagnostic_error;
  const error=snapshot?.diagnostic_error || snapshot?.execution_diagnostic_error;
  return {label:error?'Needs attention':signalCurrent&&executionCurrent?'Recording':'Checking records',
    tone:error?'error':signalCurrent&&executionCurrent?'pass':'wait',
    detail:error?'Some checks could not be saved. Earlier records remain available.':
      signalCurrent&&executionCurrent?'Strategy decisions and execution checks are saved. Recording does not mean an entry has qualified.':
      'Waiting for fresh saved strategy and execution checks. Earlier records may be available.'};
}
export function releaseStatus(snapshot, now=Date.now()) {
  const revisionOf=value=>typeof value==='string' && /^[a-f0-9]{40}$/.test(value)?value:null;
  const version=snapshot?.app_version;
  const validVersion=typeof version==='string' && version.length<=64 && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.test(version);
  const revision=revisionOf(snapshot?.revision);
  const deployment=snapshot?.deployment || {};
  const state=typeof deployment.state==='string'?deployment.state:'';
  const candidate=revisionOf(deployment.candidate_revision);
  const candidateRevision=candidate && candidate!==revision?candidate:null;
  const local=snapshot?.hosting?.mode==='local';
  const label=local?`Local preview${validVersion?` · v${version}`:''}${revision?` · ${revision.slice(0,7)}`:''}`:
    revision?(validVersion?`Installed v${version} · ${revision.slice(0,7)}`:`Version ${revision.slice(0,7)}`):
    validVersion?`Version ${version} · revision unavailable`:'Version unavailable';
  const result={label,detail:'Installed version could not be verified.',tone:'unknown',revision,candidateRevision,current:false,queued:false};
  if(local) return {...result,detail:'Running on this machine.',candidateRevision:null};
  if(!revision) return result;

  const checked=typeof deployment.checked_at==='string' && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(deployment.checked_at)?Date.parse(deployment.checked_at):NaN;
  const elapsed=now-checked;
  const fresh=Number.isFinite(now) && Number.isFinite(elapsed) && elapsed>=0 && elapsed<=600000;
  const activeRevision=revisionOf(deployment.active_revision);
  const pending=candidateRevision?.slice(0,7);
  if(!fresh) return {...result,detail:`${pending?`Last reported update ${pending} is not installed. `:''}${Number.isFinite(elapsed) && elapsed>=0?'Update check is out of date.':'Update check time is unavailable or invalid.'}`};
  if(activeRevision!==revision) return {...result,detail:`${pending?`Update ${pending} is not installed. `:''}Update status does not match the running version; verification is needed.`};
  if(pending) {
    const details={
      waiting_off:`Update ${pending} queued · not installed. The installed updater needs a one-time upgrade.`,
      bootstrap_required:`Update ${pending} queued · not installed. The installed updater needs a one-time upgrade.`,
      waiting_entry:`Update ${pending} queued · not installed. Waiting for the current entry check to finish.`,
      blocked_exposure:`Update ${pending} queued · not installed. Waiting for a verified account with no open positions or orders.`,
      recovery_required:`Update ${pending} needs recovery. New entries remain paused; installation is unconfirmed.`,
      built:`Update ${pending} prepared · not installed.`,
      building:`Preparing update ${pending} · not installed.`,
      deploying:`Installing update ${pending} · still running ${revision.slice(0,7)}.`,
      rolled_back:`Update ${pending} was not installed successfully; the previous version was restored.`,
      error:`Update ${pending} is not installed; update checks need attention.`,
    };
    return {...result,detail:Object.hasOwn(details,state)?details[state]:`Update ${pending} is not installed; update status needs verification.`,
      tone:['waiting_off','bootstrap_required','waiting_entry','blocked_exposure','built','building','deploying'].includes(state)?'pending':'unknown',
      queued:['waiting_off','bootstrap_required','waiting_entry','blocked_exposure','built'].includes(state)};
  }
  if(state==='current') return {...result,detail:'Latest checked release.',tone:'current',current:true};
  const details={
    updated:'Update installed; awaiting the next version check.',
    checking:'Checking for an update.',
    building:'Preparing an update; installed version shown.',
    deploying:'Update installation in progress; installed version shown.',
    rolled_back:'Previous version restored after an unsuccessful update.',
    recovery_required:'Update recovery needs attention. New entries remain paused.',
    error:'Update checks need attention; installed version shown.',
  };
  return {...result,detail:Object.hasOwn(details,state)?details[state]:'Update status is unconfirmed; installed version shown.'};
}
export function settingsError(settings, snapshot, now=Date.now()) {
  const value=Number(settings.target_dollars);
  if (!Number.isFinite(value) || value<1 || value>1e12 || Math.abs(value*100-Math.round(value*100))>0.0001) return 'Enter at least $1, with up to two decimal places.';
  if (!snapshot || snapshot.account_error || age(snapshot.account_at,now)>60) return 'Wait for a current account balance.';
  if (snapshot.live_enabled || snapshot.execution?.trade) return 'Turn Live money off and wait for the current trade to finish before changing size.';
  if (snapshot.positions.length || snapshot.orders.length) return 'Wait until current positions and orders have finished.';
  if (value>Number(snapshot.account.buying_power)) return 'That target exceeds your current buying power.';
  return '';
}
