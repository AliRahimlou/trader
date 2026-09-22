"""Account, analysis and order loops with separate locks and durable execution permission."""
import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone, timedelta
from threading import Event, Lock, Thread
from time import monotonic
from .rulebook import rulebook
from .strategy import analyze
from .market_context import market_context
from .sizing import decimal
from .data_health import stock_health, vix_health, quote_health, expire_health
from .diagnostics import build_decision_trace
from .observations import ObservationArchive
from .worker_health import WorkerHealth, next_tick
from . import feature_flags
logger = logging.getLogger('pivot.service')


def publication_catchup_bucket(markets, sessions, now):
    """Only a missing newest native candle earns one extra stock-only collection.

    Historic gaps, absent/invalid instruments, failed reads and closed sessions
    do not qualify. The window starts after 30 seconds of publication allowance
    and ends two minutes after a five-minute boundary.
    """
    from .models import MAG7, timestamp
    from .feeds import leader_history_sessions
    from .history_health import frame_gaps
    from .strategy import closed
    active = next((row for row in sessions.values()
                   if timestamp(row['open']) <= now < timestamp(row['close'])), None)
    if active is None:
        return None
    opening = timestamp(active['open'])
    bucket = opening + timedelta(seconds=int((now-opening).total_seconds()//300)*300)
    if bucket == opening or not 30 <= (now-bucket).total_seconds() <= 120:
        return None
    lagging = False
    for symbol in ('QQQ', *MAG7):
        market = markets.get(symbol)
        minutes = 15 if symbol == 'QQQ' else 5
        if (market is None or market.symbol != symbol or not market.realtime
                or market.source not in ('alpaca_iex', 'alpaca_sip')
                or not 0 <= (now-market.observed_at).total_seconds() < 90):
            return None
        bars = closed(market, minutes, now)
        if not bars:
            return None
        calendar = sessions if symbol == 'QQQ' else leader_history_sessions(sessions)
        gaps = frame_gaps(bars, calendar, minutes, now+timedelta(seconds=90))
        expected = opening + timedelta(minutes=int((now-opening).total_seconds()//(minutes*60))*minutes)
        if gaps['missing_count']:
            if (gaps['missing_count'] != 1 or expected <= opening
                    or timestamp(gaps['first_missing_at']) != expected):
                return None
            lagging = True
    return bucket.isoformat() if lagging else None


class Service:
    def __init__(self, feeds, store, broker=None):
        self.feeds, self.store = feeds, store
        self.lock, self.stop_event = Lock(), Event()
        self.threads = []
        self._stock_catchup_buckets = set()
        # Latest validated analysis inputs (Market objects, not JSON) for chart
        # rendering; kept outside self.state, which is serialised. See chart_inputs().
        self.latest_inputs = None
        from .execution import Executor
        self.executor = Executor(broker, store) if broker else None
        from .portfolio import Portfolio
        from .crypto_store import CryptoStore
        self.crypto_store = CryptoStore(store.path)
        self.portfolio = Portfolio(store)
        self.portfolio.register('range_reversal', self.crypto_store.active_trades)
        self.portfolio.enabled_predicate = lambda family: (self.store.strategy_selection()['socrates']
            if family == 'socrates' else self.crypto_store.control()['enabled'])
        if self.executor:
            self.executor.portfolio = self.portfolio
        self.crypto_executor = None
        from .broker import AlpacaBroker
        if isinstance(broker, AlpacaBroker):
            from .crypto_broker import CryptoBroker
            from .crypto_execution import CryptoRangeExecutor
            self.crypto_executor = CryptoRangeExecutor(CryptoBroker(feeds), self.crypto_store, store, self.portfolio)
        self.workers = WorkerHealth(['account', 'data'] + (['execution'] if self.executor else [])
                                    + (['crypto_execution'] if self.crypto_executor else []))
        self.archive = None
        self.native_capture = None
        self.range_watch = None
        self.range_watch_error = None
        self.extra_range_watches = {}
        self.extra_range_errors = {}
        # Decided once at start: a paused release never builds or starts the
        # crypto observers, so no crypto market data is polled. The crypto
        # execution worker still runs to manage any stranded position.
        self.crypto_observers_paused = feature_flags.crypto_paused()
        self.activity_ledger = None
        self.daily_reviews = None
        self.review_collection = {'status': 'starting', 'last_attempt_at': None,
                                  'last_success_at': None}
        try:
            from .activity_ledger import ActivityLedger
            from .daily_review import DailyReviewStore
            self.activity_ledger = ActivityLedger(str(store.path) + '.activities.sqlite3')
            self.daily_reviews = DailyReviewStore(str(store.path) + '.daily-reviews.sqlite3')
        except Exception:
            self.review_collection['status'] = 'unavailable'
        try:
            self.archive = ObservationArchive(str(store.path) + '.observations.sqlite3')
        except Exception:
            pass  # Observation failure is visible below, independent of trading permission.
        from .feeds import ReadOnlyFeeds
        if isinstance(feeds, ReadOnlyFeeds) and not self.crypto_observers_paused:
            from .range_watch import BitcoinBars, RangeWatch
            from .crypto_markets import SYMBOLS
            try:
                self.range_watch = RangeWatch(BitcoinBars(feeds.alpaca_headers), str(store.path) + '.range-observations.sqlite3')
            except Exception:
                self.range_watch_error = 'Bitcoin observation worker could not be initialized.'
            for symbol in SYMBOLS[1:]:
                try:
                    self.extra_range_watches[symbol] = RangeWatch(
                        BitcoinBars(feeds.alpaca_headers, symbol=symbol),
                        str(store.path) + '.' + symbol.split('/')[0].lower() + '-range-observations.sqlite3')
                except Exception:
                    self.extra_range_errors[symbol] = symbol + ' data worker could not be initialized.'
        if isinstance(feeds, ReadOnlyFeeds) and feeds.vix_provider == 'insightsentry':
            try:
                from .native_capture import NativeCapture
                self.native_capture = NativeCapture(feeds, str(store.path) + '.native-history')
            except Exception:
                pass  # Optional historical evidence cannot disable production workers.
        self.state = {'account':None, 'positions':[], 'orders':[], 'clock':None, 'account_at':None,
                      'analysis_at':None, 'setup':None, 'account_error':None, 'data_errors':[], 'observations':[], 'feeds':{},
                      'live_enabled':False, 'execution_available':False, 'data_health':None, 'data_valid_until':None, 'quote':None, 'quote_at':None, 'quote_error':None,
                      'decision_trace':None, 'diagnostic_error':None, 'market_context':None,
                      'input_archive':{'status':'starting' if self.archive else 'unavailable'},
                      'crypto_worker_error': None,
                      'worker_incidents':[]}
        try:
            self.state['decision_trace'] = self.store.latest_decision()
        except Exception:
            self.state['diagnostic_error'] = 'Saved decision trace unavailable; current analysis remains separate'

    def start(self):
        if self.threads:
            raise RuntimeError('Workers already started')
        loops = [('account', self.refresh_account, 15), ('data', self.refresh_analysis, 60)]
        if self.executor: loops.append(('execution', self.refresh_execution, 5))
        if self.crypto_executor: loops.append(('crypto_execution', self.refresh_crypto_execution, 10))
        for name, function, delay in loops:
            thread=Thread(target=self._loop,args=(name,function,delay),name=f'pivot-{name}',daemon=True)
            self.threads.append(thread)
            thread.start()
        monitor = Thread(target=self._monitor_loop, name='pivot-monitor', daemon=True)
        self.threads.append(monitor)
        monitor.start()
        # Read-only accounting is independent of order management and container
        # readiness: a reporting outage must not restart a protective exit worker.
        if self.activity_ledger is not None and callable(getattr(self.feeds, 'get', None)):
            review_thread = Thread(target=self._review_loop, name='pivot-daily-review', daemon=True)
            self.threads.append(review_thread)
            review_thread.start()
        if self.range_watch:
            try:
                self.range_watch.start(self.stop_event)
            except Exception:
                self.range_watch_error = 'Bitcoin observation worker could not be started.'
        for watch in self.extra_range_watches.values():
            try:
                watch.start(self.stop_event)
            except Exception:
                watch.error = 'This crypto data worker could not be started.'

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=1)
        if self.native_capture and self.native_capture.thread:
            self.native_capture.thread.join(timeout=1)
        if self.range_watch and self.range_watch.thread and self.range_watch.thread.is_alive():
            self.range_watch.thread.join(timeout=1)
        for watch in self.extra_range_watches.values():
            if watch.thread and watch.thread.is_alive():
                watch.thread.join(timeout=1)

    def _loop(self,name,function,delay):
        due = monotonic()
        while not self.stop_event.is_set():
            self.workers.begin(name)
            failed = False
            try:
                function()
            except Exception:
                failed = True
                logger.exception('%s worker cycle failed', name)
                with self.lock:
                    if name == 'crypto_execution':
                        self.state['crypto_worker_error'] = 'Crypto execution needs attention; check its orders and positions.'
                    else:
                        self.state['data_errors']=['Data update failed; waiting for a validated snapshot']
            finally:
                self.workers.finish(name, failed)
            due = next_tick(due, delay, monotonic())
            self.stop_event.wait(max(0, due-monotonic()))

    def worker_health(self):
        alive = {thread.name.removeprefix('pivot-') for thread in self.threads if thread.is_alive()}
        return self.workers.snapshot(alive)

    def _monitor_loop(self):
        previous = None
        while not self.stop_event.wait(5):
            health = self.worker_health()
            incidents = [{'worker': row['name'], 'status': row['status']} for row in health['workers']
                         if row['status'] in ('stalled', 'stopped', 'error')]
            with self.lock:
                self.state['worker_incidents'] = incidents
            if incidents != previous:
                try:
                    if incidents or previous:
                        self.store.event('worker_attention' if incidents else 'workers_recovered', {'workers': incidents})
                    previous = incidents
                except Exception:
                    pass  # Health endpoint and in-memory incidents remain available on ledger failure.

    def refresh_account(self):
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                quote_future = pool.submit(self.feeds.quote) if hasattr(self.feeds, 'quote') else None
                values=list(pool.map(lambda fn:fn(),[self.feeds.account,self.feeds.positions,self.feeds.orders,self.feeds.clock]))
                quote, quote_error = None, None
                if quote_future:
                    try: quote = quote_future.result()
                    except Exception: quote_error = 'Latest QQQ quote could not be refreshed'
            with self.lock:
                self.state.update(account=values[0],positions=values[1],orders=values[2],clock=values[3],
                                  account_at=datetime.now(timezone.utc).isoformat(),account_error=None,
                                  quote=quote, quote_at=datetime.now(timezone.utc).isoformat() if quote else None, quote_error=quote_error)
            if self.activity_ledger is not None and values[0].get('account_ref'):
                try:
                    self.activity_ledger.record_equity(values[0]['account_ref'], values[0],
                                                       datetime.now(timezone.utc))
                    with self.lock:
                        self.review_collection['equity_status'] = 'current'
                except Exception:
                    with self.lock:
                        self.review_collection['equity_status'] = 'unavailable'
        except Exception:
            with self.lock:
                self.state['account_error']='Account update unavailable; showing the last confirmed snapshot'

    def _review_loop(self):
        while not self.stop_event.is_set():
            self.refresh_daily_review()
            with self.lock:
                ready = self.state.get('account_at') is not None
            self.stop_event.wait(300 if ready else 30)

    def refresh_daily_review(self, now=None):
        """Refresh broker evidence off the HTTP and trading paths, then save reports.

        Rebuild recent completed days too: crypto fees can arrive after midnight.
        A revision records new evidence, never an automatic change to live rules.
        """
        from zoneinfo import ZoneInfo
        from .models import timestamp
        from .daily_review import build_review
        now = now or datetime.now(timezone.utc)
        with self.lock:
            account = deepcopy(self.state.get('account')) or {}
            account_at = self.state.get('account_at')
            account_error = self.state.get('account_error')
            self.review_collection.update(status='refreshing', last_attempt_at=now.isoformat(),
                                          account_ref=account.get('account_ref'))
        try:
            if (self.activity_ledger is None or self.daily_reviews is None
                    or not account.get('account_ref') or account_error or not account_at
                    or not 0 <= (now - timestamp(account_at)).total_seconds() <= 90):
                raise ValueError('Fresh account identity is unavailable')
            account_ref = account['account_ref']
            collected = self.activity_ledger.refresh(self.feeds, account_ref, now)
            local_day = now.astimezone(ZoneInfo('America/New_York')).date()
            days = {(local_day - timedelta(days=offset)).isoformat() for offset in range(8)}
            from datetime import date
            for changed_day in collected.get('changed_days', []):
                changed = date.fromisoformat(changed_day)
                if local_day - timedelta(days=364) <= changed <= local_day:
                    days.add(changed.isoformat())
            for day in sorted(days):
                accounting = self.activity_ledger.summary(account_ref, day, now)
                report = build_review(self.store, self.crypto_store, accounting, account_ref, day, now)
                self.daily_reviews.save(account_ref, report)
            with self.lock:
                if (self.state.get('account') or {}).get('account_ref') != account_ref:
                    self.review_collection['status'] = 'account_changed'
                elif collected.get('status') == 'current' and collected.get('data_complete'):
                    self.review_collection.update(status='current', last_success_at=now.isoformat())
                else:
                    self.review_collection['status'] = collected.get('status', 'unavailable')
        except Exception:
            with self.lock:
                self.review_collection['status'] = 'unavailable'

    def daily_review(self, day=None, *, account_snapshot=None):
        """Return cached private evidence only; viewing a report makes no broker call."""
        from datetime import date
        from zoneinfo import ZoneInfo
        now = datetime.now(timezone.utc)
        day = now.astimezone(ZoneInfo('America/New_York')).date().isoformat() if day is None else day
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('Use a review date in YYYY-MM-DD format')
        with self.lock:
            collection = deepcopy(self.review_collection)
            context = self.state if account_snapshot is None else account_snapshot
            account = deepcopy(context.get('account')) or {}
            account_error = context.get('account_error')
            account_at = context.get('account_at')
        collection_ref = collection.pop('account_ref', None)
        if collection_ref != account.get('account_ref'):
            collection = {'status': 'waiting_account', 'last_attempt_at': None,
                          'last_success_at': None}
        report = None
        if self.daily_reviews is not None and account.get('account_ref'):
            try:
                report = self.daily_reviews.get(account['account_ref'], day)
            except Exception:
                collection['status'] = 'unavailable'
        if collection.get('last_success_at'):
            try:
                if (now - datetime.fromisoformat(collection['last_success_at'])).total_seconds() > 660:
                    collection['status'] = 'overdue'
            except (ValueError, TypeError):
                collection['status'] = 'unavailable'
        if account_error:
            collection['account_status'] = 'unavailable'
        else:
            from .models import timestamp
            try:
                if not 0 <= (now - timestamp(account_at)).total_seconds() <= 90:
                    collection['account_status'] = 'stale'
            except (ValueError, TypeError, OverflowError):
                collection['account_status'] = 'unavailable'
        return {**(report or {'schema': 'daily-trading-review-v1', 'status': 'unavailable', 'day': day,
                             'timezone': 'America/New_York', 'families': {},
                             'missing_evidence': ['A saved daily review is not available yet.']}),
                'collection': collection}

    def daily_review_history(self, limit=30):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Review limit must be between 1 and 100')
        with self.lock:
            account = deepcopy(self.state.get('account')) or {}
        if self.daily_reviews is None or not account.get('account_ref'):
            return {'status': 'unavailable', 'reviews': []}
        return {'status': 'available', 'reviews': self.daily_reviews.history(account['account_ref'], limit=limit)}

    def refresh_analysis(self):
        now=datetime.now(timezone.utc)
        errors,markets,vix=[],{},None
        stock_error=vix_error=None
        from .feeds import FeedError
        with ThreadPoolExecutor(max_workers=2) as pool:
            sf = pool.submit(self.feeds.stocks,now)
            insight = getattr(self.feeds, 'vix_provider', None) == 'insightsentry'
            vf = None if insight else pool.submit(self.feeds.vix,now)
            try: markets=sf.result()
            except Exception as exc:
                stock_error=str(exc) if isinstance(exc,FeedError) else 'Stock data could not be validated'
                errors.append(stock_error)
            if markets and not stock_error:
                catchup_at = datetime.now(timezone.utc)
                bucket = publication_catchup_bucket(markets, getattr(self.feeds, 'stock_sessions', {}), catchup_at)
                with self.lock:
                    # One data worker normally calls this; reserve atomically for manual callers too.
                    attempt = bucket is not None and bucket not in self._stock_catchup_buckets
                    if attempt:
                        self._stock_catchup_buckets.add(bucket)
                        self._stock_catchup_buckets = set(sorted(self._stock_catchup_buckets)[-288:])
                if attempt:
                    if self.stop_event.wait(5):
                        return
                    now = datetime.now(timezone.utc)
                    try:
                        markets = self.feeds.stocks(now)
                    except Exception as exc:
                        # Do not relabel the first read after a failed catch-up as freshly collected.
                        markets = {}
                        stock_error = str(exc) if isinstance(exc, FeedError) else 'Stock catch-up could not be validated'
                        errors.append(stock_error)
            if insight:
                # The actual exchange calendar is a dependency of VIX coverage.
                vf = pool.submit(self.feeds.vix,datetime.now(timezone.utc))
            try: vix=vf.result()
            except Exception as exc:
                vix_error=str(exc) if isinstance(exc,FeedError) else 'data could not be validated'
                errors.append('VIX: '+vix_error)
        observations=[]
        for symbol,market in markets.items():
            minutes=15 if symbol=='QQQ' else 5
            bars=[b for b in market.bars.get(minutes,[]) if b.end<=now]
            if bars:
                observations.append({'symbol':symbol,'price':bars[-1].close,'at':bars[-1].end.isoformat(),
                                     'kind':f'{minutes}-minute close; observation only'})
        checked_at=datetime.now(timezone.utc)
        stocks=stock_health(markets, getattr(self.feeds, 'stock_sessions', {}), checked_at, error=stock_error,
            fetch_seconds=getattr(self.feeds, 'stock_fetch_seconds', None), refresh_mode=getattr(self.feeds, 'stock_refresh_mode', None),
            full_at=getattr(self.feeds, 'stock_last_full_at', None),
            leader_daily_error=getattr(self.feeds, 'stock_leader_daily_error', None))
        index=vix_health(vix, checked_at, error=vix_error, details=getattr(self.feeds, 'vix_diagnostics', None))
        if markets and stocks['status'] != 'current' and not stock_error:
            errors.append('One or more required stock histories are missing, incomplete or stale')
        qqq=markets.get('QQQ')
        ready=stocks['status']=='current' and index['status']=='current'
        deadlines=[now+timedelta(seconds=90), *[m.observed_at+timedelta(seconds=90) for m in markets.values()]]
        if stocks.get('valid_until'): deadlines.append(datetime.fromisoformat(stocks['valid_until']))
        if index['valid_until']: deadlines.append(datetime.fromisoformat(index['valid_until']))
        setup = analyze(qqq, markets, vix, checked_at) if qqq else None
        sessions = getattr(self.feeds, 'stock_sessions', {})
        with self.lock:
            self.state.update(data_health={'stocks':stocks, 'vix':index, 'ready':ready}, data_valid_until=min(deadlines).isoformat() if ready else None, analysis_at=checked_at.isoformat(),setup=setup, observations=observations,data_errors=errors,
                market_context=market_context(qqq, checked_at),
                feeds={'stocks':qqq.source if qqq else 'unavailable',
                       'vix':'current' if index['status']=='current' else 'unavailable or delayed'})
            if markets and not stock_error:
                # Plain attributes, never JSON state: QQQ frames 15/60/240/1440, leaders 5/1440,
                # the VIX market (or None), the exchange calendar and the analysis result.
                self.latest_inputs = {'at': checked_at, 'markets': markets, 'vix': vix,
                                      'sessions': sessions, 'setup': setup}
        self._record_decision(markets, vix, checked_at)
        if ready and self.threads and not self.stop_event.is_set() and self.native_capture:
            # Optional history collection has its own bounded daemon and budget;
            # it never delays the account, strategy or order-management loops.
            try:
                self.native_capture.start(getattr(self.feeds, 'stock_sessions', {}), checked_at)
            except Exception:
                pass

    def chart_inputs(self):
        """Deep copy of the latest validated analysis inputs, or None before the first success.

        Read-only evidence for chart rendering ('look left' at the levels the
        app holds); it carries no trading authority and never touches state.
        """
        with self.lock:
            return deepcopy(self.latest_inputs)

    def _record_decision(self, markets, vix, checked_at):
        """Observational only: failures cannot modify the setup or execution permission."""
        try:
            with self.lock:
                state = deepcopy(self.state)
            trace = build_decision_trace(state['setup'], markets, vix, checked_at,
                data_health=state['data_health'], live_permission=self.executor.enabled() if self.executor else False,
                execution_available=self.executor is not None, broker_clock=state.get('clock'),
                account_at=state.get('account_at'))
        except Exception:
            with self.lock:
                self.state['diagnostic_error'] = 'Current decision evidence could not be built; saved history may be older than current analysis.'
                self.state['input_archive'] = {'status':'unavailable',
                    'detail':'Current decision evidence is unavailable; complete inputs could not be archived.'}
            return
        try:
            saved = self.store.record_decision(trace)
            with self.lock:
                self.state.update(decision_trace=saved, diagnostic_error=None)
        except Exception:
            with self.lock:
                self.state['diagnostic_error'] = ('Decision trace could not be saved; latest saved trace may be older '
                                                  'than current analysis. Trading rules and permission are unchanged.')
        # The separate input archive must still capture evidence when the decision
        # ledger is busy or unwritable. Settings are optional observational context.
        try:
            if self.archive is None:
                raise ValueError('Archive unavailable')
            try:
                settings = self.store.settings()
            except Exception:
                settings = None
            archived = self.archive.record(markets, vix, checked_at, trace=trace,
                sessions=getattr(self.feeds, 'stock_sessions', {}), settings=settings,
                revision=getattr(self.executor, 'revision', '') if self.executor else '')
            with self.lock:
                self.state['input_archive'] = archived
        except Exception:
            with self.lock:
                self.state['input_archive'] = {'status':'unavailable',
                    'detail':'Complete input history could not be saved. Check archive storage; decision summaries remain separate.'}

    def refresh_execution(self):
        with self.lock: state = deepcopy(self.state)
        self.executor.tick(state)

    def refresh_crypto_execution(self):
        self.crypto_executor.tick(self.range_analyses())
        with self.lock:
            self.state['crypto_worker_error'] = None

    def reconcile_crypto(self, payload):
        if payload != {}:
            raise ValueError('Crypto reconciliation accepts no settings or order instructions')
        if not self.crypto_executor:
            raise ValueError('The crypto broker connection is not configured')
        result = self.crypto_executor.recover_incidents()
        return {**self.snapshot(), 'crypto_reconciliation':result}

    def set_live(self, payload):
        if not self.executor:
            raise ValueError('Broker execution is not configured')
        with self.portfolio.admit():
            self.executor.set_live(payload)
        return self.snapshot()

    def snapshot(self):
        with self.lock: result=deepcopy(self.state)
        if result['decision_trace']:
            trace = result['decision_trace']
            # A restored or stalled trace describes its original checkpoint, not present readiness.
            try:
                captured_at = datetime.fromisoformat(trace['captured_at'])
                if captured_at.tzinfo is None or captured_at.utcoffset() is None:
                    raise ValueError('Decision trace timestamp must include timezone')
                age = (datetime.now(timezone.utc)-captured_at).total_seconds()
                trace['matches_current_analysis'] = trace['captured_at'] == result['analysis_at']
                trace['current_at_snapshot'] = bool(trace['matches_current_analysis'] and not result['diagnostic_error'] and 0 <= age <= 90)
            except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                if not isinstance(trace, dict):
                    trace = result['decision_trace'] = {}
                trace.update(matches_current_analysis=False, current_at_snapshot=False)
                result['diagnostic_error'] = 'Saved decision trace timestamp is invalid; current analysis remains separate'
        expire_health(result['data_health'], datetime.now(timezone.utc))
        if result['data_health'] and result['data_health']['vix']['status'] != 'current':
            result['feeds']['vix']='unavailable or delayed'
        result['quote_health'] = quote_health(result['quote'], datetime.now(timezone.utc),
            market_open=bool((result.get('clock') or {}).get('is_open')), error=result['quote_error'])
        from .policy import POLICY
        if self.executor: result.update(self.executor.snapshot())
        control = self.store.control()
        from .policy import POLICY_VERSION
        global_live = control.get('enabled') is True and control.get('policy') == POLICY_VERSION
        result['live_enabled'] = global_live
        result['portfolio'] = self.portfolio_snapshot(global_live)
        try:
            result['entry_allowance'] = self.store.session_entry_allowance()
        except Exception:
            # Reporting cannot interfere with existing position supervision.
            result['entry_allowance'] = {'status': 'blocked', 'limit': 2, 'used': None,
                'remaining': 0, 'families': None, 'scope': 'per_family', 'timezone': 'America/New_York',
                'reason': 'Session entry allowance could not be verified.'}
        pause = feature_flags.strategy_pause()
        crypto_paused = pause['range_reversal']['paused']
        result['strategy_pause'] = pause
        result['portfolio']['range_reversal']['paused'] = crypto_paused
        result['crypto_execution'] = self.crypto_executor.snapshot() if self.crypto_executor else {
            'message':'Crypto broker execution is not configured for this runtime.', 'at':None, 'trades':[], 'incidents':[]}
        result['crypto_execution']['paused'] = crypto_paused
        if crypto_paused:
            result['crypto_execution']['message'] = feature_flags.CRYPTO_PAUSED_MESSAGE
        result['worker_health'] = self.worker_health()
        result['strategy_families'] = {
            'socrates': {'family_id':'socrates', 'label':'Socrates', 'execution_status':'owner_controlled',
                         'analysis':deepcopy(result.get('setup'))},
            'range_reversal': ({'family_id':'range_reversal', 'label':'4H Range Reversal', 'state':'PAUSED',
                                'detail':'Paused: Socrates-only focus.'} if crypto_paused
                               else {**self.range_snapshot(), 'analyses':self.range_analyses()})}
        result['native_history'] = self.native_capture.status() if self.native_capture else {
            'status':'unavailable', 'research_only':True, 'live_entry_ready':False,
            'detail':'Separate native validation history is not configured.'}
        try:
            result['session_review'] = self.store.session_review()
        except Exception:
            result['session_review'] = {'status':'unavailable', 'historical_only':True}
        result['daily_review'] = self.daily_review(account_snapshot=result)
        result.update(settings=self.store.settings(), rulebook=rulebook(), events=self.store.events(),
                      trade_results=self.store.trade_results(),
                      execution_policy={**POLICY, 'summary': [
                          'Live money is the master entry switch for enabled strategies. Viewing another strategy does not stop entries or position management.',
                          'Each strategy uses its own saved purchase target. Turning a strategy off stops its new entries; existing positions continue their exits.',
                          'Each strategy has its own limit of two new entry attempts per New York session; rejected and uncertain submissions count, exits do not.',
                          'A rejected, replaced or unreconciled QQQ order pauses Socrates only (the same durable change as turning it off). Global Live and the crypto strategy are unchanged; enable Socrates again after reviewing Alpaca.',
                          *['Socrates: ' + line for line in POLICY['summary']]]}, version='video-execution-v5', runtime='Video strategies · owner-controlled execution', legacy_loaded=False)
        return result

    def range_snapshot(self):
        """Optional observations cannot fail the live account/strategy response."""
        from .range_reversal import analyze as range_analysis
        error = self.range_watch_error
        if self.range_watch and not error:
            try:
                return self.range_watch.snapshot()
            except Exception:
                error = 'Bitcoin observations are temporarily unavailable.'
        result = range_analysis(None, datetime.now(timezone.utc))
        if error:
            result['detail'] = error
        result['execution_note'] = 'Execution uses this strategy’s saved permission, purchase amount and current broker checks.'
        return result

    def range_analyses(self):
        from .range_reversal import analyze as range_analysis
        result = {'BTC/USD':self.range_snapshot()}
        for symbol, error in self.extra_range_errors.items():
            result[symbol] = {**range_analysis(None, datetime.now(timezone.utc)), 'symbol':symbol, 'detail':error}
        for symbol, watch in self.extra_range_watches.items():
            try:
                result[symbol] = watch.snapshot()
            except Exception:
                result[symbol] = range_analysis(None, datetime.now(timezone.utc))
        return result

    def portfolio_snapshot(self, global_live=None):
        from .crypto_execution import POLICY as CRYPTO_POLICY
        from .crypto_markets import MARKETS
        from .policy import POLICY_VERSION
        control = self.store.control()
        if global_live is None:
            global_live = control.get('enabled') is True and control.get('policy') == POLICY_VERSION
        crypto = self.crypto_store.control()
        selection = self.store.strategy_selection()
        with self.lock:
            state = deepcopy(self.state)
        settings_exposure_verified = False
        try:
            if not state['account_error'] and state['account_at'] and 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(state['account_at'])).total_seconds() <= 60:
                if state['positions'] or state['orders']:
                    self.portfolio.assert_exposure(state['account'].get('account_ref'), state['positions'], state['orders'],
                        requesting_family='socrates', symbol='QQQ')
                settings_exposure_verified = self.store.active_trade() is None
        except Exception:
            pass
        return {'global_live_enabled':global_live,
                'socrates':{'enabled':selection['socrates'], 'target_dollars':self.store.settings()['target_dollars'],
                            # Same rule as the top-level review flag: a saved permission under an older policy
                            # version shows 'Review updated rules' on the card, accepted through the Live switch.
                            'review_required':control.get('enabled') is True and control.get('policy') != POLICY_VERSION,
                            'execution_available':self.executor is not None, 'settings_exposure_verified':settings_exposure_verified},
                'range_reversal':{**{key:crypto[key] for key in ('enabled','target_dollars','symbols')},
                    'policy_version':CRYPTO_POLICY['version'], 'policy_summary':CRYPTO_POLICY['summary'],
                    'review_required':crypto['enabled'] and crypto.get('policy') != CRYPTO_POLICY['version'],
                    'execution_available':self.crypto_executor is not None, 'route':'alpaca_crypto_spot',
                    'watch_markets':deepcopy(MARKETS),
                    'capabilities':{'long':True, 'short':False}},
                'allocation_note':'Each active trade retains its allocation until closed. Both strategies reserve shared buying power before buying.'}

    def save_strategies(self, payload):
        """One atomic owner setting change; views never call this method."""
        import json
        from .crypto_execution import POLICY_VERSION as CRYPTO_POLICY_VERSION
        from .crypto_store import SYMBOLS
        from .execution import Executor
        if not isinstance(payload, dict) or not payload or set(payload) - {'socrates','range_reversal'}:
            raise ValueError('Choose a valid strategy setting')
        if 'range_reversal' in payload and feature_flags.crypto_paused():
            # Refused before any read or write: saved crypto rows stay exactly as they are.
            raise feature_flags.CryptoPaused()
        stock = payload.get('socrates')
        if stock is not None and (not isinstance(stock, dict) or set(stock) != {'enabled'} or type(stock['enabled']) is not bool):
            raise ValueError('Socrates permission must be on or off')
        if stock is not None and stock['enabled'] and self.executor is None:
            raise ValueError('The Socrates broker connection is not configured')
        crypto = payload.get('range_reversal')
        if crypto is not None and (not isinstance(crypto, dict) or not crypto
                or set(crypto) - {'enabled','target_dollars','symbols','policy_version'}):
            raise ValueError('Invalid range-reversal settings')
        with self.portfolio.admit():
            updated = self.crypto_store.control()
            if crypto is not None:
                if 'enabled' in crypto and type(crypto['enabled']) is not bool:
                    raise ValueError('Range-reversal permission must be on or off')
                updated.update({k:v for k,v in crypto.items() if k != 'policy_version'})
                if 'symbols' in crypto:
                    symbols = crypto['symbols']
                    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= len(SYMBOLS)
                            or any(not isinstance(s,str) or s not in SYMBOLS for s in symbols) or len(set(symbols)) != len(symbols)):
                        raise ValueError('Select one or more supported crypto markets')
                amount = decimal(updated['target_dollars'])
                if amount < 1 or amount != amount.quantize(decimal('.01')):
                    raise ValueError('Enter a crypto purchase target of at least $1, in cents')
                if updated['enabled']:
                    old = self.crypto_store.control()
                    if 'policy_version' in crypto and crypto['policy_version'] != CRYPTO_POLICY_VERSION:
                        raise ValueError('Review the current crypto execution rules')
                    if (not old['enabled'] or old.get('policy') != CRYPTO_POLICY_VERSION) and crypto.get('policy_version') != CRYPTO_POLICY_VERSION:
                        raise ValueError('Review the crypto execution rules before enabling this strategy')
                    if not self.crypto_executor:
                        raise ValueError('The crypto broker connection is not configured')
                    account = self.crypto_executor.broker.account()
                    Executor._account_ready(account)
                    if account.get('mode') != 'live' or account.get('crypto_status') != 'ACTIVE' or not account.get('account_ref'):
                        raise ValueError('A verified active live crypto account is required')
                    master = self.store.control()
                    if master.get('enabled') and master.get('account_ref') != account['account_ref']:
                        raise ValueError('The master Live account changed. Turn global Live Off and review the connected account before enabling crypto.')
                    if old['enabled'] and old.get('account_ref') != account['account_ref']:
                        raise ValueError('Crypto account changed. Turn this strategy off and review the new account first.')
                    updated.update(policy=CRYPTO_POLICY_VERSION, account_ref=account['account_ref'])
                    # Broker affordability is checked per order. Enabling does not
                    # increase the amount or place an order inside this request.
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if stock is not None:
                    db.execute('UPDATE strategy_selection SET body=? WHERE id=1', (json.dumps({'socrates':stock['enabled']}),))
                if crypto is not None:
                    self.crypto_store.configure(updated, db=db)
                db.execute('UPDATE authorization_generation SET generation=generation+1 WHERE id=1')
                db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                    (datetime.now(timezone.utc).isoformat(), 'strategy_settings', json.dumps(payload)))
        return self.snapshot()

    def save_settings(self,payload):
        if set(payload)!={'sizing_mode','target_dollars'}:
            raise ValueError('Only the purchase target can be changed')
        target=decimal(payload['target_dollars'])
        if payload['sizing_mode']!='target':
            raise ValueError('Automatic allocation is not defined by the recordings yet')
        if target<1 or target>decimal('1000000000000') or target!=target.quantize(decimal('.01')):
            raise ValueError('Target must be at least $1 with up to two decimal places')
        with self.portfolio.admit():
            with self.executor.entry_lock if self.executor else nullcontext():
                if self.executor and (self.executor.enabled() or self.store.active_trade()):
                    raise ValueError('Turn Socrates Off and wait for its current trade to finish before changing its size')
                with self.lock:
                    state = deepcopy(self.state)
                at = state['account_at']
                if state['account_error'] or not at or not 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(at)).total_seconds() <= 60:
                    raise ValueError('Wait for a current account balance')
                if state['positions'] or state['orders']:
                    self.portfolio.assert_exposure(state['account'].get('account_ref'), state['positions'], state['orders'],
                        requesting_family='socrates', symbol='QQQ')
                if target > decimal(state['account']['buying_power']):
                    raise ValueError('Target exceeds current buying power')
                normalized = {'sizing_mode':'target','target_dollars':str(target.quantize(decimal('.01')))}
                self.store.save(normalized)
        return normalized
