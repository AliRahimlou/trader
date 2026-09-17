"""Single-position, restartable order lifecycle for the documented video interpretation.

Every broker POST has a durable, unique intent. An uncertain response is looked up,
never blindly retried. This module is also exercised with an offline fake broker.
"""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from math import isfinite
from threading import Lock
from .broker import BrokerRejected
from .feeds import FeedError
from .models import timestamp
from .policy import POLICY_VERSION
from .sizing import decimal, purchase_plan

TERMINAL = {'filled', 'canceled', 'expired', 'rejected'}
WORKING_PROTECTION = {'new', 'partially_filled'}
UNAVAILABLE_PROTECTION = {'suspended', 'done_for_day', 'pending_cancel', 'pending_replace', 'calculated'}
# Proposed release safety policy: a submitted stop must become executable within
# this interval. This is not a promise about venue latency or a maximum loss.
PROTECTION_CONFIRM_SECONDS = 30
CHECKS = {'Current Nasdaq observation', 'Premarked levels', 'Nasdaq level event',
          'Magnificent Seven at their zones', 'Actual VIX zone reaction', 'Stop and target'}


class Waiting(ValueError):
    pass


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
            raise Waiting('Waiting for a current QQQ quote')
        bid, ask = decimal(quote['bp']), decimal(quote['ap'])
        if bid <= 0 or ask < bid or decimal(quote['bs']) <= 0 or decimal(quote['as']) <= 0:
            raise Waiting('Waiting for a valid QQQ bid and ask')
        if (ask - bid) / bid > decimal('.005'):
            raise Waiting('QQQ spread is too wide; waiting')
        return bid, ask
    except Waiting:
        raise
    except (KeyError, TypeError, ValueError, ArithmeticError):
        # A malformed quote is unavailable data too. Position management must
        # still place the known protective stop after a confirmed entry fill.
        raise Waiting('Waiting for a valid QQQ bid and ask') from None


class Executor:
    def __init__(self, broker, store, now=None):
        self.broker, self.store = broker, store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.tick_lock, self.entry_lock = Lock(), Lock()
        self.message = 'Live money is off. Analysis continues.'
        self.last_at = None

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
                'execution': {'message': self.message, 'at': self.last_at,
                    'trade': {k: trade.get(k) for k in ('stage', 'symbol', 'direction', 'amount', 'stop', 'target', 'reason')} if trade else None}}

    def tick(self, snapshot):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            trade = self.store.active_trade()
            if trade:
                self._manage(trade)
            elif not self.enabled():
                self.message = 'Live money is off. Analysis continues.'
            else:
                self._entry(snapshot)
        except (Waiting, FeedError, ValueError) as exc:
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
        except Exception:
            self.message = 'Execution needs attention: broker state could not be validated. No new order attempted.'
        finally:
            self.last_at = self.now().isoformat()
            self.tick_lock.release()

    def _entry(self, snapshot):
        now = self.now()
        if snapshot.get('feeds', {}).get('vix') != 'current':
            raise Waiting('Live money is on — waiting for actual VIX data. No entry can be sent yet.')
        if snapshot.get('data_errors') or not snapshot.get('analysis_at') or not 0 <= elapsed(snapshot['analysis_at'], now) <= 90:
            raise Waiting('Live money is on — waiting for fresh, complete strategy data')
        if not data_unexpired(snapshot.get('data_valid_until'), now):
            raise Waiting('Live money is on — data verification expired; waiting for a fresh update')
        setup = snapshot.get('setup') or {}
        checks = setup.get('checks', [])
        if (setup.get('state') != 'SETUP_READY' or {c['name'] for c in checks} != CHECKS
                or not all(c['passed'] is True for c in checks)):
            failed = next((c['name'] for c in checks if not c['passed']), 'the complete video setup')
            raise Waiting('Live money is on — waiting for ' + failed.lower())
        if setup.get('policy_version') != 'nasdaq-video-interpretation-v1' or not 0 <= elapsed(setup['event_at'], now) <= 3690:
            raise Waiting('Waiting for a current setup under the active video rules')
        identity = f'{POLICY_VERSION}|QQQ|{setup["event_at"]}|{setup["direction"]}'
        key = sha256(identity.encode()).hexdigest()[:24]
        if self.store.trade_exists(key):
            raise Waiting('This setup has already been handled; waiting for the next event')
        account, positions, orders, clock = (self.broker.account(), self.broker.positions(), self.broker.orders(), self.broker.clock())
        self._account_ready(account)
        if account.get('account_ref') != self.store.control().get('account_ref'):
            raise Waiting('The connected account changed. Turn off, review the account and enable again.')
        if account.get('mode') != 'live':
            raise Waiting('Execution requires the reviewed live account connection')
        if not session_open(clock, self.now()):
            raise Waiting('Live money is on — waiting for the regular market session')
        if closing(clock, self.now(), 600):
            raise Waiting('No new entries in the final ten minutes of the market session')
        if positions or orders:
            raise Waiting('Waiting for existing broker positions and orders to finish')
        asset = self.broker.asset('QQQ')
        if asset.get('symbol') != 'QQQ' or asset.get('status') != 'active' or asset.get('tradable') is not True:
            raise Waiting('QQQ is not currently tradable')
        quote = self.broker.quote('QQQ')
        bid, ask = checked_quote(quote, self.now())
        entry_valid_until = min(timestamp(snapshot['data_valid_until']), timestamp(quote['t'])+timedelta(seconds=15)).isoformat()
        direction = setup['direction']
        if direction not in ('long', 'short'):
            raise Waiting('Setup direction is missing')
        price = ask if direction == 'long' else bid
        stop, target, reference = map(decimal, (setup['stop'], setup['target'], setup['entry']))
        if not (0 < stop < bid <= ask < target if direction == 'long' else 0 < target < bid <= ask < stop):
            raise Waiting('Price has left the entry area between the stop and target')
        if abs(price / reference - 1) > decimal('.01'):
            raise Waiting('Price has moved more than 1% from the signal; skipping this entry')
        amount = self.store.settings()['target_dollars']
        plan = purchase_plan(amount, price, account['buying_power'], direction, asset.get('fractionable') is True)
        if direction == 'short':
            if account.get('shorting_enabled') is not True or not asset.get('shortable') or not asset.get('easy_to_borrow'):
                raise Waiting('This short requires account permission and available QQQ borrow')
            if decimal(plan['quantity']) * ask * decimal('1.03') > decimal(account['buying_power']):
                raise Waiting('Not enough buying power for the broker’s short-sale reserve')
        # A cheap candle cache does not prove that a current index quote exists.
        # This read-only check runs only for an otherwise actionable entry.
        if getattr(self.broker, 'requires_vix_entry_quote', False):
            verification = self.broker.confirm_vix_quote(self.now())
            delay, value = verification.get('delay_seconds'), verification.get('value')
            if (verification.get('source') != 'insightsentry' or verification.get('symbol') != 'I:VIX'
                    or isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay != 0
                    or isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0
                    or not 0 <= elapsed(verification['updated_at'], self.now()) <= 90):
                raise Waiting('Waiting for a verified current actual VIX quote')
            entry_valid_until = min(timestamp(entry_valid_until), timestamp(verification['updated_at'])+timedelta(seconds=90)).isoformat()
        if not 0 <= elapsed(snapshot['analysis_at'], self.now()) <= 90:
            raise Waiting('Strategy data aged during broker checks; waiting for the next analysis')
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
            if not self.enabled():
                return
            if not self.store.reserve_trade(trade):
                raise Waiting('This setup has already been handled; waiting for the next event')
        self.store.event('entry_planned', {k: trade[k] for k in ('symbol', 'direction', 'amount', 'stop', 'target')})
        self._manage(trade)

    def _prepare(self, trade, name, payload, persist=True):
        payload = {**payload, 'client_order_id': f'pvt-{trade["id"]}-{name}'}
        trade['ops'][name] = {'state': 'prepared', 'payload': payload}
        if name == 'stop':
            trade['ops'][name]['confirmation_deadline'] = (
                self.now() + timedelta(seconds=PROTECTION_CONFIRM_SECONDS)).isoformat()
        if persist:
            self.store.save_trade(trade)

    def _order(self, trade, name):
        op = trade['ops'][name]
        if op['state'] == 'rejected':
            return {'status': 'rejected', 'filled_qty': '0', 'client_order_id': op['payload']['client_order_id']}
        try:
            if op['state'] == 'prepared':
                # Commit BEFORE POST. A crash or timeout can never produce an automatic second POST.
                if self.store.claim_operation(trade['id'], name):
                    op['state'] = 'attempted'
                    try:
                        order = self.broker.submit(op['payload'])
                    except BrokerRejected:
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
                    order = self.broker.lookup(op['payload']['client_order_id'])
            else:
                order = self.broker.lookup(op['payload']['client_order_id'])
        except FeedError:
            if name == 'stop':
                self._check_unknown_protection(trade)
            raise
        if not order:
            if name == 'stop':
                self._check_unknown_protection(trade)
            raise Waiting('Order status is uncertain. Waiting for its broker identifier; no duplicate will be sent.')
        if order.get('client_order_id') != op['payload']['client_order_id'] or order.get('symbol') != 'QQQ' or order.get('side') != op['payload']['side']:
            raise Waiting('Broker order identity mismatch; manual review required')
        if order.get('status') == 'rejected':
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
        return order

    def _finish(self, trade, reason):
        trade.update(stage='finished', reason=reason, completed_at=self.now().isoformat())
        self.store.save_trade(trade, finished=True)
        self.store.event('trade_finished', {'symbol': trade['symbol'], 'reason': reason})
        self.message = reason

    def _attention(self, trade, reason):
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
            bid, ask = checked_quote(self.broker.quote('QQQ'), self.now())
        except (Waiting, FeedError):
            # Existing broker stop remains in place while quotes are unavailable.
            # For a new fill, place its known protective stop before waiting for quotes.
            if not stop_order:
                self._protect(trade, position)
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
        if 'stop' in trade['ops'] and trade['ops']['stop']['state'] != 'prepared':
            order = self._order(trade, 'stop')
            if order['status'] not in TERMINAL:
                try:
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
