"""Live-capable long-only spot execution for separately enabled range reversals.

Every broker mutation is preceded by a durable intent. Uncertain submissions are
looked up, never replayed. Viewing a strategy does not enter this module's controls.
"""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from hashlib import sha256
from threading import Lock, RLock
from zoneinfo import ZoneInfo

from .broker import BrokerRejected
from .crypto_broker import canonical_symbol
from .crypto_store import SYMBOLS, ConcurrentChange
from .deployment import DeploymentHold
from .feeds import FeedError
from .models import timestamp
from .policy import POLICY_VERSION as MAIN_POLICY_VERSION
from .range_reversal import OPENING_BARS_MINIMUM, OPENING_SLOTS, RULE_VERSION

POLICY_VERSION = 'range-spot-execution-v2'
POLICY = {'version': POLICY_VERSION, 'summary': [
    f'The first range is New York midnight plus four elapsed hours: the high and low of the native five-minute candles available among its {OPENING_SLOTS} slots, at least {OPENING_BARS_MINIMUM} of them. This is an explicit app convention.',
    'Missing five-minute candles are tolerated. A missing later slot has no close, so it cannot start or confirm an excursion, and a slot missing inside an excursion retires it; the newest expected candle must still be present (90-second publication allowance) for a signal to be current. Prices are never filled in.',
    'Buy after a completed close below the range followed by a later close strictly inside. Use the first outside candle low as stop and the signal close plus twice that distance as target.',
    'Alpaca spot supports long entries and sells of owned crypto only. Upper-range short setups cannot open positions here.',
    'Bitcoin is demonstrated in the recording. Other selected markets are optional adaptations, not creator-validated results.',
    'A maximum of two crypto positions may be active, with at most one per selected market. This execution capacity bounds broker workload; it is not a signal rule. Each fresh entry targets the saved dollars, never above that amount; partial fills can be smaller.',
    'Each strategy has its own allowance of two entry attempts per New York session (the shared ledger reserves them; a Socrates attempt no longer consumes a crypto attempt). A submitted or rejected entry consumes one; a plan retired before its broker call does not.',
    'At most one eligible new crypto entry receives broker checks per worker cycle, rotating across selected markets. Existing positions keep their exit checks and signal deadlines are not extended.',
    'Use immediate limit entries, no more than 0.1% above the confirmation price, current bid/ask, and shared account cash. The net reward after both taker fees (target versus the limit price) must be positive and at least the net risk after both fees and the execution allowance (limit price versus stop); otherwise the rule-valid setup is skipped and the computed percentages and the implied minimum stop distance are recorded.',
    'Published tier-one taker fees are 0.25% per side; buy fees reduce acquired crypto. Exits sell net owned quantity, so dollar proceeds change with price and fees.',
    'A broker stop-limit has a limit 1% below the stop. It may remain unfilled through a gap. The worker checks targets and cancellation-safe market fallback every 10 seconds while connected; neither fill price nor profit is guaranteed.',
    'The range resets each New York day. Existing positions keep their original stop and target across midnight, settings changes and strategy views.',
    'Off prevents entries and cancels unfinished entry orders. Position exits remain active. Unknown or foreign exposure blocks new entries until reconciled.',
]}
TERMINAL = {'filled', 'canceled', 'expired', 'rejected'}
WORKING = {'new', 'partially_filled'}
STATUSES = TERMINAL | WORKING | {'accepted', 'pending_new', 'pending_cancel', 'pending_replace', 'stopped', 'suspended', 'done_for_day', 'calculated'}
FEE = Decimal('.0025')
EXIT_ALLOWANCE = Decimal('.001')
MIN_NET_REWARD_RISK = Decimal('1.0')  # Net reward must at least equal net risk after fees; chosen by the Sep 22, 2026 replay.
ONE = Decimal('1')
HUNDRED = Decimal('100')
MAX_ENTRY_DRIFT = Decimal('.001')
IOC_LIMIT_BUFFER = Decimal('.0003')  # An immediate limit fills at the best ask; this only absorbs one-tick upticks.
MAX_SPREAD = Decimal('.005')
MAX_ACTIVE_CRYPTO_TRADES = 2
ORDER_NOT_FOUND_CONFIRM_SECONDS = 60
IOC_SETTLE_GRACE_SECONDS = 5  # An immediate-or-cancel order can still report pending_new/accepted in the POST response.
MAX_EXIT_CHILD_NOTIONAL = Decimal('190000')  # Buffer below Alpaca's published $200k per-order cap.
ZERO = Decimal('0')
NY = ZoneInfo('America/New_York')


class CryptoWaiting(ValueError):
    pass


class CryptoIncident(CryptoWaiting):
    pass


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Missing or invalid crypto amount')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Invalid crypto amount') from None
    if not result.is_finite():
        raise ValueError('Invalid crypto amount')
    return result


def iso(value):
    return timestamp(value).astimezone(timezone.utc).isoformat()


def rounded(value, step, rounding=ROUND_DOWN):
    return (value / step).to_integral_value(rounding=rounding) * step


def decimal_text(value):
    return format(value, 'f')


def fresh(value, now, seconds):
    at = timestamp(value)
    return 0 <= (now - at).total_seconds() <= seconds


def net_expectancy(limit, stop, target):
    """Net win and net loss fractions of the limit price after fees.

    App interpretation of the recording's 2R target: buying at ``limit`` costs
    the taker fee in acquired crypto, and every sale pays it again in proceeds.
    ``net_win`` is the fraction gained when the target sells; ``net_loss`` is
    the fraction lost when the stop sells, with EXIT_ALLOWANCE for slippage
    below the stop. Both are exact Decimal fractions (0.012 = 1.2%).
    """
    kept = (ONE - FEE) ** 2
    net_win = target / limit * kept - ONE
    net_loss = ONE - stop * (ONE - EXIT_ALLOWANCE) / limit * kept
    return net_win, net_loss


def minimum_stop_distance(entry, limit):
    """Smallest 2R stop distance below ``entry`` (fraction of entry) that passes the gate.

    Solves net_win >= MIN_NET_REWARD_RISK * net_loss for R with target = entry + 2R
    and stop = entry - R. Returns None when no finite distance can satisfy the
    ratio (ratios of about two or more, where 2R geometry cannot outrun fees).
    """
    kept, ratio = (ONE - FEE) ** 2, MIN_NET_REWARD_RISK
    denominator = kept * (2 - ratio * (ONE - EXIT_ALLOWANCE))
    if denominator <= 0:
        return None
    distance = (limit * (ONE + ratio) - entry * kept * (ONE + ratio * (ONE - EXIT_ALLOWANCE))) / denominator
    return max(ZERO, distance / entry)


def percent(fraction):
    return decimal_text((fraction * HUNDRED).quantize(Decimal('.01')))


def checked_signal(analysis, symbol, now):
    """Revalidate the full native range/event contract independently of UI flags.

    The opening range may be built from a subset of its 48 slots since
    ``range-reversal-v2``; every other field stays strictly revalidated.
    """
    try:
        a, event = analysis, analysis['current_event']
        if (symbol not in SYMBOLS or a.get('family_id') != 'range_reversal' or a.get('symbol') != symbol
                or a.get('source') != 'alpaca_crypto_us' or a.get('rule_version') != RULE_VERSION
                or a.get('state') != 'SETUP_OBSERVED' or a.get('signal_ready') is not True
                or not isinstance(event, dict) or event.get('status') != 'CONFIRMED'
                or event.get('current') is not True or event.get('symbol') != symbol
                or event.get('source') != a['source'] or event.get('rule_version') != RULE_VERSION
                or event.get('stop_basis') != 'first_outside_candle_extreme'):
            raise ValueError
        local = now.astimezone(NY)
        start = datetime.combine(local.date(), datetime.min.time(), NY).astimezone(timezone.utc)
        end = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), NY).astimezone(timezone.utc)
        range_end = start + timedelta(hours=4)
        session, area = a['session'], a['range']
        if (timestamp(session['start_at']) != start or timestamp(session['end_at']) != end
                or timestamp(session['range_end_at']) != range_end
                or session.get('timezone') != str(NY) or session.get('range_duration_minutes') != 240
                or timestamp(area['start_at']) != start or timestamp(area['end_at']) != range_end
                or area.get('expected_candle_count') != OPENING_SLOTS
                or type(area.get('native_candle_count')) is not int
                or not OPENING_BARS_MINIMUM <= area['native_candle_count'] <= OPENING_SLOTS):
            raise ValueError
        breakout, confirmation = timestamp(event['breakout_at']), timestamp(event['confirmation_at'])
        if (not range_end < breakout < confirmation <= now < end
                or any(at.second or at.microsecond or at.minute % 5 for at in (breakout, confirmation))
                or timestamp(a['latest_bar_at']) != confirmation
                or not fresh(confirmation, now, 90) or not fresh(a['observed_at'], now, 90)
                or not fresh(a['analyzed_at'], now, 90) or timestamp(a['observed_at']) < confirmation
                or timestamp(event['observed_at']) != timestamp(a['observed_at'])):
            raise ValueError
        side, direction = event['breakout_side'], event['direction']
        if (side, direction) not in (('below', 'long'), ('above', 'short')):
            raise ValueError
        identity = '|'.join((RULE_VERSION, a['source'], symbol, iso(start), iso(breakout), side))
        if event['event_id'] != 'rr1_' + sha256(identity.encode()).hexdigest()[:24]:
            raise ValueError
        low, high, entry, stop, target, outside = [number(v) for v in
            (area['low'], area['high'], event['entry'], event['stop'], event['target'], event['breakout_close'])]
        if not ZERO < low < entry < high or min(stop, target, outside) <= 0:
            raise ValueError
        if direction == 'long':
            if not stop <= outside < low or abs(target - (entry + 2 * (entry - stop))) > Decimal('.00000001'):
                raise ValueError
        elif not high < outside <= stop or abs(target - (entry - 2 * (stop - entry))) > Decimal('.00000001'):
            raise ValueError
        expiry = min(confirmation + timedelta(seconds=90), timestamp(a['observed_at']) + timedelta(seconds=90), end)
        if not now < expiry:
            raise ValueError
        return deepcopy(event), expiry
    except (KeyError, TypeError, ValueError, OverflowError, InvalidOperation):
        raise CryptoWaiting('Waiting for a current, complete five-minute range-reversal confirmation') from None


def checked_quote(quote, symbol, now):
    try:
        if (quote.get('symbol') != symbol or quote.get('source') != 'alpaca_crypto_us'
                or not fresh(quote['t'], now, 15)):
            raise ValueError
        bid, ask = number(quote['bp']), number(quote['ap'])
        if not ZERO < bid <= ask or number(quote['bs']) <= 0 or number(quote['as']) <= 0:
            raise ValueError
        return bid, ask
    except (KeyError, TypeError, ValueError, OverflowError):
        raise CryptoWaiting('Waiting for a fresh executable crypto bid and ask') from None


class CryptoRangeExecutor:
    def __init__(self, broker, crypto_store, main_store, portfolio, now=None):
        self.broker, self.store, self.main_store, self.portfolio = broker, crypto_store, main_store, portfolio
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.tick_lock, self.entry_lock = Lock(), RLock()
        self.entry_gate, self.revision = None, None
        self.message, self.last_at = 'Crypto live trading is off.', None
        self.market_messages = {}
        self.decision_error = None
        self.last_preflight_symbol = None
        self.entry_preflights_remaining = 0

    def enabled(self):
        control, main = self.store.control(), self.main_store.control()
        return (main.get('enabled') is True and main.get('policy') == MAIN_POLICY_VERSION
                and control.get('enabled') is True and control.get('policy') == POLICY_VERSION
                and isinstance(control.get('account_ref'), str) and bool(control['account_ref'])
                and main.get('account_ref') == control['account_ref']
                and self.portfolio.family_enabled('range_reversal'))

    def snapshot(self):
        control = self.store.control()
        trades = self.store.active_trades()
        visible = ('id', 'symbol', 'stage', 'direction', 'amount', 'stop', 'target', 'reason',
                   'created_at', 'filled_qty', 'net_entry_qty', 'partial_entry', 'stop_op', 'exit_pending')
        return {'execution_available': True, 'live_enabled': self.enabled(), 'policy_version': POLICY_VERSION,
                'review_required': control['enabled'] and control.get('policy') != POLICY_VERSION,
                'control': {k: control[k] for k in ('enabled', 'symbols', 'target_dollars', 'generation')},
                'message': self.message, 'at': self.last_at, 'markets': deepcopy(self.market_messages),
                'trades': [{k: t.get(k) for k in visible if k in t} for t in trades],
                'history': [{k: t.get(k) for k in (*visible, 'completed_at') if k in t} for t in self.store.history(10)],
                'decision_review': self._decision_review(),
                'capacity': {'max_active': MAX_ACTIVE_CRYPTO_TRADES, 'active_count': len(trades)},
                'incidents': self.store.incidents(), 'execution_account_mode': 'live', 'route': 'alpaca_spot_long_only'}

    def _decision_review(self):
        try:
            review = self.store.decision_review(self.now())
            if self.decision_error:
                review.update(status='degraded', detail=self.decision_error)
            return review
        except Exception:
            return {'status': 'unavailable', 'scope': 'recorded_live_checks_only',
                    'detail': 'Saved crypto decision checks could not be read. Current execution remains separate.'}

    def _record_decisions(self, analyses, decisions):
        """Audit observations only; never changes an order or trading permission."""
        try:
            for symbol, (phase, reason, checked_at) in decisions.items():
                analysis = (analyses or {}).get(symbol) or {}
                event = analysis.get('current_event') or {}
                try:
                    checked_signal(analysis, symbol, checked_at)
                    signal_fresh = True
                except (CryptoWaiting, ValueError, TypeError):
                    signal_fresh = False
                outcome = phase
                if phase == 'entry_checked':
                    outcome = 'unsupported_direction' if signal_fresh and event.get('direction') == 'short' else 'waiting'
                    if isinstance(event.get('event_id'), str):
                        identity = sha256((POLICY_VERSION + '|' + symbol + '|' + event['event_id']).encode()).hexdigest()[:24]
                        trade = self.store.get_trade(identity)
                        if trade:
                            entry = trade.get('ops', {}).get('entry', {})
                            seen = entry.get('last_seen') or {}
                            filled = number(seen.get('filled_qty', '0'))
                            outcome = ('closed' if filled > 0 else 'entry_rejected' if seen.get('status') == 'rejected'
                                       else 'no_fill' if seen.get('status') in TERMINAL else 'entry_skipped') if trade.get('stage') == 'finished' else (
                                       'entry_filled' if filled > 0 else 'order_attempted' if entry.get('state') == 'attempted' else 'entry_prepared')
                self.store.record_decision({'checked_at': iso(checked_at), 'symbol': symbol,
                    'state': analysis.get('state') or 'DATA_WAITING', 'event_id': event.get('event_id'),
                    'direction': event.get('direction'), 'confirmation_at': event.get('confirmation_at'),
                    'latest_bar_at': analysis.get('latest_bar_at'), 'observed_at': analysis.get('observed_at'),
                    'signal_fresh': signal_fresh, 'outcome': outcome, 'reason': reason})
            self.decision_error = None
        except Exception:
            self.decision_error = 'Some crypto decision checks could not be saved. Earlier records remain historical; order management continues.'

    def recover_incidents(self):
        """Read broker proof and resolve flat, terminal incidents without trading.

        Neither live permissions nor ownership are changed. An unknown POST must
        be found by its exact client ID; a flat balance alone cannot prove failure.
        """
        def result(recovered=(), message='Crypto incident review completed.'):
            self.message = message
            return {'recovered': list(recovered), 'remaining': self.store.incidents(),
                    'checked_at': iso(self.now()), 'message': message}
        if not self.tick_lock.acquire(blocking=False):
            return result(message='Crypto order management is running. Try recovery after it completes.')
        try:
            with self.portfolio.admit():
                incidents = self.store.incidents()
                if not incidents:
                    return result(message='No unresolved crypto incidents remain.')
                if self.store.active_trades():
                    return result(message='An active or uncertain crypto lifecycle remains. Recovery cannot abandon its ownership or orders.')
                authorization = self._authorization()
                account = self.broker.account()
                self._account_ready(account)
                control, master = authorization['crypto'], authorization['global']['control']
                if (account['account_ref'] != control.get('account_ref')
                        or master.get('enabled') and master.get('account_ref') != account['account_ref']):
                    return result(message='Reconnect the previously approved live crypto account before incident recovery.')
                positions, orders = self.broker.positions(), self.broker.orders()
                if not isinstance(positions, list) or not isinstance(orders, list) or positions or orders:
                    return result(message='Recovery requires a flat account with no open broker orders; existing exposure is unchanged.')
                checked = set()
                for incident in incidents:
                    trade_id = incident.get('trade_id')
                    if not trade_id or trade_id in checked:
                        continue
                    trade = self.store.get_trade(trade_id)
                    if (not trade or trade.get('stage') != 'finished'
                            or trade.get('account_ref') != account['account_ref']):
                        return result(message='A recorded crypto lifecycle is not finished on this account; its incident remains open.')
                    for name, op in trade.get('ops', {}).items():
                        state = op.get('state')
                        if state in ('prepared', 'aborted_before_submit') and not op.get('last_seen'):
                            continue  # The durable operation was never sent.
                        if state not in ('attempted', 'rejected'):
                            return result(message='An operation has no conclusive submission record; its incident remains open.')
                        order = self.broker.lookup(op['payload']['client_order_id'])
                        if state == 'rejected' and order is None:
                            # Only a definite HTTP rejection can establish no order
                            # without a lookup result. Attempted/timeout cannot.
                            if number(op.get('last_seen', {}).get('filled_qty')) == 0:
                                continue
                        if not self._terminal_proof(trade, op, order):
                            return result(message='A crypto order is absent, active, or differs from its saved terminal record. No incident was cleared.')
                    checked.add(trade_id)
                # Recheck after order lookups: the read-only proof must finish flat.
                final_positions, final_orders = self.broker.positions(), self.broker.orders()
                if (not isinstance(final_positions, list) or not isinstance(final_orders, list)
                        or final_positions or final_orders or authorization != self._authorization()):
                    return result(message='Account exposure or settings changed during recovery. No incident was cleared.')
                recovered = []
                for incident in incidents:
                    if self.store.resolve_incident(incident['id'], proof='Owner requested recovery: matching live account, no active crypto lifecycle, flat broker account and exact terminal order proof'):
                        recovered.append(incident['id'])
                return result(recovered, 'Verified flat account and terminal orders. Crypto incidents were resolved; saved live permissions are unchanged.')
        except (FeedError, ValueError, ConcurrentChange):
            return result(message='Current broker proof is unavailable or incomplete. Incidents and live permissions are unchanged.')
        finally:
            self.tick_lock.release()

    @staticmethod
    def _terminal_proof(trade, op, order):
        try:
            payload, seen = op['payload'], op.get('last_seen') or {}
            if (not isinstance(order, dict) or order.get('status') not in TERMINAL
                    or order.get('client_order_id') != payload['client_order_id']
                    or canonical_symbol(order.get('symbol')) != trade['symbol']
                    or order.get('side') != payload['side'] or order.get('type') != payload['type']
                    or order.get('time_in_force') != payload['time_in_force']
                    or not isinstance(order.get('id'), str) or not order['id']
                    or seen.get('id') and seen['id'] != order['id']
                    or number(order.get('qty')) != number(payload['qty'])
                    or number(order.get('filled_qty')) != number(seen.get('filled_qty'))):
                return False
            for key in ('stop_price', 'limit_price'):
                if key in payload and number(order.get(key)) != number(payload[key]):
                    return False
            fills = number(order['filled_qty'])
            return (ZERO <= fills <= number(payload['qty'])
                    and (not fills or number(order.get('filled_avg_price')) > 0)
                    and (order['status'] != 'filled' or fills == number(payload['qty'])))
        except (KeyError, TypeError, ValueError):
            return False

    def tick(self, analyses):
        if not self.tick_lock.acquire(blocking=False):
            return
        decisions = {}
        retired_unsent = set()
        self.entry_preflights_remaining = 1
        try:
            trades = self.store.active_trades()
            for trade in trades:
                checked_at = self.now()
                # Some reconciliation paths deliberately return without changing
                # status; never attribute another market's earlier message here.
                self.message = f'Reconciling {trade["symbol"]} crypto position and its saved orders.'
                try:
                    self._manage(trade)
                except (CryptoWaiting, FeedError, ValueError, ConcurrentChange) as exc:
                    self.market_messages[trade['symbol']] = str(exc)
                    self.message = str(exc)
                except Exception:
                    self.message = 'Crypto order handling needs attention; reconciling saved broker state.'
                    self._incident(trade, 'management_error', self.message)
                self.market_messages[trade['symbol']] = self.message
                decisions[trade['symbol']] = ('management', self.message, checked_at)
                if trade.get('stage') == 'finished' and trade['ops']['entry']['state'] == 'prepared':
                    # Retiring an old plan is this cycle's action. A later cycle
                    # may build a new authorized plan inside the same original
                    # signal deadline; it never revives the expired payload.
                    retired_unsent.add(trade['symbol'])
            if not self.enabled():
                self.message = 'Crypto entries are off. Existing crypto positions continue their exits.'
                for symbol in self.store.control()['symbols']:
                    decisions.setdefault(symbol, ('paused', self.message, self.now()))
                return
            if self.store.incidents():
                self.message = 'Crypto new entries are paused for a recorded incident. Existing positions continue their exits.'
                for symbol in self.store.control()['symbols']:
                    decisions.setdefault(symbol, ('incident_paused', self.message, self.now()))
                return
            symbols = self.store.control()['symbols']
            if self.last_preflight_symbol in symbols:
                start = symbols.index(self.last_preflight_symbol) + 1
                symbols = symbols[start:] + symbols[:start]
            for symbol in symbols:
                if symbol in retired_unsent or self.store.active_trade(symbol):
                    continue
                checked_at = self.now()
                try:
                    with self.entry_gate.admit() if self.entry_gate else nullcontext():
                        with self.portfolio.admit():
                            with self.entry_lock:
                                self._entry((analyses or {}).get(symbol) or {}, symbol)
                except (CryptoWaiting, FeedError, ValueError, ConcurrentChange, DeploymentHold) as exc:
                    self.market_messages[symbol] = str(exc)
                    self.message = str(exc)
                except Exception:
                    self.message = 'Crypto entry checks are unavailable; no additional order will be submitted.'
                    self.market_messages[symbol] = self.message
                    self.store.incident('entry_check_error:' + symbol, self.message, symbol=symbol)
                decisions[symbol] = ('entry_checked', self.message, checked_at)
        finally:
            try:
                self._pending_deadlines()
            finally:
                self._record_decisions(analyses, decisions)
                self.last_at = iso(self.now())
                self.tick_lock.release()

    @staticmethod
    def _account_ready(account):
        if (account.get('mode') != 'live' or account.get('status') != 'ACTIVE'
                or account.get('crypto_status') != 'ACTIVE' or not account.get('account_ref')
                or any(account.get(k) is not False for k in ('trading_blocked', 'account_blocked', 'trade_suspended_by_user'))):
            raise CryptoWaiting('The live Alpaca crypto account is not available for new entries')

    def _identity(self, trade):
        account = self.broker.account()
        if account.get('mode') != 'live' or account.get('account_ref') != trade['account_ref']:
            self._incident(trade, 'account_changed', 'The crypto account connection changed; saved exposure needs reconciliation')
            raise CryptoIncident('The crypto account connection changed; no order can be sent to a different account')
        return account

    def _authorization(self):
        return {'crypto': self.store.control(), 'global': self.main_store.entry_authorization()}

    def _entry(self, analysis, symbol):
        if not self.enabled() or self.store.incidents():
            raise CryptoWaiting('Crypto new entries are off or paused')
        event, expiry = checked_signal(analysis, symbol, self.now())
        if event['direction'] != 'long':
            raise CryptoWaiting('A short setup is present. Alpaca crypto cannot open short positions; no buy is substituted.')
        identity = sha256((POLICY_VERSION + '|' + symbol + '|' + event['event_id']).encode()).hexdigest()[:24]
        if self.store.entry_consumed(identity):
            raise CryptoWaiting('This range-reversal event has already been handled; waiting for a new excursion')
        # These checks execute under the shared portfolio admission lock and
        # before any broker read. Uncertain/pending lifecycles occupy capacity;
        # their management above is never limited by this new-entry budget.
        if len(self.store.active_trades()) >= MAX_ACTIVE_CRYPTO_TRADES:
            raise CryptoWaiting('Crypto execution capacity is full (two active positions); waiting for an existing position to finish.')
        self.main_store.assert_session_entry_available(self.now(), family='range_reversal')
        if self.entry_preflights_remaining <= 0:
            raise CryptoWaiting('Waiting for next broker-check slot; the original signal deadline still applies.')
        self.entry_preflights_remaining -= 1
        self.last_preflight_symbol = symbol
        authorization = self._authorization()
        control = authorization['crypto']
        account = self.broker.account()
        self._account_ready(account)
        if account['account_ref'] != control['account_ref']:
            raise CryptoWaiting('The enabled crypto account no longer matches the connected account')
        positions, orders = self.broker.positions(), self.broker.orders()
        owned = {t['symbol']: t for t in self.portfolio.active_trades() if t['account_ref'] == account['account_ref']}
        known_clients = {op['payload']['client_order_id'] for t in owned.values() for op in t.get('ops', {}).values()}
        if (any(canonical_symbol(p.get('symbol')) not in owned for p in positions)
                or any(o.get('client_order_id') not in known_clients for o in orders)):
            self.store.incident('foreign_exposure:' + symbol, 'Unowned account exposure blocks new crypto entries', symbol=symbol)
            raise CryptoIncident('Unowned account positions or orders need reconciliation before crypto entries')
        self.portfolio.assert_exposure(account['account_ref'], positions, orders,
                                       requesting_family='range_reversal', symbol=symbol)
        asset = self.broker.asset(symbol)
        if (asset.get('symbol') != symbol or asset.get('class') != 'crypto' or asset.get('status') != 'active'
                or asset.get('tradable') is not True or asset.get('fractionable') is not True
                or asset.get('shortable') is not False or asset.get('marginable') is not False):
            raise CryptoWaiting('The crypto instrument is not currently eligible for this spot route')
        step, tick, minimum = [number(asset.get(k)) for k in ('min_trade_increment', 'price_increment', 'min_order_size')]
        if min(step, tick, minimum) <= 0 or any(v < Decimal('.000000001') for v in (step, tick)):
            raise CryptoWaiting('Crypto quantity and price increments are unavailable')
        quote = self.broker.quote(symbol)
        bid, ask = checked_quote(quote, symbol, self.now())
        entry, stop, target = [number(event[k]) for k in ('entry', 'stop', 'target')]
        if (ask - bid) / ask > MAX_SPREAD:
            raise CryptoWaiting('Crypto spread is too wide for the current entry')
        if ask > entry * (1 + MAX_ENTRY_DRIFT):
            raise CryptoWaiting('Crypto price moved beyond the confirmed entry or its stop/target')
        # Price the immediate limit slightly above the observed ask, capped at
        # the confirmed entry drift. It still fills at the best available ask,
        # but a one-tick uptick between preflight and submission no longer
        # retires the plan or expires the order with zero fill.
        limit = rounded(min(ask * (1 + IOC_LIMIT_BUFFER), entry * (1 + MAX_ENTRY_DRIFT)), tick, ROUND_UP)
        limit = max(limit, rounded(ask, tick, ROUND_UP))
        if not stop < bid <= limit < target or limit > rounded(entry * (1 + MAX_ENTRY_DRIFT), tick, ROUND_UP):
            raise CryptoWaiting('Crypto price moved beyond the confirmed entry or its stop/target')
        # Net-expectancy cost gate (app interpretation): a rule-valid 2R setup
        # is skipped when both taker fees leave no positive net reward, or a net
        # reward below MIN_NET_REWARD_RISK times the net risk. The reason
        # carries the numbers so the owner can see why nothing was sent.
        net_win, net_loss = net_expectancy(limit, stop, target)
        if net_win <= 0 or net_win < MIN_NET_REWARD_RISK * net_loss:
            actual = (entry - stop) / entry
            implied = minimum_stop_distance(entry, limit)
            raise CryptoWaiting(
                f'Skipped: net reward after fees {percent(net_win)}% versus net risk {percent(net_loss)}% of the '
                f'{decimal_text(limit)} limit (need reward ≥ {decimal_text(MIN_NET_REWARD_RISK)} × risk and > 0); '
                f'the stop is {percent(actual)}% below entry and a 2R setup needs at least '
                + (f'{percent(implied)}%' if implied is not None else 'an unattainable distance') + ' at this ratio')
        amount = number(control['target_dollars'])
        if number(self.portfolio.available(account['account_ref'], account.get('non_marginable_buying_power'))) < amount:
            raise CryptoWaiting('Waiting for enough unreserved cash for the complete crypto purchase amount')
        qty = rounded(amount / limit, step)
        if qty < minimum or qty * limit < amount * Decimal('.99') or qty * limit > amount:
            raise CryptoWaiting('The selected amount cannot satisfy this crypto pair’s minimum and quantity increment')
        # Broker reads must not extend the original signal, quote or settings deadline.
        checked_signal(analysis, symbol, self.now())
        checked_quote(quote, symbol, self.now())
        if authorization != self._authorization() or not self.enabled():
            raise CryptoWaiting('Crypto settings changed while checking this entry')
        trade = {'id': identity, 'family_id': 'range_reversal', 'symbol': symbol, 'direction': 'long',
                 'stage': 'entering', 'account_ref': account['account_ref'], 'amount': decimal_text(amount),
                 'stop': decimal_text(stop), 'target': decimal_text(target), 'source_entry': decimal_text(entry),
                 'qty_step': decimal_text(step), 'price_step': decimal_text(tick), 'minimum_qty': decimal_text(minimum),
                 'created_at': iso(self.now()), 'expires_at': iso(min(expiry, timestamp(quote['t']) + timedelta(seconds=15))),
                 'authorization': authorization, 'signal': deepcopy(analysis), 'revision': self.revision, 'ops': {}}
        self._prepare(trade, 'entry', {'symbol': symbol, 'side': 'buy', 'qty': decimal_text(qty),
                                    'type': 'limit', 'time_in_force': 'ioc', 'limit_price': decimal_text(limit)}, persist=False)
        if not self.store.reserve_trade(trade, expected_control=control):
            raise CryptoWaiting('Another worker already handled this crypto event or instrument')
        self._manage(trade)

    def _prepare(self, trade, name, payload, *, persist=True):
        if name in trade['ops']:
            raise ValueError('Crypto operation identity cannot be reused')
        trade['ops'][name] = {'state': 'prepared', 'prepared_at': iso(self.now()),
                             'payload': {**payload, 'client_order_id': f'cr-{trade["id"]}-{name}'}}
        if persist:
            self.store.save_trade(trade)

    def _entry_expired(self, trade):
        return (not self.enabled() or trade['authorization'] != self._authorization()
                or not self.now() < timestamp(trade['expires_at'])
                or not fresh(trade['created_at'], self.now(), 10))

    def _order(self, trade, name):
        op = trade['ops'][name]
        if op['state'] == 'rejected':
            return op['last_seen']
        if op['state'] == 'prepared':
            if name == 'entry':
                # Recovered prepared entries get the same cross-family admission as new entries.
                with self.entry_gate.admit() if self.entry_gate else nullcontext():
                    with self.portfolio.admit():
                        with self.entry_lock:
                            if self._entry_expired(trade):
                                self._finish(trade, 'Entry expired before submission; no crypto order sent')
                                return None
                            account = self._identity(trade)
                            self._account_ready(account)
                            self.portfolio.assert_exposure(account['account_ref'], self.broker.positions(), self.broker.orders(),
                                requesting_family='range_reversal', symbol=trade['symbol'], excluding_trade_id=trade['id'])
                            bid, ask = checked_quote(self.broker.quote(trade['symbol']), trade['symbol'], self.now())
                            if not number(trade['stop']) < bid <= ask <= number(trade['ops']['entry']['payload']['limit_price']):
                                self._finish(trade, 'Executable crypto price changed before submission; no order sent')
                                return None
                            if number(account.get('non_marginable_buying_power')) < number(trade['amount']):
                                self._finish(trade, 'Available cash changed before submission; no crypto order sent')
                                return None
                            return self._submit_claimed(trade, name)
            # A never-submitted sell may have survived a restart or delayed fee
            # posting. Reconcile again immediately before its single POST claim.
            qty, available = self._position(trade)
            step = number(trade['qty_step'])
            if qty <= 0 or abs(qty - available) > step:
                raise CryptoWaiting('Waiting for exclusive available crypto quantity before submitting protection or exit')
            sell_qty = rounded(available, step)
            if name.startswith('exit'):
                self._exit_cap(trade)
                if trade.get('exit_child_max_qty'):
                    sell_qty = min(sell_qty, number(trade['exit_child_max_qty']))
            elif sell_qty * number(op['payload']['limit_price']) > Decimal('200000'):
                self._incident(trade, 'protection_notional_cap', 'Crypto protection exceeds the broker per-order limit')
                raise CryptoIncident('Crypto protective order exceeds the broker per-order limit; no oversized order was sent')
            if sell_qty <= 0:
                raise CryptoWaiting('Owned crypto is below its quantity increment')
            if number(op['payload']['qty']) != sell_qty:
                op['payload']['qty'] = decimal_text(sell_qty)
                self.store.save_trade(trade)
            return self._submit_claimed(trade, name)
        try:
            result = self.broker.lookup(op['payload']['client_order_id'])
        except FeedError:
            self._uncertain(trade, name)
            raise
        return self._accept_order(trade, name, result)

    def _submit_claimed(self, trade, name):
        # Broker reads may have used the plan's remaining lifetime. Retire a
        # demonstrably unsent entry before reserving an irreversible allowance.
        # A deadline crossed during the claim still stays consumed below.
        if name == 'entry' and self._entry_expired(trade):
            self._finish(trade, 'Entry expired before submission; no crypto order sent')
            return None
        if not self.store.claim_operation(trade, name, expected_control=trade['authorization']['crypto'] if name == 'entry' else None,
                                          attempted_at=self.now()):
            raise ConcurrentChange('Crypto order intent changed; reloading its broker state')
        if name == 'entry' and self._entry_expired(trade):
            trade['ops'][name]['state'] = 'aborted_before_submit'
            self._finish(trade, 'Entry authorization expired before the broker call; this event remains consumed')
            return None
        try:
            result = self.broker.submit(trade['ops'][name]['payload'])
        except FeedError as exc:
            if not isinstance(exc, BrokerRejected):
                self._uncertain(trade, name)
                raise
            op = trade['ops'][name]
            op['state'] = 'rejected'
            result = {**op['payload'], 'status': 'rejected', 'filled_qty': '0', 'id': None}
            op['last_seen'] = {**result, 'reason': exc.detail()}
            self.store.save_trade(trade)
            self._incident(trade, name + '_rejected', f'A crypto {name} order was rejected ({exc.detail()}); no order was created. '
                           'New entries are paused; existing exposure is still managed.')
            return result
        return self._accept_order(trade, name, result)

    def _accept_order(self, trade, name, result):
        op = trade['ops'][name]
        if result is None:
            resolved = self._resolve_never_reached_broker(trade, name)
            if resolved is None:
                self._uncertain(trade, name)
                raise CryptoWaiting('Crypto order outcome is unknown. Reconciling its saved identifier without resubmitting.')
            return resolved
        if (op.get('last_seen') or {}).get('evidence') == 'not_found_at_broker':
            # The broker now reports an order this app had concluded never
            # arrived. Adopt the actual evidence; the synthetic record cannot win.
            op['last_seen'] = {}
            self.main_store.event('crypto_order_late_arrival', {'trade_id': trade['id'], 'operation': name, 'symbol': trade['symbol']})
        try:
            payload = op['payload']
            if (not isinstance(result, dict) or result.get('client_order_id') != payload['client_order_id']
                    or canonical_symbol(result.get('symbol')) != trade['symbol'] or result.get('side') != payload['side']
                    or result.get('type') != payload['type'] or result.get('time_in_force') != payload['time_in_force']
                    or result.get('status') not in STATUSES or not isinstance(result.get('id'), str) or not result['id']
                    or number(result.get('qty')) != number(payload['qty'])):
                raise ValueError
            for key in ('limit_price', 'stop_price'):
                if key in payload and number(result.get(key)) != number(payload[key]):
                    raise ValueError
            fills = number(result.get('filled_qty'))
            if not ZERO <= fills <= number(payload['qty']):
                raise ValueError
            old = op.get('last_seen') or {}
            if old.get('id') and old['id'] != result['id'] or fills < number(old.get('filled_qty', '0')):
                raise ValueError
            if old.get('status') in TERMINAL and result['status'] not in TERMINAL:
                raise ValueError
            if fills and number(result.get('filled_avg_price')) <= 0:
                raise ValueError
            if result['status'] == 'filled' and fills != number(payload['qty']):
                raise ValueError
        except (TypeError, ValueError):
            self._incident(trade, 'order_identity', 'Crypto order identity or cumulative fills do not match the durable intent')
            raise CryptoIncident('Crypto broker order does not match its saved identity; no competing order will be sent') from None
        keys = ('id', 'client_order_id', 'symbol', 'side', 'type', 'time_in_force', 'status', 'qty',
                'filled_qty', 'filled_avg_price', 'limit_price', 'stop_price', 'updated_at', 'filled_at')
        seen = {k: result.get(k) for k in keys}
        seen['symbol'] = trade['symbol']
        confirmed = (result['status'] in TERMINAL or
                     name.startswith('stop') and result['status'] in WORKING and not op.get('cancel_requested_at'))
        changed = seen != op.get('last_seen')
        op['last_seen'] = seen
        if confirmed and op.pop('uncertain_since', None) is not None:
            changed = True
        if name.startswith('stop') and result['status'] in WORKING and not op.get('working_confirmed_at'):
            op['working_confirmed_at'] = iso(self.now())
            changed = True
        if changed:
            self.store.save_trade(trade)
        if confirmed:
            self.store.resolve_incident(trade['id'] + ':' + name + '_unknown')
        if result['status'] == 'rejected':
            self._incident(trade, name + '_rejected', 'A crypto order was rejected. New entries are paused; position recovery continues.')
        return result

    def _resolve_never_reached_broker(self, trade, name):
        """Bounded terminal resolution for a POST that never created a broker order.

        Alpaca indexes client_order_id on acceptance. A lookup that still returns
        404 after the confirmation window, with no matching or unknown order for
        the market and no coins reserved by an invisible sell, shows the request
        never arrived. Terminal evidence lets management continue: an entry ends
        without a fill, a missing stop closes verified exposure, and a missing
        exit is followed by the next exit child. A later order with the same
        identifier is adopted, never duplicated.
        """
        op = trade['ops'][name]
        seen = op.get('last_seen') or {}
        if seen.get('evidence') == 'not_found_at_broker':
            return deepcopy(seen)
        if op.get('state') != 'attempted' or seen:
            return None
        now = self.now()
        if not op.get('not_found_since'):
            op['not_found_since'] = iso(now)
            self.store.save_trade(trade)
            return None
        if (now - timestamp(op['not_found_since'])).total_seconds() < ORDER_NOT_FOUND_CONFIRM_SECONDS:
            return None
        payload = op['payload']
        ours = {o['payload']['client_order_id'] for o in trade['ops'].values()}
        try:
            open_orders = self.broker.orders()
            positions = self.broker.positions()
        except FeedError:
            return None
        for order in open_orders:
            if canonical_symbol(order.get('symbol')) == trade['symbol'] and (
                    order.get('client_order_id') == payload['client_order_id'] or order.get('client_order_id') not in ours):
                return None  # The order, or an unexplained order, exists; keep reconciling.
        held = next((p for p in positions if canonical_symbol(p.get('symbol')) == trade['symbol']), None)
        if name == 'entry':
            if held is not None:
                return None  # Exposure without a known fill cannot prove the entry never arrived.
        elif held is not None and abs(number(held.get('qty', '0')) - number(held.get('qty_available', '0'))) > number(trade['qty_step']):
            return None  # Coins are still reserved; an invisible sell may exist.
        op['last_seen'] = {'id': None, 'client_order_id': payload['client_order_id'], 'symbol': trade['symbol'],
                           'side': payload['side'], 'type': payload['type'], 'time_in_force': payload['time_in_force'],
                           'status': 'expired', 'qty': payload['qty'], 'filled_qty': '0', 'filled_avg_price': None,
                           'limit_price': payload.get('limit_price'), 'stop_price': payload.get('stop_price'),
                           'updated_at': None, 'filled_at': None, 'evidence': 'not_found_at_broker', 'confirmed_at': iso(now)}
        op.pop('uncertain_since', None)
        self.store.save_trade(trade)
        self.store.resolve_incident(trade['id'] + ':' + name + '_unknown',
                                    proof='No broker order existed after the confirmation window; no exposure or reservation was observed')
        self.main_store.event('crypto_order_not_found', {'trade_id': trade['id'], 'operation': name, 'symbol': trade['symbol'],
                                                         'not_found_since': op['not_found_since']})
        return deepcopy(op['last_seen'])

    def _uncertain(self, trade, name):
        op = trade['ops'][name]
        if not op.get('uncertain_since'):
            op['uncertain_since'] = iso(self.now())
            self.store.save_trade(trade)
        if (self.now() - timestamp(op['uncertain_since'])).total_seconds() >= 30:
            self._incident(trade, name + '_unknown', 'A crypto order remains unconfirmed for 30 seconds; broker reconciliation is required')

    def _pending_deadlines(self):
        """Each unresolved phase has its own durable, nonrenewing deadline."""
        for trade in self.store.active_trades():
            for name, op in trade.get('ops', {}).items():
                if op.get('state') != 'attempted':
                    continue
                status = op.get('last_seen', {}).get('status')
                if status in TERMINAL:
                    continue
                if op.get('cancel_requested_at'):
                    since = op['cancel_requested_at']
                elif name == 'entry' or name.startswith('exit'):
                    since = op.get('prepared_at') or op.get('uncertain_since')
                elif op.get('working_confirmed_at'):
                    since = op.get('uncertain_since')
                elif status not in WORKING:
                    since = op.get('prepared_at') or op.get('uncertain_since')
                else:
                    since = None
                if since and (self.now() - timestamp(since)).total_seconds() >= 30:
                    self._incident(trade, name + '_unknown',
                                   'A crypto order or cancellation remains unresolved for 30 seconds; reconciliation continues')

    def _incident(self, trade, code, message):
        self.store.incident(trade['id'] + ':' + code, message, symbol=trade['symbol'], trade_id=trade['id'])

    def _cancel(self, trade, name, order):
        if order['status'] in TERMINAL:
            return order
        if not order.get('id'):
            self._uncertain(trade, name)
            raise CryptoWaiting('Waiting for a confirmed crypto order identifier before cancellation')
        op = trade['ops'][name]
        if not op.get('cancel_last_attempt_at') or (self.now() - timestamp(op['cancel_last_attempt_at'])).total_seconds() >= 5:
            op.setdefault('cancel_requested_at', iso(self.now()))
            op['cancel_last_attempt_at'] = iso(self.now())
            self.store.save_trade(trade)
            try:
                self.broker.cancel(order['id'])
            except FeedError:
                self._uncertain(trade, name)
                raise
        result = self._order(trade, name)
        if result['status'] not in TERMINAL:
            self._uncertain(trade, name)
            raise CryptoWaiting('Waiting for definitive crypto cancellation or fill; no competing sell will be sent')
        return result

    def _known_sells(self, trade):
        return sum((number(op.get('last_seen', {}).get('filled_qty', '0'))
                    for name, op in trade['ops'].items() if name != 'entry'), ZERO)

    def _position(self, trade, *, rechecked=False):
        positions, orders = self.broker.positions(), self.broker.orders()
        rows = [p for p in positions if canonical_symbol(p.get('symbol')) == trade['symbol']]
        relevant = [o for o in orders if canonical_symbol(o.get('symbol')) == trade['symbol']]
        known = {op['payload']['client_order_id']: op for op in trade['ops'].values()}
        for order in relevant:
            op = known.get(order.get('client_order_id'))
            if (not op or op.get('state') != 'attempted' or order.get('id') != op.get('last_seen', {}).get('id')
                    or order.get('side') != op['payload']['side'] or order.get('legs')):
                self._incident(trade, 'foreign_order', 'An unowned crypto order overlaps this strategy position')
                raise CryptoIncident('An unowned crypto order needs reconciliation; competing orders are blocked')
        gross = number(trade['ops']['entry'].get('last_seen', {}).get('filled_qty', '0'))
        sold, step = self._known_sells(trade), number(trade['qty_step'])
        # Confirmed net ownership may shrink through late base-asset fees, but
        # a later top-up cannot be adopted as a reversal of those fees.
        recorded_net = number(trade.get('net_entry_qty', gross))
        recorded_gross = number(trade.get('net_entry_gross_qty', gross))
        # A newly verified cumulative entry fill can add ownership; a balance
        # increase by itself cannot. Old ledgers conservatively assume no delta.
        verified_addition = max(ZERO, gross - recorded_gross)
        lower = max(ZERO, gross * (1 - FEE) - sold)
        upper = max(ZERO, min(gross, recorded_net + verified_addition) - sold)
        if len(rows) > 1:
            raise CryptoIncident('Duplicate crypto positions prevent ownership reconciliation')
        qty = number(rows[0]['qty']) if rows else ZERO
        if (rows and rows[0].get('side') != 'long' or qty < 0 or qty > upper
                or qty + step < lower or sold > gross + step):
            if not rechecked:
                # Order and position endpoints are not an atomic snapshot. A
                # stop can fill between reads; refresh known fills once before
                # classifying a genuine ownership discrepancy as an incident.
                for name, op in trade['ops'].items():
                    if op.get('state') == 'attempted':
                        self._order(trade, name)
                return self._position(trade, rechecked=True)
            self._incident(trade, 'position_mismatch', 'Crypto holdings differ from verified entry, fees and strategy exit fills')
            raise CryptoIncident('Crypto holdings changed outside the recorded lifecycle; review is required')
        available = number(rows[0].get('qty_available')) if rows else ZERO
        if available < 0 or available > qty:
            raise CryptoIncident('Crypto available quantity is invalid')
        reserved = sum((max(ZERO, number(o['qty']) - number(o.get('filled_qty', '0')))
                        for o in relevant if o.get('side') == 'sell'), ZERO)
        if abs(max(ZERO, qty - reserved) - available) > step:
            raise CryptoWaiting('Crypto quantity reservations have not reconciled yet')
        # A fee discrepancy is bounded by the published maximum used in this policy.
        # Once sell fills exist, the remaining dust still has an owned lifecycle.
        net = qty + sold
        if (trade.get('net_entry_qty') != decimal_text(net)
                or trade.get('net_entry_gross_qty') != decimal_text(gross)):
            trade['net_entry_qty'] = decimal_text(net)
            trade['net_entry_gross_qty'] = decimal_text(gross)
            self.store.save_trade(trade)
        return qty, available

    def _manage(self, trade):
        if trade['ops']['entry']['state'] == 'prepared' and self._entry_expired(trade):
            self._finish(trade, 'Entry expired before submission; no crypto order sent')
            return
        self._identity(trade)
        entry = self._order(trade, 'entry')
        if entry is None or trade['stage'] == 'finished':
            return
        if entry['status'] not in TERMINAL:
            # IOC settles at the venue within moments, but the POST response and
            # the first lookup can still say pending_new/accepted. Give the venue a
            # short grace before cancelling, so a cancel does not beat the match
            # and turn a valid entry into a no-fill that consumes the event and
            # a session attempt. Live-off cancels immediately as before.
            attempted = trade['ops']['entry'].get('attempted_at')
            if (self.enabled() and attempted
                    and 0 <= (self.now() - timestamp(attempted)).total_seconds() < IOC_SETTLE_GRACE_SECONDS):
                raise CryptoWaiting('Immediate crypto entry is settling at the venue; confirming before any cancellation')
            # Explicitly cancel any remainder before protecting only the definitive fill.
            entry = self._cancel(trade, 'entry', entry)
        filled = number(entry.get('filled_qty', '0'))
        if filled == 0:
            self._finish(trade, 'Crypto entry ended without a fill')
            return
        if trade['stage'] == 'entering':
            trade.update(stage='open', filled_qty=decimal_text(filled),
                         partial_entry=filled < number(trade['ops']['entry']['payload']['qty']))
            self.store.save_trade(trade)
            self.store.event('entry_fill', {'trade_id': trade['id'], 'symbol': trade['symbol'],
                                          'filled_qty': decimal_text(filled), 'partial': trade['partial_entry']})
        # Reconcile each known sell first so position changes cannot be mistaken
        # for manual exposure after a stop or target fills between ticks.
        current_orders = {}
        for name in list(trade['ops']):
            if name == 'entry' or trade['ops'][name]['state'] in ('prepared', 'aborted_before_submit'):
                continue
            current_orders[name] = self._order(trade, name)
        qty, available = self._position(trade)
        if qty == 0:
            # All potential sell orders must definitively terminate before freeing ownership.
            for name, op in list(trade['ops'].items()):
                if name != 'entry' and op['state'] == 'attempted':
                    seen = op.get('last_seen') or {}
                    if seen.get('status') not in TERMINAL:
                        self._cancel(trade, name, seen)
            self._finish(trade, 'Crypto position fully closed and orders reconciled')
            return
        if trade.get('exit_pending'):
            self._exit(trade)
            return
        stop_name = trade.get('stop_op')
        if stop_name:
            stop = current_orders.get(stop_name) or self._order(trade, stop_name)
            if stop['status'] in TERMINAL:
                if stop['status'] == 'filled':
                    self._start_exit(trade, 'Closing crypto remainder after stop fill')
                else:
                    self._incident(trade, 'protection_ended', 'Crypto stop protection ended before the position was closed')
                    self._start_exit(trade, 'Closing crypto position after stop protection ended')
                self._exit(trade)
                return
            if stop['status'] not in WORKING:
                self._uncertain(trade, stop_name)
                if (self.now() - timestamp(trade['ops'][stop_name]['prepared_at'])).total_seconds() >= 30:
                    self._incident(trade, 'protection_not_working', 'Crypto stop protection was not confirmed within 30 seconds')
                    self._start_exit(trade, 'Closing crypto position after protection could not be confirmed')
                    self._exit(trade)
                else:
                    raise CryptoWaiting('Waiting for confirmed broker crypto stop protection')
                return
            remaining_stop = number(stop['qty']) - number(stop['filled_qty'])
            if abs(remaining_stop - qty) > number(trade['qty_step']):
                # Fees can reduce the owned quantity after protection was placed.
                # Cancel and close, rather than retain an oversized stop or silently widen risk.
                self._start_exit(trade, 'Crypto protection quantity changed; closing the verified remainder')
                self._exit(trade)
                return
        else:
            self._protect(trade, qty, available)
            if trade['stage'] in ('finished', 'exiting'):
                return
        bid, ask = checked_quote(self.broker.quote(trade['symbol']), trade['symbol'], self.now())
        if bid <= number(trade['stop']) or bid >= number(trade['target']):
            trade['exit_reference_price'] = decimal_text(ask)
            self.store.save_trade(trade)
        if bid <= number(trade['stop']):
            self._start_exit(trade, 'Crypto stop reached; reconciling native stop before market fallback')
            self._exit(trade)
        elif bid >= number(trade['target']):
            self._start_exit(trade, 'Crypto target reached')
            self._exit(trade)
        else:
            self.message = f'{trade["symbol"]} position held with broker stop protection; watching its target.'
            self.market_messages[trade['symbol']] = self.message

    def _protect(self, trade, qty, available):
        if abs(available - qty) > number(trade['qty_step']):
            raise CryptoWaiting('Waiting for the owned crypto quantity to become available for protection')
        step, tick = number(trade['qty_step']), number(trade['price_step'])
        amount = rounded(qty, step)
        stop = rounded(number(trade['stop']), tick, ROUND_UP)
        limit = rounded(stop * Decimal('.99'), tick)
        if amount <= 0 or limit <= 0 or limit >= stop:
            self._incident(trade, 'unprotectable_quantity', 'The crypto fill is below the venue protection increment')
            raise CryptoIncident('The remaining crypto quantity cannot form a valid protective order')
        trade['stop_op'] = 'stop'
        self._prepare(trade, 'stop', {'symbol': trade['symbol'], 'side': 'sell', 'qty': decimal_text(amount),
                      'type': 'stop_limit', 'time_in_force': 'gtc', 'stop_price': decimal_text(stop), 'limit_price': decimal_text(limit)})
        order = self._order(trade, 'stop')
        if order['status'] in TERMINAL:
            self._start_exit(trade, 'Crypto protective order ended; closing verified exposure')
            self._exit(trade)
        elif order['status'] not in WORKING:
            raise CryptoWaiting('Waiting for confirmed broker crypto stop protection')

    def _start_exit(self, trade, reason):
        if not trade.get('exit_pending'):
            trade['exit_pending'] = {'reason': reason, 'since': iso(self.now())}
            trade.update(stage='exiting', reason=reason)
            self.store.save_trade(trade)
        self.message = reason
        self.market_messages[trade['symbol']] = reason

    def _exit(self, trade):
        self._exit_cap(trade)
        # Every prior sell is reconciled and canceled before the next sale.
        for name, op in list(trade['ops'].items()):
            if name == 'entry':
                continue
            if op['state'] == 'prepared' and not name.startswith('exit'):
                op['state'] = 'aborted_before_submit'
                self.store.save_trade(trade)
                continue
            if op['state'] == 'aborted_before_submit':
                continue
            order = self._order(trade, name)
            if order['status'] not in TERMINAL:
                if name.startswith('exit'):
                    self._uncertain(trade, name)
                    raise CryptoWaiting('Waiting for the existing crypto market exit; no duplicate sell will be sent')
                self._cancel(trade, name, order)
        qty, available = self._position(trade)
        if qty == 0:
            self._finish(trade, 'Crypto position fully closed and orders reconciled')
            return
        step = number(trade['qty_step'])
        if abs(available - qty) > step:
            raise CryptoWaiting('Waiting for crypto sell reservations to release before the market exit')
        if trade.get('exit_op'):
            prior = trade['ops'][trade['exit_op']].get('last_seen', {})
            if prior.get('status') == 'rejected':
                raise CryptoIncident('Crypto exit was rejected; the remaining position requires broker review')
        qty = rounded(available, step)
        if trade.get('exit_child_max_qty'):
            qty = min(qty, number(trade['exit_child_max_qty']))
        if qty <= 0:
            self._incident(trade, 'exit_dust', 'An owned crypto remainder is smaller than the trade increment')
            raise CryptoIncident('A crypto remainder is below the venue quantity increment; it remains recorded for review')
        name = 'exit' if 'exit' not in trade['ops'] else 'exit' + str(sum(k.startswith('exit') for k in trade['ops']) + 1)
        unsuccessful = 0
        exit_operations = [(name, op) for name, op in trade['ops'].items() if name.startswith('exit')]
        for operation, prior in sorted(exit_operations, key=lambda item: 1 if item[0] == 'exit' else int(item[0][4:])):
            status = prior.get('last_seen', {}).get('status')
            if status == 'filled':
                unsuccessful = 0  # A fully filled capped child is successful progress.
            elif status in TERMINAL:
                unsuccessful += 1
        if unsuccessful >= 5:
            self._incident(trade, 'exit_attempts', 'Repeated partial crypto exits require broker review')
            raise CryptoIncident('Repeated partial crypto exits require review; no additional sell will be sent')
        trade['exit_op'] = name
        self._prepare(trade, name, {'symbol': trade['symbol'], 'side': 'sell', 'qty': decimal_text(qty),
                                   'type': 'market', 'time_in_force': 'gtc'})
        result = self._order(trade, name)
        if result['status'] in TERMINAL:
            remaining, _ = self._position(trade)
            if remaining == 0:
                self._finish(trade, 'Crypto position fully closed and orders reconciled')
            elif result['status'] == 'rejected':
                raise CryptoIncident('Crypto exit was rejected; remaining exposure needs broker review')
        else:
            self.message = 'Crypto exit submitted; waiting for its confirmed fill.'

    def _exit_cap(self, trade):
        """Bound large sell children without making small emergency exits await data.

        Alpaca publishes a $200k limit for buys and sells. $190k is an app planning
        buffer, not a guarantee against market jumps. Native protection stays in
        place while a large exit waits for an executable valuation.
        """
        gross = number(trade['ops']['entry'].get('last_seen', {}).get('filled_qty', '0'))
        reference = max(number(trade.get(k, '0')) for k in ('source_entry', 'target', 'stop', 'exit_reference_price'))
        remaining = max(ZERO, number(trade.get('net_entry_qty', gross)) - self._known_sells(trade))
        if remaining * reference <= MAX_EXIT_CHILD_NOTIONAL:
            if trade.pop('exit_child_max_qty', None) is not None:
                self.store.save_trade(trade)
            return
        _, ask = checked_quote(self.broker.quote(trade['symbol']), trade['symbol'], self.now())
        cap = rounded(MAX_EXIT_CHILD_NOTIONAL / ask, number(trade['qty_step']))
        if cap <= 0:
            self._incident(trade, 'exit_notional_cap', 'Crypto quantity cannot satisfy the broker per-order notional limit')
            raise CryptoIncident('Crypto sell size cannot satisfy the broker limit; no oversized sale was sent')
        if (trade.get('exit_child_max_qty') != decimal_text(cap)
                or trade.get('exit_reference_price') != decimal_text(ask)):
            trade['exit_child_max_qty'] = decimal_text(cap)
            trade['exit_reference_price'] = decimal_text(ask)
            self.store.save_trade(trade)

    def _finish(self, trade, reason):
        trade.update(stage='finished', reason=reason, completed_at=iso(self.now()))
        self.store.save_trade(trade, finished=True)
        self.store.event('trade_finished', {'trade_id': trade['id'], 'symbol': trade['symbol'], 'reason': reason})
        self.message = reason
        self.market_messages[trade['symbol']] = reason
