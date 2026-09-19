"""Shared entry admission and durable allocation accounting across strategy engines.

The engines' durable trade ledgers are the reservation ledger: a prepared,
uncertain, open or exiting trade retains its allocation until finished. We never
infer that an uncertain POST released cash. No broker method is called here.
"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from threading import RLock, local
import fcntl
import os
import re
import stat

from .sizing import decimal
from .crypto_markets import ALIASES


class PortfolioBlocked(ValueError):
    """Entry admission is unavailable; existing position management continues."""


def canonical_symbol(symbol):
    """Only explicit known aliases are equivalent, never arbitrary punctuation."""
    if not isinstance(symbol, str) or not symbol:
        raise PortfolioBlocked('An account instrument could not be verified')
    return ALIASES.get(symbol, symbol)


class Portfolio:
    def __init__(self, store, *, lock_path=None):
        self.lock_path = Path(lock_path or (str(store.path) + '.portfolio.lock'))
        self._mutex, self._local = RLock(), local()
        self._sources = {}
        self._started = False
        self.enabled_predicate = lambda family: True
        self.register('socrates', lambda: [value] if (value := store.active_trade()) else [])

    def register(self, family, active_trades):
        """Register a fresh durable read, not an in-memory snapshot or broker view."""
        if (self._started or not isinstance(family, str)
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', family)
                or family in self._sources or not callable(active_trades)):
            raise ValueError('Invalid or late portfolio ledger registration')
        self._sources[family] = active_trades

    def family_enabled(self, family):
        try:
            return self.enabled_predicate(family) is True
        except Exception:
            return False

    @contextmanager
    def admit(self):
        """Lock order: deployment gate, portfolio admission, engine entry lock.

        Both fresh planning and recovery of an unsent intent use this gate. File
        locking also prevents a second local process from racing another engine.
        Position exits do not acquire it and remain available during an entry hold.
        """
        with self._mutex:
            if getattr(self._local, 'depth', 0):
                self._local.depth += 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            self._started = True
            fd = None
            try:
                try:
                    fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise OSError('Portfolio lock must be a regular file')
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise PortfolioBlocked('Another strategy is checking an entry; waiting for shared account admission') from None
                self._local.depth = 1
                yield
            finally:
                self._local.depth = 0
                if fd is not None:
                    os.close(fd)

    def active_trades(self):
        output = []
        try:
            for family, read in self._sources.items():
                rows = read()
                if not isinstance(rows, list):
                    raise ValueError
                for value in rows:
                    if (not isinstance(value, dict) or not isinstance(value.get('id'), str)
                            or not value['id'] or not isinstance(value.get('account_ref'), str)
                            or not value['account_ref'] or value.get('stage') == 'finished'):
                        raise ValueError
                    row = deepcopy(value)
                    row['symbol'] = canonical_symbol(row.get('symbol'))
                    row['family_id'] = family
                    output.append(row)
            if len({(row['family_id'], row['id']) for row in output}) != len(output):
                raise ValueError
        except Exception:
            raise PortfolioBlocked('A strategy ledger is unavailable; new entries are waiting for reconciliation') from None
        return output

    def reservations(self, account_ref):
        rows = []
        try:
            for trade in self.active_trades():
                if trade['account_ref'] != account_ref:
                    continue
                amount = decimal(trade['amount'])
                if amount <= 0:
                    raise ValueError
                rows.append({'family_id': trade['family_id'], 'trade_id': trade['id'],
                             'symbol': trade['symbol'], 'amount': str(amount), 'stage': trade['stage']})
        except Exception:
            raise PortfolioBlocked('A strategy allocation could not be verified; new entries are waiting') from None
        return rows

    def available(self, account_ref, broker_buying_power):
        """Conservative capital cap, deliberately retaining active allocations.

        Broker cash can already reflect fills. Subtracting retained allocations
        may leave additional cash unused, but never assumes a lagging broker
        snapshot has accounted for another strategy's POST or fill.
        """
        if not getattr(self._local, 'depth', 0):
            raise PortfolioBlocked('Shared account admission is required before planning a purchase')
        try:
            power = decimal(broker_buying_power)
            if power < 0:
                raise ValueError
            reserved = sum((decimal(row['amount']) for row in self.reservations(account_ref)), Decimal('0'))
        except Exception:
            raise PortfolioBlocked('Current shared buying power could not be verified') from None
        return str(max(Decimal('0'), power - reserved))

    @staticmethod
    def _owned_quantity(trade):
        """Terminal entry and recorded exit fills establish owned net quantity.

        Crypto ledgers may supply net_entry_qty after validated base-asset fees;
        the value must never exceed the broker's confirmed gross entry quantity.
        """
        entry = trade['ops']['entry']['last_seen']
        if entry['status'] not in ('filled', 'canceled', 'expired', 'rejected'):
            raise ValueError
        gross = decimal(entry['filled_qty'])
        net = decimal(trade.get('net_entry_qty', gross))
        if not 0 < net <= gross:
            raise ValueError
        sold = sum((decimal(op.get('last_seen', {}).get('filled_qty', '0'))
                    for name, op in trade['ops'].items() if name != 'entry'), Decimal('0'))
        if not 0 <= sold < net:
            raise ValueError
        return net - sold

    @staticmethod
    def _order_proven(order, trade):
        for op in trade['ops'].values():
            payload, seen = op.get('payload', {}), op.get('last_seen', {})
            if (op.get('state') == 'attempted' and order.get('id') == seen.get('id') and seen.get('id')
                    and order.get('client_order_id') == payload.get('client_order_id')
                    and order.get('side') == payload.get('side')
                    and order.get('status') == seen.get('status')
                    and all(key not in payload or decimal(order[key]) == decimal(payload[key])
                            for key in ('qty', 'stop_price', 'limit_price'))
                    and decimal(order.get('filled_qty', '0')) == decimal(seen.get('filled_qty', '0'))
                    and canonical_symbol(order.get('symbol')) == canonical_symbol(payload.get('symbol')) == trade['symbol']):
                return True
        return False

    def assert_exposure(self, account_ref, positions, orders, *, requesting_family, symbol, excluding_trade_id=None):
        """Permit another strategy's exact, protected exposure; block everything else.

        This is not a general exception for crypto positions. Unknown/manual
        holdings, mismatched quantities, pending entries, uncertain orders and
        overlapping instruments still stop new entries in either engine.
        """
        try:
            target = canonical_symbol(symbol)
            if not isinstance(positions, list) or not isinstance(orders, list):
                raise ValueError
            if requesting_family not in self._sources:
                raise ValueError
            trades = [row for row in self.active_trades() if row['account_ref'] == account_ref
                      and not (row['family_id'] == requesting_family and row['id'] == excluding_trade_id)]
            by_symbol = {}
            for row in trades:
                # A new entry cannot share any existing claim on its instrument.
                if row['symbol'] == target:
                    raise ValueError
                if row['symbol'] in by_symbol or row.get('stage') != 'open':
                    raise ValueError
                by_symbol[row['symbol']] = row
            seen_positions = set()
            for position in positions:
                key = canonical_symbol(position['symbol'])
                trade = by_symbol[key]
                if key in seen_positions or position.get('side') != trade['direction']:
                    raise ValueError
                quantity = self._owned_quantity(trade)
                if abs(decimal(position['qty'])) != quantity:
                    raise ValueError
                stop = trade['ops'][trade.get('stop_op', 'stop')]
                if (stop.get('state') != 'attempted'
                        or stop.get('last_seen', {}).get('status') not in ('new', 'partially_filled')):
                    raise ValueError
                # The fresh broker order collection must contain that protection.
                if not any(order.get('id') == stop['last_seen'].get('id') and self._order_proven(order, trade)
                           and order.get('status') in ('new', 'partially_filled')
                           and decimal(order['qty']) - decimal(order.get('filled_qty', '0')) == quantity
                           for order in orders):
                    raise ValueError
                seen_positions.add(key)
            if set(by_symbol) != seen_positions:
                raise ValueError
            seen_orders = set()
            for order in orders:
                if order.get('legs'):
                    raise ValueError  # Independent simple orders only; never ignore nested exposure.
                if order.get('id') in seen_orders or not self._order_proven(order, by_symbol[canonical_symbol(order['symbol'])]):
                    raise ValueError
                seen_orders.add(order['id'])
        except Exception:
            raise PortfolioBlocked('Existing account exposure needs reconciliation before another strategy can enter') from None
