"""Development-only fixture server for reviewing the Pivot interface.

Serves the real ``create_app()`` (same static files, routes, origin and intent
checks) over a ``Service`` whose feeds and broker are absent and whose state is
filled from in-memory fixtures. Nothing here reads credentials, opens a network
connection to a provider or broker, or touches ``runtime/``: every database is
created in a temporary directory that is removed on exit. Owner actions (Live,
strategy and purchase-size saves) only change that temporary database.

Usage::

    .venv/bin/python scripts/ui_fixture_server.py --port 8765 [--scenario waiting]

Open http://127.0.0.1:8765/ and switch scenarios with
http://127.0.0.1:8765/fixture/use/<name> (add ``?contract=0`` to drop the
4.5.1 ``socrates_readiness``/``strategy_pause`` fields and check that the page
degrades gracefully). ``/fixture`` lists the scenarios.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A fixture must never alert anyone or reach a provider, even if the shell exports settings.
for name in list(os.environ):
    if name.startswith(('PIVOT_ALERT', 'ALPACA', 'APCA', 'INSIGHT')):
        os.environ.pop(name, None)

from pivot.models import Bar, Zone  # noqa: E402
from pivot.policy import POLICY_VERSION  # noqa: E402
from pivot.service import Service  # noqa: E402
from pivot.store import Store  # noqa: E402

ET = ZoneInfo('America/New_York')
LEADERS = ('AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'TSLA')
OLD_POLICY = 'nasdaq-qqq-execution-v6-reward-risk-vix-persistence'
PAUSE_MESSAGE = 'Crypto is paused: Socrates-only focus. Existing crypto positions, if any, keep their exits.'
SCENARIOS = {
    'waiting': 'Market open, rules accepted, waiting for a setup',
    'review': 'Rules not accepted after an update (Live money Off, review required)',
    'ready': 'A SETUP_READY long during the session',
    'psq': 'An open PSQ short-proxy position with stop and target',
    'down': 'The open PSQ position while it is losing (live trade card in red)',
    'sold': 'No open trade; the last trade sold at the session close today',
    'paused': 'Socrates paused by the app after a rejected order',
    'closed': 'Market closed',
}


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


class FakeFeeds:
    """No provider: the fixture never downloads anything."""


def sessions(now, count):
    """The latest ``count`` weekday sessions up to today (display fixtures only)."""
    day, rows = now.astimezone(ET).date(), []
    while len(rows) < count:
        if day.weekday() < 5:
            rows.append(day)
        day -= timedelta(days=1)
    return list(reversed(rows))


def session_time(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), ET)


class Walk:
    """Deterministic price path so screenshots are comparable between runs."""

    def __init__(self, seed, price, step):
        self.random, self.price, self.step = random.Random(seed), price, step

    def bar(self, end, minutes, drift=0.0):
        open_ = self.price
        close = max(0.01, open_ * (1 + drift + self.random.gauss(0, self.step)))
        high = max(open_, close) * (1 + abs(self.random.gauss(0, self.step / 2)))
        low = min(open_, close) * (1 - abs(self.random.gauss(0, self.step / 2)))
        self.price = close
        return Bar(end, minutes, round(open_, 2), round(high, 2), round(low, 2), round(close, 2), 1000)


def qqq_bars(now):
    walk, hourly, four = Walk(7, 596.0, 0.0022), [], []
    for index, day in enumerate(sessions(now, 25)):
        drift = 0.0009 * math.sin(index / 3.0)
        day_bars = []
        for hour in range(10, 17):
            end = session_time(day, hour, 30) if hour < 16 else session_time(day, 16)
            if end > now:
                break
            day_bars.append(walk.bar(end, 60, drift))
        # Hourly frame: 09:30-15:30 buckets; the 15:30-16:00 half hour is dropped.
        hourly.extend(day_bars[:6])
        for part, end in ((day_bars[:4], session_time(day, 13, 30)), (day_bars[4:], session_time(day, 16))):
            if part and end <= now:
                four.append(Bar(end, 240, part[0].open, max(b.high for b in part), min(b.low for b in part), part[-1].close, 4000))
    return hourly, four


def vix_bars(now):
    walk, rows = Walk(11, 17.2, 0.004), []
    for day in sessions(now, 3):
        start = session_time(day, 9, 30)
        for slot in range(1, 27):
            end = start + timedelta(minutes=15 * slot)
            if end > now:
                break
            rows.append(walk.bar(end, 15, 0.0004 * math.cos(slot / 4)))
    return rows


def levels_from(four, reference):
    """Swing-pivot bands the way the fixture's owner would see them (display data only)."""
    zones = []
    for i in range(1, len(four) - 1):
        bar, before, after = four[i], four[i - 1], four[i + 1]
        for price, kind in ((bar.high, 'high'), (bar.low, 'low')):
            pivot = price >= max(before.high, after.high) if kind == 'high' else price <= min(before.low, after.low)
            if not pivot:
                continue
            low, high = round(price * 0.999, 2), round(price * 1.001, 2)
            if any(not (high < z.low or low > z.high) for z in zones):
                continue
            touches = sum(1 for b in four if b.low <= high and b.high >= low)
            if touches >= 2:
                zones.append(Zone(low, high, bar.end, '4h repeated interaction', touches))
    zones.sort(key=lambda z: abs((z.low + z.high) / 2 - reference))
    return sorted(zones[:10], key=lambda z: z.low)


class FixtureService(Service):
    def __init__(self, root, scenario='waiting', contract=True):
        self.root, self.counter = Path(root), 0
        self.use(scenario, contract)

    # -- scenario switching --------------------------------------------------
    def use(self, scenario, contract=True):
        if scenario not in SCENARIOS:
            raise ValueError('Unknown scenario')
        self.counter += 1
        store = Store(self.root / f'{self.counter}-{scenario}' / 'pivot.sqlite3')
        Service.__init__(self, FakeFeeds(), store, broker=None)
        self.scenario, self.contract = scenario, contract
        store.save({'sizing_mode': 'target', 'target_dollars': '15.00'})
        self.crypto_store.configure({'enabled': False, 'target_dollars': '15.00', 'symbols': ['BTC/USD']})
        if scenario == 'review':
            store.set_control(True, OLD_POLICY, 'fixture-account')
        else:
            store.set_control(True, POLICY_VERSION, 'fixture-account')
        if scenario == 'paused':
            store.pause_family('socrates', 'The broker rejected the QQQ entry order (insufficient buying power). '
                               'No position was opened.')
        now = datetime.now(timezone.utc)
        self.hourly, self.four = qqq_bars(now)
        self.vix = vix_bars(now)
        self.reference = self.hourly[-1].close
        self.levels = levels_from(self.four, self.reference)
        self.event = min(self.levels, key=lambda z: abs(z.mid - self.reference)) if self.levels else None

    # -- owner actions only touch the temporary database ---------------------
    def set_live(self, payload):
        if not isinstance(payload, dict) or type(payload.get('enabled')) is not bool:
            raise ValueError('Choose On or Off')
        if payload['enabled'] and payload.get('policy_version') != POLICY_VERSION:
            raise ValueError('Review the current execution rules')
        control = self.store.control()
        self.store.set_control(payload['enabled'], POLICY_VERSION if payload['enabled'] else control.get('policy'), 'fixture-account')
        if payload['enabled'] and self.scenario == 'review':
            self.scenario = 'waiting'
        return self.snapshot()

    def save_strategies(self, payload):
        stock = payload.get('socrates') if isinstance(payload, dict) else None
        if isinstance(stock, dict) and type(stock.get('enabled')) is bool:
            with self.store.connect() as db:
                db.execute('UPDATE strategy_selection SET body=? WHERE id=1', (json.dumps({'socrates': stock['enabled']}),))
            self.store.event('strategy_settings', {'socrates': stock})
            if stock['enabled'] and self.scenario == 'paused':
                self.scenario = 'waiting'
        if isinstance(payload, dict) and 'range_reversal' in payload:
            raise ValueError('Crypto is paused: Socrates-only focus.')
        return self.snapshot()

    def save_settings(self, payload):
        self.store.save({'sizing_mode': 'target', 'target_dollars': f"{float(payload['target_dollars']):.2f}"})
        return self.store.settings()

    def reconcile_crypto(self, payload):
        return {**self.snapshot(), 'crypto_reconciliation': {'message': 'Fixture: no crypto incidents.'}}

    def chart_inputs(self):
        now = datetime.now(timezone.utc)
        strategies = self._strategies(now)
        return {'at': now, 'markets': {'QQQ': {'bars': {60: self.hourly, 240: self.four}}},
                'vix': {'bars': {15: self.vix}},
                'setup': {'levels': [z.__dict__ for z in self.levels], 'strategies': strategies,
                          'strategy_id': 'four_hour_retest', 'direction': strategies[0].get('direction')}}

    # -- snapshot -------------------------------------------------------------
    def snapshot(self):
        now = datetime.now(timezone.utc)
        with self.lock:
            self.state.update(self._state(now))
        result = super().snapshot()
        control = self.store.control()
        review = control.get('enabled') is True and control.get('policy') != POLICY_VERSION
        result.update(execution_available=True, execution_account_mode='live', review_required=review,
                      execution={'message': self._message(review), 'at': iso(now), 'trade': self._trade()},
                      execution_check={'captured_at': iso(now - timedelta(seconds=4)), 'persisted': True,
                                       'current_at_snapshot': True, 'from_current_process': True},
                      worker_health={'ready': True, 'workers': [{'name': name, 'status': 'running'} for name in
                                                                ('account', 'data', 'execution', 'crypto_execution')]})
        result['portfolio']['socrates']['execution_available'] = True
        result['portfolio']['range_reversal']['execution_available'] = True
        if self.contract:
            self._contract(result, now, review)
        return result

    def live_trade(self, state=None):
        """Fixture live view: the real builder over a synthetic PSQ trade whose price drifts with the clock."""
        import math
        from pivot.live_trade import build_live_trade
        now = datetime.now(timezone.utc)
        entered = now - timedelta(minutes=127)
        if self.scenario == 'sold':
            return {'live_trade': None, 'live_trade_error': None, 'last_trade': {
                'trade_id': 'fixture', 'label': 'Socrates short via PSQ (inverse QQQ)', 'symbol': 'PSQ', 'quantity': '0.402576',
                'entry_price': '37.26', 'entered_at': iso(entered), 'exit_price': '37.3900', 'completed_at': iso(now - timedelta(minutes=3)),
                'gross_pnl': '0.05', 'percent': '0.35', 'status': 'verified_gross',
                'exit_reason': 'Closing the position before the session ends', 'stop': '36.71', 'target': '38.02'}}
        if self.scenario not in ('psq', 'down'):
            return {'live_trade': None, 'last_trade': None, 'live_trade_error': None}
        sign = 1 if self.scenario == 'psq' else -1
        def bid(at):
            minutes = (at - entered).total_seconds() / 60
            return round(37.26 + sign * (0.0025 * minutes + 0.06 * math.sin(minutes / 6)), 2)
        trade = {**self._trade(), 'id': 'fixture-psq', 'stage': 'open', 'filled_qty': '0.402576', 'created_at': iso(entered),
                 'ops': {'entry': {'state': 'attempted', 'payload': {'client_order_id': 'fx-entry'},
                                   'last_seen': {'status': 'filled', 'filled_qty': '0.402576', 'filled_avg_price': '37.26',
                                                 'filled_at': iso(entered + timedelta(seconds=1))}},
                         'stop': {'state': 'attempted', 'payload': {'client_order_id': 'fx-stop'},
                                  'last_seen': {'status': 'new', 'filled_qty': '0'}}}}
        track = [{'trade_id': 'fixture-psq', 'symbol': 'PSQ', 'bid': str(bid(entered + timedelta(seconds=15 * i))),
                  'at': iso(entered + timedelta(seconds=15 * i))} for i in range(int((now - entered).total_seconds() // 15))]
        latest = bid(now)
        mark = {'trade_id': 'fixture-psq', 'symbol': 'PSQ', 'bid': str(latest), 'ask': f'{latest + 0.01:.2f}',
                'quote_at': iso(now - timedelta(seconds=2)), 'at': iso(now - timedelta(seconds=2))}
        clock = {'is_open': True, 'timestamp': iso(now), 'next_close': iso(now + timedelta(minutes=88))}
        live = build_live_trade(trade, mark=mark, track=track, clock=clock, orders=[{'client_order_id': 'fx-stop', 'status': 'new'}],
                                signal_quote={'bp': round(self.reference - sign * 0.6, 2), 't': iso(now)},
                                message='Managing PSQ (inverse-ETF proxy for the QQQ short): stop $36.71, target $38.02. Broker protection: new.',
                                now=now)
        return {'live_trade': live, 'last_trade': None, 'live_trade_error': None}

    def _message(self, review):
        if review:
            return 'Live money is off. Analysis continues.'
        return {'waiting': 'Waiting for a Nasdaq level event: price has not returned to a watched area.',
                'ready': 'Setup ready: checking the broker quote before the purchase.',
                'psq': 'Managing PSQ (inverse-ETF proxy for the QQQ short)',
                'paused': 'Socrates is paused. Existing positions continue their exits.',
                'closed': 'The regular market session is closed.'}.get(self.scenario, '')

    def _trade(self):
        if self.scenario not in ('psq', 'down'):
            return None
        return {'stage': 'protected', 'symbol': 'PSQ', 'direction': 'long', 'signal_symbol': 'QQQ', 'signal_direction': 'short',
                'proxy': 'inverse_etf', 'amount': '15.00', 'stop': '36.71', 'target': '38.02', 'reason': 'Four-hour break & retest short',
                'signal_geometry': {'symbol': 'QQQ', 'direction': 'short', 'entry': f'{self.reference:.2f}',
                                    'stop': f'{self.reference * 1.0042:.2f}', 'target': f'{self.reference * 0.9938:.2f}'},
                'proxy_geometry': {'symbol': 'PSQ', 'kind': 'inverse_etf', 'reference': '37.26', 'stop': '36.71', 'target': '38.02'}}

    def _state(self, now):
        open_ = self.scenario != 'closed'
        analysis = now - timedelta(seconds=18)
        day = now.astimezone(ET).date()
        next_open = session_time(day + timedelta(days=1 if day.weekday() < 4 else 7 - day.weekday()), 9, 30)
        positions = orders = []
        if self.scenario in ('psq', 'down'):
            positions = [{'symbol': 'PSQ', 'qty': '0.4025', 'side': 'long', 'asset_class': 'us_equity',
                          'market_value': '15.04', 'unrealized_pl': '0.04'}]
            orders = [{'symbol': 'PSQ', 'side': 'sell', 'qty': '0.4025', 'type': 'stop', 'status': 'new'}]
        frames = lambda at: [{'label': label, 'count': count, 'latest_at': iso(at), 'status': 'current',
                              'valid_until': iso(now + timedelta(minutes=5)), 'reason': ''}
                             for label, count in (('15-minute', 120), ('1-hour', 30), ('4-hour', 50), ('Daily', 60))]
        instruments = [{'symbol': symbol, 'status': 'current', 'observed_at': iso(now - timedelta(seconds=12)),
                        'valid_until': iso(now + timedelta(minutes=5)), 'frames': frames(analysis)}
                       for symbol in ('QQQ',) + LEADERS]
        leaders = self._leaders(now)
        return {
            'account': {'equity': '92.14', 'last_equity': '91.80', 'cash': '92.14', 'buying_power': '92.14',
                        'mode': 'live', 'status': 'ACTIVE', 'account_ref': 'fixture-account', 'shorting_enabled': False},
            'account_at': iso(now - timedelta(seconds=6)), 'account_error': None,
            'clock': {'is_open': open_, 'next_open': iso(next_open), 'next_close': iso(session_time(day, 16))},
            'positions': positions, 'orders': orders,
            'analysis_at': iso(analysis), 'setup': self._setup(now),
            'observations': [{'symbol': 'QQQ', 'price': self.reference}],
            'data_errors': [], 'feeds': {'stocks': 'alpaca iex', 'vix': 'insightsentry'},
            'data_health': {'ready': True, 'stocks': {'status': 'current', 'checked_at': iso(now - timedelta(seconds=12)),
                'coverage': 'QQQ and seven leaders', 'fetch_seconds': 2.4, 'refresh_mode': 'incremental',
                'frame_policy': 'Completed regular-session candles only.', 'instruments': instruments},
                'vix': {'status': 'current' if open_ else 'market_closed', 'candles_current': True,
                        'valid_until': iso(now + timedelta(minutes=10)), 'source': 'InsightSentry Free · actual Cboe VIX',
                        'bar_count': len(self.vix), 'latest_at': iso(self.vix[-1].end),
                        'verification': {'timeframe': '15-minute completed candles',
                                         'budget': {'used': 412, 'limit': 1000, 'actual_requests': 400, 'reserved': 12},
                                         'latest_value': round(self.vix[-1].close, 2), 'latest_value_at': iso(now - timedelta(seconds=50)),
                                         'next_refresh_at': iso(now + timedelta(minutes=9))}}},
            'quote': {'t': iso(now - timedelta(seconds=1)), 'bp': round(self.reference - 0.02, 2), 'ap': round(self.reference + 0.02, 2), 'bs': 3, 'as': 4} if open_ else None,
            'decision_trace': {'captured_at': iso(analysis), 'persisted': True, 'leaders': leaders,
                               'setup': {'leader_rule': {'minimum_agree': 4, 'maximum_opposing': 1, 'persistence_minutes': 60}}},
            'market_context': {'data_current': True, 'frames': [
                {'timeframe_minutes': 60, 'status': 'ready', 'direction': 'up', 'latest_bar_at': iso(self.hourly[-1].end),
                 'detail': 'Higher swing highs and higher swing lows on completed hourly candles.'},
                {'timeframe_minutes': 240, 'status': 'ready', 'direction': 'mixed', 'latest_bar_at': iso(self.four[-1].end),
                 'detail': 'The last four-hour swing low broke while highs keep rising.'}]},
            'input_archive': {'status': 'recording', 'captured_at': iso(now - timedelta(seconds=10))},
        }

    def _leaders(self, now):
        votes = {'ready': ['long'] * 5 + [None, None], 'psq': ['short'] * 4 + ['long', None, None]}.get(
            self.scenario, ['long', None, 'short', None, None, None, None])
        rows = {}
        for symbol, vote in zip(LEADERS, votes):
            rows[symbol] = {'vote': vote, 'reason': 'reaction at previous-day high' if vote else 'no reaction at a level',
                            'latest_bar_at': iso(now - timedelta(minutes=2)),
                            'observation_valid_until': iso(now + timedelta(minutes=6)),
                            'evidence_valid_until': iso(now + timedelta(minutes=40)),
                            'reaction_at': iso(now - timedelta(minutes=12)) if vote else None,
                            'observational_only': self.scenario == 'waiting'}
        return rows

    def _strategies(self, now):
        zone = self.event
        ready = self.scenario == 'ready'
        retest = {'id': 'four_hour_retest', 'label': 'Four-hour break & retest',
                  'state': 'SETUP_READY' if ready else 'AT_LEVEL' if self.scenario in ('psq',) else 'WATCHING',
                  'levels': [z.__dict__ for z in self.levels]}
        if zone is not None and self.scenario in ('ready', 'psq'):
            direction = 'long' if ready else 'short'
            risk = self.reference * 0.0042
            retest.update(event_zone=zone.__dict__, event_origin_at=iso(self.hourly[-4].end), event_at=iso(self.hourly[-1].end),
                          event_expires_at=iso(now + timedelta(hours=20)), direction=direction, entry=self.reference,
                          stop=round(self.reference - risk if ready else self.reference + risk, 2),
                          target=round(self.reference + 1.5 * risk if ready else self.reference - 1.48 * risk, 2),
                          leader_evidence_valid_until=iso(now + timedelta(minutes=40)),
                          leader_observation_valid_until=iso(now + timedelta(minutes=6)))
        passed = lambda name, detail, ok=True: {'name': name, 'passed': ok, 'detail': detail}
        if ready:
            retest['checks'] = [passed('Premarked levels', '10 watched four-hour areas.'),
                                passed('Nasdaq level event', 'Hourly break, then a return to the area.'),
                                passed('Magnificent Seven at their zones', '5 of 7 leaders up, none opposing.'),
                                passed('Actual VIX reaction', 'VIX turned down from its consolidation base.'),
                                passed('Trade plan', 'Target 1.5R away; stop beyond the area.')]
        else:
            retest['checks'] = [passed('Premarked levels', '10 watched four-hour areas.'),
                                passed('Nasdaq level event', 'Price has not returned to a watched area since the last break.', False)]
        sweep = {'id': 'previous_day_sweep', 'label': 'Previous-day sweep', 'state': 'WATCHING',
                 'levels': [{'low': round(self.reference * 1.006, 2), 'high': round(self.reference * 1.006, 2), 'source': 'previous-day high'},
                            {'low': round(self.reference * 0.991, 2), 'high': round(self.reference * 0.991, 2), 'source': 'previous-day low'}],
                 'checks': [passed('Premarked levels', 'Previous-day high and low are set.'),
                            passed('Nasdaq level event', 'No sweep of the previous-day high or low yet.', False)]}
        return [retest, sweep]

    def _setup(self, now):
        strategies = self._strategies(now)
        return {'strategy_id': 'four_hour_retest', 'strategies': strategies,
                'leader_rule': {'minimum_agree': 4, 'maximum_opposing': 1, 'persistence_minutes': 60}}

    # -- the 4.5.1 snapshot contract, as the backend produces it -------------
    def _contract(self, result, now, review):
        pause = {'paused': True, 'reason': 'Socrates-only focus.', 'source': 'code_default'}
        result['strategy_pause'] = {'range_reversal': pause}
        result['portfolio']['range_reversal']['paused'] = True
        result['crypto_execution'] = {**(result.get('crypto_execution') or {}), 'paused': True, 'message': PAUSE_MESSAGE}
        result['strategy_families']['range_reversal'] = {'family_id': 'range_reversal', 'label': '4H Range Reversal',
                                                         'state': 'PAUSED', 'detail': 'Paused: Socrates-only focus.'}
        result['socrates_readiness'] = self._readiness(now, review)

    def _readiness(self, now, review):
        scenario, closed = self.scenario, self.scenario == 'closed'
        item = lambda id_, label, status, detail: {'id': id_, 'label': label, 'status': status, 'detail': detail,
                                                   'needs_owner': status == 'fail'}
        live_on = not review
        allowance = self.store.session_entry_allowance()['families']['socrates']
        items = [
            item('live_permission', 'Live money', 'ok' if live_on else 'fail',
                 'On, and the current Socrates rules are accepted.' if live_on else
                 'Off: the Socrates rules changed in an update and need your review.'),
            item('strategy_enabled', 'Socrates selected', 'fail' if scenario == 'paused' else 'ok',
                 'Paused by the app after the broker rejected an order. Check Alpaca, then turn Socrates back on.'
                 if scenario == 'paused' else 'Selected to trade.'),
            item('account', 'Alpaca account', 'ok', 'Live account is active and allowed to trade.'),
            item('buying_power', 'Buying power', 'ok', '$92.14 available for the $15.00 purchase.'),
            item('instrument_qqq', 'QQQ (long trades)', 'ok', 'Active, tradable and fractionable.'),
            item('instrument_psq', 'PSQ (short trades)', 'ok', 'Active, tradable and fractionable. Shorts buy PSQ.'),
            item('market_session', 'Market hours', 'info' if closed else 'ok',
                 f"Closed. Next open {session_time(now.astimezone(ET).date() + timedelta(days=1), 9, 30).strftime('%a %-I:%M %p')} ET."
                 if closed else 'Open. New entries stop at 3:30 PM ET.'),
            item('data_qqq', 'QQQ candles', 'ok', '15-minute, hourly, four-hour and daily candles are current.'),
            item('data_leaders', 'Seven leaders', 'ok', 'All seven leaders have current five-minute candles.'),
            item('data_vix', 'Actual VIX', 'ok', 'Current 15-minute candles. 588 of 1,000 monthly requests left.'),
            item('entry_allowance', 'Entries left today', 'ok' if allowance['remaining'] else 'fail',
                 f"{allowance['remaining']} of 2 Socrates attempts left this session."),
            item('deployment', 'App updates', 'ok', 'No update is holding entries.'),
            item('exposure', 'Positions and orders', 'info' if scenario in ('psq', 'down') else 'ok',
                 'Managing one PSQ position; no new entry until it closes.' if scenario in ('psq', 'down') else
                 'No other QQQ or PSQ position or order.'),
            item('workers', 'App workers', 'ok', 'Account, data and order workers are running.'),
            item('setup', 'Current setup', 'ok' if scenario == 'ready' else 'info',
                 {'ready': 'Four-hour retest long is ready: leaders and VIX agree.',
                  'psq': 'Short setup taken; the position is being managed.',
                  'closed': 'Waiting for the next session.'}.get(scenario, 'Waiting for price to return to a watched level.')),
        ]
        failing = [row for row in items if row['status'] == 'fail']
        status = 'blocked' if failing else 'ready' if scenario == 'ready' else 'waiting'
        headline = {'blocked': 'Socrates cannot place orders until you act.',
                    'ready': 'Socrates has a ready setup and is checking the broker quote.',
                    'waiting': 'Market closed. Socrates will watch again at the next open.' if closed else
                    'Managing an open PSQ position.' if scenario in ('psq', 'down') else
                    'Everything is set. Socrates is waiting for a setup.'}[status]
        next_action = ('Accept the updated Socrates rules: click Live money' if review else
                       'Check Alpaca, then turn Socrates back on' if scenario == 'paused' else None)
        # An app pause names no control: Alpaca is checked before Socrates is turned back on.
        return {'checked_at': iso(now), 'status': status, 'headline': headline, 'next_action': next_action,
                'action': 'live' if review and scenario != 'paused' else None, 'items': items}


def build(root, scenario='waiting', contract=True):
    from fastapi.responses import HTMLResponse, RedirectResponse
    from starlette.routing import Route
    from pivot.api import create_app
    service = FixtureService(root, scenario, contract)
    app = create_app(service, background=False)

    async def listing(request):
        rows = ''.join(f'<li><a href="fixture/use/{name}">{name}</a> · {text}</li>' for name, text in SCENARIOS.items())
        return HTMLResponse(f'<!doctype html><title>Fixtures</title><h1>Pivot UI fixtures</h1><p>Current: '
                            f'{service.scenario}</p><ul>{rows}</ul><p><a href="./">Open the app</a></p>')

    async def use(request):
        name = request.path_params['name']
        if name not in SCENARIOS:
            return HTMLResponse('Unknown scenario', status_code=404)
        service.use(name, request.query_params.get('contract') != '0')
        return RedirectResponse('/', status_code=303)

    # Ahead of the static mount so '/' does not swallow the fixture routes.
    app.router.routes[0:0] = [Route('/fixture', listing), Route('/fixture/use/{name}', use)]
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--scenario', choices=sorted(SCENARIOS), default='waiting')
    parser.add_argument('--no-contract', action='store_true', help='omit the 4.5.1 readiness and pause fields')
    args = parser.parse_args()
    import uvicorn
    with tempfile.TemporaryDirectory(prefix='pivot-ui-fixture-') as root:
        uvicorn.run(build(root, args.scenario, not args.no_contract), host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
