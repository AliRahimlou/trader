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
    retryAt: Number.isFinite(Date.parse(verification.retry_at))?verification.retry_at:null,
    quoteRetryAt: Number.isFinite(Date.parse(verification.entry_quote_retry_at))?verification.entry_quote_retry_at:null,
    error: vix?.error || (!closed && vix?.status==='current' && !ready?'VIX candle verification expired; waiting for the next update.':null),
  };
}
export function operationStatus(snapshot, now=Date.now()) {
  const health=snapshot.worker_health;
  const workers=health?.workers || [];
  const issues=workers.filter(row=>row.status!=='running');
  const partial=snapshot.execution?.trade?.partial_entry;
  const activePartial=partial?.raised_at && !partial.resolved_at;
  const exit=snapshot.execution?.trade?.exit_pending;
  const activeExit=exit?.raised_at && !exit.resolved_at;
  const archive=snapshot.input_archive;
  const archiveCurrent=archive?.status==='recording' && age(archive.captured_at,now)<=90;
  const quote=snapshot.quote_health;
  const price=Number(quote?.ask);
  const target=Number(snapshot.settings?.target_dollars);
  const smallTarget=Number.isFinite(price)&&price>0&&Number.isFinite(target)&&target>0&&target<price;
  const directionNote=smallTarget?`${money(target)} can support fractional QQQ buys. QQQ shorts require whole shares, so this target cannot open a short at the displayed price.`:'QQQ shorts require whole shares and broker approval. Purchase and stop-distance loss are different amounts.';
  return {
    workerLabel:!health?'Checking':health.ready?'Running':issues.some(row=>['stalled','stopped','error'].includes(row.status))?'Needs attention':'Starting',
    workerDetail:workers.map(row=>`${row.name}: ${row.status.replaceAll('_',' ')}`).join(' · '),
    incident:activeExit?'Exit needs attention: broker cancellation or the remaining exit is unconfirmed. New entries are paused. Check the QQQ position and orders in Alpaca. The app continues reconciliation without sending a competing order.':activePartial?`Partial entry needs attention: ${partial.filled_qty || 'some'} shares filled while cancellation is unconfirmed. New entries are paused. Check the position and orders in Alpaca.`:'',
    archiveLabel:archiveCurrent?'Recording inputs':archive?.status==='unavailable'?'Needs attention':'Waiting for inputs',
    directionNote:directionNote+(snapshot.account?.shorting_enabled===false?' Short selling is also disabled on this Alpaca account.':''),
    timingNote:'Current hourly methods: earliest same-day sweep confirmation 10:30 a.m. ET; break-and-retest 11:30 a.m. ET. These are eligibility times, not scheduled trades.',
  };
}

export function sessionReview(snapshot) {
  const review=snapshot.session_review;
  if(review?.status!=='available')return {available:false,detail:'Session history is not available yet.',stages:[],blockers:[],checks:[]};
  const blockers=Object.entries(review.latest_execution_blockers || {}).map(([name,count])=>({name:name.replaceAll('_',' '),count,scope:'Execution'}));
  blockers.push(...Object.entries(review.latest_signal_blockers || {}).map(([name,count])=>({name,count,scope:'Strategy'})));
  return {available:true,day:review.day,events:review.events_seen,checkpoints:review.checkpoints,
    stages:review.stages || [], orders:review.orders || {}, blockers, checks:review.latest_checks || [],
    detail:review.complete_candidate_coverage?'Distinct recorded events; repeated checks are not extra opportunities.':'Earlier records contain partial candidate history. Counts cover retained observations only.'};
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
    const leaderDeadline=Date.parse(method.leader_evidence_valid_until), eventDeadline=Date.parse(method.event_expires_at);
    const observationDeadline=Date.parse(method.leader_observation_valid_until);
    const timingKnown=Number.isFinite(leaderDeadline) && Number.isFinite(eventDeadline) && Number.isFinite(observationDeadline);
    const observationExpired=Number.isFinite(observationDeadline) && now>=observationDeadline;
    const timingCurrent=timingKnown && now<leaderDeadline && now<eventDeadline && !observationExpired;
    const expiredReady=method.state==='SETUP_READY' && !timingCurrent;
    const qualified=method.state==='SETUP_READY' && checks.length>0 && !blocked && timingCurrent;
    const state=marketClosed?'Market closed':!current?'Analysis out of date':expiredReady?
      (observationExpired?'Leader data out of date':timingKnown?'Confirmation expired':'Waiting for confirmation timing'):qualified?'Setup found':String(method.state || 'WATCHING').replaceAll('_',' ').toLowerCase();
    const detail=marketClosed?'Waiting for the next regular session and fresh completed candles. Saved history remains available under Data connections.':!current?'Waiting for a current analysis. Saved observations cannot authorize an entry.':
      expiredReady?(observationExpired?'The latest five-minute leader candles are too old. Waiting for fresh observations.':timingKnown?'A confirmation window has ended. Waiting for a new analysis.':'Confirmation timing is unavailable. Waiting for a complete analysis.'):
      qualified?'The strategy checks pass. Fresh broker checks and your Live money permission are still required.':
      blocked?.name==='Premarked levels'?(method.id==='prior_day_sweep'?'Waiting for verified previous-day high and low.':'Waiting for repeated historical touches or crossings to establish an area.'):
      blocked?.detail || 'Waiting for the next qualifying observation.';
    const reference=snapshot?.observations?.find(row=>row.symbol==='QQQ')?.price;
    const levels=(Array.isArray(method.levels)?method.levels:[]).filter(z=>[z.low,z.high].every(n=>typeof n==='number'&&Number.isFinite(n)&&n>0)&&z.low<=z.high);
    const distance=z=>Math.max(z.low-reference,reference-z.high,0);
    const area=typeof reference==='number' && Number.isFinite(reference)?levels.sort((a,b)=>distance(a)-distance(b)||a.low-b.low)[0]:null;
    return {...method,checks,state,detail,current,area:current?area:null,qualified:current&&!marketClosed&&qualified,selected:snapshot.setup.strategy_id===method.id};
  });
}
export function marketOverview(snapshot, now=Date.now()) {
  const context=snapshot?.market_context;
  const marketClosed=snapshot?.clock?.is_open===false && age(snapshot.account_at,now)<=60 && !snapshot.account_error;
  const current=!marketClosed && age(snapshot?.analysis_at,now)<=90 && context?.data_current===true &&
    snapshot?.data_health?.stocks?.status==='current';
  const names={up:'Upward structure',down:'Downward structure',mixed:'Mixed structure',unknown:'Waiting for structure'};
  const frames=[60,240].map(minutes=>{
    const frame=context?.frames?.find(f=>f.timeframe_minutes===minutes);
    const usable=current && frame?.status==='ready' && ['up','down','mixed'].includes(frame.direction);
    return {minutes,label:minutes===60?'1-hour view':'4-hour view',direction:usable?frame.direction:'unknown',
      value:usable?names[frame.direction]:marketClosed?'Market closed':frame?.status==='insufficient'?'More history needed':'Waiting for current data',
      detail:current?frame?.detail || 'Waiting for structure observations.':'Saved observations do not describe current entry readiness.',
      latestAt:frame?.latest_bar_at || null};
  });
  return {current,frames,detail:marketClosed?'Market closed · the overview resumes with fresh session data.':
    current?'QQQ price structure across completed candles. Entry setups and leader confirmation are checked separately.':
    'Waiting for current Nasdaq context.',
    explanation:'Higher swing highs and lows indicate upward structure; lower swing highs and lows indicate downward structure. Mixed means the evidence differs or price has broken the last supporting swing. This describes price history; it is not a forecast or an extra entry rule.'};
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
    const deadline=Date.parse(observation.evidence_valid_until);
    const expired=Number.isFinite(deadline) && deadline<=now;
    const observationDeadline=Date.parse(observation.observation_valid_until);
    const stale=Number.isFinite(observationDeadline) && observationDeadline<=now;
    const missing=!observation.reason || observation.reason==='current 5-minute data missing';
    const conflict=observation.conflicting_reactions===true;
    const vote=!stale && !expired && !missing && !conflict && ['long','short'].includes(observation.vote)?observation.vote:null;
    const reactionAge=age(observation.reaction_at,now);
    const reactionLabel=vote && Number.isFinite(reactionAge)?`${Math.floor(reactionAge/60)}m ago`:'';
    return {symbol,vote,missing,conflict,expired,stale,reactionLabel,
      label:missing?'Data missing':stale?'Data stale':conflict?'Conflicting':expired?'Expired':vote==='long'?'Up':vote==='short'?'Down':'Neutral',
      reason:stale?'The latest five-minute candle is too old; waiting for fresh data.':expired?'This reaction expired; waiting for a fresh analysis.':observation.reason || 'Observation missing',
      latestAt:observation.latest_bar_at || null};
  });
  const up=rows.filter(row=>row.vote==='long').length, down=rows.filter(row=>row.vote==='short').length;
  const rule=trace.setup?.leader_rule || snapshot?.setup?.leader_rule;
  const hasRule=Number.isInteger(rule?.minimum_agree) && rule.minimum_agree>=1 && rule.minimum_agree<=7 &&
    Number.isInteger(rule?.maximum_opposing) && rule.maximum_opposing>=0 && rule.maximum_opposing<rule.minimum_agree;
  const ruleText=hasRule?`Entry rule: at least ${rule.minimum_agree} agreeing; ${rule.maximum_opposing===0?'none opposing':`up to ${rule.maximum_opposing} opposing`}.`:'Waiting for the current entry rule.';
  const observational=names.every(symbol=>trace.leaders?.[symbol]?.observational_only===true);
  const timestamps=rows.map(row=>Date.parse(row.latestAt));
  const synchronized=timestamps.every(Number.isFinite) && new Set(timestamps).size===1;
  const issues=rows.filter(row=>row.missing || row.conflict || row.expired || row.stale);
  const majority=up>=4?'long':down>=4?'short':null;
  const dissent=majority?rows.filter(row=>row.vote && row.vote!==majority):[];
  const alignment=issues.length?`${issues.map(row=>`${row.symbol}: ${row.label.toLowerCase()}`).join(' · ')}.`:
    !synchronized?'Leader candles are still updating to the same observation time.':
    majority?`${majority==='long'?'Upward':'Downward'} majority${dissent.length?`; ${dissent.map(row=>row.symbol).join(', ')} ${dissent.length===1?'opposes':'oppose'}`:''}.`:
    'No majority of qualifying zone reactions.';
  const window=Number.isFinite(rule?.persistence_minutes)?rule.persistence_minutes:15;
  return {current:true,rows,detail:`Recent leader reactions · ${up} up / ${down} down.`,ruleText,alignment,
    timing:`Reactions can start on different five-minute candles and remain valid for up to ${window} minutes with continued confirmation.`,
    scope:observational?'Market observations only · waiting for a Nasdaq event.':'Leader confirmation for the leading analysis event.'};
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
  const stockLive=snapshot.portfolio?snapshot.portfolio.global_live_enabled===true && snapshot.portfolio.socrates?.enabled===true:snapshot.live_enabled;
  if (stockLive || snapshot.execution?.trade) return snapshot.portfolio?'Turn Socrates Off and wait for its current trade to finish before changing its size.':'Turn Live money off and wait for the current trade to finish before changing size.';
  if ((snapshot.positions.length || snapshot.orders.length) && snapshot.portfolio?.socrates?.settings_exposure_verified!==true)
    return snapshot.portfolio?'Wait for open positions and orders to be verified as belonging to another strategy before changing Socrates size.':'Wait until current positions and orders have finished.';
  if (value>Number(snapshot.account.buying_power)) return 'That target exceeds your current buying power.';
  return '';
}
