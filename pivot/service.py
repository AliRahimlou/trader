"""Account, analysis and order loops with separate locks and durable execution permission."""
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
        from .execution import Executor
        self.executor = Executor(broker, store) if broker else None
        self.workers = WorkerHealth(['account', 'data'] + (['execution'] if self.executor else []))
        self.archive = None
        self.native_capture = None
        try:
            self.archive = ObservationArchive(str(store.path) + '.observations.sqlite3')
        except Exception:
            pass  # Observation failure is visible below, independent of trading permission.
        from .feeds import ReadOnlyFeeds
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
        for name, function, delay in loops:
            thread=Thread(target=self._loop,args=(name,function,delay),name=f'pivot-{name}',daemon=True)
            self.threads.append(thread)
            thread.start()
        monitor = Thread(target=self._monitor_loop, name='pivot-monitor', daemon=True)
        self.threads.append(monitor)
        monitor.start()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=1)
        if self.native_capture and self.native_capture.thread:
            self.native_capture.thread.join(timeout=1)

    def _loop(self,name,function,delay):
        due = monotonic()
        while not self.stop_event.is_set():
            self.workers.begin(name)
            failed = False
            try:
                function()
            except Exception:
                failed = True
                with self.lock:
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
        except Exception:
            with self.lock:
                self.state['account_error']='Account update unavailable; showing the last confirmed snapshot'

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
            full_at=getattr(self.feeds, 'stock_last_full_at', None))
        index=vix_health(vix, checked_at, error=vix_error, details=getattr(self.feeds, 'vix_diagnostics', None))
        if markets and stocks['status'] != 'current' and not stock_error:
            errors.append('One or more required stock histories are missing, incomplete or stale')
        qqq=markets.get('QQQ')
        ready=stocks['status']=='current' and index['status']=='current'
        deadlines=[now+timedelta(seconds=90), *[m.observed_at+timedelta(seconds=90) for m in markets.values()]]
        if stocks.get('valid_until'): deadlines.append(datetime.fromisoformat(stocks['valid_until']))
        if index['valid_until']: deadlines.append(datetime.fromisoformat(index['valid_until']))
        with self.lock:
            self.state.update(data_health={'stocks':stocks, 'vix':index, 'ready':ready}, data_valid_until=min(deadlines).isoformat() if ready else None, analysis_at=checked_at.isoformat(),setup=analyze(qqq,markets,vix,checked_at) if qqq else None, observations=observations,data_errors=errors,
                market_context=market_context(qqq, checked_at),
                feeds={'stocks':qqq.source if qqq else 'unavailable',
                       'vix':'current' if index['status']=='current' else 'unavailable or delayed'})
        self._record_decision(markets, vix, checked_at)
        if ready and self.threads and not self.stop_event.is_set() and self.native_capture:
            # Optional history collection has its own bounded daemon and budget;
            # it never delays the account, strategy or order-management loops.
            try:
                self.native_capture.start(getattr(self.feeds, 'stock_sessions', {}), checked_at)
            except Exception:
                pass

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

    def set_live(self, payload):
        if not self.executor:
            raise ValueError('Broker execution is not configured')
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
        result['worker_health'] = self.worker_health()
        result['native_history'] = self.native_capture.status() if self.native_capture else {
            'status':'unavailable', 'research_only':True, 'live_entry_ready':False,
            'detail':'Separate native validation history is not configured.'}
        try:
            result['session_review'] = self.store.session_review()
        except Exception:
            result['session_review'] = {'status':'unavailable', 'historical_only':True}
        result.update(settings=self.store.settings(), rulebook=rulebook(), events=self.store.events(),
                      trade_results=self.store.trade_results(),
                      execution_policy=POLICY, version='video-execution-v5', runtime='Video strategies · owner-controlled execution', legacy_loaded=False)
        return result

    def save_settings(self,payload):
        if set(payload)!={'sizing_mode','target_dollars'}:
            raise ValueError('Only the purchase target can be changed')
        target=decimal(payload['target_dollars'])
        if payload['sizing_mode']!='target':
            raise ValueError('Automatic allocation is not defined by the recordings yet')
        if target<1 or target>decimal('1000000000000') or target!=target.quantize(decimal('.01')):
            raise ValueError('Target must be at least $1 with up to two decimal places')
        with self.executor.entry_lock if self.executor else nullcontext():
            if self.executor and (self.executor.enabled() or self.store.active_trade()):
                raise ValueError('Turn Live money off and wait for the current trade to finish before changing size')
            with self.lock:
                at=self.state['account_at']
                if self.state['account_error'] or not at or (datetime.now(timezone.utc)-datetime.fromisoformat(at)).total_seconds()>60:
                    raise ValueError('Wait for a current account balance')
                if self.state['positions'] or self.state['orders']:
                    raise ValueError('Existing positions and orders must finish before changing size')
                if target>decimal(self.state['account']['buying_power']):
                    raise ValueError('Target exceeds current buying power')
                normalized={'sizing_mode':'target','target_dollars':str(target.quantize(decimal('.01')))}
                self.store.save(normalized)
        return normalized
