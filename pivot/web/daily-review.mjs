import {escape as esc} from './model.mjs';

const FAMILIES={socrates:'Socrates',range_reversal:'4H Range Reversal'};
const plain=value=>typeof value==='string'?value:'';
const count=value=>Number.isSafeInteger(value)&&value>=0?value:null;
const amount=value=>typeof value==='number'&&Number.isFinite(value)?value:
  typeof value==='string'&&/^[+-]?\d+(?:\.\d+)?$/.test(value)&&Number.isFinite(Number(value))?Number(value):null;
const cash=value=>value===null?'Not available':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:value!==0&&Math.abs(value)<.01?4:2}).format(value);
const verified=status=>['complete','verified'].includes(status);
const countText=value=>value===null?'—':String(value);

export function validReviewDay(value) {
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;
  const date=new Date(value+'T12:00:00Z');
  return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===value;
}

export function reviewDay(now=Date.now()) {
  if(!Number.isFinite(now))return null;
  const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);
  const part=type=>parts.find(value=>value.type===type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

export function previousReviewDay(day) {
  if(!validReviewDay(day))return null;
  return new Date(Date.parse(day+'T12:00:00Z')-86400000).toISOString().slice(0,10);
}

function timeLabel(value) {
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)||!Number.isFinite(Date.parse(value)))return 'Not recorded';
  return new Date(value).toLocaleString('en-US',{timeZone:'America/New_York',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' ET';
}

function collectionNotice(report) {
  const collection=report?.collection||{};
  if(['waiting_account','account_changed'].includes(collection.status))return 'Waiting for a fresh review of the current account. Previous records may not reflect its latest activity.';
  if(['unavailable','stale'].includes(collection.account_status))return 'The account check needs to refresh. This saved review may not reflect the latest activity.';
  if(collection.status==='refreshing')return 'Updating the review. Showing the last saved records.';
  if(collection.status==='overdue')return 'The review update is overdue. Showing the last saved records.';
  if(['unavailable','stale','error'].includes(collection.status))return 'The review could not be refreshed. Showing the last saved records.';
  if(collection.status==='partial')return 'Some account records are incomplete. Showing the records available so far.';
  if(collection.status==='starting')return 'The first review update is still in progress.';
  return '';
}

function familyView(id,raw) {
  const row=raw&&typeof raw==='object'&&!Array.isArray(raw)?raw:{};
  const net=verified(row.net_pnl_status)?amount(row.verified_net_pnl):null;
  const netLabel=net!==null?cash(net):row.net_pnl_status==='no_closed_trades'?'No closed trades':
    row.net_pnl_status==='partial'?'Some results pending':'Not verified';
  const trades=(Array.isArray(row.trades)?row.trades:[]).filter(trade=>trade&&typeof trade==='object').slice(0,30).map(trade=>{
    const pnl=verified(trade.cost_status)?amount(trade.net_pnl):null;
    const gross=trade.gross_status==='verified_gross'?amount(trade.gross_pnl):null;
    return {symbol:plain(trade.symbol)||'Market not recorded',entry:timeLabel(trade.created_at),exit:timeLabel(trade.completed_at),
      entryReason:plain(trade.entry_reason)||'Entry reason not recorded.',exitReason:plain(trade.exit_reason)||'Exit reason not recorded.',
      net:pnl,netLabel:pnl===null?'Result not verified':cash(pnl),grossLabel:gross===null?null:cash(gross)+' before fees',
      grossDetail:pnl===null?'Cost review still determines the final result.':'Before the verified costs above.',
      outcome:pnl===null?'Pending costs or fill review':pnl>0?'Gain after verified costs':pnl<0?'Loss after verified costs':'Even after verified costs'};
  });
  return {id,label:FAMILIES[id],entries:count(row.filled_entries),closed:count(row.closed_trades),open:count(row.open_at_end),
    attempts:count(row.submission_attempts),checks:count(row.checks_recorded),wins:count(row.wins),losses:count(row.losses),
    even:count(row.breakeven),unverified:count(row.unverified_outcomes),net,netLabel,trades,
    netDetail:net!==null?'Closed-trade result after verified costs.':row.net_pnl_status==='no_closed_trades'
      ?'There is no closed-trade return to assess.':'Missing fills or costs are not counted as zero.',
    blockers:(Array.isArray(row.blockers)?row.blockers:[]).filter(item=>item&&typeof item.reason==='string').slice(0,12)
      .map(item=>({reason:item.reason,count:count(item.count)}))};
}

export function dailyReviewView(report,selected='socrates') {
  const ids=selected==='all'?Object.keys(FAMILIES):[Object.hasOwn(FAMILIES,selected)?selected:'socrates'];
  const available=!!report&&report.schema==='daily-trading-review-v1'&&validReviewDay(report.day)
    &&['available','partial'].includes(report.status);
  if(!available)return {available:false,day:validReviewDay(report?.day)?report.day:null,families:[],notice:collectionNotice(report),
    summary:'Review not available',detail:'No saved review is available for this date. Missing records are not a zero-trade or zero-cost result.'};
  const families=ids.map(id=>familyView(id,report.families?.[id]));
  const total=field=>families.every(family=>family[field]!==null)?families.reduce((sum,family)=>sum+family[field],0):null;
  const entries=total('entries'),closed=total('closed');
  const fees=report.accounting?.fees||{},knownFees=amount(fees.observed_usd_cost);
  const change=report.accounting?.account_change||{},movement=change.status==='observed'?amount(change.cash_flow_adjusted_change_usd):null;
  const movementStart=timeLabel(change.start_at),movementEnd=timeLabel(change.end_at);
  const noTrades=entries===0&&closed===0&&families.every(family=>family.open===0);
  const scope=selected==='all'?'All strategies':FAMILIES[ids[0]];
  const summary=entries===null||closed===null?`${scope} · Some records unavailable`:`${scope} · ${entries} entries · ${closed} closed`;
  return {available:true,day:report.day,scope,families,entries,closed,noTrades,notice:collectionNotice(report),
    summary:collectionNotice(report)?'Saved records · '+summary:summary,
    detail:noTrades?'No entries, closed trades or open positions were recorded for this view.':
      'Recorded trades and checks for this date. Reasons describe what the app recorded; they do not establish why a strategy will win or lose next time.',
    generated:timeLabel(report.as_of||report.generated_at),reviewed:timeLabel(report.generated_at),period:report.period_status==='day_ended'?'Day ended · saved records':report.period_status==='in_progress'?'Day in progress · recorded so far':'Saved records',
    revision:Number.isSafeInteger(report.revision)&&report.revision>0?'#'+report.revision:'Not recorded',
    movement:movement!==null&&movementStart!=='Not recorded'&&movementEnd!=='Not recorded'
      ?{label:cash(movement),period:movementStart+' to '+movementEnd}:null,
    feesLabel:knownFees===null?'Not available':cash(knownFees)+' recorded',
    feesDetail:knownFees===null?'Account-wide cost records have not been verified.':fees.final===true&&verified(fees.status)
      ?'Recorded account-wide costs. These are not assigned to one strategy.':'Account-wide recorded costs; more costs may arrive. These are not a final total or a per-strategy charge.',
    missing:(Array.isArray(report.missing_evidence)?report.missing_evidence:[]).filter(value=>typeof value==='string'&&
      !Object.entries(FAMILIES).some(([id,label])=>!ids.includes(id)&&value.startsWith(label+':'))).slice(0,12),
    investigations:(Array.isArray(report.investigations)?report.investigations:[])
      .filter(row=>row&&typeof row==='object'&&(!row.family||ids.includes(row.family))).slice(0,12)
      .map(row=>({title:plain(row.title)||'Review needed',reason:plain(row.reason)}))};
}

function familyMarkup(family) {
  const tally=(value,label)=>`<div><strong>${esc(countText(value))}</strong><span>${label}</span></div>`;
  return `<article class="daily-family" data-review-family="${family.id}"><h3>${family.label}</h3>
    <div class="daily-review-counts">${tally(family.entries,'Filled entries')}${tally(family.closed,'Closed trades')}${tally(family.open,'Open at review')}</div>
    <p class="daily-result"><span>Closed results</span><strong>${esc(family.netLabel)}</strong></p><p class="help">${esc(family.netDetail)}</p>
    <p class="help">Verified outcomes: ${esc(countText(family.wins))} gains · ${esc(countText(family.losses))} losses · ${esc(countText(family.even))} even. ${esc(countText(family.unverified))} outcomes awaiting verification.</p>
    <details class="evidence" data-review-details="${family.id}"><summary>Entry checks and recorded reasons</summary><p>${esc(countText(family.checks))} checks recorded · ${esc(countText(family.attempts))} entry attempts. Attempts are not fills.</p>
      ${family.blockers.length?`<ul class="daily-review-list">${family.blockers.map(row=>`<li>${esc(row.reason)} <span>(${esc(countText(row.count))} recorded)</span></li>`).join('')}</ul>`:'<p>No detailed entry blockers recorded.</p>'}
      ${family.trades.map(trade=>`<div class="daily-trade"><strong>${esc(trade.symbol)}</strong><p>${esc(trade.netLabel)} · ${esc(trade.outcome)}</p>${trade.grossLabel?`<p>${esc(trade.grossLabel)} · ${esc(trade.grossDetail)}</p>`:''}<p>Entry plan: ${esc(trade.entry)} · ${esc(trade.entryReason)}</p><p>Exit recorded: ${esc(trade.exit)} · ${esc(trade.exitReason)}</p></div>`).join('')||'<p>No individual trade records in this review.</p>'}
    </details></article>`;
}

export function dailyReviewMarkup(report,selected='socrates') {
  const view=dailyReviewView(report,selected);
  const notice=view.notice?`<p class="daily-review-notice" role="status">${esc(view.notice)}</p>`:'';
  if(!view.available)return `${notice}<p class="help daily-review-empty">${esc(view.detail)}</p>`;
  return `${notice}<p class="daily-review-context">${esc(view.day)} · New York day · ${esc(view.scope)}</p><p class="help">${esc(view.period)}. ${esc(view.detail)} Closed results cover trades closed on this date, including earlier entries.</p>
    <div class="daily-family-grid">${view.families.map(familyMarkup).join('')}</div>
    <div class="daily-costs"><strong>Account-wide costs: ${esc(view.feesLabel)}</strong><p class="help">${esc(view.feesDetail)}</p></div>
    ${view.movement?`<div class="daily-costs"><strong>Account movement during recorded period: ${esc(view.movement.label)}</strong><p class="help">${esc(view.movement.period)}. Adjusted for recorded deposits and withdrawals. Includes open-position changes across the account; this is not strategy profit or a full-day return.</p></div>`:''}
    ${view.investigations.length?`<div class="daily-investigations"><h3>What needs a closer look</h3><ul class="daily-review-list">${view.investigations.map(row=>`<li><strong>${esc(row.title)}</strong>${row.reason?`<p>${esc(row.reason)}</p>`:''}</li>`).join('')}</ul></div>`:''}
    ${view.missing.length?`<details class="evidence" data-review-details="missing"><summary>Records still needed</summary><ul class="daily-review-list">${view.missing.map(text=>`<li>${esc(text)}</li>`).join('')}</ul></details>`:''}
    <p class="help daily-review-stamp">Reviewed ${esc(view.reviewed)} · Records through ${esc(view.generated)} · Review revision ${esc(view.revision)}. Results from a short period do not establish profitability.</p>`;
}

export async function fetchDailyReview(fetcher,api,day,{signal}={}) {
  if(!validReviewDay(day))throw Error('Choose a valid review date.');
  const url=new URL('reviews',api);url.searchParams.set('day',day);
  const response=await fetcher(url,{method:'GET',signal});
  if(!response.ok)throw Error('Could not load the saved review. Try again.');
  const report=await response.json();
  if(!report||report.day!==day||report.schema!=='daily-trading-review-v1')throw Error('The saved review response was incomplete. Try again.');
  return report;
}
