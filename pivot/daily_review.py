"""Read-only trading evidence summaries and separately persisted daily revisions.

Nothing in this module imports a broker, executes a strategy, or changes its
permissions. Account-wide fee observations cannot prove a strategy's net P&L.
"""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from .models import timestamp
from .performance import socrates_labels, trade_result

SCHEMA = 'daily-trading-review-v1'
NY = ZoneInfo('America/New_York')
MAX_DAYS = 365
MAX_REVISIONS = 8
MAX_TRADES = 200
FAMILIES = {'socrates': 'Socrates', 'range_reversal': '4H Range Reversal'}
SOURCE_REFERENCES = {
    'socrates': [{'title': 'Socrates source rules and app interpretations',
                  'path': 'docs/research/current-method-specifications.md'}],
    'range_reversal': [{'title': 'Range reversal recording review',
                        'path': 'docs/research/range-reversal-video-20260919.md'},
                       {'title': 'Executable range interpretation', 'path': 'docs/production-v4.0.0.md'}],
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _scope(account_ref):
    if not isinstance(account_ref, str) or not account_ref.strip():
        raise ValueError('A verified account reference is required for a daily review')
    return sha256(account_ref.encode()).hexdigest()


def _day(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Use an ISO calendar date')
    return parsed


def _at(value):
    try:
        return timestamp(value) if value is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return format(result, 'f') if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value, account_ref, length=300):
    if not isinstance(value, str):
        return None
    # Only application reasons/labels reach this function, never raw payloads.
    value = value.replace(account_ref, '[account]')
    value = re.sub(r'https?://\S+', '[link omitted]', value)
    return ''.join(char for char in value if char.isprintable())[:length]


def _bodies(store, table, *, account_ref=None, begin=None, end=None):
    # Static table names only; query_only also protects caller-owned ledgers.
    if table not in ('trades', 'crypto_trades', 'decision_traces', 'execution_checks', 'crypto_decisions'):
        raise ValueError('Unsupported review source')
    if store is None:
        return []
    with store.connect() as db:
        db.execute('PRAGMA query_only=ON')
        where, params = [], []
        if account_ref is not None:
            where.append("json_extract(body,'$.account_ref')=?")
            params.append(account_ref)
        if begin is not None and end is not None:
            field = 'checked_at' if table == 'crypto_decisions' else 'captured_at'
            where.extend([f"julianday(json_extract(body,'$.{field}'))>=julianday(?)",
                          f"julianday(json_extract(body,'$.{field}'))<julianday(?)"])
            params.extend([begin.isoformat(), end.isoformat()])
        query = f'SELECT body FROM {table}' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY rowid'
        return [json.loads(row[0]) for row in db.execute(query, params)]


def _execution_account(row, trade_accounts):
    """Explicit execution identity wins; old rows need a proven trade owner."""
    trade = row.get('trade') if isinstance(row.get('trade'), dict) else {}
    owner = trade_accounts.get(trade.get('id'))
    identity = row.get('account_ref')
    if isinstance(identity, str) and identity.strip():
        # Contradictory evidence must not be assigned to either account.
        return None if owner is not None and owner != identity else identity
    return owner


def _signal_session_state(row):
    """A copied session flag needs a current broker clock at this observation.

    Fifteen seconds matches the account poll interval and stock executor clock
    admission. Decision traces retain the raw clock flag even after outages;
    unlike execution checks, that flag was not validated when it was recorded.
    """
    execution = row.get('execution') if isinstance(row.get('execution'), dict) else {}
    state = execution.get('regular_session_open')
    captured, clock = _at(row.get('captured_at')), _at(execution.get('clock_at'))
    if type(state) is not bool or captured is None or clock is None:
        return None
    return state if 0 <= (captured - clock).total_seconds() <= 15 else None


def _ranked_observations(counts):
    return [{'reason': reason, 'count': count}
            for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]]


def _accounting(summary, account_ref, day):
    """Allowlist aggregate facts; never reuse another account/day's evidence."""
    value = summary if isinstance(summary, dict) else {}
    if value.get('account_scope') != _scope(account_ref) or value.get('day') != day:
        return {'status': 'unavailable', 'data_complete': False,
                'fees': {'observed_usd_cost': None, 'status': 'unavailable', 'final': False},
                'realized_net_pnl_usd': None, 'net_status': 'unverified',
                'detail': 'Matching account and day activity evidence is unavailable.'}, []
    result = {'status': _text(value.get('status'), account_ref),
              'currentness': _text(value.get('currentness'), account_ref),
              'data_complete': value.get('data_complete') is True,
              'realized_net_pnl_usd': None, 'net_status': 'unverified',
              'detail': _text(value.get('detail'), account_ref, 600)}
    for key in ('last_success_at', 'last_attempt_at'):
        at = _at(value.get(key))
        result[key] = at.isoformat() if at else None
    for section, money_keys, other_keys in (
        ('fills', ('buy_notional_usd', 'sell_notional_usd', 'executed_notional_usd'), ('count', 'unresolved_count')),
        ('fees', ('observed_usd_cost',), ('activity_count', 'unresolved_count', 'status', 'day_basis', 'detail')),
        ('cash_flows', ('deposits_usd', 'withdrawals_usd', 'net_usd'), ('unresolved_count', 'unclassified_transfer_count')),
        ('account_change', ('start_equity_usd', 'end_equity_usd', 'equity_change_usd', 'cash_flow_adjusted_change_usd'),
         ('start_at', 'end_at', 'partial_day', 'status', 'detail')),
        ('coverage', (), ('created_after', 'created_until', 'pages_complete')),
    ):
        source = value.get(section) if isinstance(value.get(section), dict) else {}
        result[section] = {key: _money(source.get(key)) for key in money_keys}
        for key in other_keys:
            item = source.get(key)
            result[section][key] = (_text(item, account_ref, 600) if isinstance(item, str)
                                    else item if isinstance(item, (int, bool)) else None)
    result['fees']['final'] = False
    # This optional future contract requires separately verified, attributed
    # costs. Today's activity reader intentionally returns an empty list.
    return result, value.get('per_trade_net') if isinstance(value.get('per_trade_net'), list) else []


def _fill(op):
    seen = op.get('last_seen') if isinstance(op, dict) else None
    seen = seen if isinstance(seen, dict) else {}
    qty, price = _money(seen.get('filled_qty')), _money(seen.get('filled_avg_price'))
    return seen, Decimal(qty) if qty is not None and Decimal(qty) >= 0 else None, price


SOCRATES_METHODS = {'four_hour_retest': 'QQQ broke a four-hour level and returned to it',
                    'prior_day_sweep': "QQQ swept the previous day's high or low"}


def _price(value):
    money = _money(value)
    return money if money is not None and Decimal(money) > 0 else None


def _signal_geometry(value):
    """The QQQ plan a Socrates trade came from (allowlisted prices only)."""
    if not isinstance(value, dict) or value.get('symbol') != 'QQQ' or value.get('direction') not in ('long', 'short'):
        return None
    return {'symbol': 'QQQ', 'direction': value['direction'],
            **{key: _price(value.get(key)) for key in ('entry', 'stop', 'target')}}


def _execution_geometry(trade):
    """The PSQ purchase that executed a QQQ short (translated stop and target)."""
    geometry = trade.get('proxy_geometry') if isinstance(trade.get('proxy_geometry'), dict) else {}
    return {'symbol': 'PSQ', 'side': 'buy', 'kind': 'inverse_etf',
            'reference': _price(geometry.get('reference')), 'stop': _price(trade.get('stop')),
            'target': _price(trade.get('target')), 'signal_price': _price(geometry.get('signal_price'))}


def _trade_view(trade, family, account_ref, net_rows):
    ops = trade.get('ops') if isinstance(trade.get('ops'), dict) else {}
    seen, qty, price = _fill(ops.get('entry'))
    signal = trade.get('signal') if isinstance(trade.get('signal'), dict) else {}
    event = signal.get('current_event') if isinstance(signal.get('current_event'), dict) else {}
    source_version = signal.get('policy_version') or signal.get('rule_version')
    entry_reason = trade.get('entry_reason') or signal.get('strategy_id') or event.get('direction')
    if family == 'range_reversal' and event.get('direction') == 'long':
        entry_reason = 'Completed lower-range break followed by a later close inside the range'
    exit_pending = trade.get('exit_pending') if isinstance(trade.get('exit_pending'), dict) else {}
    exit_reason = trade.get('exit_reason') or exit_pending.get('reason')
    exit_reason_status = 'recorded' if exit_reason else 'completion_only'
    if not exit_reason:
        for name, operation in ops.items():
            observed, filled, _ = _fill(operation)
            if name.startswith('stop') and observed.get('status') == 'filled' and filled is not None and filled > 0:
                exit_reason, exit_reason_status = 'Broker protective stop filled', 'fill_evidence'
                break
    authorization = trade.get('authorization') if isinstance(trade.get('authorization'), dict) else {}
    permission = authorization.get('crypto' if family == 'range_reversal' else 'control') or {}
    result = {'id': _text(trade.get('id'), account_ref, 100), 'family': family,
              'symbol': _text(trade.get('symbol'), account_ref, 20),
              'direction': trade.get('direction') if trade.get('direction') in ('long', 'short') else None,
              'stage': _text(trade.get('stage'), account_ref, 30),
              'strategy_version': _text(source_version, account_ref, 100),
              'execution_policy_version': _text(permission.get('policy'), account_ref, 100),
              'revision': trade.get('revision') if isinstance(trade.get('revision'), str) and re.fullmatch(r'[a-f0-9]{40}', trade['revision']) else None,
              'created_at': _at(trade.get('created_at')).isoformat() if _at(trade.get('created_at')) else None,
              'completed_at': _at(trade.get('completed_at')).isoformat() if _at(trade.get('completed_at')) else None,
              'entry_reason': _text(entry_reason, account_ref),
              'exit_reason': _text(exit_reason or trade.get('reason'), account_ref),
              'exit_reason_status': exit_reason_status,
              'entry_price': price if qty is not None and qty > 0 else None,
              'filled_qty': str(qty) if qty is not None else None,
              'exit_price': None, 'gross_pnl': None, 'gross_status': 'unverified',
              'net_pnl': None, 'cost_status': 'pending', 'outcome': 'unresolved',
              'source_references': deepcopy(SOURCE_REFERENCES[family])}
    if family == 'socrates':
        # A PSQ proxy trade shows both sides: the QQQ short signal and the PSQ purchase.
        labels = socrates_labels(trade)
        result.update(labels, signal=_signal_geometry(trade.get('signal_geometry')),
                      execution=_execution_geometry(trade) if labels['proxy'] else None)
        if labels['label']:
            method = SOCRATES_METHODS.get(signal.get('strategy_id'))
            result['entry_reason'] = labels['label'] + (f' · {method}' if method else '')
    exits = [_fill(op) for name, op in ops.items() if name != 'entry' and op.get('state') not in ('prepared', 'aborted_before_submit')]
    known_exits = [(q, Decimal(p)) for _, q, p in exits if q is not None and q > 0 and p is not None]
    if known_exits and all(q is not None and (q == 0 or p is not None) for _, q, p in exits):
        result['exit_price'] = format(sum(q*p for q, p in known_exits)/sum(q for q, _ in known_exits), 'f')
    if trade.get('stage') == 'finished' and qty is not None and qty > 0:
        result['outcome'] = 'closed_unverified'
        if family == 'socrates':
            gross = trade_result(trade)
            result['gross_pnl'], result['gross_status'] = gross['gross_pnl'], gross['status']
        candidates = [row for row in net_rows if isinstance(row, dict)
                      and row.get('trade_id') == trade.get('id') and row.get('family_id') == family]
        if len(candidates) == 1:
            row = candidates[0]
            net = _money(row.get('net_pnl_usd'))
            if (row.get('status') == 'verified' and row.get('fees_status') == 'verified'
                    and net is not None and not trade.get('manual_reconciliation')):
                result.update(net_pnl=net, cost_status='verified',
                              outcome='win' if Decimal(net) > 0 else 'loss' if Decimal(net) < 0 else 'breakeven')
    elif trade.get('stage') == 'finished' and qty == 0 and seen.get('status') in ('canceled', 'expired', 'rejected'):
        result.update(outcome='unfilled', cost_status='unavailable')
    elif trade.get('stage') != 'finished' and qty is not None and qty > 0:
        result['outcome'] = 'open'
    return result


def build_review(store, crypto_store, accounting_summary, account_ref, day, now):
    """Build an observational report; returned counts describe retained proof.

    P&L and wins concern lifecycles completed during the selected New York day,
    including entries from earlier days. Submission attempts use the recorded
    attempt time, or the intent's creation day where older ledgers lack it.
    """
    _scope(account_ref)
    day_date, now = _day(day), timestamp(now)
    begin = datetime.combine(day_date, time.min, NY)
    end = datetime.combine(day_date + timedelta(days=1), time.min, NY)
    if now < begin:
        raise ValueError('A future daily review is unavailable')
    cutoff = min(now, end)
    within = lambda at: at is not None and begin <= at < end and at <= now
    accounting, net_rows = _accounting(accounting_summary, account_ref, day)
    missing = ['Retained logs do not establish uninterrupted full-day coverage. Market decision observations are not account-specific.']
    if accounting.get('data_complete') is not True:
        missing.append('Broker activity coverage is incomplete or unavailable for this account and day.')
    families = {}
    investigations = []
    for family, table, source in (('socrates', 'trades', store), ('range_reversal', 'crypto_trades', crypto_store)):
        # Retain stock trade ownership for legacy execution checks, including
        # the proof that a check belongs to a different account after a switch.
        retained_trades = _bodies(source, table, account_ref=None if family == 'socrates' else account_ref)
        trades = [trade for trade in retained_trades if trade.get('account_ref') == account_ref]
        attempts, entries, closed, open_count = 0, 0, [], 0
        unknown_fill_day = False
        visible = []
        for trade in trades:
            created, completed = _at(trade.get('created_at')), _at(trade.get('completed_at'))
            op = (trade.get('ops') or {}).get('entry') or {}
            seen, qty, _ = _fill(op)
            attempted = _at(op.get('attempted_at')) or created
            filled = _at(seen.get('filled_at'))
            if op.get('state') in ('attempted', 'rejected') and within(attempted):
                attempts += 1
            if qty is not None and qty > 0:
                if within(filled):
                    entries += 1
                if filled is None and created is not None and created < end and (completed is None or completed >= begin):
                    unknown_fill_day = True
                if (filled is not None and filled <= cutoff and created is not None and created <= cutoff
                        and (completed is None or completed > cutoff)):
                    open_count += 1
            relevant = (within(created) or within(completed) or within(attempted)
                        or created is not None and created < cutoff and (completed is None or completed > begin))
            if relevant:
                row = _trade_view(trade, family, account_ref, net_rows)
                row['completed_on_day'] = within(completed)
                if completed is not None and completed > cutoff:
                    # A later exit is not an outcome of the selected day.
                    row.update(completed_at=None, exit_reason=None, exit_price=None,
                               gross_pnl=None, gross_status='unverified', net_pnl=None,
                               cost_status='pending', outcome='open' if qty is not None and qty > 0 else 'unresolved')
                visible.append(row)
                if trade.get('stage') == 'finished' and qty is not None and qty > 0 and within(completed):
                    closed.append(row)
        blockers = Counter()
        checks = 0
        session_counts = {'closed_session_checks': 0, 'session_unknown_checks': 0,
                          'execution_checks_recorded': 0, 'unassigned_execution_checks': 0,
                          'closed_session_execution_checks': 0}
        closed_observations, unknown_observations = Counter(), Counter()
        if family == 'socrates':
            for row in _bodies(store, 'decision_traces', begin=begin, end=end):
                if within(_at(row.get('captured_at'))):
                    checks += 1
                    first = row.get('first_blocker') or {}
                    reason = _text(first.get('name'), account_ref)
                    session_open = _signal_session_state(row)
                    if session_open is False:
                        session_counts['closed_session_checks'] += 1
                        observations = closed_observations
                    elif session_open is not True:
                        session_counts['session_unknown_checks'] += 1
                        observations = unknown_observations
                    else:
                        observations = blockers
                    if reason:
                        observations[reason] += 1
            trade_accounts = {trade['id']: trade['account_ref'] for trade in retained_trades
                              if isinstance(trade.get('id'), str) and trade['id']
                              and isinstance(trade.get('account_ref'), str) and trade['account_ref'].strip()}
            for row in _bodies(store, 'execution_checks', begin=begin, end=end):
                if not within(_at(row.get('captured_at'))):
                    continue
                owner = _execution_account(row, trade_accounts)
                if owner is None:
                    session_counts['unassigned_execution_checks'] += 1
                    continue
                if owner != account_ref:
                    continue
                session_counts['execution_checks_recorded'] += 1
                trade = row.get('trade') if isinstance(row.get('trade'), dict) else {}
                closed_entry_check = row.get('regular_session_open') is False and not trade.get('id')
                if closed_entry_check:
                    session_counts['closed_session_execution_checks'] += 1
                if row.get('outcome') in ('waiting', 'feed_error', 'invalid_data', 'unexpected_error', 'attention', 'order_rejected'):
                    reason = _text(row.get('gate'), account_ref)
                    if reason:
                        (closed_observations if closed_entry_check else blockers)[reason] += 1
        else:
            for row in _bodies(crypto_store, 'crypto_decisions', begin=begin, end=end):
                if within(_at(row.get('checked_at'))):
                    checks += 1
                    if row.get('outcome') not in ('management', 'entry_filled', 'order_attempted', 'closed'):
                        reason = _text(row.get('reason'), account_ref)
                        if reason:
                            blockers[reason] += 1
        verified = [row for row in closed if row['cost_status'] == 'verified']
        net_status = ('no_closed_trades' if not closed else 'complete' if len(verified) == len(closed)
                      else 'partial' if verified else 'unavailable')
        family_missing = []
        if session_counts['session_unknown_checks']:
            family_missing.append('Some retained signal observations lack a known market-session state from a current broker clock; they are shown separately and are not actionable entry blockers.')
        if session_counts['unassigned_execution_checks']:
            family_missing.append('Some retained execution observations lack a consistent account association; their reasons are excluded from this account report.')
        if unknown_fill_day:
            family_missing.append('Some confirmed entry fills lack a broker fill timestamp; exact daily entry/open counts are unknown.')
        if len(verified) < len(closed):
            family_missing.append('Closed trade costs are not fully attributed and verified; gross results are not net profit.')
        unresolved = sum(row['outcome'] == 'unresolved' for row in visible)
        if unresolved:
            family_missing.append('Some lifecycles lack conclusive fill or completion evidence and are not counted as verified outcomes.')
        if any(row['exit_reason_status'] == 'completion_only' for row in closed):
            family_missing.append('Some older lifecycles retain only a completion message, not the original exit trigger.')
        if not checks:
            family_missing.append('No retained decision observations were found for this day; this does not prove the worker ran without signals.')
        if len(visible) > MAX_TRADES:
            family_missing.append('Only the most recent 200 lifecycle details are displayed; counts include all retained matching records.')
        for detail in family_missing:
            missing.append(FAMILIES[family] + ': ' + detail)
        families[family] = {
            'label': FAMILIES[family], 'checks_recorded': checks,
            'submission_attempts': attempts, 'filled_entries': None if unknown_fill_day else entries,
            'closed_trades': len(closed), 'open_at_end': None if unknown_fill_day else open_count,
            'wins': sum(row['outcome'] == 'win' for row in verified),
            'losses': sum(row['outcome'] == 'loss' for row in verified),
            'breakeven': sum(row['outcome'] == 'breakeven' for row in verified),
            'unverified_outcomes': len(closed)-len(verified),
            'unresolved_lifecycles': unresolved,
            'outcome_scope': 'verified_net_only',
            'verified_net_pnl': format(sum((Decimal(row['net_pnl']) for row in verified), Decimal(0)), 'f') if verified else None,
            'net_pnl_status': net_status, 'known_fees': None,
            'fees_status': 'verified' if closed and len(verified) == len(closed) else 'pending',
            'blockers': _ranked_observations(blockers),
            'trades': sorted(visible, key=lambda row: row['completed_at'] or row['created_at'] or '', reverse=True)[:MAX_TRADES],
            'missing_evidence': family_missing,
        }
        if family == 'socrates':
            families[family].update(session_counts,
                                    closed_session_observations=_ranked_observations(closed_observations),
                                    session_unknown_observations=_ranked_observations(unknown_observations))
        if not checks:
            investigations.append({'code': 'observation_coverage', 'family': family, 'title': 'Check observation coverage',
                                   'reason': 'No retained decisions establish what this strategy observed during the day.'})
        if not attempts and blockers:
            reason = sorted(blockers.items(), key=lambda item: (-item[1], item[0]))[0][0]
            investigations.append({'code': 'entry_blocker', 'family': family, 'title': 'Review the recorded entry blocker',
                                   'reason': reason + '. Compare source timestamps and frozen rules; do not relax rules just to create a trade.'})
        if len(verified) < len(closed):
            investigations.append({'code': 'unverified_costs', 'family': family, 'title': 'Reconcile closed-trade costs',
                                   'reason': 'Wait for matching fill and fee evidence before classifying a profitable or losing outcome.'})
    investigations.append({'code': 'frozen_evaluation', 'family': None, 'title': 'Continue the frozen evaluation',
                           'reason': 'One day cannot establish a strategy edge. Preserve source rules and evaluate separate periods after costs; no settings or rules are changed by this report.'})
    return {'schema': SCHEMA, 'account_scope': _scope(account_ref), 'day': day, 'timezone': str(NY), 'generated_at': now.isoformat(),
            'as_of': cutoff.isoformat(), 'period_status': 'day_ended' if now >= end else 'in_progress',
            'status': 'partial' if missing else 'available', 'historical_only': True,
            'scope': 'Retained evidence only. Outcomes and net results cover lifecycles completed on this New York day, including earlier entries. Entry counts require fill timestamps; attempts may use older intent creation dates. Open counts are as of the report cutoff.',
            'families': families, 'accounting': accounting, 'missing_evidence': missing,
            'investigations': investigations}


class DailyReviewStore:
    """Separate private journal; save never touches trading or settings tables."""
    def __init__(self, path):
        self.path = str(path)
        candidate = Path(path)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if candidate.is_symlink() or candidate.exists() and not candidate.is_file():
            raise ValueError('Daily review storage must be a regular private file')
        fd = os.open(candidate, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS daily_reviews (account_scope TEXT NOT NULL, day TEXT NOT NULL, '
                       'revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, saved_at TEXT NOT NULL, body TEXT NOT NULL, '
                       'PRIMARY KEY(account_scope,day,revision))')

    def connect(self):
        return sqlite3.connect(self.path, timeout=.25)

    def save(self, account_ref, report):
        scope = _scope(account_ref)
        if not isinstance(report, dict) or report.get('schema') != SCHEMA:
            raise ValueError('Unsupported daily review')
        if report.get('account_scope') != scope:
            raise ValueError('Daily review account differs from its storage scope')
        day = _day(report['day']).isoformat()
        body = {k: deepcopy(v) for k, v in report.items() if k not in ('revision', 'saved_at')}
        evidence = {k: v for k, v in body.items() if k not in ('generated_at', 'as_of')}
        evidence = deepcopy(evidence)
        if isinstance(evidence.get('accounting'), dict):
            for key in ('last_attempt_at', 'last_success_at', 'currentness'):
                evidence['accounting'].pop(key, None)
            coverage = evidence['accounting'].get('coverage')
            if isinstance(coverage, dict):
                begin = datetime.combine(_day(day), time.min, NY)
                end = datetime.combine(_day(day)+timedelta(days=1), time.min, NY)
                for key, bound, operation in (('created_after', begin, max), ('created_until', end, min)):
                    observed = _at(coverage.get(key))
                    if observed is not None:
                        coverage[key] = operation(observed, bound).astimezone(timezone.utc).isoformat()
        fingerprint = sha256(_json(evidence).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,fingerprint,body FROM daily_reviews WHERE account_scope=? AND day=? '
                             'ORDER BY revision DESC LIMIT 1', (scope, day)).fetchone()
            if row and row[1] == fingerprint:
                return json.loads(row[2])
            body.update(revision=row[0]+1 if row else 1, saved_at=datetime.now(timezone.utc).isoformat())
            if len(_json(body).encode()) > 1_000_000:
                raise ValueError('Daily review exceeds the storage limit')
            db.execute('INSERT INTO daily_reviews VALUES(?,?,?,?,?,?)',
                       (scope, day, body['revision'], fingerprint, body['saved_at'], _json(body)))
            db.execute('DELETE FROM daily_reviews WHERE account_scope=? AND day=? AND revision NOT IN '
                       '(SELECT revision FROM daily_reviews WHERE account_scope=? AND day=? ORDER BY revision DESC LIMIT ?)',
                       (scope, day, scope, day, MAX_REVISIONS))
            db.execute('DELETE FROM daily_reviews WHERE account_scope=? AND day NOT IN '
                       '(SELECT DISTINCT day FROM daily_reviews WHERE account_scope=? ORDER BY day DESC LIMIT ?)',
                       (scope, scope, MAX_DAYS))
        return body

    def get(self, account_ref, day, revision=None):
        scope, day = _scope(account_ref), _day(day).isoformat()
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError('Invalid review revision')
        with self.connect() as db:
            row = db.execute('SELECT body FROM daily_reviews WHERE account_scope=? AND day=?' +
                             (' AND revision=?' if revision is not None else '') + ' ORDER BY revision DESC LIMIT 1',
                             (scope, day, revision) if revision is not None else (scope, day)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, account_ref, limit=30, before_day=None):
        if type(limit) is not int or not 1 <= limit <= MAX_DAYS:
            raise ValueError('Invalid review history limit')
        scope = _scope(account_ref)
        if before_day is not None:
            _day(before_day)
        with self.connect() as db:
            rows = db.execute('SELECT day,MAX(revision) FROM daily_reviews WHERE account_scope=?' +
                              (' AND day<?' if before_day else '') + ' GROUP BY day ORDER BY day DESC LIMIT ?',
                              (scope, before_day, limit) if before_day else (scope, limit)).fetchall()
        return [self.get(account_ref, day, revision) for day, revision in rows]
