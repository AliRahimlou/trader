"""Single-position, restartable order lifecycle for the documented video interpretation.

Every broker POST has a durable, unique intent. An uncertain response is looked up,
never blindly retried. This module is also exercised with an offline fake broker.

Since 4.5.0 the executor is symbol-aware: a long setup buys QQQ and a short setup
buys PSQ (inverse-ETF proxy, an app interpretation the videos do not mention).
Every trade record names its traded 'symbol'; management never assumes QQQ.
"""
from datetime import datetime, timezone, timedelta
from contextlib import nullcontext
from copy import deepcopy
from decimal import ROUND_HALF_UP
from hashlib import sha256
from math import isfinite
import json
import logging
import re
from threading import Lock
from zoneinfo import ZoneInfo
from .broker import BrokerRejected, PROXY_KIND, PROXY_SYMBOL, PROXY_SYMBOLS, SIGNAL_SYMBOL
from .feeds import FeedError
from .models import timestamp
from .policy import POLICY_VERSION
from .sizing import decimal, purchase_plan
from .version import APP_VERSION
from .deployment import DeploymentHold
from .portfolio import PortfolioBlocked
from .strategy import (ANALYSIS_VERSION, LEADER_MINUTES, CANDLE_PUBLICATION_GRACE_SECONDS, MAX_PERSISTENCE_BARS,
                       event_expiry)

TERMINAL = {'filled', 'canceled', 'expired', 'rejected'}
WORKING_PROTECTION = {'new', 'partially_filled'}
UNAVAILABLE_PROTECTION = {'suspended', 'done_for_day', 'pending_cancel', 'pending_replace', 'calculated'}
# Proposed release safety policy: a submitted stop must become executable within
# this interval. This is not a promise about venue latency or a maximum loss.
PROTECTION_CONFIRM_SECONDS = 30
PARTIAL_ENTRY_CONFIRM_SECONDS = 30
EXIT_CONFIRM_SECONDS = 30
MANUAL_FLAT_CONFIRM_SECONDS = 30
ORDER_NOT_FOUND_CONFIRM_SECONDS = 60
# App interpretations, not video rules. The host clock has been observed about a
# second behind Alpaca's; a broker or provider timestamp slightly in the local
# future is still current. Positive staleness limits are unchanged.
CLOCK_SKEW_SECONDS = 5
# Entries stop 30 minutes before the close (positions still start closing at 5),
# so a fresh entry has room for its stop and target instead of a forced exit.
ENTRY_CUTOFF_SECONDS = 1800
SIGNAL_POLICY_VERSION = ANALYSIS_VERSION  # The executor admits only the analyzer's current interpretation.
PAPER_SIGNAL_POLICY_VERSION = 'synthetic-paper-commissioning-v1'
PAPER_SIGNAL_PURPOSE = 'broker_order_lifecycle_only'
SIGNAL_METHODS = {'four_hour_retest', 'prior_day_sweep'}
SIGNAL_FIELDS = ('policy_version', 'strategy_id', 'event_id', 'event_origin_at',
                 'event_at', 'event_expires_at', 'latest_evidence_at', 'event_zone',
                 'leader_evidence_valid_until', 'leader_observation_at',
                 'leader_observations_synchronized', 'leader_observation_valid_until')
NEW_YORK = ZoneInfo('America/New_York')
logger = logging.getLogger('pivot.execution')
CHECKS = {'Current Nasdaq observation', 'Premarked levels', 'Nasdaq level event',
          'Magnificent Seven at their zones', 'Actual VIX zone reaction', 'Stop and target'}
SIGNAL_GATES = {'Current Nasdaq observation': 'signal_observation', 'Premarked levels': 'signal_levels',
                'Nasdaq level event': 'signal_event', 'Magnificent Seven at their zones': 'signal_leaders',
                'Actual VIX zone reaction': 'signal_vix', 'Stop and target': 'signal_exits'}
EXECUTION_GATES = set(SIGNAL_GATES.values()) | {
    'runtime_state', 'live_permission', 'deployment_hold', 'vix_candles', 'analysis_freshness', 'data_expiry',
    'signal_checks', 'signal_age_policy', 'setup_deduplication', 'broker_snapshot', 'account_status',
    'account_identity', 'account_mode', 'market_session', 'entry_cutoff', 'existing_exposure',
    'asset_eligibility', 'quote_read', 'quote_validation', 'quote_stale', 'quote_invalid', 'quote_spread',
    'signal_direction', 'price_geometry', 'price_drift', 'purchase_size', 'proxy_eligibility',
    'vix_entry_quote', 'final_analysis_freshness', 'final_data_expiry',
    'entry_reservation', 'trade_management', 'order_prepare', 'order_submit', 'order_lookup',
    'order_identity', 'order_rejection', 'order_reconciliation', 'position_reconciliation',
    'order_cancel', 'protection', 'trade_finish', 'owner_attention', 'exit_management',
}
EXECUTION_OUTCOMES = {'disabled', 'waiting', 'feed_error', 'invalid_data', 'unexpected_error',
                      'entry_planned', 'managing', 'completed', 'attention', 'order_rejected', 'protection_failure'}
ORDER_STATUSES = TERMINAL | WORKING_PROTECTION | UNAVAILABLE_PROTECTION | {
    'accepted', 'pending_new', 'accepted_for_bidding', 'held', 'stopped', 'replaced',
}
UNEXPECTED_EXCEPTION_KINDS = {kind: kind.__name__ for kind in (
    KeyError, TypeError, AttributeError, RuntimeError, ArithmeticError, OverflowError,
    ZeroDivisionError, OSError, AssertionError, IndexError,
)}


class Waiting(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


class ExpiredSignal(Waiting):
    """A structurally valid observation whose time window has ended."""


def elapsed(at, now):
    return (now - timestamp(at)).total_seconds()


def recent(at, now, seconds):
    """A broker/provider timestamp is current within `seconds`, tolerating small negative skew."""
    return -CLOCK_SKEW_SECONDS <= elapsed(at, now) <= seconds


def data_unexpired(until, now):
    return bool(until and 0 < (timestamp(until) - now).total_seconds() <= 90)


def signal_expiry(setup, now, *, expected_policy_version=SIGNAL_POLICY_VERSION):
    """Validate the versioned event window, independently of trade direction.

    A later retest may refresh hourly evidence but cannot extend the originating
    event's lifetime or create a second opportunity after an order attempt.
    """
    try:
        paper = expected_policy_version == PAPER_SIGNAL_POLICY_VERSION
        prefix = 'paper_ev1_' if paper else 'ev2_'
        if (expected_policy_version not in (SIGNAL_POLICY_VERSION, PAPER_SIGNAL_POLICY_VERSION)
                or not isinstance(setup, dict) or setup.get('policy_version') != expected_policy_version
                or setup.get('strategy_id') not in SIGNAL_METHODS
                or not isinstance(setup.get('event_id'), str)
                or not re.fullmatch(prefix + r'[a-f0-9]{64}', setup['event_id'])):
            raise ValueError
        if paper:
            if setup.get('commissioning_purpose') != PAPER_SIGNAL_PURPOSE:
                raise ValueError
        elif (setup.get('commissioning_purpose') is not None
                or setup.get('commissioning_only') or setup.get('synthetic_evidence')):
            raise ValueError
        origin, event, expires, evidence = (
            timestamp(setup[key]) for key in ('event_origin_at', 'event_at', 'event_expires_at', 'latest_evidence_at'))
        leader_deadline = timestamp(setup['leader_evidence_valid_until'])
        observation_at = timestamp(setup['leader_observation_at'])
        observation_deadline = timestamp(setup['leader_observation_valid_until'])
        zone = setup['event_zone']
        if (not isinstance(zone, dict)
                or any(isinstance(zone.get(key), bool) or not isinstance(zone.get(key), (int, float))
                       or not isfinite(zone[key]) for key in ('low', 'high'))
                or not 0 < zone['low'] <= zone['high']):
            raise ValueError
        established = timestamp(zone['established_at'])
        identity = [SIGNAL_SYMBOL, setup['strategy_id'], float(zone['low']).hex(), float(zone['high']).hex(),
                    established.astimezone(timezone.utc).isoformat(), origin.astimezone(timezone.utc).isoformat()]
        if paper:
            identity = [PAPER_SIGNAL_POLICY_VERSION, PAPER_SIGNAL_PURPOSE, *identity]
        expected_id = prefix + sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
        if setup['event_id'] != expected_id:
            raise ValueError
        if (established >= origin - timedelta(minutes=60)
                or not origin <= event <= evidence <= now
                or not origin < expires <= event_expiry(origin)
                or leader_deadline > now + timedelta(minutes=LEADER_MINUTES * MAX_PERSISTENCE_BARS)
                or setup.get('leader_observations_synchronized') is not True
                or observation_at > now
                or observation_deadline != observation_at + timedelta(
                    minutes=LEADER_MINUTES, seconds=CANDLE_PUBLICATION_GRACE_SECONDS)):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise Waiting('Waiting for a current setup under the active video rules') from None
    if (now >= expires or now >= leader_deadline or now >= observation_deadline
            or (now - evidence).total_seconds() > 3690
            or evidence.astimezone(NEW_YORK).date() != now.astimezone(NEW_YORK).date()):
        raise ExpiredSignal('Waiting for a current setup under the active video rules')
    return min(expires, leader_deadline, observation_deadline)


def session_open(clock, now):
    return clock.get('is_open') is True and recent(clock['timestamp'], now, 15)


def closing(clock, now, seconds=300):
    return (timestamp(clock['next_close']) - now).total_seconds() <= seconds


def checked_quote(quote, now, symbol=SIGNAL_SYMBOL):
    """Freshness, validity and spread rules; identical for the QQQ signal and the PSQ proxy."""
    try:
        if not recent(quote['t'], now, 15):
            raise Waiting(f'Waiting for a current {symbol} quote', code='quote_stale')
        bid, ask = decimal(quote['bp']), decimal(quote['ap'])
        if bid <= 0 or ask < bid or decimal(quote['bs']) <= 0 or decimal(quote['as']) <= 0:
            raise Waiting(f'Waiting for a valid {symbol} bid and ask', code='quote_invalid')
        if (ask - bid) / bid > decimal('.005'):
            raise Waiting(f'{symbol} spread is too wide; waiting', code='quote_spread')
        return bid, ask
    except Waiting:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError):
        # A malformed quote is unavailable data too. Position management must
        # still place the known protective stop after a confirmed entry fill.
        raise Waiting(f'Waiting for a valid {symbol} bid and ask', code='quote_invalid') from None


def proxy_translation(proxy_ask, entry, stop, target):
    """Translate a QQQ short's geometry (stop > entry > target) onto a PSQ purchase.

    App interpretation, not a video rule: the videos short Nasdaq futures and say
    nothing about an inverse ETF. PSQ tracks -1x QQQ's daily move, so the same
    fractional distances are mirrored around the current PSQ ask, rounded to
    cents: stop = ask * (1 - (S - E) / E) below it, target = ask * (1 + (E - T) / E)
    above it. ``entry`` must be the live QQQ price the entry is admitted at (the
    QQQ bid a short would sell at), not the plan's hourly-close reference: the
    PSQ exits then sit as far away as QQQ's structural stop and target levels
    are from where QQQ trades now. Daily-reset decay and PSQ's own spread are
    accepted as the cost of executing a setup this cash account could not
    otherwise take.
    """
    proxy_ask, entry, stop, target = map(decimal, (proxy_ask, entry, stop, target))
    if not 0 < target < entry < stop or proxy_ask <= 0:
        raise ValueError('A short proxy needs stop > entry > target and a positive proxy ask')
    cent = decimal('.01')
    proxy_stop = (proxy_ask * (1 - (stop - entry) / entry)).quantize(cent, rounding=ROUND_HALF_UP)
    proxy_target = (proxy_ask * (1 + (entry - target) / entry)).quantize(cent, rounding=ROUND_HALF_UP)
    return proxy_ask, proxy_stop, proxy_target


# Alpaca Trading API v2 order rules the Socrates payloads must satisfy (4.5.1 audit,
# docs/order-path-audit-4.5.1.md). Sources, read 2026-09-22:
#   https://docs.alpaca.markets/reference/postorder  (client_order_id <= 128
#     characters; notional only on market orders, DAY; qty/notional up to 9 decimals;
#     extended_hours only with limit orders)
#   https://docs.alpaca.markets/docs/fractional-trading  (fractional orders: market,
#     limit, stop and stop_limit with time_in_force=day; qty or notional, not both)
#   https://docs.alpaca.markets/docs/orders-at-alpaca  (stop/limit prices >= $1 at most
#     2 decimals, below $1 at most 4; error 42210000 otherwise; a stop elects a market order)
#   https://forum.alpaca.markets/t/apierror-potential-wash-trade-detected-use-complex-orders/13441
#     (an order is rejected while an opposite-side market/stop order is open in the symbol)
CLIENT_ORDER_ID_MAX = 128
QUANTITY_PLACES = 9
MINIMUM_NOTIONAL = decimal('1')
ORDER_KEYS = {'symbol', 'side', 'type', 'time_in_force', 'extended_hours', 'qty', 'notional',
              'stop_price', 'client_order_id'}


def valid_price_increment(price):
    """Alpaca price increments: whole cents at or above $1, four decimals below it."""
    try:
        price = decimal(price)
    except ValueError:
        return False
    return price > 0 and price == price.quantize(decimal('.01') if price >= 1 else decimal('.0001'))


def quantity_text(value):
    """Plain decimal share text for an order; str(Decimal) would print 1E-7 for a tiny remnant."""
    return format(abs(decimal(value)), 'f')


def order_payload_violations(payload):
    """Every Alpaca v2 rule a Socrates order payload breaks; an empty list conforms.

    Covers the payloads this executor can POST: entry market buys by notional
    (fractionable) or whole-share qty, DAY sell stops with a fractional qty, and
    DAY market sells. Cancellation is a DELETE by order id and has no payload.
    """
    problems = []
    if not isinstance(payload, dict):
        return ['payload is not an object']
    if set(payload) - ORDER_KEYS:
        problems.append('unexpected fields: ' + ', '.join(sorted(set(payload) - ORDER_KEYS)))
    if payload.get('symbol') not in PROXY_SYMBOLS:
        problems.append('symbol is not QQQ or PSQ')
    if payload.get('side') not in ('buy', 'sell'):
        problems.append('side must be buy or sell')
    kind = payload.get('type')
    if kind not in ('market', 'stop'):
        problems.append('type must be market or stop')
    if payload.get('time_in_force') != 'day':
        problems.append('time_in_force must be day (required for fractional orders)')
    if payload.get('extended_hours') is not False:
        problems.append('extended_hours must be false (only limit orders may trade extended hours)')
    identifier = payload.get('client_order_id')
    if not isinstance(identifier, str) or not 0 < len(identifier) <= CLIENT_ORDER_ID_MAX:
        problems.append(f'client_order_id must be 1-{CLIENT_ORDER_ID_MAX} characters')
    if ('qty' in payload) == ('notional' in payload):
        problems.append('exactly one of qty or notional is required')
    if 'notional' in payload:
        try:
            notional = decimal(payload['notional'])
            if kind != 'market' or payload.get('side') != 'buy':
                problems.append('notional is only used on market buys')
            if notional < MINIMUM_NOTIONAL or notional != notional.quantize(decimal('.01')):
                problems.append('notional must be at least $1 in whole cents')
        except ValueError:
            problems.append('notional is not a number')
    if 'qty' in payload:
        text = payload['qty']
        try:
            qty = decimal(text)
            if (not isinstance(text, str) or 'e' in text.lower() or qty <= 0
                    or qty != qty.quantize(decimal(1).scaleb(-QUANTITY_PLACES))):
                problems.append(f'qty must be positive plain decimal text with at most {QUANTITY_PLACES} decimals')
        except ValueError:
            problems.append('qty is not a number')
    if kind == 'stop':
        if not valid_price_increment(payload.get('stop_price')):
            problems.append('stop_price must be whole cents at or above $1 (four decimals below $1)')
        if payload.get('side') != 'sell':
            problems.append('Socrates stops only protect long positions')
    elif 'stop_price' in payload:
        problems.append('stop_price is only sent on stop orders')
    return problems


class Executor:
    def __init__(self, broker, store, now=None, *, revision=None, expected_account_mode='live'):
        if expected_account_mode not in ('live', 'paper'):
            raise ValueError('Unsupported execution account mode')
        if expected_account_mode == 'paper':
            from .paper_broker import PaperAlpacaBroker
            if not isinstance(broker, PaperAlpacaBroker):
                raise ValueError('Paper execution requires the fixed paper-only broker adapter')
            broker.assert_paper_only()
        self.expected_account_mode = expected_account_mode
        self.broker, self.store = broker, store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.tick_lock, self.entry_lock = Lock(), Lock()
        self.entry_gate = None
        self.portfolio = None
        self.message = 'Live money is off. Analysis continues.'
        self.last_at = None
        self.revision = revision
        self.execution_check = None
        self.execution_diagnostic_error = None
        self._execution_check_from_current_process = False
        self._diagnostic_gate = 'runtime_state'
        self._diagnostic_trade = None
        self._diagnostic_broker_snapshot = {}
        self._selected_entry_signal = None
        self._diagnostic_outcome = None
        self._submission_attempted = False
        try:
            self.execution_check = self.store.latest_execution_check()
        except Exception:
            self.execution_diagnostic_error = 'Saved execution diagnostics unavailable; order handling is unchanged.'

    def _gate(self, code):
        """An observational marker only; it never permits, skips or changes a broker action."""
        self._diagnostic_gate = code

    def _signal_expiry(self, setup, now):
        return signal_expiry(setup, now, expected_policy_version=(
            PAPER_SIGNAL_POLICY_VERSION if self.expected_account_mode == 'paper' else SIGNAL_POLICY_VERSION))

    @staticmethod
    def _diagnostic_time(value):
        try:
            return timestamp(value).astimezone(timezone.utc).isoformat() if value is not None else None
        except (ValueError, TypeError, OverflowError):
            return None

    @staticmethod
    def _safe_account_ref(account):
        """The broker adapter supplies a hash; never persist raw account identifiers."""
        value = account.get('account_ref') if isinstance(account, dict) else None
        return value if isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) else None

    def _snapshot_account_ref(self, snapshot, now):
        account = snapshot.get('account')
        if (not isinstance(account, dict) or account.get('mode') != self.expected_account_mode
                or snapshot.get('account_error')):
            return None
        try:
            if not 0 <= elapsed(snapshot.get('account_at'), now) <= 30:
                return None
        except (ValueError, TypeError, OverflowError):
            return None
        return self._safe_account_ref(account)

    @staticmethod
    def _regular_session_state(clock, now):
        """Only a current, explicit broker clock proves an open or closed session."""
        if not isinstance(clock, dict) or type(clock.get('is_open')) is not bool:
            return None
        try:
            return clock['is_open'] if recent(clock.get('timestamp'), now, 15) else None
        except (ValueError, TypeError, OverflowError):
            return None

    def _diagnostic_signal(self, setup):
        """Allowlisted signal evidence, shared by analysis and entry selection."""
        setup = setup if isinstance(setup, dict) else {}
        checks = setup.get('checks') if isinstance(setup.get('checks'), list) else []
        first_failed = next((SIGNAL_GATES[c['name']] for c in checks if isinstance(c, dict)
                             and isinstance(c.get('name'), str) and c['name'] in SIGNAL_GATES
                             and c.get('passed') is not True), None)
        return {
            'event_at': self._diagnostic_time(setup.get('event_at')),
            'event_origin_at': self._diagnostic_time(setup.get('event_origin_at')),
            'event_expires_at': self._diagnostic_time(setup.get('event_expires_at')),
            'latest_evidence_at': self._diagnostic_time(setup.get('latest_evidence_at')),
            'leader_evidence_valid_until': self._diagnostic_time(setup.get('leader_evidence_valid_until')),
            'leader_observation_at': self._diagnostic_time(setup.get('leader_observation_at')),
            'leader_observation_valid_until': self._diagnostic_time(setup.get('leader_observation_valid_until')),
            'leader_observations_synchronized': setup.get('leader_observations_synchronized') is True,
            'event_id': setup.get('event_id') if isinstance(setup.get('event_id'), str) and re.fullmatch(r'(?:ev2_|paper_ev1_)[a-f0-9]{64}', setup['event_id']) else None,
            'strategy_id': setup.get('strategy_id') if isinstance(setup.get('strategy_id'), str) and setup['strategy_id'] in SIGNAL_METHODS else None,
            'event': setup.get('event') if setup.get('event') in ('break and retest', 'sweep and reclaim', 'previous-day level sweep') else None,
            'policy_version': setup.get('policy_version') if setup.get('policy_version') in (SIGNAL_POLICY_VERSION, PAPER_SIGNAL_POLICY_VERSION) else None,
            'direction': setup.get('direction') if setup.get('direction') in ('long', 'short') else None,
            'state': setup.get('state') if setup.get('state') in ('WATCHING', 'AT_LEVEL', 'WAITING_FOR_RETEST', 'CONFIRMING', 'SETUP_READY') else None,
            'first_failed_gate': first_failed,
        }

    def _record_execution_check(self, snapshot, outcome, exception_kind):
        """Persist allowlisted evidence; a logging failure cannot enter the trading path."""
        try:
            now = timestamp(self.last_at).astimezone(timezone.utc)
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            setup = snapshot.get('setup') if isinstance(snapshot.get('setup'), dict) else {}
            trade = self._diagnostic_trade if isinstance(self._diagnostic_trade, dict) else {}
            ops = trade.get('ops') if isinstance(trade.get('ops'), dict) else {}
            operations = {}
            for name in ('entry', 'stop', 'exit0', 'exit1', 'exit2'):
                operation = ops.get(name)
                if not isinstance(operation, dict):
                    continue
                state = operation.get('state')
                evidence = operation.get('last_seen') if isinstance(operation.get('last_seen'), dict) else {}
                status = evidence.get('status')
                operations[name] = {
                    'state': state if state in ('prepared', 'attempted', 'rejected', 'aborted_before_submit') else 'unknown',
                    'broker_status': status if isinstance(status, str) and status in ORDER_STATUSES else None,
                }
            identity = trade.get('id')
            trade_state = trade.get('stage')
            if trade:
                account_ref = self._safe_account_ref(trade)
            elif 'account' in self._diagnostic_broker_snapshot:
                account_ref = self._safe_account_ref(self._diagnostic_broker_snapshot['account'])
            else:
                account_ref = self._snapshot_account_ref(snapshot, now)
            clock = self._diagnostic_broker_snapshot.get('clock', snapshot.get('clock'))
            record = {
                'version': 'execution-check-v1', 'captured_at': now.isoformat(),
                'checkpoint_at': now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0).isoformat(),
                'app_version': APP_VERSION,
                'revision': self.revision if isinstance(self.revision, str) and re.fullmatch(r'[a-f0-9]{40}', self.revision) else None,
                'execution_policy': POLICY_VERSION,
                'account_mode': self.expected_account_mode,
                'account_ref': account_ref,
                'regular_session_open': self._regular_session_state(clock, now),
                'evidence_kind': 'synthetic_commissioning' if self.expected_account_mode == 'paper' else 'strategy_observation',
                'outcome': outcome if outcome in EXECUTION_OUTCOMES else 'unexpected_error',
                'gate': self._diagnostic_gate if self._diagnostic_gate in EXECUTION_GATES else 'runtime_state',
                'exception_kind': exception_kind,
                'analysis_at': self._diagnostic_time(snapshot.get('analysis_at')),
                'signal': self._diagnostic_signal(setup),
                'selected_entry_signal': self._diagnostic_signal(self._selected_entry_signal) if self._selected_entry_signal is not None else None,
                'trade': {
                    'id': identity if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{24}', identity) else None,
                    'stage': trade_state if trade_state in ('entering', 'open', 'exiting', 'attention', 'finished') else None,
                    'direction': trade.get('direction') if trade.get('direction') in ('long', 'short') else None,
                    'symbol': trade.get('symbol') if trade.get('symbol') in PROXY_SYMBOLS else None,
                    'signal_symbol': trade.get('signal_symbol') if trade.get('signal_symbol') == SIGNAL_SYMBOL else None,
                    'signal_direction': trade.get('signal_direction') if trade.get('signal_direction') in ('long', 'short') else None,
                    'proxy': trade.get('proxy') if trade.get('proxy') == PROXY_KIND else None,
                    'operation_states': operations,
                } if trade else None,
                'submission_attempted_this_tick': self._submission_attempted is True,
                'historical_only': True, 'order_authorized': False,
            }
            saved = self.store.record_execution_check(record)
            self.execution_check = saved
            self._execution_check_from_current_process = True
            self.execution_diagnostic_error = None
        except Exception:
            self.execution_diagnostic_error = 'Execution diagnostics could not be saved; latest saved check may be older. Order handling is unchanged.'

    def _execution_diagnostics_snapshot(self):
        check = deepcopy(self.execution_check)
        if check is not None and not isinstance(check, dict):
            return {'execution_check': None, 'execution_diagnostic_error': 'Saved execution diagnostics are invalid; order handling is unchanged.'}
        if check:
            try:
                seconds = (self.now() - timestamp(check['captured_at'])).total_seconds()
                current = self._execution_check_from_current_process and not self.execution_diagnostic_error and 0 <= seconds <= 30
            except Exception:
                current = False
            check.update(from_current_process=self._execution_check_from_current_process, current_at_snapshot=bool(current))
        return {'execution_check': check, 'execution_diagnostic_error': self.execution_diagnostic_error}

    def enabled(self):
        """Global Live, the reviewed policy, and the Socrates selection all agree.

        The durable selection is read here as well as through the portfolio hook
        so an app-initiated pause stops entries even in a runtime without a
        portfolio. An unreadable selection fails closed for new entries only.
        """
        c = self.store.control()
        return (c['enabled'] is True and c.get('policy') == POLICY_VERSION and self._family_selected()
                and (self.portfolio is None or self.portfolio.family_enabled('socrates')))

    def _family_selected(self):
        try:
            return self.store.strategy_selection().get('socrates') is True
        except Exception:
            return False

    def _pause(self, reason):
        """Stop new Socrates entries only; global Live stays as the owner set it and crypto keeps trading."""
        self.store.pause_family('socrates', reason)

    def set_live(self, payload):
        if set(payload) != {'enabled', 'policy_version'} or type(payload['enabled']) is not bool:
            raise ValueError('Invalid live-money setting')
        if payload['enabled']:
            if payload['policy_version'] != POLICY_VERSION:
                raise ValueError('Review the current execution rules before turning on')
            account = self.broker.account()
            if account.get('mode') != self.expected_account_mode:
                raise ValueError(f'This connection is not a {self.expected_account_mode} Alpaca account')
            self._account_ready(account)
            if not account.get('account_ref'):
                raise ValueError('The connected account identity could not be verified')
        # Off is serialized against starting an entry POST, but not against market-data loading.
        with self.entry_lock:
            result = self.store.set_control(payload['enabled'], POLICY_VERSION if payload['enabled'] else None,
                                            account.get('account_ref') if payload['enabled'] else None)
        self.message = ('Live money is on. Checking strategy and broker readiness.' if payload['enabled']
                        else 'Live money is off. Existing positions continue their exits.')
        return result

    @staticmethod
    def _account_ready(account):
        if account.get('status') != 'ACTIVE' or any(account.get(k) is not False for k in
                ('trading_blocked', 'account_blocked', 'trade_suspended_by_user')):
            raise Waiting('Alpaca account is not currently available for trading')

    def snapshot(self):
        trade = self.store.active_trade()
        control = self.store.control()
        review_required = control.get('enabled') is True and control.get('policy') != POLICY_VERSION
        return {'live_enabled': self.enabled(), 'execution_available': True, 'execution_account_mode': self.expected_account_mode,
                'review_required': review_required,
                **self._execution_diagnostics_snapshot(),
                'execution': {'message': self.message, 'at': self.last_at,
                    'trade': {k: trade.get(k) for k in ('stage', 'symbol', 'direction', 'signal_symbol', 'signal_direction', 'proxy',
                                                      'signal_geometry', 'proxy_geometry', 'amount', 'stop', 'target', 'reason',
                                                      'partial_entry', 'exit_pending', 'flat_reconciliation_started_at')} if trade else None}}

    def tick(self, snapshot):
        if not self.tick_lock.acquire(blocking=False):
            return
        self._diagnostic_gate = 'runtime_state'
        self._diagnostic_trade = None
        self._diagnostic_broker_snapshot = {}
        self._selected_entry_signal = None
        self._diagnostic_outcome = None
        self._submission_attempted = False
        outcome, exception_kind = 'managing', None
        try:
            trade = self.store.active_trade()
            self._diagnostic_trade = trade
            if trade:
                self._manage(trade)
                if self._check_exit_incident(trade):
                    self.message = trade['exit_pending']['reason']
            elif not self.enabled():
                self._gate('live_permission')
                outcome = 'disabled'
                self.message = 'Live money is off. Analysis continues.'
            else:
                outcome = 'entry_planned'
                # The updater's exclusive lock cannot begin between broker reads,
                # durable reservation and the first entry submission.
                with self.entry_gate.admit() if self.entry_gate else nullcontext():
                    with self.portfolio.admit() if self.portfolio else nullcontext():
                        self._entry(snapshot)
        except (Waiting, DeploymentHold, PortfolioBlocked, FeedError, ValueError) as exc:
            outcome = 'waiting' if isinstance(exc, (Waiting, DeploymentHold, PortfolioBlocked)) else 'feed_error' if isinstance(exc, FeedError) else 'invalid_data'
            exception_kind = 'Waiting' if isinstance(exc, (Waiting, DeploymentHold, PortfolioBlocked)) else 'FeedError' if isinstance(exc, FeedError) else 'ValueError'
            if isinstance(exc, (Waiting, DeploymentHold)) and isinstance(exc.code, str) and exc.code in EXECUTION_GATES:
                self._gate(exc.code)
            self.message = str(exc)
            if isinstance(exc, FeedError):
                # Account/clock/position reads can fail before management reaches
                # its protection check. Preserve the deadline for a stop already
                # known to be unconfirmed, using only durable local evidence.
                saved = self.store.active_trade()
                if saved and saved.get('stage') == 'entering':
                    if self._check_partial_entry_incident(saved):
                        self.message = saved['reason']
                op = (saved or {}).get('ops', {}).get('stop')
                status = ((op or {}).get('last_seen') or {}).get('status')
                if (saved and saved['stage'] in ('open', 'exiting') and op
                        and op['state'] == 'attempted' and status not in WORKING_PROTECTION | TERMINAL):
                    try:
                        self._check_unknown_protection(saved)
                    except Waiting as uncertainty:
                        self.message = str(uncertainty)
            # Failed reads and reconciliation waits cannot repeatedly grant an
            # unresolved exit a fresh deadline. Keep its known incident visible
            # while management continues to reconcile the existing orders.
            saved = self.store.active_trade()
            if saved and self._check_exit_incident(saved):
                self.message = saved['exit_pending']['reason']
        except Exception as exc:
            outcome, exception_kind = 'unexpected_error', UNEXPECTED_EXCEPTION_KINDS.get(type(exc), 'unexpected')
            logger.exception('Stock execution tick failed unexpectedly')
            self.message = 'Execution needs attention: broker state could not be validated. An order may already have been attempted; waiting for reconciliation.'
        finally:
            try:
                self.last_at = self.now().isoformat()
                # Preserve the primary failure classification. Known protection
                # and rejection outcomes can annotate otherwise ordinary waits.
                if outcome not in ('unexpected_error', 'invalid_data', 'feed_error') and self._diagnostic_outcome:
                    outcome = self._diagnostic_outcome
                try:
                    self._record_execution_check(snapshot, outcome, exception_kind)
                except Exception:
                    # This final boundary also protects order handling if the
                    # diagnostics implementation itself unexpectedly fails.
                    self.execution_diagnostic_error = 'Execution diagnostics could not be saved; order handling is unchanged.'
            finally:
                self.tick_lock.release()

    def _ready_contract(self, setup, now):
        setup = setup if isinstance(setup, dict) else {}
        checks = setup.get('checks') if isinstance(setup.get('checks'), list) else []
        self._gate('signal_checks')
        if (setup.get('state') != 'SETUP_READY' or len(checks) != len(CHECKS)
                or not all(isinstance(c, dict) and isinstance(c.get('name'), str) for c in checks)
                or {c['name'] for c in checks} != CHECKS
                or not all(c.get('passed') is True for c in checks)):
            failed = next((c['name'] for c in checks if isinstance(c, dict) and isinstance(c.get('name'), str) and c['name'] in CHECKS
                           and c.get('passed') is not True), 'the complete video setup')
            self._gate(SIGNAL_GATES.get(failed, 'signal_checks'))
            raise Waiting('Live money is on — waiting for ' + failed.lower())
        self._gate('signal_direction')
        if setup.get('direction') not in ('long', 'short'):
            raise Waiting('Setup direction is missing')
        self._gate('price_geometry')
        try:
            stop, reference, target = map(decimal, (setup['stop'], setup['entry'], setup['target']))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            raise Waiting('Waiting for valid setup entry, stop and target prices') from None
        if not (0 < stop < reference < target if setup['direction'] == 'long' else 0 < target < reference < stop):
            raise Waiting('Waiting for valid setup entry, stop and target prices')
        self._gate('signal_age_policy')
        return self._signal_expiry(setup, now)

    @staticmethod
    def _event_key(setup):
        # The opportunity is the QQQ event whichever instrument executes it.
        identity = f'{POLICY_VERSION}|{SIGNAL_SYMBOL}|{setup["event_id"]}'
        return sha256(identity.encode()).hexdigest()[:24]

    def _entry_candidate(self, setup, now):
        # Malformed alternatives cannot hide behind an otherwise valid top.
        # Independently expired windows are not ready opportunities, and must
        # not hide a still-current event. No broker failure triggers fallback.
        try:
            self._ready_contract(setup, now)
        except ExpiredSignal:
            pass
        self._gate('signal_checks')
        candidates = setup.get('entry_candidates', [setup])
        if not isinstance(candidates, list) or not candidates:
            raise Waiting('Waiting for a valid list of ready entry opportunities')
        validated = []
        for candidate in candidates:
            try:
                expires = self._ready_contract(candidate, now)
            except ExpiredSignal:
                continue
            validated.append((candidate, expires, self._event_key(candidate)))
        if not validated:
            self._gate('signal_age_policy')
            raise Waiting('Waiting for a current setup under the active video rules')
        self._gate('setup_deduplication')
        for candidate, expires, key in validated:
            if not self.store.entry_consumed(key):
                self._selected_entry_signal = candidate
                return candidate, expires, key
        raise Waiting('This setup has already been handled; waiting for the next event')

    def _assert_entry_exposure(self, account_ref, positions, orders, *, symbol=SIGNAL_SYMBOL, excluding_trade_id=None):
        if self.portfolio:
            self.portfolio.assert_exposure(account_ref, positions, orders, requesting_family='socrates',
                                           symbol=symbol, excluding_trade_id=excluding_trade_id)
        elif positions or orders:
            raise Waiting('Waiting for existing broker positions and orders to finish')

    def _entry(self, snapshot):
        now = self.now()
        if self.expected_account_mode == 'paper':
            if snapshot.get('commissioning_only') is not True or snapshot.get('synthetic_evidence') is not True:
                raise Waiting('Paper commissioning requires its explicitly labeled workflow fixture')
        elif snapshot.get('commissioning_only') or snapshot.get('synthetic_evidence'):
            raise Waiting('Synthetic commissioning evidence cannot authorize a live entry')
        # A known closed session is the entry wait even when overnight strategy
        # feeds are unavailable. Unknown clocks still follow the existing checks.
        # This runs only after live/deployment admission and never during management.
        if self._regular_session_state(snapshot.get('clock'), now) is False:
            self._gate('market_session')
            raise Waiting('Live money is on — waiting for the regular market session')
        self._gate('vix_candles')
        if snapshot.get('feeds', {}).get('vix') != 'current':
            raise Waiting('Live money is on — waiting for actual VIX data. No entry can be sent yet.')
        self._gate('analysis_freshness')
        if snapshot.get('data_errors') or not snapshot.get('analysis_at') or not 0 <= elapsed(snapshot['analysis_at'], now) <= 90:
            raise Waiting('Live money is on — waiting for fresh, complete strategy data')
        self._gate('data_expiry')
        if not data_unexpired(snapshot.get('data_valid_until'), now):
            raise Waiting('Live money is on — data verification expired; waiting for a fresh update')
        setup = snapshot.get('setup') if isinstance(snapshot.get('setup'), dict) else {}
        setup, expires, key = self._entry_candidate(setup, now)
        self._gate('entry_reservation')
        self.store.assert_session_entry_available(self.now(), family='socrates')
        authorization = self.store.entry_authorization()
        self._gate('broker_snapshot')
        account = self.broker.account()
        self._diagnostic_broker_snapshot['account'] = account
        positions, orders, clock = (self.broker.positions(), self.broker.orders(), self.broker.clock())
        self._diagnostic_broker_snapshot['clock'] = clock
        self._gate('account_status')
        self._account_ready(account)
        self._gate('account_identity')
        if account.get('account_ref') != authorization['control'].get('account_ref'):
            raise Waiting('The connected account changed. Turn off, review the account and enable again.')
        self._gate('account_mode')
        if account.get('mode') != self.expected_account_mode:
            raise Waiting(f'Execution requires the reviewed {self.expected_account_mode} account connection')
        self._gate('market_session')
        if not session_open(clock, self.now()):
            raise Waiting('Live money is on — waiting for the regular market session')
        self._gate('entry_cutoff')
        if closing(clock, self.now(), ENTRY_CUTOFF_SECONDS):
            raise Waiting('No new entries in the final 30 minutes of the market session')
        direction = setup['direction']
        self._gate('signal_direction')
        if direction not in ('long', 'short'):
            raise Waiting('Setup direction is missing')
        # A long buys QQQ. A short is executed as a PSQ purchase (inverse-ETF
        # proxy, see proxy_translation): every management step below then treats
        # the trade as a long in its traded symbol. App choice, not a video rule.
        proxy = direction == 'short'
        traded = PROXY_SYMBOL if proxy else SIGNAL_SYMBOL
        self._gate('existing_exposure')
        self._assert_entry_exposure(account['account_ref'], positions, orders, symbol=traded)
        self._gate('proxy_eligibility' if proxy else 'asset_eligibility')
        asset = self.broker.asset(traded)
        if asset.get('symbol') != traded or asset.get('status') != 'active' or asset.get('tradable') is not True:
            raise Waiting(f'{traded} is not currently tradable' + ('; this short setup is skipped, not an error' if proxy else ''))
        fractionable = asset.get('fractionable') is True
        self._gate('quote_read')
        quote = self.broker.quote(SIGNAL_SYMBOL)
        self._gate('quote_validation')
        bid, ask = checked_quote(quote, self.now())
        # The quote is already required to be at most 15 seconds old. Anchor the
        # submission budget to its receipt, not its exchange timestamp: the broker
        # reads that follow must not consume a window that was partly spent
        # before the quote arrived, or entries churn "expired before submission".
        entry_valid_until = min(timestamp(snapshot['data_valid_until']), self.now()+timedelta(seconds=15), expires).isoformat()
        self._gate('price_geometry')
        stop, target, reference = map(decimal, (setup['stop'], setup['target'], setup['entry']))
        if not (0 < stop < bid <= ask < target if direction == 'long' else 0 < target < bid <= ask < stop):
            raise Waiting('Price has left the entry area between the stop and target')
        self._gate('price_drift')
        # The signal reference is a QQQ price for both directions; a short is
        # still gated on QQQ having stayed near its retest before PSQ is read.
        if abs((ask if direction == 'long' else bid) / reference - 1) > decimal('.01'):
            raise Waiting('Price has moved more than 1% from the signal; skipping this entry')
        price, execution_stop, execution_target, proxy_geometry = ask, stop, target, None
        if proxy:
            self._gate('quote_read')
            proxy_quote = self.broker.quote(PROXY_SYMBOL)
            self._gate('quote_validation')
            proxy_bid, proxy_ask = checked_quote(proxy_quote, self.now(), PROXY_SYMBOL)
            self._gate('price_geometry')
            # Distances are measured from the live QQQ bid admitted above, not the
            # plan's hourly-close reference, so the PSQ stop and target mirror
            # where QQQ's structural stop and target levels are from here.
            price, execution_stop, execution_target = proxy_translation(proxy_ask, bid, stop, target)
            if not 0 < execution_stop < proxy_bid <= proxy_ask < execution_target:
                raise Waiting(f'{PROXY_SYMBOL} is not between its translated stop and target; skipping this short')
            proxy_geometry = {'symbol': PROXY_SYMBOL, 'kind': PROXY_KIND, 'reference': str(proxy_ask),
                              'bid': str(proxy_bid), 'stop': str(execution_stop), 'target': str(execution_target),
                              'signal_price': str(bid),
                              'note': ('QQQ short distances from the live QQQ bid to its stop and target, mirrored '
                                       'onto a PSQ purchase; app interpretation, not a video rule')}
        # The protective stop is placed from this price after the fill. A price
        # Alpaca would reject (sub-penny) would leave filled shares unprotected,
        # so such an entry is skipped before any order is sent.
        if not valid_price_increment(execution_stop):
            raise Waiting(f'The {traded} stop price {execution_stop} is not in whole cents, which Alpaca rejects; skipping this entry')
        self._gate('purchase_size')
        amount = authorization['settings']['target_dollars']
        buying_power = (self.portfolio.available(account['account_ref'], account['buying_power'])
                        if self.portfolio else account['buying_power'])
        try:
            plan = purchase_plan(amount, price, buying_power, 'long', fractionable)
        except ValueError as exc:
            # A valid setup that cannot be sized is a skipped opportunity, not
            # invalid data: a non-fractionable proxy needs whole shares within 1%.
            if proxy and not fractionable:
                raise Waiting(f'{PROXY_SYMBOL} is not fractionable and no whole-share quantity fits within 1% of the '
                              f'${amount} purchase target; this short is skipped') from None
            raise Waiting(f'Purchase cannot be sized: {exc}') from None
        # A cheap candle cache does not prove that a current index quote exists.
        # This read-only check runs only for an otherwise actionable entry.
        if getattr(self.broker, 'requires_vix_entry_quote', False):
            self._gate('vix_entry_quote')
            verification = self.broker.confirm_vix_quote(self.now())
            delay, value = verification.get('delay_seconds'), verification.get('value')
            if (verification.get('source') != 'insightsentry' or verification.get('symbol') != 'I:VIX'
                    or isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay != 0
                    or isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0
                    or not recent(verification['updated_at'], self.now(), 90)):
                raise Waiting('Waiting for a verified current actual VIX quote')
            entry_valid_until = min(timestamp(entry_valid_until), timestamp(verification['updated_at'])+timedelta(seconds=90)).isoformat()
        self._gate('final_analysis_freshness')
        if not 0 <= elapsed(snapshot['analysis_at'], self.now()) <= 90:
            raise Waiting('Strategy data aged during broker checks; waiting for the next analysis')
        self._gate('final_data_expiry')
        self._signal_expiry(setup, self.now())
        if not data_unexpired(entry_valid_until, self.now()):
            raise Waiting('Data verification expired during broker checks; waiting for a fresh update')
        # One attempt per underlying event, including a rejected or completed
        # attempt. A later retest or changed leader direction cannot re-enter it.
        payload = {'symbol': traded, 'side': 'buy', 'type': 'market', 'time_in_force': 'day', 'extended_hours': False}
        if fractionable:
            payload['notional'] = plan['target_dollars']
        else:
            payload['qty'] = plan['quantity']
        self._gate('order_prepare')
        if order_payload_violations({**payload, 'client_order_id': f'pvt-{key}-entry'}):
            raise Waiting('The entry order would not meet Alpaca order rules; skipping this entry')
        fields = SIGNAL_FIELDS + (('commissioning_purpose',) if self.expected_account_mode == 'paper' else ())
        # 'direction' is the executed side in 'symbol' (always a purchase here);
        # 'signal_direction' keeps the QQQ setup's side for the journal and UI.
        trade = {'id': key, 'stage': 'entering', 'symbol': traded, 'direction': 'long',
                 'signal_symbol': SIGNAL_SYMBOL, 'signal_direction': direction, 'proxy': PROXY_KIND if proxy else None,
                 'signal_geometry': {'symbol': SIGNAL_SYMBOL, 'direction': direction, 'entry': str(reference),
                                     'stop': str(stop), 'target': str(target)},
                 'proxy_geometry': proxy_geometry,
                 'signal': {field: setup[field] for field in fields},
                 'amount': plan['target_dollars'], 'stop': str(execution_stop), 'target': str(execution_target),
                 'created_at': self.now().isoformat(), 'data_valid_until': entry_valid_until, 'ops': {}, 'exit_number': 0, 'account_ref': account['account_ref'],
                 'authorization': authorization, 'execution_mode': self.expected_account_mode}
        self._prepare(trade, 'entry', payload, persist=False)
        with self.entry_lock:
            self._gate('live_permission')
            if not self.enabled():
                self._diagnostic_outcome = 'disabled'
                return
            if self.store.entry_authorization() != authorization:
                raise Waiting('Purchase settings or live permission changed during entry checks; waiting for a fresh plan')
            self._gate('entry_reservation')
            if not self.store.reserve_trade(trade, expected_authorization=authorization):
                raise Waiting('This setup has already been handled; waiting for the next event')
        self._diagnostic_trade = trade
        self.store.event('entry_planned', {k: trade[k] for k in ('symbol', 'direction', 'signal_symbol', 'signal_direction',
                                                                 'proxy', 'amount', 'stop', 'target')})
        self._manage(trade)

    def _prepare(self, trade, name, payload, persist=True):
        self._gate('order_prepare')
        self._diagnostic_trade = trade
        payload = {**payload, 'client_order_id': f'pvt-{trade["id"]}-{name}'}
        trade['ops'][name] = {'state': 'prepared', 'payload': payload}
        if name == 'stop':
            trade['ops'][name]['confirmation_deadline'] = (
                self.now() + timedelta(seconds=PROTECTION_CONFIRM_SECONDS)).isoformat()
        if persist:
            self.store.save_trade(trade)

    def _prepared_entry_locally_expired(self, trade):
        # Exact control/settings generation, not merely a currently enabled
        # switch. Off -> resize -> On cannot revive a plan using the old amount.
        if not trade.get('authorization') or trade['authorization'] != self.store.entry_authorization():
            return True
        now = self.now()
        try:
            self._signal_expiry(trade.get('signal') or {}, now)
        except Waiting:
            # Only unsent entry intents need the current signal contract. An
            # already attempted entry or held position still needs management.
            return True
        return (not self.enabled() or elapsed(trade['created_at'], now) > 10
                or not data_unexpired(trade.get('data_valid_until'), now))

    def _prepared_entry_expired(self, trade, clock):
        now = self.now()
        return (self._prepared_entry_locally_expired(trade)
                or not session_open(clock, now) or closing(clock, now, ENTRY_CUTOFF_SECONDS))

    def _order(self, trade, name):
        if name == 'entry' and trade['ops'][name]['state'] == 'prepared':
            # Recovered durable intents also need admission. Acquire deployment
            # before entry_lock; live-off keeps its existing entry_lock ordering.
            with self.entry_gate.admit() if self.entry_gate else nullcontext():
                with self.portfolio.admit() if self.portfolio else nullcontext():
                    return self._recover_prepared_entry(trade, name)
        return self._order_admitted(trade, name)

    def _recover_prepared_entry(self, trade, name):
        with self.entry_lock:
            # Local proof of an expired never-submitted intent does not
            # depend on a reachable/unchanged broker account. Retiring it
            # is conditional on its exact durable prepared state.
            if self._prepared_entry_locally_expired(trade):
                self._expire_entry(trade)
                return None
            if self.store.active_trade() != trade:
                raise Waiting('Entry state changed; waiting for durable order reconciliation')
            self._gate('broker_snapshot')
            # A durable but unsent intent can be recovered after the
            # account changed or another order/position appeared. Only
            # attempted operations may skip these entry-only reads.
            account = self.broker.account()
            self._gate('account_status')
            self._account_ready(account)
            self._gate('account_identity')
            if account.get('account_ref') != trade['account_ref']:
                raise Waiting('The connected account changed before entry submission')
            self._gate('account_mode')
            if account.get('mode') != self.expected_account_mode:
                raise Waiting('The account environment changed before entry submission')
            self._gate('existing_exposure')
            self._assert_entry_exposure(account['account_ref'], self.broker.positions(), self.broker.orders(),
                                        symbol=trade['symbol'], excluding_trade_id=trade['id'])
            self._gate('market_session')
            clock = self.broker.clock()
            self._diagnostic_broker_snapshot['clock'] = clock
            if self._prepared_entry_expired(trade, clock):
                self._expire_entry(trade)
                return None
            return self._order_admitted(trade, name)

    def _order_admitted(self, trade, name):
        self._gate('order_reconciliation')
        self._diagnostic_trade = trade
        op = trade['ops'][name]
        if op['state'] == 'rejected':
            return {'status': 'rejected', 'filled_qty': '0', 'client_order_id': op['payload']['client_order_id']}
        try:
            if op['state'] == 'prepared':
                # Commit BEFORE POST. A crash or timeout can never produce an automatic second POST.
                if self.store.claim_operation(trade['id'], name, expected_entry=trade if name == 'entry' else None,
                                              attempted_at=self.now()):
                    op['state'] = 'attempted'
                    if name == 'entry':
                        # Waiting for the durable claim can cross an evidence
                        # deadline. Keep the claim consumed, but do not POST an
                        # entry whose saved authorization has now expired. This
                        # process knows submit has not been called: retire the
                        # consumed event without blocking all later events.
                        self._gate('final_data_expiry')
                        authorized = trade.get('authorization') == self.store.entry_authorization()
                        now = self.now()
                        try:
                            self._signal_expiry(trade.get('signal') or {}, now)
                            valid = authorized and elapsed(trade['created_at'], now) <= 10 and data_unexpired(trade.get('data_valid_until'), now)
                        except Waiting:
                            valid = False
                        if not valid:
                            self._abort_claimed_entry(trade)
                            return None
                    try:
                        self._gate('order_submit')
                        self._submission_attempted = True
                        order = self.broker.submit(op['payload'])
                    except BrokerRejected as exc:
                        self._gate('order_rejection')
                        self._diagnostic_outcome = 'order_rejected'
                        # The cause (HTTP status and Alpaca's numeric error code,
                        # never the response text) travels with the trade record,
                        # the pause reason, the journal and the webhook alert: a
                        # 4xx POST creates no order at Alpaca, so the app is the
                        # only place the owner can read why.
                        detail = exc.detail()
                        op['state'] = 'rejected'
                        op['last_seen'] = {'symbol':op['payload']['symbol'],'side':op['payload']['side'],
                                          'status':'rejected','qty':op['payload'].get('qty'),'filled_qty':'0',
                                          'client_order_id':op['payload']['client_order_id'],'evidence':'http_rejection',
                                          'reason':detail}
                        self.store.save_trade(trade)
                        self._pause(f'The broker rejected a {trade["symbol"]} {name} order ({detail}); no order was created at Alpaca. '
                                    'Review the account and this code before enabling Socrates again.')
                        self.store.event('order_rejected', {'purpose': name, 'symbol': trade['symbol'],
                                                           'evidence': 'http_rejection', 'reason': detail})
                        return {'status': 'rejected', 'filled_qty': '0'}
                else:
                    if name == 'entry':
                        # Another worker may have attempted, retired, or replaced
                        # this reservation. Reload its durable state next tick;
                        # this stale object cannot submit or manage the replacement.
                        raise Waiting('Entry state changed; waiting for durable order reconciliation')
                    op['state'] = 'attempted'
                    self._gate('order_lookup')
                    order = self.broker.lookup(op['payload']['client_order_id'])
            else:
                self._gate('order_lookup')
                order = self.broker.lookup(op['payload']['client_order_id'])
        except FeedError:
            if name == 'stop':
                self._check_unknown_protection(trade)
            raise
        if not order:
            order = self._resolve_never_reached_broker(trade, name)
            if order is None:
                if name == 'stop':
                    self._check_unknown_protection(trade)
                raise Waiting('Order status is uncertain. Waiting for its broker identifier; no duplicate will be sent.')
            return order
        if (op.get('last_seen') or {}).get('evidence') == 'not_found_at_broker':
            # The broker now reports an order this app had concluded never
            # arrived. Adopt the actual evidence; the synthetic record cannot win.
            op['last_seen'] = {}
            self.store.event('order_late_arrival', {'trade_id': trade['id'], 'operation': name, 'symbol': trade['symbol']})
        self._gate('order_identity')
        try:
            self._validate_order_payload(order, op['payload'], op.get('last_seen') or {})
        except (ValueError, KeyError, TypeError):
            # Keep the lifecycle and last verified evidence intact. A later good
            # lookup must still resume supervision, including while entries are
            # paused; malformed evidence cannot authorize a competing sale.
            self._pause(f'A {trade["symbol"]} {name} order at the broker does not match its saved request. Review Alpaca before enabling Socrates again.')
            incidents = trade.setdefault('order_validation', {})
            if name not in incidents or incidents[name].get('resolved_at'):
                incidents[name] = {'first_observed_at': self.now().isoformat(),
                                   'reason': 'Broker order does not match its durable request'}
                self.store.save_trade(trade)
                self.store.event('order_validation_failed', {'trade_id': trade['id'], 'operation': name,
                                                            'symbol': trade['symbol']})
            self._diagnostic_outcome = 'attention'
            raise Waiting('Broker order details do not match the saved request. New entries paused; reconciling the existing order without another submission.') from None
        incident = trade.get('order_validation', {}).get(name)
        if incident and not incident.get('resolved_at'):
            incident['resolved_at'] = self.now().isoformat()
            self.store.save_trade(trade)
            self.store.event('order_validation_reconciled', {'trade_id': trade['id'], 'operation': name})
        if order.get('status') == 'rejected':
            self._gate('order_rejection')
            self._diagnostic_outcome = 'order_rejected'
            # A successful HTTP response can later become a venue rejection.
            # Pause entries just as for HTTP rejection, but keep the broker's
            # actual fill evidence so existing exposure can still be managed.
            self._pause(f'The venue rejected a {trade["symbol"]} {name} order. Review Alpaca before enabling Socrates again.')
            if (op.get('last_seen') or {}).get('status') != 'rejected':
                self.store.event('order_rejected', {'purpose': name, 'symbol': trade['symbol'],
                                                   'evidence': 'broker_status'})
        evidence = {k:order.get(k) for k in ('id','client_order_id','symbol','side','status','qty','notional',
                    'type','time_in_force','stop_price','limit_price','extended_hours','order_class',
                    'filled_qty','filled_avg_price','updated_at','filled_at')}
        if op.get('last_seen') != evidence:
            op['last_seen'] = evidence
            self.store.save_trade(trade)
        if not self._diagnostic_outcome:
            self._gate('order_reconciliation')
        return order

    @staticmethod
    def _validate_order_payload(order, payload, previous):
        """Validate actual broker evidence without imposing requested-qty on notional buys.

        Alpaca documents stop -> market election. Its legacy official order
        documentation also describes buy-stop -> stop-limit collars (4% below
        $50, 2.5% otherwise). Preserve these legal transitions while verifying the
        original stop, requested quantity, DAY lifetime and immutable identity.
        """
        if (not isinstance(order, dict) or not isinstance(order.get('id'), str) or not order['id']
                or any(order.get(key) != payload[key] for key in ('client_order_id', 'symbol', 'side', 'time_in_force'))
                # Unknown future statuses remain unconfirmed, so the existing
                # cancellation-safe protection deadline can still recover them.
                or not isinstance(order.get('status'), str) or not order['status']
                or order.get('order_class') not in (None, '', 'simple') or order.get('legs')
                or order.get('extended_hours', False) is not payload.get('extended_hours', False)
                or previous.get('id') and order['id'] != previous['id']):
            raise ValueError('Order identity or execution scope differs')
        requested_type, reported_type = payload['type'], order.get('type')
        if requested_type == 'stop':
            stop = decimal(payload['stop_price'])
            if stop <= 0 or decimal(order.get('stop_price')) != stop:
                raise ValueError('Stop changed')
            if reported_type not in ('stop', 'market'):
                if payload['side'] != 'buy' or reported_type not in ('stop_limit', 'limit'):
                    raise ValueError('Unsupported stop conversion')
                collar = stop * decimal('1.04' if stop < 50 else '1.025')
                if abs(decimal(order.get('limit_price')) - collar) > decimal('.01'):
                    raise ValueError('Buy-stop collar differs')
        elif reported_type != requested_type:
            raise ValueError('Order type differs')
        for key in ('qty', 'notional', 'limit_price'):
            if key in payload and (decimal(payload[key]) <= 0 or decimal(order.get(key)) != decimal(payload[key])):
                raise ValueError('Requested size or limit differs')
        filled = decimal(order.get('filled_qty'))
        if filled < 0 or filled < decimal(previous.get('filled_qty') or '0'):
            raise ValueError('Invalid cumulative fill quantity')
        if order.get('qty') is not None:
            qty = decimal(order['qty'])
            if qty < 0 or filled > qty or ('qty' in payload and qty <= 0):
                raise ValueError('Fill exceeds order quantity')
        if filled > 0 and decimal(order.get('filled_avg_price')) <= 0:
            raise ValueError('Filled order needs a valid execution price')
        if order['status'] == 'filled' and (filled <= 0 or ('qty' in payload and filled != decimal(payload['qty']))):
            raise ValueError('Terminal fill does not match requested quantity')

    def _expire_entry(self, trade):
        self._gate('trade_finish')
        finished = self.store.expire_prepared_entry(trade, self.now().isoformat())
        if finished is None:
            raise Waiting('Entry state changed; waiting for durable order reconciliation')
        trade.update(finished)
        self._diagnostic_trade = trade
        self._diagnostic_outcome = self._diagnostic_outcome or 'completed'
        self.message = trade['reason']

    def _abort_claimed_entry(self, trade):
        """Only called by the claiming process before its broker submit call."""
        finished = self.store.abort_claimed_entry(trade, self.now().isoformat())
        if finished is None:
            raise Waiting('Entry state changed; waiting for durable order reconciliation')
        trade.update(finished)
        self._diagnostic_trade = trade
        self._diagnostic_outcome = 'completed'
        self.message = trade['reason']

    def _resolve_never_reached_broker(self, trade, name):
        """Bounded terminal resolution for a POST that never created a broker order.

        A transport failure can leave a claimed operation with no broker order at
        all. Alpaca indexes client_order_id on acceptance, so a lookup that keeps
        returning 404 after the confirmation window, with no unknown order and no
        unexplained exposure in the trade's symbol (QQQ or PSQ), shows the request
        never arrived. Recording
        that as terminal evidence lets management continue: an entry finishes
        without a fill, a missing stop closes the position through the existing
        protection-failure exit, and a missing exit is followed by the next exit.
        A later order with the same identifier is adopted, never duplicated.
        """
        op = trade['ops'][name]
        seen = op.get('last_seen') or {}
        if seen.get('evidence') == 'not_found_at_broker':
            return deepcopy(seen)
        if op.get('state') != 'attempted' or seen:
            return None
        now = self.now()
        if not op.get('not_found_since'):
            op['not_found_since'] = now.isoformat()
            self.store.save_trade(trade)
            return None
        if elapsed(op['not_found_since'], now) < ORDER_NOT_FOUND_CONFIRM_SECONDS:
            return None
        payload = op['payload']
        ours = {o['payload']['client_order_id'] for o in trade['ops'].values()}
        try:
            open_orders = self.broker.orders()
            positions = self.broker.positions()
        except FeedError:
            return None
        for order in open_orders:
            if order.get('symbol') == trade['symbol'] and (
                    order.get('client_order_id') == payload['client_order_id'] or order.get('client_order_id') not in ours):
                return None  # The order, or an unexplained order, exists; keep reconciling.
        held = next((p for p in positions if p.get('symbol') == trade['symbol']), None)
        if name == 'entry' and held is not None:
            return None  # Exposure without a known fill cannot prove the entry never arrived.
        op['last_seen'] = {'symbol': payload['symbol'], 'side': payload['side'], 'status': 'expired',
                           'qty': payload.get('qty'), 'notional': payload.get('notional'), 'filled_qty': '0',
                           'client_order_id': payload['client_order_id'], 'evidence': 'not_found_at_broker',
                           'confirmed_at': now.isoformat()}
        self.store.save_trade(trade)
        self.store.event('order_not_found', {'trade_id': trade['id'], 'operation': name, 'symbol': trade['symbol'],
                                             'not_found_since': op['not_found_since']})
        return deepcopy(op['last_seen'])

    def _finish(self, trade, reason):
        self._gate('trade_finish')
        self._diagnostic_trade = trade
        self._diagnostic_outcome = self._diagnostic_outcome or 'completed'
        pending = trade.get('exit_pending')
        if pending and not pending.get('resolved_at'):
            pending['resolved_at'] = self.now().isoformat()
        trade.update(stage='finished', reason=reason, completed_at=self.now().isoformat())
        self.store.save_trade(trade, finished=True)
        if pending and pending.get('raised_at'):
            self.store.event('exit_reconciled', {'symbol': trade['symbol'],
                'first_observed_at': pending['first_observed_at'], 'reason': reason})
        self.store.event('trade_finished', {'symbol': trade['symbol'], 'reason': reason})
        self.message = reason

    def _attention(self, trade, reason):
        self._gate('owner_attention')
        self._diagnostic_trade = trade
        self._diagnostic_outcome = 'attention'
        self._pause(reason)
        trade.update(stage='attention', reason=reason)
        self.store.save_trade(trade)
        self.store.event('execution_needs_attention', {'symbol':trade['symbol'],'reason':reason})
        raise Waiting(reason)

    def _reconcile_attention(self, trade):
        # Manual resolution is read-only: never compete with an owner's changes.
        symbol=trade['symbol']
        if any(p['symbol']==symbol for p in self.broker.positions()) or any(o['symbol']==symbol for o in self.broker.orders()):
            raise Waiting(trade['reason'])
        for name, op in trade['ops'].items():
            if op['state']=='prepared':
                continue  # Durable intent proves this operation was never attempted.
            order=self._order(trade,name)
            if order['status'] not in TERMINAL | {'replaced'}:
                raise Waiting(trade['reason'])
        # Confirm flatness again after order lookups, which can take time.
        if any(p['symbol']==symbol for p in self.broker.positions()) or any(o['symbol']==symbol for o in self.broker.orders()):
            raise Waiting(trade['reason'])
        # Socrates stays paused after a manual resolution; global Live is the owner's.
        self._pause(f'A {symbol} trade was resolved manually at the broker. Review the outcome before enabling Socrates again.')
        trade['manual_reconciliation']=True
        self.store.event('manual_resolution_confirmed', {'symbol':symbol,'note':f'Broker is flat with no working {symbol} orders; external fill economics remain unverified'})
        self._finish(trade,'Manual resolution confirmed. Socrates remains paused; review before enabling it again.')

    def _position(self, trade):
        self._gate('position_reconciliation')
        positions = self.broker.positions()
        position = next((p for p in positions if p['symbol'] == trade['symbol']), None)
        # Inspect owned orders before classifying a replacement's new identifier
        # as foreign, so manual resolution remains available afterward.
        exited = decimal('0')
        for name, op in trade['ops'].items():
            if name == 'entry' or op['state'] == 'prepared': continue
            order = self._order(trade, name)
            if order['status']=='replaced':
                self._attention(trade,f'An owned {trade["symbol"]} order was replaced outside the app. Check the position and working orders in Alpaca now.')
            exited += decimal(order.get('filled_qty') or '0')
        if position:
            qty = decimal(position['qty'])
            if (qty > 0) != (trade['direction'] == 'long'):
                self._attention(trade,f'{trade["symbol"]} position direction changed outside the app; manual review required')
            # Only manage the shares this app opened, less confirmed exit fills.
            expected = decimal(trade.get('filled_qty', '0')) - exited
            if abs(qty) != expected:
                raise Waiting(f'{trade["symbol"]} share count differs from this app’s fills; waiting for broker reconciliation')
        # Foreign orders in the traded symbol could change or reserve the position. Never cancel or compete with them.
        ours = {op['payload']['client_order_id'] for op in trade['ops'].values()}
        if any(o['symbol'] == trade['symbol'] and o.get('client_order_id') not in ours for o in self.broker.orders()):
            self._attention(trade,f'A {trade["symbol"]} order outside this app needs review before automated position changes')
        return position

    def _check_partial_entry_incident(self, trade, order=None):
        """Persist an incident while cancellation is uncertain, without competing exits."""
        if trade.get('stage') != 'entering':
            return False
        order = order or trade.get('ops', {}).get('entry', {}).get('last_seen') or {}
        if order.get('status') in TERMINAL or decimal(order.get('filled_qty') or '0') <= 0:
            return False
        now = self.now()
        incident = trade.get('partial_entry')
        if incident is None:
            incident = trade['partial_entry'] = {
                'first_observed_at': now.isoformat(),
                'confirmation_deadline': (now + timedelta(seconds=PARTIAL_ENTRY_CONFIRM_SECONDS)).isoformat(),
                'filled_qty': str(decimal(order['filled_qty'])),
            }
            self.store.save_trade(trade)
        elif decimal(order['filled_qty']) > decimal(incident['filled_qty']):
            incident['filled_qty'] = str(decimal(order['filled_qty']))
            self.store.save_trade(trade)
        if now < timestamp(incident['confirmation_deadline']):
            return False
        self._pause(f'A {trade["symbol"]} entry partially filled while its cancellation stayed unconfirmed. Review Alpaca before enabling Socrates again.')
        reason = ('Entry partially filled, but cancellation is still unconfirmed. New entries paused; '
                  f'no competing exit will be sent. Check the {trade["symbol"]} position and orders in Alpaca now.')
        if not incident.get('raised_at'):
            incident['raised_at'] = now.isoformat()
            trade['reason'] = reason
            self.store.save_trade(trade)
            self.store.event('partial_entry_needs_attention', {
                'symbol': trade['symbol'], 'filled_qty': incident['filled_qty'],
                'first_observed_at': incident['first_observed_at'], 'reason': reason})
        self._diagnostic_outcome = 'attention'
        self._gate('owner_attention')
        return True

    def _manage(self, trade):
        self._gate('trade_management')
        self._diagnostic_trade = trade
        self._check_exit_incident(trade)
        if trade.get('execution_mode', 'live') != self.expected_account_mode:
            raise Waiting('This trade belongs to a different execution environment; restore its isolated runtime')
        if trade['stage'] == 'entering' and trade['ops']['entry']['state'] == 'prepared':
            with self.entry_lock:
                if self._prepared_entry_locally_expired(trade):
                    self._expire_entry(trade)
                    return
        if self.broker.account().get('account_ref') != trade['account_ref']:
            raise Waiting('This position belongs to a different Alpaca connection; restore its account to manage it')
        clock = self.broker.clock()
        self._diagnostic_broker_snapshot['clock'] = clock
        now = self.now()
        opened = session_open(clock, now)
        if trade['stage'] == 'entering':
            # Expiry is not new exposure and must keep working during a hold.
            with self.entry_lock:
                if trade['ops']['entry']['state'] == 'prepared' and self._prepared_entry_expired(trade, clock):
                    self._expire_entry(trade)
                    return
            order = self._order(trade, 'entry')
            if order is None:
                return
            if order['status']=='replaced':
                self._attention(trade,f'Entry order was replaced outside the app. Check the {trade["symbol"]} position and orders in Alpaca now.')
            if order['status'] not in TERMINAL:
                incident = self._check_partial_entry_incident(trade, order)
                if decimal(order.get('filled_qty') or '0') > 0 or not self.enabled() or elapsed(trade['created_at'], now) > 20 or (opened and closing(clock, now)):
                    self._gate('order_cancel')
                    try:
                        self.broker.cancel(order['id'])
                    except FeedError:
                        if incident:
                            raise Waiting(trade['reason']) from None
                        raise
                    self.message = trade['reason'] if incident else 'Canceling the unfinished entry before managing filled shares'
                else:
                    self.message = 'Entry submitted; waiting for the broker fill'
                return
            qty = decimal(order.get('filled_qty') or '0')
            partial = trade.get('partial_entry')
            if partial and not partial.get('resolved_at'):
                partial.update(resolved_at=self.now().isoformat(), final_filled_qty=str(qty))
                if partial.get('raised_at'):
                    trade['reason'] = 'Partial entry reconciled; new entries remain paused while confirmed shares are managed.'
                self.store.save_trade(trade)
                if partial.get('raised_at'):
                    self.store.event('partial_entry_reconciled', {
                        'symbol': trade['symbol'], 'filled_qty': str(qty), 'status': order['status']})
            if not qty:
                self._finish(trade, 'Entry ended without a fill')
                return
            trade.update(stage='open', filled_qty=str(qty))
            self.store.save_trade(trade)
            self.store.event('entry_filled', {'symbol': trade['symbol'], 'signal_symbol': trade.get('signal_symbol'),
                                              'signal_direction': trade.get('signal_direction'), 'proxy': trade.get('proxy'),
                                              'quantity': str(qty), 'price': order.get('filled_avg_price')})
        if trade['stage'] == 'attention':
            self._reconcile_attention(trade)
            return
        position = self._position(trade)
        if not position:
            # Missing position is only final after all entry shares have confirmed exit fills.
            exits=[self._order(trade,n) for n,op in trade['ops'].items() if n!='entry' and op['state']!='prepared']
            total = sum((decimal(order.get('filled_qty') or '0') for order in exits), decimal('0'))
            if total < decimal(trade['filled_qty']):
                if exits and all(order['status'] in TERMINAL for order in exits):
                    self._attention(trade,f'{trade["symbol"]} is flat but the app’s exit fills do not reconcile. Checking for manual resolution; results remain unverified.')
                if not exits:
                    entry = self._order(trade, 'entry')
                    if entry['status'] in TERMINAL:
                        since = trade.get('flat_reconciliation_started_at')
                        if since is None:
                            trade['flat_reconciliation_started_at'] = self.now().isoformat()
                            self.store.save_trade(trade)
                        elif elapsed(since, self.now()) >= MANUAL_FLAT_CONFIRM_SECONDS:
                            self._attention(trade, f'{trade["symbol"]} is flat but no app exit was recorded. Checking for manual resolution; results remain unverified.')
                raise Waiting('Waiting for the broker position to reconcile with confirmed fills')
            for name, op in trade['ops'].items():
                if name == 'entry' or op['state'] == 'prepared': continue
                order = self._order(trade, name)
                if order['status'] not in TERMINAL:
                    self._gate('order_cancel')
                    self.broker.cancel(order['id'])
                    raise Waiting('Position closed; confirming remaining owned orders are canceled')
            self._finish(trade, 'Position closed; waiting for the next video setup')
            return
        if trade.pop('flat_reconciliation_started_at', None) is not None:
            self.store.save_trade(trade)
        stop_order = None
        stop_op = trade['ops'].get('stop')
        if stop_op and (stop_op['state'] != 'prepared' or (opened and trade['stage'] != 'exiting')):
            # Looking up an attempted order is safe outside the session, but a
            # recovered prepared intent must not place a new after-hours stop.
            stop_order = self._order(trade, 'stop')
        if stop_order and stop_order['status'] == 'replaced':
            self._attention(trade,f'Protective order was replaced outside the app. Check {trade["symbol"]} protection in Alpaca now.')
        if stop_order and trade['stage'] != 'exiting':
            failure = self._protection_failure(trade, stop_order)
            if failure:
                self._start_protection_exit(trade, failure)
        if not opened:
            raise Waiting('Position still open outside the regular session. DAY protection may expire; keep AllSpark running and connected.')
        if trade['stage'] == 'exiting':
            self._exit(trade, position)
            return
        # On a resumed session, retire any previous day's position rather than reuse its signal.
        if elapsed(trade['created_at'], now) > 12 * 3600 or closing(clock, now):
            self._start_exit(trade, 'Closing the position before the session ends')
            self._exit(trade, position)
            return
        try:
            self._gate('quote_read')
            bid, ask = checked_quote(self.broker.quote(trade['symbol']), self.now(), trade['symbol'])
        except (Waiting, FeedError):
            # Existing broker stop remains in place while quotes are unavailable.
            # For a new fill, place its known protective stop before waiting for quotes.
            failed_gate = self._diagnostic_gate
            if not stop_order:
                self._protect(trade, position)
            # A successfully placed stop must not relabel the original quote
            # failure. If protection itself fails, its exception/gate wins.
            self._gate(failed_gate)
            raise
        price = bid if trade['direction'] == 'long' else ask
        stop, target = decimal(trade['stop']), decimal(trade['target'])
        reached = (price <= stop or price >= target) if trade['direction'] == 'long' else (price >= stop or price <= target)
        if reached:
            self._start_exit(trade, 'Stop or target reached; closing the held shares')
            self._exit(trade, position)
        elif not stop_order:
            self._protect(trade, position)
        elif stop_order['status'] not in WORKING_PROTECTION:
            self.message = f'Protective order is {stop_order["status"]}; executable protection is not yet confirmed.'
        else:
            self.message = (f'Managing {trade["symbol"]}' + (' (inverse-ETF proxy for the QQQ short)' if trade.get('proxy') else '')
                            + f': stop ${stop}, target ${target}. Broker protection: {stop_order["status"]}.')

    def _protection_failure(self, trade, order):
        self._gate('protection')
        status = order['status']
        if status in WORKING_PROTECTION:
            return None
        if status in TERMINAL or status in UNAVAILABLE_PROTECTION:
            return f'Protective order is {status}; executable protection is unavailable'
        op = trade['ops']['stop']
        if not op.get('confirmation_deadline'):
            # An older durable intent gets a deadline on first observation;
            # restarting cannot repeatedly grant another grace period.
            op['confirmation_deadline'] = (
                self.now() + timedelta(seconds=PROTECTION_CONFIRM_SECONDS)).isoformat()
            self.store.save_trade(trade)
        if self.now() >= timestamp(op['confirmation_deadline']):
            return f'Protective order remained {status} past its confirmation deadline'
        return None

    def _start_protection_exit(self, trade, reason):
        self._gate('protection')
        self._diagnostic_trade = trade
        self._diagnostic_outcome = 'protection_failure'
        self._pause(reason + '. Review Alpaca before enabling Socrates again.')
        if not trade.get('protection_failure'):
            # The pause above is a no-op once Socrates is already Off (an owner
            # toggle or an earlier pause), so this alert-kind journal entry is
            # what tells the owner a live position lost its protection and is
            # being closed at market.
            trade['protection_failure'] = {'at': self.now().isoformat(), 'reason': reason}
            self.store.save_trade(trade)
            self.store.event('protection_failed', {'symbol': trade['symbol'], 'trade_id': trade['id'], 'reason': reason})
        self._start_exit(trade, reason + '. New entries paused; canceling before closing remaining shares.')

    def _check_unknown_protection(self, trade):
        failure = self._protection_failure(trade, {'status': 'unconfirmed'})
        if failure:
            if not trade.get('protection_failure'):
                self._start_protection_exit(trade, failure)
            else:
                self._pause(f'{trade["symbol"]} protection stayed unconfirmed past its deadline. Review Alpaca before enabling Socrates again.')
            raise Waiting(f'Protection status is uncertain past its confirmation deadline. New entries paused; no competing sale will be sent. Check the {trade["symbol"]} position and orders in Alpaca now.')

    def _protect(self, trade, position):
        payload = {'symbol': trade['symbol'], 'side': 'sell' if trade['direction'] == 'long' else 'buy',
                   'qty': quantity_text(position['qty']), 'type': 'stop',
                   'stop_price': trade['stop'], 'time_in_force': 'day', 'extended_hours': False}
        self._prepare(trade, 'stop', payload)
        order = self._order(trade, 'stop')
        failure = self._protection_failure(trade, order)
        if failure:
            self._start_protection_exit(trade, failure)
            self._exit(trade, position)
        else:
            self.message = 'Protective stop submitted. Waiting for broker confirmation.'

    def _start_exit(self, trade, reason):
        # Completion has its own reconciliation message; retain the original
        # trigger so a daily review can distinguish targets, stops and incidents.
        trade.setdefault('exit_reason', reason)
        trade.update(stage='exiting', reason=reason)
        if not trade.get('exit_pending'):
            now = self.now()
            trade['exit_pending'] = {'first_observed_at': now.isoformat(),
                'confirmation_deadline': (now + timedelta(seconds=EXIT_CONFIRM_SECONDS)).isoformat()}
        self.store.save_trade(trade)
        self.store.event('exit_started', {'symbol': trade['symbol'], 'trade_id': trade['id'], 'reason': reason})

    def _check_exit_incident(self, trade):
        """Escalate a stalled close without replacing, racing or abandoning it."""
        if trade.get('stage') != 'exiting':
            return False
        pending = trade.get('exit_pending')
        now = self.now()
        if not pending:
            # Migrate an older in-progress close once. A restart cannot reset
            # this persisted grace period, including while broker reads fail.
            pending = trade['exit_pending'] = {'first_observed_at': now.isoformat(),
                'confirmation_deadline': (now + timedelta(seconds=EXIT_CONFIRM_SECONDS)).isoformat()}
            self.store.save_trade(trade)
        if now < timestamp(pending['confirmation_deadline']):
            return False
        self._pause(f'A {trade["symbol"]} exit or stop cancellation stayed unconfirmed past its deadline. Review Alpaca before enabling Socrates again.')
        reason = ('Exit or protective-stop cancellation is still unconfirmed. New entries paused; '
                  'existing orders continue to be reconciled without a competing closing order. '
                  f'Check the {trade["symbol"]} position and orders in Alpaca now.')
        if not pending.get('raised_at'):
            pending.update(raised_at=now.isoformat(), reason=reason)
            trade['reason'] = reason
            self.store.save_trade(trade)
            self.store.event('exit_needs_attention', {'symbol': trade['symbol'],
                'first_observed_at': pending['first_observed_at'], 'reason': reason})
        self._diagnostic_trade = trade
        self._diagnostic_outcome = 'attention'
        self._gate('owner_attention')
        return True

    def _exit(self, trade, position):
        self._gate('exit_management')
        if 'stop' in trade['ops'] and trade['ops']['stop']['state'] != 'prepared':
            order = self._order(trade, 'stop')
            if order['status'] not in TERMINAL:
                try:
                    self._gate('order_cancel')
                    self.broker.cancel(order['id'])
                except FeedError:
                    if trade.get('protection_failure'):
                        raise Waiting(f'Protection and stop cancellation are uncertain. New entries remain paused. Check the {trade["symbol"]} position and orders in Alpaca now.') from None
                    raise
                self.message = (f'Protection is not confirmed; waiting for stop cancellation before any sale. New entries paused. Check {trade["symbol"]} in Alpaca now.'
                                if trade.get('protection_failure') else 'Waiting for stop cancellation before sending the exit')
                return
        # Re-read AFTER cancellation: the stop may have filled while cancellation was in flight.
        position = self._position(trade)
        if not position:
            self.message = 'Exit filled; confirming the final broker state'
            return
        name = f'exit{trade["exit_number"]}'
        if name in trade['ops']:
            order = self._order(trade, name)
            if order['status']=='replaced':
                self._attention(trade,f'Exit order was replaced outside the app. Check the {trade["symbol"]} position and orders in Alpaca now.')
            if order['status'] not in TERMINAL:
                self.message = 'Exit submitted; waiting for all held shares to fill'
                return
            if order['status'] == 'rejected' or trade['exit_number'] >= 2:
                self._attention(trade,f'Broker could not complete the exit. Check the {trade["symbol"]} position in Alpaca now.')
            # A canceled/expired exit can leave shares; size a new intent only after terminal confirmation.
            position = self._position(trade)
            if not position: return
            trade['exit_number'] += 1
            name = f'exit{trade["exit_number"]}'
        payload = {'symbol': trade['symbol'], 'side': 'sell' if trade['direction'] == 'long' else 'buy',
                   'qty': quantity_text(position['qty']), 'type': 'market', 'time_in_force': 'day', 'extended_hours': False}
        self._prepare(trade, name, payload)
        self._order(trade, name)
        self.message = 'Exit submitted for the remaining held shares'
