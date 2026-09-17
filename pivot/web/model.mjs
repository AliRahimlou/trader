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
  if(snapshot.clock?.is_open===false && age(snapshot.account_at,now)<=60 && !snapshot.account_error) return {
    title:snapshot.live_enabled?'Live money on · market closed':'Market closed · watching account status',
    text:'The regular market session is closed. New entries wait for the next session and fresh strategy checks.',
  };
  const title=age(snapshot.analysis_at,now)>90?'Waiting for a current analysis':snapshot.live_enabled?
    (vixStatus(snapshot.data_health?.vix,now).label==='Candles ready'?'Live money on · watching for a setup':'Live money on · waiting for VIX'):'Watching the primary Nasdaq flow';
  return {title,text:snapshot.execution?.message || 'Live money is off. Analysis continues.'};
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
