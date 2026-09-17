"""Single-position, restartable order lifecycle for the documented video interpretation.

Every broker POST has a durable, unique intent. An uncertain response is looked up,
never blindly retried. This module is also exercised with an offline fake broker.
"""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
from hashlib import sha256
from math import isfinite
import re
from threading import Lock
from .broker import BrokerRejected
from .feeds import FeedError
from .models import timestamp
from .policy import POLICY_VERSION
from .sizing import decimal, purchase_plan
from .version import APP_VERSION

TERMINAL = {'filled', 'canceled', 'expired', 'rejected'}
WORKING_PROTECTION = {'new', 'partially_filled'}
UNAVAILABLE_PROTECTION = {'suspended', 'done_for_day', 'pending_cancel', 'pending_replace', 'calculated'}
# Proposed release safety policy: a submitted stop must become executable within
# this interval. This is not a promise about venue latency or a maximum loss.
PROTECTION_CONFIRM_SECONDS = 30
CHECKS = {'Current Nasdaq observation', 'Premarked levels', 'Nasdaq level event',
          'Magnificent Seven at their zones', 'Actual VIX zone reaction', 'Stop and target'}
SIGNAL_GATES = {'Current Nasdaq observation': 'signal_observation', 'Premarked levels': 'signal_levels',
                'Nasdaq level event': 'signal_event', 'Magnificent Seven at their zones': 'signal_leaders',
                'Actual VIX zone reaction': 'signal_vix', 'Stop and target': 'signal_exits'}
EXECUTION_GATES = set(SIGNAL_GATES.values()) | {
    'runtime_state', 'live_permission', 'vix_candles', 'analysis_freshness', 'data_expiry',
    'signal_checks', 'signal_age_policy', 'setup_deduplication', 'broker_snapshot', 'account_status',
    'account_identity', 'account_mode', 'market_session', 'entry_cutoff', 'existing_exposure',
    'asset_eligibility', 'quote_read', 'quote_validation', 'quote_stale', 'quote_invalid', 'quote_spread',
    'signal_direction', 'price_geometry', 'price_drift', 'purchase_size', 'short_eligibility',
    'short_reserve', 'vix_entry_quote', 'final_analysis_freshness', 'final_data_expiry',
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


def elapsed(at, now):
    return (now - timestamp(at)).total_seconds()


def data_unexpired(until, now):
    return bool(until and 0 <= (timestamp(until) - now).total_seconds() <= 90)


def session_open(clock, now):
    return clock.get('is_open') is True and 0 <= elapsed(clock['timestamp'], now) <= 15


def closing(clock, now, seconds=300):
    return (timestamp(clock['next_close']) - now).total_seconds() <= seconds


def checked_quote(quote, now):
    try:
        if not 0 <= elapsed(quote['t'], now) <= 15:
            raise Waiting('Waiting for a current QQQ quote', code='quote_stale')
        bid, ask = decimal(quote['bp']), decimal(quote['ap'])
        if bid <= 0 or ask < bid or decimal(quote['bs']) <= 0 or decimal(quote['as']) <= 0:
            raise Waiting('Waiting for a valid QQQ bid and ask', code='quote_invalid')
        if (ask - bid) / bid > decimal('.005'):
            raise Waiting('QQQ spread is too wide; waiting', code='quote_spread')
        return bid, ask
    except Waiting:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError):
        # A malformed quote is unavailable data too. Position management must
        # still place the known protective stop after a confirmed entry fill.
        raise Waiting('Waiting for a valid QQQ bid and ask', code='quote_invalid') from None


class Executor:
    def __init__(self, broker, store, now=None, *, revision=None):
        self.broker, self.store = broker, store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.tick_lock, self.entry_lock = Lock(), Lock()
        self.message = 'Live money is off. Analysis continues.'
        self.last_at = None
        self.revision = revision
        self.execution_check = None
        self.execution_diagnostic_error = None
        self._execution_check_from_current_process = False
        self._diagnostic_gate = 'runtime_state'
        self._diagnostic_trade = None
        self._diagnostic_outcome = None
        self._submission_attempted = False
        try:
            self.execution_check = self.store.latest_execution_check()
        except Exception:
            self.execution_diagnostic_error = 'Saved execution diagnostics unavailable; order handling is unchanged.'

    def _gate(self, code):
        """An observational marker only; it never permits, skips or changes a broker action."""
        self._diagnostic_gate = code

    @staticmethod
    def _diagnostic_time(value):
        try:
            return timestamp(value).astimezone(timezone.utc).isoformat() if value is not None else None
        except (ValueError, TypeError, OverflowError):
            return None

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
                    'state': state if state in ('prepared', 'attempted', 'rejected') else 'unknown',
                    'broker_status': status if isinstance(status, str) and status in ORDER_STATUSES else None,
                }
            identity = trade.get('id')
            trade_state = trade.get('stage')
            direction = setup.get('direction')
            checks = setup.get('checks') if isinstance(setup.get('checks'), list) else []
            first_failed = next((SIGNAL_GATES[c['name']] for c in checks if isinstance(c, dict)
                                 and isinstance(c.get('name'), str) and c['name'] in SIGNAL_GATES
                                 and c.get('passed') is not True), None)
            record = {
                'version': 'execution-check-v1', 'captured_at': now.isoformat(),
                'checkpoint_at': now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0).isoformat(),
                'app_version': APP_VERSION,
                'revision': self.revision if isinstance(self.revision, str) and re.fullmatch(r'[a-f0-9]{40}', self.revision) else None,
                'execution_policy': POLICY_VERSION,
                'outcome': outcome if outcome in EXECUTION_OUTCOMES else 'unexpected_error',
                'gate': self._diagnostic_gate if self._diagnostic_gate in EXECUTION_GATES else 'runtime_state',
                'exception_kind': exception_kind,
                'analysis_at': self._diagnostic_time(snapshot.get('analysis_at')),
                'signal': {
                    'event_at': self._diagnostic_time(setup.get('event_at')),
                    'event': setup.get('event') if setup.get('event') in ('break and retest', 'sweep and reclaim', 'previous-day level sweep') else None,
                    'policy_version': setup.get('policy_version') if setup.get('policy_version') == 'nasdaq-video-interpretation-v1' else None,
                    'direction': direction if direction in ('long', 'short') else None,
                    'state': setup.get('state') if setup.get('state') in ('WATCHING', 'AT_LEVEL', 'WAITING_FOR_RETEST', 'CONFIRMING', 'SETUP_READY') else None,
                    'first_failed_gate': first_failed,
                },
                'trade': {
                    'id': identity if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{24}', identity) else None,
                    'stage': trade_state if trade_state in ('entering', 'open', 'exiting', 'attention', 'finished') else None,
                    'direction': trade.get('direction') if trade.get('direction') in ('long', 'short') else None,
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
        c = self.store.control()
        return c['enabled'] is True and c.get('policy') == POLICY_VERSION

    def set_live(self, payload):
        if set(payload) != {'enabled', 'policy_version'} or type(payload['enabled']) is not bool:
            raise ValueError('Invalid live-money setting')
        if payload['enabled']:
            if payload['policy_version'] != POLICY_VERSION:
                raise ValueError('Review the current execution rules before turning on')
            account = self.broker.account()
            if account.get('mode') != 'live':
                raise ValueError('This connection is not a live Alpaca account')
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
        return {'live_enabled': self.enabled(), 'execution_available': True,
                'review_required': review_required,
                **self._execution_diagnostics_snapshot(),
                'execution': {'message': self.message, 'at': self.last_at,
                    'trade': {k: trade.get(k) for k in ('stage', 'symbol', 'direction', 'amount', 'stop', 'target', 'reason')} if trade else None}}

    def tick(self, snapshot):
        if not self.tick_lock.acquire(blocking=False):
            return
        self._diagnostic_gate = 'runtime_state'
        self._diagnostic_trade = None
        self._diagnostic_outcome = None
        self._submission_attempted = False
        outcome, exception_kind = 'managing', None
        try:
            trade = self.store.active_trade()
            self._diagnostic_trade = trade
            if trade:
                self._manage(trade)
            elif not self.enabled():
                self._gate('live_permission')
                outcome = 'disabled'
                self.message = 'Live money is off. Analysis continues.'
            else:
                outcome = 'entry_planned'
                self._entry(snapshot)
        except (Waiting, FeedError, ValueError) as exc:
            outcome = 'waiting' if isinstance(exc, Waiting) else 'feed_error' if isinstance(exc, FeedError) else 'invalid_data'
            exception_kind = 'Waiting' if isinstance(exc, Waiting) else 'FeedError' if isinstance(exc, FeedError) else 'ValueError'
            if isinstance(exc, Waiting) and isinstance(exc.code, str) and exc.code in EXECUTION_GATES:
                self._gate(exc.code)
            self.message = str(exc)
            if isinstance(exc, FeedError):
                # Account/clock/position reads can fail before management reaches
                # its protection check. Preserve the deadline for a stop already
                # known to be unconfirmed, using only durable local evidence.
                saved = self.store.active_trade()
                op = (saved or {}).get('ops', {}).get('stop')
                status = ((op or {}).get('last_seen') or {}).get('status')
                if (saved and saved['stage'] in ('open', 'exiting') and op
                        and op['state'] == 'attempted' and status not in WORKING_PROTECTION | TERMINAL):
                    try:
                        self._check_unknown_protection(saved)
                    except Waiting as uncertainty:
                        self.message = str(uncertainty)
        except Exception as exc:
            outcome, exception_kind = 'unexpected_error', UNEXPECTED_EXCEPTION_KINDS.get(type(exc), 'unexpected')
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

    def _entry(self, snapshot):
        now = self.now()
        self._gate('vix_candles')
        if snapshot.get('feeds', {}).get('vix') != 'current':
            raise Waiting('Live money is on — waiting for actual VIX data. No entry can be sent yet.')
        self._gate('analysis_freshness')
        if snapshot.get('data_errors') or not snapshot.get('analysis_at') or not 0 <= elapsed(snapshot['analysis_at'], now) <= 90:
            raise Waiting('Live money is on — waiting for fresh, complete strategy data')
        self._gate('data_expiry')
        if not data_unexpired(snapshot.get('data_valid_until'), now):
            raise Waiting('Live money is on — data verification expired; waiting for a fresh update')
        setup = snapshot.get('setup') or {}
        checks = setup.get('checks', [])
        self._gate('signal_checks')
        if (setup.get('state') != 'SETUP_READY' or {c['name'] for c in checks} != CHECKS
                or not all(c['passed'] is True for c in checks)):
            failed = next((c['name'] for c in checks if not c['passed']), 'the complete video setup')
            self._gate(SIGNAL_GATES.get(failed, 'signal_checks'))
            raise Waiting('Live money is on — waiting for ' + failed.lower())
        self._gate('signal_age_policy')
        if setup.get('policy_version') != 'nasdaq-video-interpretation-v1' or not 0 <= elapsed(setup['event_at'], now) <= 3690:
            raise Waiting('Waiting for a current setup under the active video rules')
        identity = f'{POLICY_VERSION}|QQQ|{setup["event_at"]}|{setup["direction"]}'
        key = sha256(identity.encode()).hexdigest()[:24]
        self._gate('setup_deduplication')
        if self.store.trade_exists(key):
            raise Waiting('This setup has already been handled; waiting for the next event')
        self._gate('broker_snapshot')
        account, positions, orders, clock = (self.broker.account(), self.broker.positions(), self.broker.orders(), self.broker.clock())
        self._gate('account_status')
        self._account_ready(account)
        self._gate('account_identity')
        if account.get('account_ref') != self.store.control().get('account_ref'):
            raise Waiting('The connected account changed. Turn off, review the account and enable again.')
        self._gate('account_mode')
        if account.get('mode') != 'live':
            raise Waiting('Execution requires the reviewed live account connection')
        self._gate('market_session')
        if not session_open(clock, self.now()):
            raise Waiting('Live money is on — waiting for the regular market session')
        self._gate('entry_cutoff')
        if closing(clock, self.now(), 600):
            raise Waiting('No new entries in the final ten minutes of the market session')
        self._gate('existing_exposure')
        if positions or orders:
            raise Waiting('Waiting for existing broker positions and orders to finish')
        self._gate('asset_eligibility')
        asset = self.broker.asset('QQQ')
        if asset.get('symbol') != 'QQQ' or asset.get('status') != 'active' or asset.get('tradable') is not True:
            raise Waiting('QQQ is not currently tradable')
        self._gate('quote_read')
        quote = self.broker.quote('QQQ')
        self._gate('quote_validation')
        bid, ask = checked_quote(quote, self.now())
        entry_valid_until = min(timestamp(snapshot['data_valid_until']), timestamp(quote['t'])+timedelta(seconds=15)).isoformat()
        direction = setup['direction']
        self._gate('signal_direction')
        if direction not in ('long', 'short'):
            raise Waiting('Setup direction is missing')
        price = ask if direction == 'long' else bid
        self._gate('price_geometry')
        stop, target, reference = map(decimal, (setup['stop'], setup['target'], setup['entry']))
        if not (0 < stop < bid <= ask < target if direction == 'long' else 0 < target < bid <= ask < stop):
            raise Waiting('Price has left the entry area between the stop and target')
        self._gate('price_drift')
        if abs(price / reference - 1) > decimal('.01'):
            raise Waiting('Price has moved more than 1% from the signal; skipping this entry')
        self._gate('purchase_size')
        amount = self.store.settings()['target_dollars']
        plan = purchase_plan(amount, price, account['buying_power'], direction, asset.get('fractionable') is True)
        if direction == 'short':
            self._gate('short_eligibility')
            if account.get('shorting_enabled') is not True or not asset.get('shortable') or not asset.get('easy_to_borrow'):
                raise Waiting('This short requires account permission and available QQQ borrow')
            self._gate('short_reserve')
            if decimal(plan['quantity']) * ask * decimal('1.03') > decimal(account['buying_power']):
                raise Waiting('Not enough buying power for the broker’s short-sale reserve')
        # A cheap candle cache does not prove that a current index quote exists.
        # This read-only check runs only for an otherwise actionable entry.
        if getattr(self.broker, 'requires_vix_entry_quote', False):
            self._gate('vix_entry_quote')
            verification = self.broker.confirm_vix_quote(self.now())
            delay, value = verification.get('delay_seconds'), verification.get('value')
            if (verification.get('source') != 'insightsentry' or verification.get('symbol') != 'I:VIX'
                    or isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay != 0
                    or isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0
                    or not 0 <= elapsed(verification['updated_at'], self.now()) <= 90):
                raise Waiting('Waiting for a verified current actual VIX quote')
            entry_valid_until = min(timestamp(entry_valid_until), timestamp(verification['updated_at'])+timedelta(seconds=90)).isoformat()
        self._gate('final_analysis_freshness')
        if not 0 <= elapsed(snapshot['analysis_at'], self.now()) <= 90:
            raise Waiting('Strategy data aged during broker checks; waiting for the next analysis')
        self._gate('final_data_expiry')
        if not data_unexpired(entry_valid_until, self.now()):
            raise Waiting('Data verification expired during broker checks; waiting for a fresh update')
        # One attempt per event/direction, including a rejected or completed attempt.
        payload = {'symbol': 'QQQ', 'side': 'buy' if direction == 'long' else 'sell',
                   'type': 'market', 'time_in_force': 'day', 'extended_hours': False}
        if direction == 'long' and asset.get('fractionable') is True:
            payload['notional'] = plan['target_dollars']
        else:
            payload['qty'] = plan['quantity']
        trade = {'id': key, 'stage': 'entering', 'symbol': 'QQQ', 'direction': direction,
                 'amount': plan['target_dollars'], 'stop': str(stop), 'target': str(target),
                 'created_at': self.now().isoformat(), 'data_valid_until': entry_valid_until, 'ops': {}, 'exit_number': 0, 'account_ref': account['account_ref']}
        self._prepare(trade, 'entry', payload, persist=False)
        with self.entry_lock:
            self._gate('live_permission')
            if not self.enabled():
                self._diagnostic_outcome = 'disabled'
                return
            self._gate('entry_reservation')
            if not self.store.reserve_trade(trade):
                raise Waiting('This setup has already been handled; waiting for the next event')
        self._diagnostic_trade = trade
        self.store.event('entry_planned', {k: trade[k] for k in ('symbol', 'direction', 'amount', 'stop', 'target')})
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

    def _order(self, trade, name):
        self._gate('order_reconciliation')
        self._diagnostic_trade = trade
        op = trade['ops'][name]
        if op['state'] == 'rejected':
            return {'status': 'rejected', 'filled_qty': '0', 'client_order_id': op['payload']['client_order_id']}
        try:
            if op['state'] == 'prepared':
                # Commit BEFORE POST. A crash or timeout can never produce an automatic second POST.
                if self.store.claim_operation(trade['id'], name):
                    op['state'] = 'attempted'
                    try:
                        self._gate('order_submit')
                        self._submission_attempted = True
                        order = self.broker.submit(op['payload'])
                    except BrokerRejected:
                        self._gate('order_rejection')
                        self._diagnostic_outcome = 'order_rejected'
                        op['state'] = 'rejected'
                        op['last_seen'] = {'symbol':op['payload']['symbol'],'side':op['payload']['side'],
                                          'status':'rejected','qty':op['payload'].get('qty'),'filled_qty':'0',
                                          'client_order_id':op['payload']['client_order_id'],'evidence':'http_rejection'}
                        self.store.save_trade(trade)
                        self.store.set_control(False)
                        self.store.event('order_rejected', {'purpose': name, 'symbol': trade['symbol']})
                        return {'status': 'rejected', 'filled_qty': '0'}
                else:
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
            if name == 'stop':
                self._check_unknown_protection(trade)
            raise Waiting('Order status is uncertain. Waiting for its broker identifier; no duplicate will be sent.')
        self._gate('order_identity')
        if order.get('client_order_id') != op['payload']['client_order_id'] or order.get('symbol') != 'QQQ' or order.get('side') != op['payload']['side']:
            raise Waiting('Broker order identity mismatch; manual review required')
        if order.get('status') == 'rejected':
            self._gate('order_rejection')
            self._diagnostic_outcome = 'order_rejected'
            # A successful HTTP response can later become a venue rejection.
            # Pause entries just as for HTTP rejection, but keep the broker's
            # actual fill evidence so existing exposure can still be managed.
            if self.store.control().get('enabled') is True:
                self.store.set_control(False)
            if (op.get('last_seen') or {}).get('status') != 'rejected':
                self.store.event('order_rejected', {'purpose': name, 'symbol': trade['symbol'],
                                                   'evidence': 'broker_status'})
        evidence = {k:order.get(k) for k in ('id','client_order_id','symbol','side','status','qty','filled_qty','filled_avg_price','updated_at','filled_at')}
        if op.get('last_seen') != evidence:
            op['last_seen'] = evidence
            self.store.save_trade(trade)
        if not self._diagnostic_outcome:
            self._gate('order_reconciliation')
        return order

    def _finish(self, trade, reason):
        self._gate('trade_finish')
        self._diagnostic_trade = trade
        self._diagnostic_outcome = self._diagnostic_outcome or 'completed'
        trade.update(stage='finished', reason=reason, completed_at=self.now().isoformat())
        self.store.save_trade(trade, finished=True)
        self.store.event('trade_finished', {'symbol': trade['symbol'], 'reason': reason})
        self.message = reason

    def _attention(self, trade, reason):
        self._gate('owner_attention')
        self._diagnostic_trade = trade
        self._diagnostic_outcome = 'attention'
        self.store.set_control(False)
        trade.update(stage='attention', reason=reason)
        self.store.save_trade(trade)
        self.store.event('execution_needs_attention', {'symbol':'QQQ','reason':reason})
        raise Waiting(reason)

    def _reconcile_attention(self, trade):
        # Manual resolution is read-only: never compete with an owner's changes.
        if any(p['symbol']=='QQQ' for p in self.broker.positions()) or any(o['symbol']=='QQQ' for o in self.broker.orders()):
            raise Waiting(trade['reason'])
        for name, op in trade['ops'].items():
            if op['state']=='prepared':
                continue  # Durable intent proves this operation was never attempted.
            order=self._order(trade,name)
            if order['status'] not in TERMINAL | {'replaced'}:
                raise Waiting(trade['reason'])
        # Confirm flatness again after order lookups, which can take time.
        if any(p['symbol']=='QQQ' for p in self.broker.positions()) or any(o['symbol']=='QQQ' for o in self.broker.orders()):
            raise Waiting(trade['reason'])
        if self.enabled():
            self.store.set_control(False)
        trade['manual_reconciliation']=True
        self.store.event('manual_resolution_confirmed', {'symbol':'QQQ','note':'Broker is flat with no working QQQ orders; external fill economics remain unverified'})
        self._finish(trade,'Manual resolution confirmed. Live money remains off; review before enabling again.')

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
                self._attention(trade,'An owned QQQ order was replaced outside the app. Check the position and working orders in Alpaca now.')
            exited += decimal(order.get('filled_qty') or '0')
        if position:
            qty = decimal(position['qty'])
            if (qty > 0) != (trade['direction'] == 'long'):
                self._attention(trade,'QQQ position direction changed outside the app; manual review required')
            # Only manage the shares this app opened, less confirmed exit fills.
            expected = decimal(trade.get('filled_qty', '0')) - exited
            if abs(qty) != expected:
                raise Waiting('QQQ share count differs from this app’s fills; waiting for broker reconciliation')
        # Foreign QQQ orders could change or reserve the position. Never cancel or compete with them.
        ours = {op['payload']['client_order_id'] for op in trade['ops'].values()}
        if any(o['symbol'] == 'QQQ' and o.get('client_order_id') not in ours for o in self.broker.orders()):
            self._attention(trade,'A QQQ order outside this app needs review before automated position changes')
        return position

    def _manage(self, trade):
        self._gate('trade_management')
        self._diagnostic_trade = trade
        if self.broker.account().get('account_ref') != trade['account_ref']:
            raise Waiting('This position belongs to a different Alpaca connection; restore its account to manage it')
        clock = self.broker.clock()
        now = self.now()
        opened = session_open(clock, now)
        if trade['stage'] == 'entering':
            with self.entry_lock:
                if trade['ops']['entry']['state'] == 'prepared' and (not self.enabled() or not opened or closing(clock, now, 600) or elapsed(trade['created_at'], now) > 10 or not data_unexpired(trade.get('data_valid_until'), now)):
                    self._finish(trade, 'Entry expired before submission; no order sent')
                    return
                order = self._order(trade, 'entry')
            if order['status']=='replaced':
                self._attention(trade,'Entry order was replaced outside the app. Check the QQQ position and orders in Alpaca now.')
            if order['status'] not in TERMINAL:
                if decimal(order.get('filled_qty') or '0') > 0 or not self.enabled() or elapsed(trade['created_at'], now) > 20 or (opened and closing(clock, now)):
                    self._gate('order_cancel')
                    self.broker.cancel(order['id'])
                    self.message = 'Canceling the unfinished entry before managing filled shares'
                else:
                    self.message = 'Entry submitted; waiting for the broker fill'
                return
            qty = decimal(order.get('filled_qty') or '0')
            if not qty:
                self._finish(trade, 'Entry ended without a fill')
                return
            trade.update(stage='open', filled_qty=str(qty))
            self.store.save_trade(trade)
            self.store.event('entry_filled', {'symbol': 'QQQ', 'quantity': str(qty), 'price': order.get('filled_avg_price')})
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
                    self._attention(trade,'QQQ is flat but the app’s exit fills do not reconcile. Checking for manual resolution; results remain unverified.')
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
        stop_order = None
        stop_op = trade['ops'].get('stop')
        if stop_op and (stop_op['state'] != 'prepared' or (opened and trade['stage'] != 'exiting')):
            # Looking up an attempted order is safe outside the session, but a
            # recovered prepared intent must not place a new after-hours stop.
            stop_order = self._order(trade, 'stop')
        if stop_order and stop_order['status'] == 'replaced':
            self._attention(trade,'Protective order was replaced outside the app. Check QQQ protection in Alpaca now.')
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
            bid, ask = checked_quote(self.broker.quote('QQQ'), self.now())
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
            self.message = f'Managing QQQ: stop ${stop}, target ${target}. Broker protection: {stop_order["status"]}.'

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
        if self.store.control().get('enabled') is True:
            self.store.set_control(False)
        trade['protection_failure'] = {'at': self.now().isoformat(), 'reason': reason}
        self._start_exit(trade, reason + '. New entries paused; canceling before closing remaining shares.')

    def _check_unknown_protection(self, trade):
        failure = self._protection_failure(trade, {'status': 'unconfirmed'})
        if failure:
            if not trade.get('protection_failure'):
                self._start_protection_exit(trade, failure)
            elif self.store.control().get('enabled') is True:
                self.store.set_control(False)
            raise Waiting('Protection status is uncertain past its confirmation deadline. New entries paused; no competing sale will be sent. Check the QQQ position and orders in Alpaca now.')

    def _protect(self, trade, position):
        payload = {'symbol': 'QQQ', 'side': 'sell' if trade['direction'] == 'long' else 'buy',
                   'qty': str(abs(decimal(position['qty']))), 'type': 'stop',
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
        trade.update(stage='exiting', reason=reason)
        self.store.save_trade(trade)
        self.store.event('exit_started', {'symbol': 'QQQ', 'reason': reason})

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
                        raise Waiting('Protection and stop cancellation are uncertain. New entries remain paused. Check the QQQ position and orders in Alpaca now.') from None
                    raise
                self.message = ('Protection is not confirmed; waiting for stop cancellation before any sale. New entries paused. Check QQQ in Alpaca now.'
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
                self._attention(trade,'Exit order was replaced outside the app. Check the QQQ position and orders in Alpaca now.')
            if order['status'] not in TERMINAL:
                self.message = 'Exit submitted; waiting for all held shares to fill'
                return
            if order['status'] == 'rejected' or trade['exit_number'] >= 2:
                self._attention(trade,'Broker could not complete the exit. Check the QQQ position in Alpaca now.')
            # A canceled/expired exit can leave shares; size a new intent only after terminal confirmation.
            position = self._position(trade)
            if not position: return
            trade['exit_number'] += 1
            name = f'exit{trade["exit_number"]}'
        payload = {'symbol': 'QQQ', 'side': 'sell' if trade['direction'] == 'long' else 'buy',
                   'qty': str(abs(decimal(position['qty']))), 'type': 'market', 'time_in_force': 'day', 'extended_hours': False}
        self._prepare(trade, name, payload)
        self._order(trade, name)
        self.message = 'Exit submitted for the remaining held shares'
