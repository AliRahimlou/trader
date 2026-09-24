// Socrates readiness summary: pure render helpers over snapshot['socrates_readiness'].
// The backend decides every item; this module only words and draws it, tolerates
// missing or unknown fields, and never turns a failing item into a ready look.
// No DOM, no fetch and no permission change lives here: an action button only
// names a control the page already has, and that control keeps its own review dialog.
import {escape as esc} from './model.mjs';

export const READINESS_ORDER=['live_permission','strategy_enabled','account','buying_power','instrument_qqq','instrument_psq',
  'market_session','data_qqq','data_leaders','data_vix','entry_allowance','deployment','exposure','workers','setup'];
const ITEM_STATUS=new Set(['ok','warn','fail','info']);
export const ICONS={ok:'✓',warn:'!',fail:'✕',info:'i'};
const ICON_TEXT={ok:'OK',warn:'Check',fail:'Needs action',info:'Note'};
// A failing item the backend marks needs_owner:false blocks entries but clears by itself (late data,
// the monthly VIX allowance): it pauses Socrates without asking the owner to do anything.
const WAIT_TEXT='Blocking until it clears';
const ACTIONS=new Set(['live','socrates']);
export const STATUS_LABELS={ready:'Ready',waiting:'Waiting',blocked:'Needs your action',checking:'Checking'};
// A summary older than this is shown as unverified rather than as a current answer.
export const READINESS_MAX_AGE_MS=120000;
const text=value=>typeof value==='string'?value.trim():'';

function items(raw) {
  const rows=(Array.isArray(raw)?raw:[]).filter(row=>row&&typeof row==='object'&&!Array.isArray(row)).slice(0,30).map((row,index)=>({
    id:text(row.id)||`item-${index}`,
    label:text(row.label)||text(row.id)||'Check',
    // An unknown status can never read as passing.
    status:ITEM_STATUS.has(row.status)?row.status:'warn',
    detail:text(row.detail),
    // Only an explicit false means nothing for the owner to do; anything else is treated as needing action.
    needsOwner:row.needs_owner!==false,
  }));
  const rank=id=>{const at=READINESS_ORDER.indexOf(id);return at<0?READINESS_ORDER.length:at;};
  return rows.map((row,index)=>({row,index})).sort((a,b)=>rank(a.row.id)-rank(b.row.id)||a.index-b.index).map(({row})=>({...row,icon:ICONS[row.status],iconText:row.status==='fail'&&!row.needsOwner?WAIT_TEXT:ICON_TEXT[row.status]}));
}

// fallback: {title,text} from the older status line, used when the backend sends no readiness.
export function readinessView(snapshot,now=Date.now(),fallback=null) {
  const raw=snapshot?.socrates_readiness;
  if(!raw||typeof raw!=='object'||Array.isArray(raw)) {
    return {available:false,status:'checking',label:STATUS_LABELS.checking,headline:text(fallback?.title)||'Checking whether Socrates can trade…',
      detail:text(fallback?.text),nextAction:null,actionKind:null,items:[],counts:{ok:0,warn:0,fail:0,info:0,owner:0,waiting:0},checkedAt:null,stale:false,
      note:'The detailed checklist is not available from this app version.'};
  }
  const rows=items(raw.items);
  const counts={ok:0,warn:0,fail:0,info:0,owner:0,waiting:0};
  for(const row of rows){counts[row.status]++;if(row.status==='fail')counts[row.needsOwner?'owner':'waiting']++;}
  const checked=Date.parse(raw.checked_at),age=now-checked;
  const stale=!Number.isFinite(checked)||age>READINESS_MAX_AGE_MS||age<-60000;
  // The backend status is honoured, but a failing item the owner must act on always means
  // 'Needs your action', a failing item that clears by itself means 'Waiting', and 'ready' is
  // only shown with no failing or warning item and a current check.
  let status=['ready','waiting','blocked'].includes(raw.status)?raw.status:counts.owner?'blocked':'waiting';
  if(counts.owner)status='blocked';
  else if(status==='blocked')status='waiting';
  if(status==='ready'&&(counts.fail||counts.warn||!rows.length))status='waiting';
  if(stale)status='checking';
  const headline=text(raw.headline)||({ready:'Socrates has a ready setup.',waiting:'Socrates is waiting for a setup.',blocked:'Socrates needs your action before it can trade.',checking:'Checking whether Socrates can trade…'})[status];
  return {available:true,status,label:STATUS_LABELS[status],headline,detail:'',
    nextAction:text(raw.next_action)||null,
    // The control the backend names for its next step; undefined only from a backend that predates it.
    actionKind:raw.action===undefined?undefined:ACTIONS.has(raw.action)?raw.action:null,items:rows,counts,checkedAt:Number.isFinite(checked)?new Date(checked).toISOString():null,stale,
    note:stale?'This checklist is out of date. Waiting for a fresh check; nothing here can authorize an order.':''};
}

// Which existing control the next-action button may open. Both open a review dialog;
// neither changes a permission by itself. Global Live On is never offered here, so the
// button can never become the header's Off action. The button follows the control the
// backend names for its next step, so the button and the text beside it always agree.
// options.liveReviewable: the header's own rule (an enabled strategy that can trade);
// without it, Socrates On with order sending configured.
export function readinessAction(view,snapshot,options={}) {
  if(!view?.available||view.stale)return null;
  const failing=id=>view.items.some(row=>row.id===id&&row.status==='fail');
  const portfolio=snapshot?.portfolio;
  const globalOn=typeof portfolio?.global_live_enabled==='boolean'?portfolio.global_live_enabled:snapshot?.live_enabled===true;
  const liveReviewable=typeof options.liveReviewable==='boolean'?options.liveReviewable
    :portfolio?.socrates?.enabled===true&&snapshot?.execution_available===true;
  const live=failing('live_permission')&&!globalOn&&liveReviewable
    ?{kind:'live',label:snapshot?.review_required===true||portfolio?.socrates?.review_required===true?'Accept updated rules':'Turn Live money On'}:null;
  const socrates=failing('strategy_enabled')&&portfolio?.socrates?.enabled===false?{kind:'socrates',label:'Turn Socrates back on'}:null;
  if(view.actionKind!==undefined)return view.actionKind==='live'?live:view.actionKind==='socrates'?socrates:null;
  return live||socrates;
}

function time(value) {
  const ms=Date.parse(value);
  return Number.isFinite(ms)?new Date(ms).toLocaleTimeString('en-US',{timeZone:'America/New_York',hour:'numeric',minute:'2-digit',second:'2-digit'})+' ET':'';
}
export const readinessTimeText=checkedAt=>checkedAt?`Checked ${time(checkedAt)}`:'';
// Everything the card shows except the check time, so a refresh that changes nothing leaves the
// card (and a focused button) in place and only the time is updated.
export const readinessKey=(view,action=null)=>JSON.stringify([view.status,view.label,view.headline,view.detail,view.note,
  view.nextAction,action?.kind||null,action?.label||null,!!view.checkedAt,view.items.map(row=>[row.id,row.label,row.status,row.detail,row.needsOwner])]);

export function readinessItemsMarkup(rows) {
  return rows.length?`<ul class="readiness-list">${rows.map(row=>`<li class="readiness-item is-${row.status}" data-item="${esc(row.id)}"><span class="readiness-icon" aria-hidden="true">${row.icon}</span><span class="readiness-text"><b>${esc(row.label)}</b>${row.detail?`<span>${esc(row.detail)}</span>`:''}</span><span class="visually-hidden">${esc(row.iconText)}</span></li>`).join('')}</ul>`:'';
}

export function readinessMarkup(view,action=null) {
  // Anything that is not simply OK is shown first; the passing checks stay one click away.
  const notable=view.items.filter(row=>row.status!=='ok');
  const total=view.items.length;
  const owner=view.counts.owner??view.counts.fail,waiting=view.counts.waiting??0;
  const summary=total?`${view.counts.ok} of ${total} checks OK${owner?` · ${owner} need${owner===1?'s':''} action`:''}${waiting?` · ${waiting} blocking until ${waiting===1?'it clears':'they clear'}`:''}${view.counts.warn?` · ${view.counts.warn} to check`:''}`:'';
  const callout=view.nextAction||action?`<div class="next-action" role="note"><span class="next-action-label">Next step</span><p>${esc(view.nextAction||action.label)}</p>${action?`<button id="readiness-action" class="primary" type="button" data-action="${action.kind}">${esc(action.label)}</button>`:''}</div>`:'';
  return `<div class="readiness-head"><span class="readiness-badge is-${view.status}">${esc(view.label)}</span>${view.checkedAt?`<span class="readiness-time">${esc(readinessTimeText(view.checkedAt))}</span>`:''}</div>
    <p class="readiness-headline">${esc(view.headline)}</p>${view.detail?`<p class="readiness-detail">${esc(view.detail)}</p>`:''}
    ${view.note?`<p class="readiness-note">${esc(view.note)}</p>`:''}${callout}
    ${notable.length?readinessItemsMarkup(notable):''}
    ${total?`<details class="readiness-all" data-readiness-details="all"><summary>${esc(summary)} · show all checks</summary>${readinessItemsMarkup(view.items)}</details>`:''}`;
}

// The 4.5 Socrates rules in plain words, shown above the full rule list in the Live dialog.
// Display copy only; the saved permission is still bound to the backend policy version.
export const SOCRATES_KEY_RULES=[
  'Levels: four-hour swing highs and lows touched at least twice, plus the previous day’s high and low.',
  'Setup: an hourly break of a level, then a return to it. The break stays valid until the end of the next session, and the entry must be at the level (within 0.4%).',
  'Direction: at least 4 of the 7 tech leaders agree (at most 1 against) within 60 minutes, and the actual VIX turns the opposite way at its own level.',
  'Trade: longs only (QQQ). The setup needs a level at least as far as the stop (1R minimum); profit is taken at today’s open or the next key level at least 0.20% away. Skipped if the stop is more than 1.5% away. Closed the same day.',
  'Limits: new entries only 10:00 AM–12:00 PM ET, one Socrates position at a time, two entry attempts per New York session, no new entries in the last 30 minutes.',
];

const price=value=>{const n=Number(value);return value!==null&&value!==''&&Number.isFinite(n)&&n>0?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n):'—';};
const STAGES={entering:'Buying',entered:'Bought',protecting:'Placing the stop',protected:'Protected by a stop',open:'Open',exiting:'Selling',exit_pending:'Exit needs attention',closed:'Closed'};

// The open Socrates trade, including the PSQ short proxy. Values come from the execution
// snapshot; missing values read as a dash, never as zero.
export function socratesTradeMarkup(trade,message='') {
  if(!trade||typeof trade!=='object')return '';
  const symbol=text(trade.symbol)||'QQQ',proxy=trade.proxy==='inverse_etf'||(symbol==='PSQ'&&trade.signal_direction==='short');
  const stage=STAGES[trade.stage]||text(trade.stage).replaceAll('_',' ')||'Managing';
  const signal=trade.signal_geometry&&typeof trade.signal_geometry==='object'?trade.signal_geometry:null;
  const side=proxy?'Short setup · bought PSQ':trade.signal_direction==='short'?'Short':'Long';
  return `<article class="card trade-card" aria-labelledby="socrates-trade-title"><div class="section-heading"><h3 id="socrates-trade-title">Open trade · ${esc(symbol)}</h3><span class="pill">${esc(stage)}</span></div>
    <p class="observation">${esc(side)}${text(trade.reason)?` · ${esc(trade.reason)}`:''}</p>
    <div class="trade-grid"><div><span>Purchase</span><b>${esc(price(trade.amount))}</b></div><div><span>Stop (${esc(symbol)})</span><b>${esc(price(trade.stop))}</b></div><div><span>Target (${esc(symbol)})</span><b>${esc(price(trade.target))}</b></div></div>
    ${proxy?`<p class="help">QQQ gave a short setup. The account cannot short, so the app bought PSQ, the inverse Nasdaq-100 ETF. Its stop and target are the QQQ distances translated to PSQ by percentage${signal?` (QQQ entry ${esc(price(signal.entry))}, stop ${esc(price(signal.stop))}, target ${esc(price(signal.target))})`:''}. PSQ is sold before the close; it is never held overnight.</p>`:''}
    ${text(message)?`<p class="help">${esc(message)}</p>`:''}</article>`;
}
